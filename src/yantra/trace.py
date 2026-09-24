"""What a turn DID, kept -- so a real failure can become a case.

``case_from_trace`` has existed since notes/10 and has been pointing at a
missing store ever since: it turns a production failure into a regression
case, and every one of its arguments had to be typed by hand, because
nothing in this harness wrote a turn down. Note 42 built a store and was
careful to say it was the other kind -- a report holds VERDICTS (which
cases passed, how many attempts, what it cost) and not the trajectories
that produced them.

This is the other kind. One JSON object per turn, appended to a file:

    yantra --trace runs/today.jsonl "summarize the release notes"
    yantra --fossil 3f9c21ab --trace runs/today.jsonl >> evals/cases.toml

The privacy question, first, because it is the reason this took so long
to build. A trajectory contains whatever the agent READ -- file contents,
web pages, whatever a tool returned -- and a verdict does not. A store
that kept everything by default would be a store that quietly
accumulates other people's data in a file somebody later pastes into a
ticket.

SHAPE, NOT CONTENT, IS THE DEFAULT. A recorded turn keeps:

* the task (the message a person typed -- without it there is no case),
* which tools ran, in order, and whether each one errored,
* counts: iterations, tokens, dollars, seconds,
* how the turn ended.

and deliberately not: tool ARGUMENTS, tool RESULTS, or the model's
answer. Those are what ``detail="full"`` adds, opt-in, per recorder, and
every line records which level wrote it -- so a reader can tell, without
reading the contents, whether a file is safe to hand to somebody else.

That split is not a compromise between privacy and usefulness; the shape
is what a regression case is made of. ``case_from_trajectory`` needs the
task and the tool names, and asserts on those. A case built from a full
trajectory would be asserting on the contents of a file that will have
changed by the time anybody re-runs it.

A CHILD IS PART OF THE TURN THAT SPAWNED IT (notes/63). A sub-agent's
work used to reach the recording as one ``ToolExecuted`` with the child's
name on it, so a delegation that failed three calls deep inside the child
looked, afterwards, like a tool that returned. Each child now rides in its
parent's line under ``children``, at the same level of detail as the
parent: its tool calls in order and whether each worked, its iterations,
tokens and how it stopped, always; the task the parent gave it and what
it answered, only at FULL -- the parent model WROTE that task out of
whatever it had read, so it is content, not shape. A child's step keeps
the gate's refusal code too (notes/65), so a child turned away by a rule
does not read as a child whose call failed.

A SUITE RECORDS WHAT IT GRADED (notes/65). ``--eval --trace`` writes
every run of every case, tagged with the case's id and whether its grader
passed it (notes/70), and the report names those turns back. The eval
runners call ``agent.run()`` and have no stream to tee, so
``from_history`` writes the line afterwards from what the agent kept --
recording must not change how the graded run was driven.

APPEND-ONLY JSONL, one turn per line, because the failure being recorded
may be a crash: a format that has to be closed to be valid loses the one
turn worth keeping. A malformed line is skipped on read with a count, not
raised -- half a line at the end of a file is what a killed process
leaves behind, and it must not cost you the two hundred turns above it.

A PATTERN IS SCRUBBED BEFORE IT IS WRITTEN (notes/79). Shape keeps
arguments and results out, but not the task a person typed, and FULL
keeps whatever the agent read. ``redact`` takes regular expressions --
or the names ``email`` and ``token`` for the two everybody needs -- and
every match in the task, the arguments, the results and the answers
becomes ``[redacted]`` on the way to disk. The line says how many were
replaced, even when that is none, so a reader can tell a scrubbed file
from one nobody scrubbed without reading either. Scrubbing a file that
was already written is not offered: a pattern added afterwards is a
pattern the file was shared without.

AGE IS THE ONLY THING THAT PRUNES, AND ONLY WHEN ASKED (notes/67). A suite
run with ``--repeat 10 --trace`` writes ten lines a case, so the file
grows as fast as anybody evaluates. ``prune`` removes turns older than a
number of days, as its own command -- never as a side effect of recording,
because a recorder that deletes is a recorder nobody can leave switched
on without reading its fine print. A line it cannot read has no date, so
it is kept: the prune's job is age, and deciding a line is garbage is not
the same judgement.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yantra.errors import ConfigError

#: Bumped only when an OLD reader would misread a NEW line (eval_report's
#: rule). Adding a key readers may ignore does not bump it.
FORMAT = "yantra.trace.v1"

#: What a recorder keeps. See the module docstring -- this is the privacy
#: decision, and it is written into every line.
SHAPE = "shape"
FULL = "full"
LEVELS = (SHAPE, FULL)

#: Who gave a turn its ``passed`` (notes/74): a suite's case, or a person.
GRADER = "grader"
PERSON = "person"

#: What a redacted match becomes (notes/79).
REDACTED = "[redacted]"

#: Patterns common enough to have a name, for ``--trace-redact``. Anything
#: else passed there is a regular expression. Deliberately conservative:
#: a token pattern that fired on every long word would scrub the evidence
#: the recording exists to keep.
REDACT_PRESETS = {
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
    "token": "|".join((
        r"\bsk-[A-Za-z0-9_-]{16,}",                    # OpenAI, Anthropic
        r"\bgh[pousr]_[A-Za-z0-9]{20,}",               # GitHub
        r"\bgithub_pat_[A-Za-z0-9_]{20,}",
        r"\bxox[abprs]-[A-Za-z0-9-]{10,}",             # Slack
        r"\bAKIA[0-9A-Z]{16}\b",                       # AWS access key id
        r"\bAIza[0-9A-Za-z_-]{35}",                    # Google API key
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",  # JWT
        r"\b[Bb]earer\s+[A-Za-z0-9._~+/-]{16,}=*",
    )),
}

#: Longest tool argument/result string kept at FULL. A trajectory is
#: evidence, not an archive: a 200KB file read makes the store unusable
#: for the thing it exists for.
MAX_KEPT_CHARS = 2000


@dataclass(slots=True)
class ToolStep:
    """One tool call inside a turn."""

    name: str
    ok: bool
    #: Present only at FULL, and only when the tool had them.
    arguments: dict[str, Any] | None = None
    result: str | None = None
    #: The gate's refusal code, when the call never ran (notes/39). Kept
    #: at SHAPE too: it is a token, not content, and "this turn did
    #: nothing because everything was refused" is exactly the kind of
    #: failure somebody wants to turn into a case.
    refusal: str | None = None


@dataclass(slots=True)
class ChildRun:
    """One sub-agent the turn spawned, as its parent's recording keeps it."""

    number: int                  # 1-based spawn number, as the live view shows
    agent: str                   # the tool the parent called
    model: str
    steps: list[ToolStep] = field(default_factory=list)
    iterations: int = 0
    tokens: int = 0
    #: ``SubagentResult.code``: None when the child finished, else why not.
    code: str | None = None
    #: Only at FULL: the task it was given and what it answered.
    task: str | None = None
    answer: str | None = None

    @property
    def failed(self) -> bool:
        return self.code is not None or any(not s.ok for s in self.steps)


