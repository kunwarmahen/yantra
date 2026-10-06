"""Does the agent offer a schedule at the right moments, and say *when* right?

notes/115 linked Yantra to Samay: the agent can offer to do something
later or on a repeat, with ``preview_schedule`` and ``create_schedule``,
and the person approves a card. That was checked by hand on one message.
This measures it, several messages at a time, the same way on any model:

    turn 1   the person's message, as they would type it ("check my mail
             every 2 hours and tell me if anything needs me")
    turn 2   "Yes, please set that up." -- only when turn 1 offered and
             did not already create it

and grades, per message, what a person would care about:

    offered     previewed (or created) a schedule where one fits; for
                the four messages where one does NOT fit ("what's in
                README.md?"), any Samay call is a false offer
    asked       previewed before creating, rather than creating at once
    when        the ``when`` it saved reads the same as the expected one.
                Samay is the judge: both go through ``samay preview
                --json``, and the readings (the sentence before "-- next")
                must match -- {"every": "60m"} and {"every": "1h"} are the
                same schedule, and Samay says so
    notify      when_new for a check, always for a digest or a reminder
    allow_tools names that match no tool (a small model's invention);
                RISKY grants -- tools that act on the person's accounts or
                machine (send_message, bash, file writes) on a job that
                only reads; and MISSING ones -- a site check allowed
                neither web_fetch nor browser_open, so at its time it is
                refused. Reads need no entry: they run unasked anyway

NOTHING REAL IS TOUCHED. Samay's state is a fresh folder per message, the
person's mail is four stub tools named the way Setu names a merged Gmail
(``mcp__gmail__search_threads`` reads; ``send_message`` and
``create_draft`` change things), and every write but ``create_schedule``
is refused by the gate. The rest of the tool set is a stock build.

    uv run python examples/schedule_offer_trial.py \\
        --provider ollama --model qwen3.8:latest --out qwen.jsonl
    uv run python examples/schedule_offer_trial.py \\
        --provider ollama --model gemma4:12b --out gemma.jsonl
    uv run python examples/schedule_offer_trial.py --rescore qwen.jsonl gemma.jsonl

``--cases examples/schedule_offer_sites.jsonl`` sends six site checks
instead, written after the prompt layer last changed and never used to
choose a wording: a change to how tools are allowed is judged there, on
messages it was not fitted to (notes/118).

``--samay PATH`` names the samay program (default: ``$YANTRA_SAMAY``, then
PATH). ``--repeat N`` runs every message N times; counts then carry a 95%
interval. Expected times are relative to now ("tomorrow at 3pm"), so
``{tomorrow}`` in a case is filled in when it runs.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from yantra import get_provider, load_settings
from yantra.mcp import MCPManager
from yantra.samay_link import Samay, SamayLinkError, load
from yantra.spec import AgentSpec
from yantra.tools.base import Tool

CASES = Path(__file__).resolve().parent / "schedule_offer_cases.jsonl"
#: Site checks written AFTER the prompt layer was last changed, and never
#: tuned against: what a change to that layer is judged on (notes/117).
SITES = CASES.with_name("schedule_offer_sites.jsonl")


def every_case() -> dict[str, dict]:
    """Both sets, by id, so a saved row is graded whichever set it came from."""
    return {c["id"]: c for path in (CASES, SITES) for c in load_cases(path, None)}
YES = "Yes, please set that up."
#: Words that offer a schedule without calling a tool: counted apart,
#: because an offer the model never previewed has no sentence behind it.
OFFER_WORDS = re.compile(
    r"\b(schedul\w*|recurring|set (this|that|it) up|every (day|morning|weekday|"
    r"\d+ ?(h|hours?|min\w*))|remind you)\b", re.IGNORECASE)
SAMAY = "mcp__samay__"
#: Allowed ahead of time, these act on the person's accounts or machine.
#: Network reads (web_fetch, browser_open) are writes to the gate but
#: not here: a job that checks a site cannot run without one.
RISKY = ("mcp__gmail__send_message", "mcp__gmail__create_draft", "bash", "bash_start",
         "bash_kill", "write_file", "edit_file", "browser_fill", "write_note")


class StubMail(Tool):
    """The person's Gmail, as Setu would name a merged connector's tools.
    Reads answer with three canned threads; writes never get this far."""

    description = ""
    parameters: dict = {"type": "object", "properties": {
        "query": {"type": "string"}, "id": {"type": "string"},
        "to": {"type": "string"}, "body": {"type": "string"}}}

    def __init__(self, raw: str, read_only: bool, text: str) -> None:
        self.name = f"mcp__gmail__{raw}"
        self.read_only = read_only
        self.description = text

    def summary(self, args, ctx) -> str:
        return f"{self.name}({json.dumps(args)})"

    def run(self, args, ctx) -> str:
        return ("3 threads, newest first:\n"
                "t1  Acme Billing <billing@acme.test>  Invoice 4471 due Friday  (unread)\n"
                "t2  Priya (landlord) <priya@rent.test>  Re: lease renewal  (read)\n"
                "t3  HN Digest <digest@hn.test>  This week on Hacker News  (unread)")


STUBS = [("search_threads", True, "Search the person's Gmail (you@gmail.com)."),
         ("get_thread", True, "Read one Gmail thread by id."),
         ("send_message", False, "Send an email from the person's Gmail."),
         ("create_draft", False, "Save a draft in the person's Gmail.")]


def load_cases(path: Path, only: set[str] | None) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [c for c in cases if not only or c["id"] in only]


def reading(program: str, state: Path, when, tz: str | None) -> tuple[str, list] | None:
    """Samay's own reading of a ``when``: the sentence before "-- next",
    and the next three times. None when Samay cannot read it."""
    argv = [program, "--state", str(state), "preview",
            when if isinstance(when, str) else json.dumps(when), "--json"]
    argv += ["--tz", tz] if tz else []
    done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if done.returncode != 0:
        return None
    said = json.loads(done.stdout)
    return said["sentence"].split(" -- next")[0].strip(), said.get("next") or []


def same_when(got, wanted) -> bool:
    """The same schedule: Samay reads them alike, or -- for two spellings
    of one time, like {"at": "18:00", "days": "sun"} and the cron line
    "0 18 * * 0" -- they run at the same next times."""
    return got is not None and any(
        w is not None and (got[0] == w[0] or (got[1] and got[1] == w[1])) for w in wanted)


def fill(when, today: date):
    """``{tomorrow}`` in an expected ``when``, made a date."""
    text = json.dumps(when).replace("{tomorrow}", (today + timedelta(days=1)).isoformat())
    return json.loads(text)


class Trial:
    def __init__(self, args) -> None:
        self.args = args
        self.provider = get_provider(args.provider, load_settings(args.provider))

    def run_case(self, case: dict, rep: int) -> dict:
        row: dict = {"id": case["id"], "rep": rep, "offer": case["offer"],
                     "model": self.args.model, "provider": self.args.provider}
        started = time.time()
        with tempfile.TemporaryDirectory(prefix="schedule-trial-") as tmp:
            work = Path(tmp)
            (work / "README.md").write_text("# Notes\n\nA folder of meeting notes.\n")
            os.environ["SAMAY_STATE"] = str(work / "samay")
            os.environ["YANTRA_BROWSER_PROFILE"] = str(work / "profile")
            asked: list[dict] = []

            def gate(request) -> bool:
                # as a session in ask mode: reads run unasked; of the
                # writes, only the schedule is said yes to (the card)
                if request.read_only:
                    return True
                yes = request.tool_name == f"{SAMAY}create_schedule"
                asked.append({"tool": request.tool_name, "allowed": yes})
                return yes

            agent = AgentSpec(env_context="local", max_iterations=12).build(
                provider=self.provider, provider_name=self.args.provider,
                model=self.args.model, permissions=gate, cwd=work)
            for raw, read_only, text in STUBS:
                agent.registry.register(StubMail(raw, read_only, text))
            manager = MCPManager(agent.registry, agent=agent,
                                 memory_path=work / ".yantra" / "mcp.json")
            try:
                found = load("on", self.args.samay)
                assert found is not None
                samay = Samay(mode="on", data=found[0], program=found[1])
                samay.connect(manager, agent)
                one, text1, err = self.turn(agent, case["say"])
                row.update(error=err, answer1=text1[:400])
                two: list = []
                created = [c for c in one if c["name"] == f"{SAMAY}create_schedule"]
                previewed = [c for c in one if c["name"] == f"{SAMAY}preview_schedule"]
                if case["offer"] != "no" and not created and (
                        previewed or OFFER_WORDS.search(text1)):
                    two, text2, err2 = self.turn(agent, YES)
                    row.update(answer2=text2[:400], error=err or err2)
                self.grade(case, row, one, two, text1, agent.registry, work)
            finally:
                manager.shutdown()
        row["denied"] = [a["tool"] for a in asked if not a["allowed"]]
        row["seconds"] = round(time.time() - started, 1)
        return row

    def turn(self, agent, text: str) -> tuple[list[dict], str, str | None]:
        """This turn's tool calls (name, arguments), its answer, an error."""
        before = len(agent.history)
        try:
            response = agent.run(text)
        except Exception as exc:  # a trial reports, never stops
            return [], "", f"{type(exc).__name__}: {exc}"
        calls = [{"name": call.name, "args": call.arguments}
                 for message in agent.history[before:] if message.role == "assistant"
                 for call in message.tool_calls()]
        return calls, response.message.text(), None

    def grade(self, case, row, one, two, text1, registry, work) -> None:
        def named(calls, verb):
            return [c for c in calls if c["name"] == f"{SAMAY}{verb}"]

        samay_one = [c for c in one if c["name"].startswith(SAMAY)
                     and c["name"] != f"{SAMAY}list_schedules"]
        row["previewed"] = bool(named(one, "preview_schedule"))
        row["created_at_once"] = bool(named(one, "create_schedule"))
        row["created_after_yes"] = bool(named(two, "create_schedule"))
        row["offered"] = bool(samay_one)
        row["offered_in_words"] = not samay_one and bool(OFFER_WORDS.search(text1))
        made = (named(one, "create_schedule") + named(two, "create_schedule"))
        source = made[-1]["args"] if made else (
            named(one, "preview_schedule")[-1]["args"] if row["previewed"] else None)
        row["when"] = source.get("when") if source else None
        row["tz"] = source.get("tz") if source else None
        if made:
            args = made[-1]["args"]
            row["notify"] = args.get("notify") or "when_new"
            allow = args.get("allow_tools") or []
            allow = [allow] if isinstance(allow, str) else list(allow)
            row["allow_tools"] = allow
            names = [n for n in registry.names() if not n.startswith(SAMAY)]
            row["unknown_tools"] = [p for p in allow
                                    if not any(fnmatch.fnmatchcase(n, str(p)) for n in names)]
            row["write_tools"] = sorted({n for p in allow for n in names
                                         if fnmatch.fnmatchcase(n, str(p))
                                         and not registry.get(n).read_only})
            row["prompt_chars"] = len(str(args.get("prompt") or ""))
        expected = case.get("when")
        if expected and row["when"] is not None:
            program, state = self.args.samay, work / "samay"
            got = reading(program, state, row["when"], row["tz"])
            wanted = [reading(program, state, fill(w, date.today()), row["tz"])
                      for w in expected]
            row["when_read"] = got[0] if got else None
            row["when_expected"] = [w[0] if w else None for w in wanted]
            row["when_ok"] = same_when(got, wanted)
        if case.get("notify") and made:
            row["notify_ok"] = row["notify"] == case["notify"]


