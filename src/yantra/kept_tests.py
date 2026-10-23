"""A conversation that went well, kept as a test that runs again tonight.

A package's suite (eval_suite.py) is written by its author before
anybody has used the agent, on made-up cases. The cases that matter
most to a person are the ones they already had: "what did Amazon
refund me in May", asked last Tuesday and answered right. History
(history.py) keeps every one of those, word for word and tool by tool,
so the test is already written down -- what was asked, which tools
answered, what they said, and what the agent made of it. This module
turns one into a ``[[case]]`` plus a RECORDING beside it, and gives the
eval runner the fakes that answer from that recording.

NOTHING REAL RUNS. Every tool the agent is offered tonight is a stand-in
that answers from what was recorded, writes included: a test that sent
the email again, or pressed Buy on the phone again, is not a test
anybody can run every night. That is also why a fake says it is
read-only -- it is, whatever the tool it stands for would have done.

SAME NAME, BEST MATCH. A model asked the same question again rarely
calls a tool with the same words twice, so an exact-match replay would
go red every time it rephrased a search. The fake answers:

    the same tool, the same arguments    -> what that call returned
    the same tool, other arguments       -> the recording of that tool
                                            sharing the most arguments
    a tool never called in the recording -> "not recorded in this test"

The last one is information, not an error the model has to fight: an
agent that wanders into a tool it never needed is told so, and either
comes back or goes red on the answer.

THE AGENT UNDER TEST IS TONIGHT'S. Its prompt, its package and its tool
list are whatever they are when the run starts -- that is the point of
running it: a changed prompt or a swapped model is what the test is
for. A tool the recording used that tonight's agent does not have (a
Setu connection the eval run does not start, a phone tool) is offered
as a stand-in from the recording, so a test kept on Telegram still
runs from a timer with nothing connected.

THREE CHECKS, chosen by the person (cases.toml keys, eval_suite.py):
the tools the kept turn used must be used again (``required_tools``);
the answer must contain the words they typed when they kept it
(``answer_contains``); and the model judges tonight's answer against
the kept one (``judge``) -- a die roll of its own, which is why it is
one of three and never the only one.

EVERY NIGHT, EVERY SUITE. ``yantra --tests`` runs each kept suite under
the root -- yours, one folder per agent, and each Dvara person's under
``people/<person>/`` -- as its own ``--eval --kept`` process against the
agent it was kept from (``agent.json`` beside its cases says which
package), writes each run's report into the suite's ``runs/``, compares
it with the one before, and leaves one summary at the root
(``last.json``, ``yantra.tests.v1``): how many passed, which broke and
which were fixed since. A clock (Samay) starts it; a page (Sarathi's
home) reads the summary. Nothing here decides when.

    YANTRA_TESTS=PATH        kept tests live under PATH instead
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from yantra.errors import ConfigError, ToolError
from yantra.eval_suite import _toml_list, _toml_str
from yantra.tools.base import Tool, ToolContext, ToolRegistry

#: The folder under a suite that holds each case's recording.
REPLAY_DIR = "replay"
#: Beside a suite's cases: which agent it tests, and whose it is.
ABOUT = "agent.json"
#: Under a suite: each night's report, one file per run.
RUNS_DIR = "runs"
#: At the root: the last night's summary, what a page reads.
LAST = "last.json"
#: The summary's shape. A reader refuses one it does not know.
TESTS_FORMAT = "yantra.tests.v1"
#: Under the root: Dvara's people, one folder each.
PEOPLE_DIR = "people"
#: What a fake says for a tool the recording never called.
NOT_RECORDED = ("not recorded in this test: {name} was not used when this "
                "conversation was kept, so it has no answer here. Answer "
                "with what the other tools gave you.")
#: What the judge is told to look for. Facts, not wording: a local model
#: rephrases every time, and a judge that wanted the same sentence back
#: would be grading style.
JUDGE_CRITERIA = ("Does the candidate answer give the same facts as the "
                  "reference answer (names, numbers, dates, yes/no)? "
                  "Different wording, order or length is fine. Extra "
                  "detail is fine unless it contradicts the reference.")

_HEAD = """\
# Conversations you kept as tests. Each [[case]] replays one turn with
# the tools answering from its recording in replay/ -- nothing real runs.
# Written by Yantra when you keep a conversation; yours to edit or delete.
"""


def default_root() -> Path:
    """Where kept tests live: beside History, one folder per agent."""
    chosen = os.environ.get("YANTRA_TESTS")
    if chosen:
        return Path(chosen)
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "yantra" / "tests"


def suite_dir(agent_name: str | None, root: Path | None = None) -> Path:
    """One agent's kept tests: ``<root>/<agent>/``. An agent with no
    package name is ``yantra``, the agent you get with no --agent."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", agent_name or "").strip("-.") or "yantra"
    return (root or default_root()) / slug