@dataclass(slots=True)
class Trajectory:
    """One turn, written down."""

    id: str
    at: str                      # ISO-8601 UTC, to the second
    provider: str
    model: str
    detail: str                  # SHAPE | FULL
    task: str
    steps: list[ToolStep] = field(default_factory=list)
    iterations: int = 0
    tokens: int = 0
    usd: float | None = None
    seconds: float = 0.0
    outcome: str = "end_turn"     # TurnEnd.reason, or "crashed"
    #: Only at FULL: what the model finally said.
    answer: str | None = None
    #: Sub-agents this turn spawned, in the order they FINISHED (two may
    #: run at once, notes/55); ``number`` says the order they started.
    children: list[ChildRun] = field(default_factory=list)
    #: The eval case this turn was a run of, when a suite recorded it
    #: (notes/65); None for a turn somebody typed. The report names the
    #: turn from the other side, so either file finds the other.
    case: str | None = None
    #: Whether that case's grader passed this run (notes/70), when a suite
    #: recorded it: the one verdict a trace can carry honestly, because a
    #: person wrote the case that reached it. None for a typed turn.
    #: Kept at SHAPE -- a boolean, not the grader's words, which may quote
    #: whatever the agent said.
    passed: bool | None = None
    #: Who gave ``passed``: ``"grader"`` (a suite's case) or ``"person"``
    #: (``--mark``, notes/74). None on a line from before either existed.
    judged_by: str | None = None
    #: The person's own reason, when they gave one with ``--mark``.
    why: str | None = None
    #: The grader's verdict a person's mark replaced, kept so that
    #: clearing the mark gives it back (notes/77). None when no grader
    #: had judged the turn, or nobody has marked it.
    graded: bool | None = None
    #: How many matches ``--trace-redact`` replaced in this line
    #: (notes/79); None when the line was written without any patterns.
    redacted: int | None = None

    @property
    def tools_used(self) -> list[str]:
        """Tool names in call order, duplicates kept -- "outline, outline,
        read_file" is a different trajectory from "outline, read_file"."""
        return [step.name for step in self.steps]

    @property
    def failed(self) -> bool:
        """Whether anything went wrong: the turn did not end cleanly, or a
        tool errored inside it.

        Not a verdict -- a turn that ended cleanly can still have produced
        a wrong answer, and only a person can say so. This is the cheap
        half, and it is what a host filters on before asking one.

        A child that failed counts: its trouble is this turn's trouble,
        even when the parent smoothed it over in its answer.
        """
        return (self.outcome != "end_turn"
                or any(not s.ok for s in self.steps)
                or any(c.failed for c in self.children))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clip(text: str) -> str:
    if len(text) <= MAX_KEPT_CHARS:
        return text
    return f"{text[:MAX_KEPT_CHARS]}\n[... {len(text) - MAX_KEPT_CHARS} more chars ...]"


