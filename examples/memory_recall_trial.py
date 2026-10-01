"""Does a fact said in passing come back in a later conversation? Per store.

notes/100 and notes/101 built memory of the person, and notes/102 let the
memory live in a store behind an MCP server. Each was checked by hand, once
per feature. This measures the whole road, several facts at a time, the
same way on every store:

    session 1   a fact is said IN PASSING ("flights from RDU to Denver"),
                never "remember this"; the conversation ends, the look
                back (memory/reflect.py) proposes what to keep, and the
                trial keeps all of it -- a person answering "a"
    session 2   a FRESH agent, nothing carried over but the store, gets a
                question that needs the fact and does not name it ("find
                me flights to Austin"); the answer is graded by a pattern

and, per scenario, two things to compare against:

    baseline    the same later question from a DIFFERENT identity, which
                must start with nothing in its prompt -- so the answer
                shows what the model does without memory, and the prompt
                shows that one person's facts never reach another
    not kept    one scenario says something true for a week ("flying to
                SFO Tuesday"); keeping it is a failure of the look back

and once per run, a store that is down: the later question must still get
an answer, with the memory notice set.

BURYING THE FACT. With a handful of memories every store looks the same,
because the prompt layer tops up with the most recent ones (memory/
__init__.py) and the fact is among them. ``--distractors N`` keeps N
unrelated facts about the same person AFTER session 1, so the fact is the
oldest thing in the store and only the store's SEARCH can bring it into
the prompt. "Find me flights to Austin" shares no word with "lives near
RDU": word overlap (the ``local`` store) cannot find it, and a store that
searches by meaning might. The ``in prompt`` column is that measurement,
and it costs no model call.

    uv run python examples/memory_recall_trial.py \\
        --provider ollama --model qwen3.8:latest

    # the same, against a store behind an MCP server
    uv run python examples/memory_recall_trial.py --store smritikosh \\
        --mcp-config smritikosh.json --distractors 30

ONE RUN IS A PICTURE, NOT A RATE. The look back keeps a different fact
on a different run, and a model has off turns. ``--repeat N`` runs every
scenario N times, each under a fresh identity, and the report gives
counts with a 95% interval. Every row records its model, store and
distractors, so runs saved with ``--out`` can be graded together:

    uv run python examples/memory_recall_trial.py --repeat 5 --out qwen.jsonl
    uv run python examples/memory_recall_trial.py --repeat 5 \\
        --model gemma4:12b --out gemma.jsonl
    uv run python examples/memory_recall_trial.py --rescore qwen.jsonl gemma.jsonl

Given runs of more than one setting, ``--rescore`` reports each and then
puts them side by side.

Every scenario runs under an identity of its own (``trial-<run>-<id>``,
with ``-r<n>`` per repeat), and on an MCP store what the trial kept is
forgotten again at the end.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
import time
import uuid
from pathlib import Path

from yantra import get_provider, load_settings
from yantra.memory import PROMPT_LIMIT, MemoryItem, enable_memory
from yantra.memory.reflect import keep, reflect
from yantra.spec import AgentSpec

CASES = Path(__file__).resolve().parent / "memory_recall_cases.jsonl"

#: True of a person, and no help with any scenario's later question.
DISTRACTORS = [
    "Has a cat named Biscuit.", "Plays badminton on Sunday mornings.",
    "Is learning to play the ukulele.", "Prefers tea over coffee.",
    "Reads mostly science fiction.", "Has a younger brother in Toronto.",
    "Drives a 2019 Honda Civic.", "Is allergic to cats' dander but keeps one anyway.",
    "Supports Arsenal.", "Likes dark mode in every app.",
    "Grows tomatoes and basil on a balcony.", "Studied mechanical engineering.",
    "Keeps a paper notebook for to-do lists.", "Uses a split mechanical keyboard.",
    "Is training for a half marathon.", "Collects vintage postcards.",
    "Volunteers at a food bank monthly.", "Prefers window seats on trains.",
    "Has a standing desk.", "Listens to jazz while working.",
    "Takes photos with a Fujifilm camera.", "Is learning Japanese on Duolingo.",
    "Does not drink alcohol.", "Wakes up around 6am.",
    "Owns a Kindle Paperwhite.", "Likes board games, especially Catan.",
    "Has a sourdough starter named Gerald.", "Uses Firefox as the main browser.",
    "Prefers email over phone calls.", "Knits scarves in winter.",
    "Has two monitors at home.", "Enjoys bouldering at a local gym.",
    "Keeps houseplants, mostly pothos.", "Is saving for a trip to Iceland.",
    "Plays chess online most evenings.", "Writes a small blog about bread.",
    "Uses a Pixel phone.", "Likes spicy food.", "Rides a bike to work in summer.",
    "Has a vegetable CSA box every other week.",
]


def load_cases(path: Path, only: set[str] | None) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text().splitlines()
             if line.strip()]
    return [c for c in cases if not only or c["id"] in only]


def hit(pattern: str, text: str) -> bool:
    return bool(pattern) and re.search(pattern, text, re.I) is not None


def score(case: dict, row: dict) -> dict:
    """Every verdict a row carries, from what it recorded -- so a saved run
    can be graded again (--rescore) when a pattern turns out to be wrong."""
    row["kept_fact"] = any(hit(case["fact"], s) for s in row["kept"])
    if case.get("not_kept"):
        row["false_keep"] = any(hit(case["not_kept"], s) for s in row["kept"])
    if "later_answer" in row:
        if "search" in row:          # rows saved before these were kept
            row["rank"] = next((n for n, s in enumerate(row["search"], 1)
                                if hit(case["fact"], s)), None)
        if "prompt" in row:
            row["in_prompt"] = any(hit(case["fact"], s) for s in row["prompt"])
        row["passed"] = not row.get("error") and grade(case, row["later_answer"])
    if "baseline_answer" in row:
        row["baseline_passed"] = (not row.get("baseline_error")
                                  and grade(case, row["baseline_answer"]))
    return row


def grade(case: dict, answer: str) -> bool:
    """The later answer starts from the fact. ``expect`` must be there and
    ``avoid`` must not; with a ``rival`` -- the answer a model gives with no
    memory, "pip install" to "uv add" -- the fact must come FIRST, because
    "pip install ... or, with uv, uv add" was answered without it."""
    found = re.search(case["expect"], answer, re.I) if case["expect"] else None
    if found is None or hit(case.get("avoid", ""), answer):
        return False
    rival = re.search(case["rival"], answer, re.I) if case.get("rival") else None
    return rival is None or found.start() < rival.start()


class Trial:
    def __init__(self, args, provider, store) -> None:
        self.args = args
        self.provider = provider
        self.store = store

    def agent(self, user: str | None, store=None):
        """One conversation: a fresh agent, nothing shared but the store.
        Only the two memory tools, so a trial never touches the disk, and
        every tool call allowed -- ``remember`` asking would stall it."""
        spec = AgentSpec(tool_allow=("remember", "recall_memory"),
                         env_context="local", max_iterations=6)
        agent = spec.build(provider=self.provider,
                           provider_name=self.args.provider,
                           model=self.args.model,
                           permissions=lambda request: True)
        if user is not None:
            enable_memory(agent, store or self.store, user=user)
        return agent

    def turn(self, agent, text: str) -> tuple[str, list[str], str | None]:
        """The answer, the tools it called, and an error if it failed."""
        try:
            response = agent.run(text)
        except Exception as exc:  # a trial reports, never stops
            return "", [], f"{type(exc).__name__}: {exc}"
        calls = [call.name for message in agent.history
                 if message.role == "assistant"
                 for call in message.tool_calls()]
        return response.message.text(), calls, None

    def user(self, case: dict, run_id: str, rep: int) -> str:
        user = f"trial-{run_id}-{case['id']}"
        return user + f"-r{rep}" if getattr(self.args, "repeat", 1) > 1 else user

    def run_case(self, case: dict, run_id: str, rep: int = 1) -> dict:
        user = self.user(case, run_id, rep)
        row: dict = {"id": case["id"], "user": user, "rep": rep,
                     **setting_of(self.args)}
        started = time.time()

        # ---- session 1: said in passing, then the look back -------------
        one = self.agent(user)
        answer, calls, error = self.turn(one, case["said"])
        row.update(said_answer=answer, said_calls=calls, error=error)
        row["remember_called"] = "remember" in calls
        try:
            found = reflect(one)
        except Exception as exc:
            found, row["error"] = [], f"look back failed: {exc}"
        row["proposed"] = [c.statement for c in found]
        keep(one.memory, found)                 # the person says "a"
        kept = [i.statement for i in one.memory.list(200)]
        row["kept"] = kept

        if not case.get("later"):
            row["seconds"] = round(time.time() - started, 1)
            return score(case, row)

        # ---- between: newer, unrelated memories bury the fact -------------
        try:
            for statement in DISTRACTORS[:self.args.distractors]:
                one.memory.remember(statement, kind="fact")
        except Exception as exc:  # a store's rate limit, say: record it
            row["error"] = row.get("error") or f"distractors: {exc}"

        # ---- the store's own search, before any model sees it -------------
        try:
            row["search"] = [i.statement for i in
                             one.memory.recall(case["later"], PROMPT_LIMIT)]
        except Exception as exc:
            row["search"], row["error"] = [], f"recall failed: {exc}"
        row["stored"] = len(kept) + self.args.distractors

        # ---- session 2: a fresh agent, the later question -----------------
        two = self.agent(user)
        answer, calls, error = self.turn(two, case["later"])
        row.update(later_answer=answer, later_calls=calls)
        row["error"] = row.get("error") or error
        row["prompt"] = [i.statement for i in two.memory.in_prompt]
        row["recall_called"] = "recall_memory" in calls

        # ---- baseline: the same question from somebody else ---------------
        if not self.args.no_baseline:
            other = self.agent(f"{user}-other")
            answer, _, error = self.turn(other, case["later"])
            row["baseline_answer"] = answer
            row["isolated"] = not other.memory.in_prompt
            row["baseline_error"] = error
        row["seconds"] = round(time.time() - started, 1)
        return score(case, row)

    def store_down(self, case: dict) -> dict:
        """The later question with a store that fails every call."""
        agent = self.agent("trial-down", store=BrokenStore())
        answer, _, error = self.turn(agent, case["later"])
        return {"answered": bool(answer) and error is None,
                "notice": agent.memory.notice}

    def cleanup(self, users: list[str]) -> None:
        """Forget what the trial kept, on a store that outlives the run."""
        for user in users:
            for suffix in ("", "-other"):
                try:
                    for item in self.store.list(user + suffix, 500):
                        self.store.forget(user + suffix, item.id)
                except Exception as exc:
                    print(f"  cleanup of {user + suffix} failed: {exc}",
                          file=sys.stderr)


class BrokenStore:
    name = "down"

    def remember(self, *args) -> str:
        raise ConnectionError("store is down")

    def recall(self, *args) -> list[MemoryItem]:
        raise ConnectionError("store is down")

    forget = list = recall


def mcp_store(args):
    """A store behind an MCP server, connected and bound the way a host
    binds one (memory/mcp.py), without an agent to hang it on."""
    from yantra.mcp import MCPManager, load_mcp_configs
    from yantra.memory.mcp import McpStore
    from yantra.tools.base import ToolRegistry

    configs = [c for path in args.mcp_config for c in load_mcp_configs(path)]
    config = next((c for c in configs if c.name == args.store), None)
    if config is None:
        sys.exit(f"no server named {args.store!r} in --mcp-config")
    verbs = dict(pair.split("=", 1) for pair in args.verbs.split(",") if pair)
    manager = MCPManager(ToolRegistry())
    manager.connect(config)
    store = McpStore(args.store, verbs)
    prefix = f"mcp__{args.store}__"
    store.bind(lambda: manager.sessions.get(args.store),
               {n.removeprefix(prefix) for n in manager.tool_names[args.store]})
    return store, manager


def mark(value) -> str:
    return {True: "yes", False: "no", None: "-"}.get(value, str(value))


SETTING = ("provider", "model", "store", "distractors")


def setting_of(source) -> dict:
    """What a row was run with, so a pile of saved runs can be told apart."""
    get = source.get if isinstance(source, dict) else \
        lambda key, default=None: getattr(source, key, default)
    return {key: get(key) for key in SETTING}


def label(setting: dict) -> str:
    buried = f", buried {setting['distractors']}" if setting["distractors"] else ""
    return f"{setting['model']}, {setting['store']}{buried}"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """A 95% interval for a rate of k in n. Honest at the small n a trial
    has, where "5/5" still means somewhere above about 57%."""
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


def tally(values: list) -> str:
    """One case's cell: yes/no for a single run, a count over repeats."""
    if len(values) == 1:
        return mark(values[0])
    known = [v for v in values if v is not None]
    return f"{sum(bool(v) for v in known)}/{len(known)}" if known else "-"