# ---- the report -------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """A 95% interval for a rate of k in n (as in memory_recall_trial)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(k: int, n: int, interval: bool) -> str:
    if not interval or n == 0:
        return f"{k}/{n}"
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({round(100 * lo)}-{round(100 * hi)}%)"


def risky(row: dict) -> list[str]:
    return [t for t in row.get("write_tools") or [] if t in RISKY]


def missing(row: dict, case: dict) -> bool:
    """A job that needs one of ``needs`` and was allowed none: at its
    time, nobody is there, and the tool is refused."""
    needs = case.get("needs") or []
    return bool(needs) and not set(needs) & set(row.get("write_tools") or [])


def totals(rows: list[dict]) -> dict[str, tuple[int, int]]:
    cases = every_case()
    yes = [r for r in rows if r["offer"] == "yes"]
    no = [r for r in rows if r["offer"] == "no"]
    may = [r for r in rows if r["offer"] == "may"]
    made = [r for r in rows if r.get("created_at_once") or r.get("created_after_yes")]
    timed = [r for r in rows if "when_ok" in r]
    told = [r for r in rows if "notify_ok" in r]
    return {
        "offered where it fits": (sum(r["offered"] for r in yes), len(yes)),
        "  only in words, no preview": (sum(r["offered_in_words"] for r in yes), len(yes)),
        "previewed before creating": (
            sum(r["previewed"] and not r["created_at_once"] for r in made), len(made)),
        "saved after the yes": (sum(bool(r.get("created_after_yes")
                                         or r.get("created_at_once")) for r in yes), len(yes)),
        "when read right": (sum(r["when_ok"] for r in timed), len(timed)),
        "notify as expected": (sum(r["notify_ok"] for r in told), len(told)),
        "named a tool that doesn't exist": (sum(bool(r.get("unknown_tools")) for r in made),
                                            len(made)),
        "allowed something risky": (sum(bool(risky(r)) for r in made), len(made)),
        "left out a tool the job needs": (
            sum(missing(r, cases[r["id"]]) for r in made if cases[r["id"]].get("needs")),
            sum(1 for r in made if cases[r["id"]].get("needs"))),
        "false offer where none fits": (sum(r["offered"] for r in no), len(no)),
        "offered on the optional one": (sum(r["offered"] for r in may), len(may)),
        "errors": (sum(bool(r.get("error")) for r in rows), len(rows)),
    }