class TrajectoryLog:
    """An append-only JSONL file of turns.

    A file rather than a database for the reason note 42's reports are
    files: whatever anybody wants to do with these can be written in ten
    lines against JSON, and a store with a schema migration in its future
    is a store nobody starts using.
    """

    def __init__(self, path: Path | str, *, detail: str = SHAPE,
                 redact: Iterable[str] = ()) -> None:
        if detail not in LEVELS:
            raise ConfigError(
                f"trace detail must be one of {'|'.join(LEVELS)}, got "
                f"{detail!r}; {SHAPE!r} keeps the task, the tool names and "
                f"the counts, {FULL!r} adds arguments, results and the "
                f"answer -- which is whatever the agent read")
        self.path = Path(path)
        self.detail = detail
        #: What is scrubbed before a line is written (notes/79); None is
        #: nothing, and a line written without it carries no count.
        patterns = list(redact)
        self.redact = _compile_redactions(patterns)
        self.redact_count = len(patterns)

    @property
    def label(self) -> str:
        """The level, and whether anything is scrubbed -- for a banner."""
        if not self.redact_count:
            return self.detail
        return f"{self.detail}, redacting {self.redact_count} pattern(s)"

    def record(self, trajectory: Trajectory) -> str:
        """Append one turn; return its id."""
        payload = _as_json(trajectory)
        if self.redact is not None:
            payload = _redacted(payload, self.redact)
        line = json.dumps(payload, ensure_ascii=False)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            raise ConfigError(f"cannot write trace {self.path}: {exc}") from None
        return trajectory.id

    def read(self) -> list[Trajectory]:
        """Every readable turn, oldest first.

        A malformed line is SKIPPED, not raised: half a line at the end of
        a file is what a killed process leaves behind, and it must not
        cost the reader the turns above it. ``unreadable`` counts them.
        """
        self.unreadable = 0
        out: list[Trajectory] = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ConfigError(
                f"no trace file at {self.path}; record one first with: "
                f"--trace {self.path}") from None
        except OSError as exc:
            raise ConfigError(f"cannot read trace {self.path}: {exc}") from None
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                out.append(_from_json(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError):
                self.unreadable += 1
        return out

    def get(self, trace_id: str) -> Trajectory:
        """One turn by id, or by any unambiguous PREFIX of one.

        By prefix because an id is a uuid and nobody types thirty-two
        characters off a terminal. An ambiguous prefix is an error naming
        the candidates rather than a guess at which was meant.
        """
        matches = [t for t in self.read() if t.id.startswith(trace_id)]
        if not matches:
            raise ConfigError(
                f"no turn in {self.path} has an id starting {trace_id!r}")
        if len(matches) > 1:
            raise ConfigError(
                f"{trace_id!r} matches {len(matches)} turns in {self.path} "
                f"({', '.join(t.id[:8] for t in matches[:5])}...); give more "
                f"of the id")
        return matches[0]


    def prune(self, days: int, *, now: float | None = None) -> Pruned:
        """Remove every turn recorded more than ``days`` days ago.

        The file is rewritten beside itself and swapped in with one
        rename, so a reader never sees half of it. A session still
        appending while this runs is the one hazard: lines written after
        the read are copied across before the swap, which leaves only the
        instant between that copy and the rename -- small, and named here
        rather than papered over. Nothing is rewritten when nothing is old.
        """
        if days < 1:
            raise ConfigError(
                f"--trace-prune takes a number of days of at least 1, got "
                f"{days}; to drop every turn, delete {self.path}")
        cutoff = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime((time.time() if now is None else now) - days * 86400))
        data = self._raw()
        kept: list[bytes] = []
        result = Pruned(path=self.path, days=days)
        for line in data.splitlines():
            if not line.strip():
                continue
            at = _recorded_at(line)
            if at is None:
                result.unreadable += 1
                kept.append(line)
            elif at < cutoff:
                result.removed += 1
            else:
                result.kept += 1
                kept.append(line)
        if not result.removed:
            return result
        self._rewrite(data, kept)
        return result

    def mark(self, trace_id: str, *, passed: bool | None,
             why: str | None = None) -> Trajectory:
        """Write a PERSON's verdict into one recorded turn (notes/74), or
        take it back (``passed=None``, notes/77).

        Yantra still decides nothing about whether a turn was a failure
        (notes/57); this is where the person who did decide writes it
        down, so ``--turns`` can show it and ``--fossil`` can use the
        reason. The line is rewritten in place -- the same rename swap as
        ``prune`` -- rather than a second line appended, because an older
        reader would count that second line as unreadable and say so.
        A person's mark replaces a grader's: the person has read the
        answer, and the grader only checked its shape.

        REPLACED, NOT LOST. The grader's verdict a mark replaces is kept
        under ``graded``, so clearing the mark puts the turn back exactly
        as the suite left it. A mark cleared on a turn nobody graded
        leaves no verdict at all.
        """
        target = self.get(trace_id)
        data = self._raw()
        lines: list[bytes] = []
        for line in data.splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                lines.append(line)
                continue
            if isinstance(raw, dict) and raw.get("id") == target.id:
                _apply_mark(raw, passed, why)
                line = json.dumps(raw, ensure_ascii=False).encode()
                target = _from_json(raw)
            lines.append(line)
        self._rewrite(data, lines)
        return target

    def _raw(self) -> bytes:
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            raise ConfigError(f"no trace file at {self.path}") from None
        except OSError as exc:
            raise ConfigError(f"cannot read trace {self.path}: {exc}") from None

    def _rewrite(self, data: bytes, lines: list[bytes]) -> None:
        """Swap in ``lines`` for the file ``data`` was read from, keeping
        anything appended since the read (see ``prune``)."""
        temp = None
        try:
            handle, temp = tempfile.mkstemp(dir=self.path.parent,
                                            prefix=f".{self.path.name}.")
            with os.fdopen(handle, "wb") as out:
                out.write(b"".join(line + b"\n" for line in lines))
                with self.path.open("rb") as current:
                    current.seek(len(data))
                    out.write(current.read())   # appended while we read
            os.chmod(temp, self.path.stat().st_mode & 0o777)
            os.replace(temp, self.path)
        except OSError as exc:
            if temp is not None and os.path.exists(temp):
                os.unlink(temp)           # the original is untouched
            raise ConfigError(f"cannot rewrite trace {self.path}: {exc}") from None