# ---- what was recorded ---------------------------------------------------------


@dataclass(frozen=True)
class Call:
    """One tool call the kept conversation made, and what it returned."""
    name: str
    arguments: dict[str, Any]
    content: str
    is_error: bool = False


@dataclass
class Recording:
    """Everything a replay needs, read from one kept conversation."""
    question: str
    answer: str
    #: The conversation BEFORE the kept turn, in the checkpoint's shape
    #: (session._dump_message): the turn is asked with it behind it, the
    #: way it was asked the first time.
    before: list[dict] = field(default_factory=list)
    #: Every call in the whole conversation, not only the kept turn's: an
    #: answer the agent gave earlier is still an answer it may look up.
    calls: list[Call] = field(default_factory=list)
    #: The tools the kept turn itself used, in order, once each.
    used: list[str] = field(default_factory=list)
    #: Name -> {description, parameters} for the tools that were called,
    #: when the conversation was kept from a live session that knew them.
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    model: str = ""
    kept_at: str = ""
    conversation: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"format": 1, "question": self.question, "answer": self.answer,
                "before": self.before,
                "calls": [c.__dict__ for c in self.calls],
                "used": self.used, "tools": self.tools, "model": self.model,
                "kept_at": self.kept_at, "conversation": self.conversation}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Recording:
        return cls(question=raw.get("question", ""), answer=raw.get("answer", ""),
                   before=list(raw.get("before") or []),
                   calls=[Call(c["name"], c.get("arguments") or {},
                               c.get("content", ""), bool(c.get("is_error")))
                          for c in raw.get("calls") or []],
                   used=list(raw.get("used") or []),
                   tools=dict(raw.get("tools") or {}),
                   model=raw.get("model", ""), kept_at=raw.get("kept_at", ""),
                   conversation=raw.get("conversation", ""))


def read_recording(path: Path) -> Recording:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"{path}: cannot read the recording: {exc}") from None
    if not isinstance(raw, dict) or raw.get("format") != 1:
        raise ConfigError(f"{path}: not a recording this Yantra can replay")
    return Recording.from_json(raw)


def _text(message: dict) -> str:
    return "\n".join(b.get("text", "") for b in message.get("content") or []
                     if b.get("kind") == "text").strip()


def asked_at(payload: dict) -> list[int]:
    """Where in the history each thing the person asked is: turn 1 is the
    first. A tool's result rides in a user message too and is not a turn."""
    return [i for i, m in enumerate(payload.get("history") or [])
            if m.get("role") == "user" and _text(m)]