def totals(rows: list[dict]) -> dict[str, tuple[int, int]]:
    """Every measure of one setting, as (hits, out of)."""
    graded = [r for r in rows if "passed" in r]
    searched = [r for r in graded if "rank" in r]
    baseline = [r for r in graded if "baseline_passed" in r]
    negatives = [r for r in rows if "false_keep" in r]
    out = {"kept": (sum(r["kept_fact"] for r in graded), len(graded))}
    if searched:
        out["found by search"] = (sum(bool(r["rank"]) for r in searched),
                                  len(searched))
    out["in prompt"] = (sum(r["in_prompt"] for r in graded), len(graded))
    out["answered from memory"] = (sum(r["passed"] for r in graded), len(graded))
    out["recall_memory called"] = (sum(r["recall_called"] for r in graded),
                                   len(graded))
    if baseline:
        out["baseline"] = (sum(r["baseline_passed"] for r in baseline), len(baseline))
        out["isolated"] = (sum(r["isolated"] for r in baseline), len(baseline))
    if negatives:
        out["kept what it should not"] = (sum(r["false_keep"] for r in negatives),
                                          len(negatives))
    return out


def report(rows: list[dict], down: dict | None, setting: dict) -> None:
    reps = max((r.get("rep", 1) for r in rows), default=1)
    print(f"\nstore={setting['store']} model={setting['model']} "
          f"distractors={setting['distractors']}"
          + (f" repeat={reps}" if reps > 1 else "") + "\n")
    head = ("case", "remember", "kept", "search", "in prompt", "answer",
            "recall", "baseline", "isolated")
    print("  ".join(f"{h:<10}" for h in head))
    by_case: dict[str, list[dict]] = {}
    for row in rows:
        if "passed" in row:
            by_case.setdefault(row["id"], []).append(row)
    for case_id, group in by_case.items():
        if len(group) == 1 and "rank" in group[0]:
            row = group[0]
            search = (f"#{row['rank']} of {row['stored']}" if row["rank"]
                      else "missed")
        elif all("rank" in r for r in group):
            search = f"{sum(bool(r['rank']) for r in group)}/{len(group)}"
        else:
            search = "-"
        answer = (("PASS" if group[0]["passed"] else "fail") if len(group) == 1
                  else f"{sum(r['passed'] for r in group)}/{len(group)}")
        cells = (case_id, tally([r["remember_called"] for r in group]),
                 tally([r["kept_fact"] for r in group]), search,
                 tally([r["in_prompt"] for r in group]), answer,
                 tally([r["recall_called"] for r in group]),
                 tally([r.get("baseline_passed") for r in group]),
                 tally([r.get("isolated") for r in group]))
        print("  ".join(f"{c:<10}" for c in cells))
    print("\n" + " · ".join(f"{name} {rate(k, n, reps > 1)}"
                            for name, (k, n) in totals(rows).items()))
    if down is not None:
        print("store down: " + ("answered, with notice: " + str(down["notice"])
                                if down["answered"] and down["notice"]
                                else f"FAILED ({down})"))
    for row in rows:
        where = row["id"] + (f" r{row['rep']}" if reps > 1 else "")
        if row.get("error"):
            print(f"  {where}: {row['error']}")
        elif "later_answer" in row and not row["later_answer"].strip():
            # Not a memory miss: the model said nothing at all. Counted as a
            # fail, and named, so nobody reads it as the store's fault.
            print(f"  {where}: empty answer (no text, no error)")