@dataclass(slots=True)
class Pruned:
    """What ``TrajectoryLog.prune`` did, for the line the CLI prints."""

    path: Path
    days: int
    removed: int = 0
    kept: int = 0
    #: Lines with no readable date, kept because age cannot judge them.
    unreadable: int = 0


def _apply_mark(raw: dict[str, Any], passed: bool | None,
                why: str | None) -> None:
    """A person's verdict onto one raw line; ``passed=None`` clears it."""
    if raw.get("judged_by") != PERSON and "passed" in raw:
        # A line from before ``judged_by`` existed that has a verdict got
        # it from a suite: note 70 wrote ``passed`` and nothing else did.
        raw["graded"] = raw["passed"]
    if passed is None:
        graded = raw.pop("graded", None)
        raw.pop("why", None)
        if graded is None:
            raw.pop("passed", None)
            raw.pop("judged_by", None)
        else:
            raw["passed"], raw["judged_by"] = graded, GRADER
        return
    raw["passed"] = passed
    raw["judged_by"] = PERSON
    if why:
        raw["why"] = why
    else:
        raw.pop("why", None)


def _compile_redactions(patterns: list[str]) -> re.Pattern[str] | None:
    """Names and regular expressions -> one pattern, or None for none.

    A pattern that does not compile, or one that matches the empty
    string, is an error at startup rather than a surprise in the file:
    ``a*`` would "redact" between every character and scrub nothing.
    """
    if not patterns:
        return None
    parts = []
    for pattern in patterns:
        source = REDACT_PRESETS.get(pattern, pattern)
        try:
            compiled = re.compile(source)
        except re.error as exc:
            raise ConfigError(
                f"--trace-redact {pattern!r} is not a regular expression "
                f"({exc}); the names {', '.join(REDACT_PRESETS)} are "
                f"built in") from None
        if compiled.match(""):
            raise ConfigError(
                f"--trace-redact {pattern!r} matches an empty string, so it "
                f"would scrub nothing; make it match at least one character")
        parts.append(f"(?:{source})")
    return re.compile("|".join(parts))