def report(rows: list[dict]) -> None:
    cases = every_case()
    interval = max((r["rep"] for r in rows), default=1) > 1
    print(f"\n{rows[0]['model']} ({rows[0]['provider']}) -- "
          f"{len({r['id'] for r in rows})} messages x {max(r['rep'] for r in rows)}")
    for name, (k, n) in totals(rows).items():
        print(f"  {name:36} {rate(k, n, interval)}")
    print("\n  per message:")
    for r in rows:
        bits = [("offered" if r["offered"] else "words" if r["offered_in_words"]
                 else "no offer")]
        if r.get("created_at_once"):
            bits.append("CREATED AT ONCE")
        if "when_ok" in r:
            bits.append(f"when {'ok' if r['when_ok'] else 'WRONG'}: {r.get('when_read')}"
                        + ("" if r["when_ok"] else f" (wanted {r['when_expected'][0]})"))
        elif r.get("when") is not None:
            bits.append(f"when: {json.dumps(r['when'])}")
        if r.get("notify"):
            bits.append(f"notify {r['notify']}" + ("" if r.get("notify_ok", True) else " (!)"))
        if r.get("unknown_tools"):
            bits.append(f"NO SUCH TOOL {r['unknown_tools']}")
        if risky(r):
            bits.append(f"RISKY {', '.join(t.split('__')[-1] for t in risky(r))}")
        if (r.get("created_at_once") or r.get("created_after_yes")) and missing(
                r, cases[r["id"]]):
            bits.append("MISSING " + " or ".join(cases[r["id"]]["needs"]))
        if r.get("error"):
            bits.append(f"error: {r['error'][:80]}")
        print(f"  {r['id']:13} [{r['offer']:3}] {' · '.join(bits)}  ({r['seconds']}s)")