def from_payload(payload: dict, *, conversation: str = "",
                 tools: dict[str, dict[str, Any]] | None = None,
                 turn: int | None = None) -> Recording:
    """One turn of a kept conversation, with everything before it: the
    LAST by default, or ``turn`` (1 is the first thing the person asked).

    The last turn is the usual one -- it is what the person was looking
    at when they said "this went well". A good answer earlier in a long
    conversation is kept by its number. ONLY WHAT WAS KNOWN THEN: the
    calls recorded are the ones made up to the end of that turn. A later
    call's answer was not there the first time, and a replay that could
    look it up would be asking an easier question than the one kept.
    """
    history = list(payload.get("history") or [])
    turns = asked_at(payload)
    if not turns:
        raise ConfigError("that conversation has nothing you asked in it")
    if turn is not None and not 1 <= turn <= len(turns):
        raise ConfigError(f"that conversation has {len(turns)} turn"
                          f"{'s' if len(turns) != 1 else ''}, not {turn}")
    place = len(turns) if turn is None else turn
    asked = turns[place - 1]
    end = turns[place] if place < len(turns) else len(history)
    results: dict[str, dict] = {}
    for message in history[:end]:
        for block in message.get("content") or []:
            if block.get("kind") == "tool_result":
                results[block.get("tool_call_id", "")] = block
    calls: list[Call] = []
    used: list[str] = []
    answer = ""
    for i, message in enumerate(history[:end]):
        for block in message.get("content") or []:
            if block.get("kind") != "tool_call":
                continue
            result = results.get(block.get("id", ""))
            if result is None:
                continue        # asked and never answered: a held call
            calls.append(Call(block.get("name", ""), block.get("arguments") or {},
                              str(result.get("content", "")),
                              bool(result.get("is_error"))))
            if i > asked and block.get("name") not in used:
                used.append(block.get("name", ""))
        if i > asked and message.get("role") == "assistant" and _text(message):
            answer = _text(message)
    if not answer:
        raise ConfigError("that turn has no answer yet -- keep it once the "
                          "agent has replied")
    names = {c.name for c in calls}
    return Recording(
        question=_text(history[asked]), answer=answer, before=history[:asked],
        calls=calls, used=used,
        tools={k: v for k, v in (tools or {}).items() if k in names},
        model=payload.get("model") or "",
        kept_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        conversation=conversation)


def roster_of(registry: ToolRegistry | None) -> dict[str, dict[str, Any]]:
    """A live session's tools as a recording keeps them: what a stand-in
    needs to look like the real one to the model."""
    if registry is None:
        return {}
    return {t.name: {"description": t.description, "parameters": t.parameters}
            for t in registry}


# ---- keeping one ---------------------------------------------------------------


def keep(recording: Recording, directory: Path, *, words: Sequence[str] = (),
         title: str = "", agent: str | None = None, package: Path | None = None,
         person: str = "") -> str:
    """Write ``recording`` into the suite at ``directory`` as a new case;
    returns its id. The suite file is created the first time, and each
    keep appends one ``[[case]]`` -- never rewrites the ones above it,
    which may have been edited by hand since."""
    base = f"kept-{(recording.conversation or 'chat')[:8]}"
    cases = directory / "cases.toml"
    existing = cases.read_text(encoding="utf-8") if cases.is_file() else ""
    case_id, n = base, 2
    while f'id = "{case_id}"' in existing:
        case_id, n = f"{base}-{n}", n + 1
    replay = directory / REPLAY_DIR / f"{case_id}.json"
    replay.parent.mkdir(parents=True, exist_ok=True)
    # Rewritten on every keep: the agent's package may have moved, and
    # the night run should build the one the person is using now.
    (directory / ABOUT).write_text(json.dumps(
        {"agent": agent or "yantra", "package": str(package) if package else "",
         "person": person}, indent=1) + "\n", encoding="utf-8")
    replay.write_text(json.dumps(recording.to_json(), ensure_ascii=False, indent=1),
                      encoding="utf-8")
    words = [w.strip() for w in words if w.strip()]
    described = (title or recording.question)[:80].replace("\n", " ")
    described += (f" (kept {recording.kept_at[:10]}, answered by "
                  f"{recording.model or 'unknown'})")
    lines = ["", "[[case]]", f"id = {_toml_str(case_id)}",
             f"description = {_toml_str(described)}",
             f"user_message = {_toml_str(recording.question)}",
             f"replay = {_toml_str(f'{REPLAY_DIR}/{case_id}.json')}",
             "judge = true"]
    if recording.used:
        lines.append(f"required_tools = {_toml_list(recording.used)}")
    if words:
        lines.append(f"answer_contains = {_toml_list(words)}")
    with cases.open("a", encoding="utf-8") as handle:
        if not existing:
            handle.write(_HEAD)
        handle.write("\n".join(lines) + "\n")
    return case_id