#: The parts of a line that hold what somebody typed or the agent read.
#: Everything else is shape -- names, counts, codes, timestamps -- and is
#: left alone so a scrubbed line is still a line ``--turns`` can read.
_CONTENT_KEYS = ("task", "answer", "arguments", "result")


def _redacted(payload: dict[str, Any], pattern: re.Pattern[str]
              ) -> dict[str, Any]:
    """A copy of a line with every match in its content replaced, and a
    count of how many there were. A copy, because the arguments dicts are
    the live ones the turn ran with."""
    count = 0

    def scrub(value: Any) -> Any:
        nonlocal count
        if isinstance(value, str):
            value, n = pattern.subn(REDACTED, value)
            count += n
            return value
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    def content(row: dict[str, Any]) -> dict[str, Any]:
        return {k: scrub(v) if k in _CONTENT_KEYS else v
                for k, v in row.items()}

    out = content(payload)
    out["steps"] = [content(step) for step in payload.get("steps", [])]
    if "children" in payload:
        out["children"] = [content(child) for child in payload["children"]]
    out["redacted"] = count
    return out


def _recorded_at(line: bytes) -> str | None:
    """A trace line's ``at``, or None when the line cannot say.

    Only the timestamp is read -- not the whole turn -- so a line written
    by a newer version with keys this one does not know still has an age.
    """
    try:
        raw = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    at = raw.get("at") if isinstance(raw, dict) else None
    return at if isinstance(at, str) and len(at) == 20 else None


def _as_json(trajectory: Trajectory) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "format": FORMAT,
        "id": trajectory.id,
        "at": trajectory.at,
        "provider": trajectory.provider,
        "model": trajectory.model,
        "detail": trajectory.detail,
        "task": trajectory.task,
        "iterations": trajectory.iterations,
        "tokens": trajectory.tokens,
        "usd": trajectory.usd,
        "seconds": trajectory.seconds,
        "outcome": trajectory.outcome,
        "steps": [],
    }
    for step in trajectory.steps:
        row: dict[str, Any] = {"name": step.name, "ok": step.ok}
        if step.refusal is not None:
            row["refusal"] = step.refusal
        if step.arguments is not None:
            row["arguments"] = step.arguments
        if step.result is not None:
            row["result"] = step.result
        payload["steps"].append(row)
    if trajectory.answer is not None:
        payload["answer"] = trajectory.answer
    if trajectory.case is not None:
        payload["case"] = trajectory.case
    if trajectory.passed is not None:
        payload["passed"] = trajectory.passed
    if trajectory.judged_by is not None:
        payload["judged_by"] = trajectory.judged_by
    if trajectory.why is not None:
        payload["why"] = trajectory.why
    if trajectory.graded is not None:
        payload["graded"] = trajectory.graded
    if trajectory.children:
        # Only when there were any, so a turn without delegation reads
        # exactly as it always did.
        payload["children"] = [_child_json(c) for c in trajectory.children]
    return payload