def regrade(rows: list[dict], program: str) -> None:
    """A saved row marked wrong, read again by today's rule (same next
    times counts). Absolute times only: a {tomorrow} case would compare
    against a different day than the one it ran on, so it is left be."""
    cases = every_case()
    with tempfile.TemporaryDirectory() as tmp:
        for r in rows:
            expected = cases[r["id"]].get("when") or []
            if r.get("when_ok") is not False or "{tomorrow}" in json.dumps(expected):
                continue
            got = reading(program, Path(tmp), r["when"], r.get("tz"))
            r["when_ok"] = same_when(got, [reading(program, Path(tmp), w, r.get("tz"))
                                           for w in expected])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", default="qwen3.8:latest")
    parser.add_argument("--samay", default=os.environ.get("YANTRA_SAMAY")
                        or shutil.which("samay"), help="the samay program")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--cases", type=Path, default=CASES,
                        help="the messages to send (default: the fourteen of "
                             "notes/116; examples/schedule_offer_sites.jsonl is "
                             "six site checks held out from tuning)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--out", help="append each row to this JSONL file")
    parser.add_argument("--rescore", nargs="+", metavar="JSONL",
                        help="report saved rows instead of running")
    args = parser.parse_args()
    if args.rescore:
        rows = [json.loads(line) for path in args.rescore
                for line in Path(path).read_text().splitlines() if line.strip()]
        if args.samay:
            regrade(rows, args.samay)
        for model in dict.fromkeys(r["model"] for r in rows):
            report([r for r in rows if r["model"] == model])
        return 0
    if not args.samay:
        print("error: no samay program: --samay PATH, or YANTRA_SAMAY", file=sys.stderr)
        return 2
    try:
        load("on", args.samay)
    except SamayLinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    trial = Trial(args)
    cases = load_cases(args.cases, set(args.only.split(",")) if args.only else None)
    rows = []
    for rep in range(1, args.repeat + 1):
        for case in cases:
            row = trial.run_case(case, rep)
            rows.append(row)
            print(f"  {case['id']} r{rep}: offered={row['offered']} when_ok={row.get('when_ok')} "
                  f"({row['seconds']}s)", flush=True)
            if args.out:
                with open(args.out, "a") as out:
                    out.write(json.dumps(row) + "\n")
    report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