def compare(groups: dict[tuple, list[dict]]) -> None:
    """Several settings side by side: one column each, one measure a line."""
    columns = [(dict(zip(SETTING, key, strict=True)), totals(rows))
               for key, rows in groups.items()]
    measures = list(dict.fromkeys(m for _, t in columns for m in t))
    print("\n| | " + " | ".join(label(s) for s, _ in columns) + " |")
    print("|---" * (len(columns) + 1) + "|")
    for measure in measures:
        cells = [rate(*t[measure], True) if measure in t else "-"
                 for _, t in columns]
        print(f"| {measure} | " + " | ".join(cells) + " |")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", default="qwen3.8:latest")
    parser.add_argument("--store", default="local",
                        help="local, or the name of a server in --mcp-config")
    parser.add_argument("--mcp-config", action="append", default=[], type=Path)
    parser.add_argument("--verbs", default="",
                        help="verb=tool pairs, comma-separated, for a server "
                             "whose tools are named differently")
    parser.add_argument("--distractors", type=int, default=0,
                        help=f"unrelated memories kept after session 1 "
                             f"(max {len(DISTRACTORS)})")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--only", default="", help="case ids, comma-separated")
    parser.add_argument("--no-baseline", action="store_true",
                        help="skip the other-identity run (one call less a case)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="run every scenario N times, for a rate")
    parser.add_argument("--out", type=Path, help="write every row as JSON lines")
    parser.add_argument("--rescore", type=Path, nargs="+",
                        help="grade saved --out files again, calling nothing; "
                             "runs of different settings are compared")
    args = parser.parse_args()
    args.distractors = max(0, min(args.distractors, len(DISTRACTORS)))
    args.repeat = max(1, args.repeat)

    if args.rescore:
        return rescore(args)

    provider = get_provider(args.provider, load_settings(args.provider))
    manager = None
    if args.store == "local":
        from yantra.memory.local import LocalStore
        store = LocalStore(Path(tempfile.mkdtemp()) / "memory.sqlite")
    else:
        store, manager = mcp_store(args)

    trial = Trial(args, provider, store)
    run_id = uuid.uuid4().hex[:6]
    cases = load_cases(args.cases, set(filter(None, args.only.split(","))))
    rows: list[dict] = []
    users: list[str] = []
    # Each row is written the moment it is graded: a store or model that
    # dies forty minutes in costs the scenario it died in, not the run.
    out = args.out.open("w", encoding="utf-8") if args.out else None
    try:
        for rep in range(1, args.repeat + 1):
            for case in cases:
                tag = f" r{rep}" if args.repeat > 1 else ""
                print(f"{case['id']}{tag} ...", end=" ", flush=True)
                users.append(trial.user(case, run_id, rep))
                row = trial.run_case(case, run_id, rep)
                rows.append(row)
                if out is not None:
                    out.write(json.dumps(row) + "\n")
                    out.flush()
                verdict = ("PASS" if row.get("passed") else "fail") \
                    if "passed" in row \
                    else ("kept it" if row.get("false_keep") else "let it go")
                print(f"{verdict} ({row['seconds']}s)")
        first = next((c for c in cases if c.get("later")), None)
        down = trial.store_down(first) if first else None
    finally:
        if out is not None:
            out.close()
        if manager is not None:
            trial.cleanup(users)       # every case, even one that crashed
            manager.shutdown()
        provider.close()
    report(rows, down, setting_of(args))
    return 0


def rescore(args) -> int:
    """Grade saved runs again. A row saved before rows carried their
    setting takes it from the command line."""
    cases = {c["id"]: c for c in load_cases(args.cases, None)}
    fallback = setting_of(args)
    groups: dict[tuple, list[dict]] = {}
    for path in args.rescore:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            score(cases[row["id"]], row)
            setting = {k: row.get(k, fallback[k]) for k in SETTING}
            groups.setdefault(tuple(setting[k] for k in SETTING), []).append(row)
    for key, rows in groups.items():
        report(rows, None, dict(zip(SETTING, key, strict=True)))
    if len(groups) > 1:
        compare(groups)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