def _child_json(child: ChildRun) -> dict[str, Any]:
    row: dict[str, Any] = {
        "number": child.number, "agent": child.agent, "model": child.model,
        "iterations": child.iterations, "tokens": child.tokens,
        "code": child.code,
        "steps": [_child_step_json(s) for s in child.steps],
    }
    if child.task is not None:
        row["task"] = child.task
    if child.answer is not None:
        row["answer"] = child.answer
    return row


def _child_step_json(step: ToolStep) -> dict[str, Any]:
    # Refusal only when there was one, as in the parent's steps: a child
    # step recorded before notes/65 and one that simply ran read the same.
    row: dict[str, Any] = {"name": step.name, "ok": step.ok}
    if step.refusal is not None:
        row["refusal"] = step.refusal
    return row


def _from_json(raw: dict[str, Any]) -> Trajectory:
    if raw.get("format") != FORMAT:
        raise TypeError(f"unknown trace format {raw.get('format')!r}")
    return Trajectory(
        id=raw["id"], at=raw["at"], provider=raw["provider"],
        model=raw["model"], detail=raw["detail"], task=raw["task"],
        steps=[ToolStep(name=s["name"], ok=s["ok"],
                        arguments=s.get("arguments"), result=s.get("result"),
                        refusal=s.get("refusal"))
               for s in raw.get("steps", [])],
        iterations=raw.get("iterations", 0), tokens=raw.get("tokens", 0),
        usd=raw.get("usd"), seconds=raw.get("seconds", 0.0),
        outcome=raw.get("outcome", "end_turn"), answer=raw.get("answer"),
        children=[ChildRun(
            number=c["number"], agent=c["agent"], model=c.get("model", ""),
            steps=[ToolStep(name=s["name"], ok=s["ok"],
                            refusal=s.get("refusal"))
                   for s in c.get("steps", [])],
            iterations=c.get("iterations", 0), tokens=c.get("tokens", 0),
            code=c.get("code"), task=c.get("task"), answer=c.get("answer"),
        ) for c in raw.get("children", [])],
        case=raw.get("case"),
        passed=raw.get("passed"),
        judged_by=raw.get("judged_by"),
        why=raw.get("why"),
        graded=raw.get("graded"),
        redacted=raw.get("redacted"),
    )


def _child_run(result: Any, detail: str) -> ChildRun:
    """A spawner's ``SubagentResult`` -> the shape a recording keeps."""
    child = ChildRun(
        number=result.number, agent=result.agent, model=result.model,
        steps=[ToolStep(name=name, ok=ok, refusal=refusal)
               for name, ok, refusal in result.steps],
        iterations=result.iterations_used,
        tokens=result.input_tokens + result.output_tokens, code=result.code,
    )
    if detail == FULL:
        child.task = _clip(result.objective)
        child.answer = _clip(result.summary)
    return child