# ---- the fakes -----------------------------------------------------------------


def best_match(calls: Sequence[Call], name: str, args: dict[str, Any]) -> Call | None:
    """SAME NAME, BEST MATCH (module docstring): the identical call if
    there was one, else the call to this tool sharing the most argument
    values, the earliest on a tie; None when this tool was never called."""
    mine = [c for c in calls if c.name == name]
    if not mine:
        return None
    for call in mine:
        if call.arguments == args:
            return call
    def shared(call: Call) -> int:
        return sum(1 for k, v in args.items() if call.arguments.get(k) == v)
    return max(mine, key=shared)        # max keeps the first of equals


class ReplayTool(Tool):
    """A tool that answers from the recording and never runs the real one.

    Same identity as what it stands for (name, description, schema), so
    the model sees tonight's tool list exactly. READ-ONLY WHATEVER IT
    STANDS FOR, because nothing it does reaches anything.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[dict[str, Any]] = {}
    read_only: ClassVar[bool] = True

    def __init__(self, name: str, description: str, parameters: dict[str, Any],
                 recording: Recording) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self._recording = recording

    @classmethod
    def standing_for(cls, tool: Tool, recording: Recording) -> ReplayTool:
        return cls(tool.name, tool.description, tool.parameters, recording)

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return f"{self.name} (replayed from the recording)"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        call = best_match(self._recording.calls, self.name, args)
        if call is None:
            return NOT_RECORDED.format(name=self.name)
        if call.is_error:
            raise ToolError(call.content)
        return call.content


def stand_ins(recording: Recording, present: Sequence[str]) -> list[ReplayTool]:
    """Fakes for the tools the recording used that tonight's agent does
    not have. Shaped from the recording when it kept the schema, else as
    a tool taking any arguments -- enough for the model to call it."""
    out = []
    for name in dict.fromkeys(c.name for c in recording.calls):
        if name in present:
            continue
        known = recording.tools.get(name) or {}
        out.append(ReplayTool(
            name, known.get("description") or f"{name} (as recorded)",
            known.get("parameters") or {"type": "object", "properties": {}},
            recording))
    return out


def people_root(person: str, root: Path | None = None) -> Path:
    """A Dvara person's kept tests, under the same root as everyone's, so
    one night run reads them all: ``<root>/people/<person>/<agent>/``."""
    slug = re.sub(r"[^A-Za-z0-9_.@-]+", "-", person).strip("-.") or "someone"
    return (root or default_root()) / PEOPLE_DIR / slug


def keep_from_log(log: Any, conversation_id: str, agent_name: str | None, *,
                  words: Sequence[str] = (), registry: ToolRegistry | None = None,
                  root: Path | None = None, package: Path | None = None,
                  person: str = "", turn: int | None = None
                  ) -> tuple[str, Path, Recording]:
    """One History conversation kept as a test of ``agent_name``: the
    one road every surface takes (the command, /chats test, the page,
    Dvara). ``registry`` is the live session's, when there is one, so the
    stand-ins look like the real tools."""
    payload = log.payload(conversation_id)
    if payload is None:
        raise ConfigError("that conversation is gone")
    found = [r for r in log.listed(limit=100_000) if r.id == conversation_id]
    recording = from_payload(payload, conversation=conversation_id,
                             tools=roster_of(registry), turn=turn)
    directory = suite_dir(agent_name, root)
    case_id = keep(recording, directory, words=words,
                   title=found[0].title if found else "", agent=agent_name,
                   package=package, person=person)
    return case_id, directory, recording


# ---- what is kept, and taking one back -----------------------------------------


@dataclass
class Kept:
    """One kept test as a list shows it, with how it did last time."""
    id: str
    question: str
    words: list[str]
    tools: list[str]
    kept_at: str
    #: True / False on the last run that graded it, None if never run.
    passed: bool | None = None
    why: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _blocks(text: str) -> tuple[str, list[str]]:
    """A suite file as its head and its ``[[case]]`` blocks, as written."""
    parts = re.split(r"(?m)^(?=\[\[case\]\]\s*$)", text)
    return parts[0], parts[1:]


def _block_id(block: str) -> str:
    found = re.search(r'(?m)^id\s*=\s*"([^"]*)"', block)
    return found.group(1) if found else ""


def last_results(suite: Path) -> dict[str, tuple[bool, list[str]]]:
    """Each case's verdict in the newest report under ``runs/``."""
    runs = sorted((suite / RUNS_DIR).glob("*.json")) if (suite / RUNS_DIR).is_dir() else []
    if not runs:
        return {}
    try:
        raw = json.loads(runs[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {c.get("id", ""): (bool(c.get("passed")), list(c.get("failures") or []))
            for c in raw.get("cases") or []}


def kept_in(suite: Path) -> list[Kept]:
    """The tests in one suite, in the order they were kept. Read for a
    list, never loaded as a suite: a recording that will not open still
    shows here, so it can be deleted."""
    cases = suite / "cases.toml"
    if not cases.is_file():
        return []
    import tomllib
    try:
        data = tomllib.loads(cases.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    done = last_results(suite)
    out = []
    for entry in data.get("case") or []:
        if not isinstance(entry, dict):
            continue
        case_id = str(entry.get("id") or "")
        kept_at = ""
        replay = entry.get("replay")
        if isinstance(replay, str):
            try:
                kept_at = json.loads((suite / replay).read_text(encoding="utf-8")
                                     ).get("kept_at", "")
            except (OSError, ValueError):
                pass
        verdict = done.get(case_id)
        out.append(Kept(case_id, str(entry.get("user_message") or ""),
                        list(entry.get("answer_contains") or []),
                        list(entry.get("required_tools") or []), kept_at,
                        verdict[0] if verdict else None,
                        verdict[1] if verdict else []))
    return out


def forget(suite: Path, case_id: str) -> bool:
    """Take a kept test back: its block out of the suite file (the rest
    left exactly as written, hand edits included) and its recording
    deleted. The last one taken leaves no suite, so a night never runs an
    empty file. False when there is no such test."""
    cases = suite / "cases.toml"
    if not cases.is_file():
        return False
    head, blocks = _blocks(cases.read_text(encoding="utf-8"))
    gone = [b for b in blocks if _block_id(b) == case_id]
    if not gone:
        return False
    for block in gone:
        found = re.search(r'(?m)^replay\s*=\s*"([^"]*)"', block)
        if found:
            target = (suite / found.group(1)).resolve()
            if target.is_relative_to(suite.resolve()):
                target.unlink(missing_ok=True)
    left = [b for b in blocks if _block_id(b) != case_id]
    if left:
        cases.write_text(head + "".join(left), encoding="utf-8")
    else:
        cases.unlink()
    return True


# ---- every suite, one night ----------------------------------------------------


def suites(root: Path) -> list[Path]:
    """Every kept suite under ``root``: yours at ``<agent>/``, a person's
    at ``people/<person>/<agent>/``. Never deeper -- a recording folder
    is not a suite."""
    found = [p.parent for p in [*root.glob("*/cases.toml"),
                                *root.glob(f"{PEOPLE_DIR}/*/*/cases.toml")]]
    return sorted(p for p in found if p.name != PEOPLE_DIR)


def about(suite: Path) -> dict[str, str]:
    try:
        raw = json.loads((suite / ABOUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    return {"agent": str(raw.get("agent") or suite.name),
            "package": str(raw.get("package") or ""),
            "person": str(raw.get("person") or "")}


def _yantra() -> list[str]:
    return [sys.executable, "-c",
            "import sys; from yantra.cli.main import main; sys.exit(main(sys.argv[1:]))"]


def run_suite(suite: Path, *, extra: Sequence[str] = (), command: Sequence[str] | None = None,
              timeout: float | None = None) -> dict[str, Any]:
    """One suite, run as its own ``--eval --kept`` process; what it found,
    compared with the run before. A suite that could not run says why
    and is red -- a night that quietly skipped one is a green card that
    checked less than it says."""
    from yantra.eval_report import compare, read_report
    info = about(suite)
    runs = suite / RUNS_DIR
    before = sorted(runs.glob("*.json")) if runs.is_dir() else []
    report = runs / (time.strftime("%Y%m%dT%H%M%S") + ".json")
    argv = [*(command or _yantra()), "--eval", "--kept", "--suite", str(suite),
            "--report", str(report)]
    package = Path(info["package"]) if info["package"] else None
    if package is not None and package.is_dir():
        argv += ["--agent", str(package)]
    argv += list(extra)
    row: dict[str, Any] = {"suite": str(suite), **info, "report": str(report),
                           "cases": 0, "passed": 0, "red": [], "broke": [],
                           "fixed": [], "error": ""}
    if package is not None and not package.is_dir():
        row["error"] = f"its agent's package is gone: {package}"
        return row
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        row["error"] = f"stopped after {timeout:.0f}s"
        return row
    except OSError as exc:
        row["error"] = f"could not start yantra: {exc}"
        return row
    if not report.is_file():
        tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
        row["error"] = " ".join(tail) or f"yantra exited {done.returncode} with no report"
        return row
    try:
        now = read_report(report)
    except ConfigError as exc:
        row["error"] = str(exc)
        return row
    row.update(cases=len(now.cases), passed=now.passed, model=now.model,
               red=[c.id for c in now.cases if not c.passed])
    if before:
        try:
            moved = compare(read_report(before[-1]), now)
            row.update(broke=[d.id for d in moved.broke], fixed=[d.id for d in moved.fixed])
        except ConfigError:
            pass                    # an unreadable old report moves nothing
    return row


def night(root: Path | None = None, *, extra: Sequence[str] = (),
          command: Sequence[str] | None = None,
          timeout: float | None = None) -> dict[str, Any]:
    """Every suite under ``root``, then the summary written to ``LAST``."""
    root = root or default_root()
    rows = [run_suite(s, extra=extra, command=command, timeout=timeout)
            for s in suites(root)]
    summary = {
        "format": TESTS_FORMAT,
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "suites": rows,
        "cases": sum(r["cases"] for r in rows),
        "passed": sum(r["passed"] for r in rows),
        "broke": sum(len(r["broke"]) for r in rows),
        "fixed": sum(len(r["fixed"]) for r in rows),
        "errors": sum(1 for r in rows if r["error"]),
    }
    summary["green"] = bool(rows) and not summary["errors"] and (
        summary["passed"] == summary["cases"])
    root.mkdir(parents=True, exist_ok=True)
    (root / LAST).write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    return summary


def said(summary: dict[str, Any]) -> str:
    """The summary in a line or a few, as a chat or a logbook says it."""
    if not summary["suites"]:
        return "No conversation is kept as a test yet."
    head = (f"{summary['passed']} of {summary['cases']} kept tests passed"
            + (f", {summary['broke']} broke since last time" if summary["broke"] else "")
            + (f", {summary['fixed']} fixed" if summary["fixed"] else "")
            + (f", {summary['errors']} could not run" if summary["errors"] else "") + ".")
    lines = [head]
    for row in summary["suites"]:
        whose = f"{row['person']}'s {row['agent']}" if row["person"] else row["agent"]
        if row["error"]:
            lines.append(f"· {whose}: could not run -- {row['error']}")
        elif row["broke"] or row["red"]:
            lines.append(f"· {whose}: {row['passed']}/{row['cases']}"
                         + (f", broke: {', '.join(row['broke'])}" if row["broke"] else "")
                         + (f", red: {', '.join(row['red'])}" if row["red"] else ""))
    return "\n".join(lines)