def watch(task: str, events: Iterable[Any], sink: Callable[[Trajectory], Any],
          *, provider: str = "", model: str = "", detail: str = SHAPE,
          usd: float | None = None, spawner: Any | None = None
          ) -> Iterator[Any]:
    """Pass every event through, and hand the finished turn to ``sink``.

    A TEE rather than a consumer, the same shape the sub-agent stream
    tee uses (notes/08): a host that records turns must still be able to
    render them, and a recorder that swallowed the stream would make
    recording and showing mutually exclusive.

    The trajectory is handed over when the turn ENDS -- including when it
    ends badly. A turn that raises is recorded with ``outcome="crashed"``
    and the exception re-raised, because the turn somebody most wants a
    case for is the one that fell over.

    ``spawner`` is the session's ``SubagentSpawner``, when it has one: the
    children it finished while this turn ran are this turn's children.
    Read off its ``results`` list rather than off the event stream, which
    carries the parent's events only -- one turn at a time is how every
    host here drives an agent, so "finished during this turn" is exact.
    """
    started = time.monotonic()
    if not isinstance(getattr(spawner, "results", None), list):
        spawner = None           # a host with no spawner, or a stand-in
    already = len(spawner.results) if spawner is not None else 0
    trajectory = Trajectory(id=uuid.uuid4().hex, at=_now(), provider=provider,
                            model=model, detail=detail, task=task)
    try:
        for event in events:
            kind = type(event).__name__
            if kind == "ToolExecuted":
                step = ToolStep(name=event.call.name,
                                ok=not event.result.is_error,
                                refusal=event.refusal)
                if detail == FULL:
                    step.arguments = dict(event.call.arguments)
                    step.result = _clip(str(event.result.content))
                trajectory.steps.append(step)
            elif kind == "TurnEnd":
                trajectory.outcome = event.reason
                trajectory.iterations = event.iterations
                if event.response is not None:
                    usage = event.response.usage
                    trajectory.tokens = (usage.input_tokens
                                         + usage.output_tokens)
                    if detail == FULL:
                        trajectory.answer = _clip(
                            event.response.message.text().strip())
            yield event
    except BaseException:
        trajectory.outcome = "crashed"
        raise
    finally:
        # In a finally so a crashed or ABANDONED turn is still recorded:
        # a generator closed early (Ctrl-C at the terminal, Stop in the
        # browser) is exactly the shape of turn somebody wants to look at
        # afterwards. The inner stream is closed HERE, explicitly, so the
        # agent's outstanding-call synthesis has run -- and its children
        # have reported -- before the line is written, rather than
        # whenever the collector gets round to it.
        close = getattr(events, "close", None)
        if close is not None:
            close()
        trajectory.seconds = round(time.monotonic() - started, 3)
        trajectory.usd = usd
        if spawner is not None:
            trajectory.children = [_child_run(r, detail)
                                   for r in spawner.results[already:]]
        sink(trajectory)


def from_history(agent: Any, task: str, *, provider: str = "",
                 model: str = "", detail: str = SHAPE,
                 outcome: str = "end_turn", seconds: float = 0.0,
                 usd: float | None = None, case: str | None = None,
                 passed: bool | None = None) -> Trajectory:
    """A turn that was RUN rather than streamed, written down afterwards.

    ``watch`` needs the event stream, and an eval runner does not have
    one: it calls ``agent.run()``, on purpose, so that grading drives the
    agent exactly the way a library caller would. Changing HOW a case is
    run in order to record it would make the recording a witness to a
    different run than the one graded. So this reads what the agent kept
    instead -- its history, its usage, and ``turn_refusals`` -- which
    holds everything a SHAPE line needs.

    For an agent that has run ONE turn, which is what a suite builds for
    every run of every case: a longer history would put earlier turns'
    calls into this one.
    """
    history = getattr(agent, "history", [])
    refused = getattr(agent, "turn_refusals", {})
    results = {block.tool_call_id: block for message in history
               for block in message.content
               if type(block).__name__ == "ToolResult"}
    trajectory = Trajectory(id=uuid.uuid4().hex, at=_now(), provider=provider,
                            model=model, detail=detail, task=task,
                            outcome=outcome, seconds=seconds, usd=usd,
                            case=case, passed=passed,
                            judged_by=GRADER if passed is not None else None)
    for message in history:
        if message.role != "assistant":
            continue
        trajectory.iterations += 1
        for call in message.tool_calls():
            result = results.get(call.id)
            step = ToolStep(name=call.name,
                            ok=result is not None and not result.is_error,
                            refusal=refused.get(call.id))
            if detail == FULL:
                step.arguments = dict(call.arguments)
                if result is not None:
                    step.result = _clip(str(result.content))
            trajectory.steps.append(step)
    usage = getattr(agent, "total_usage", None)
    if usage is not None:
        trajectory.tokens = usage.input_tokens + usage.output_tokens
    if detail == FULL:
        for message in reversed(history):
            if message.role == "assistant" and message.text().strip():
                trajectory.answer = _clip(message.text().strip())
                break
    spawner = getattr(agent, "subagents", None)
    if isinstance(getattr(spawner, "results", None), list):
        trajectory.children = [_child_run(r, detail) for r in spawner.results]
    return trajectory
