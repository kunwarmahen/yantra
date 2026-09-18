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

APPEND-ONLY JSONL, one turn per line, because the failure being recorded
may be a crash: a format that has to be closed to be valid loses the one
turn worth keeping. A malformed line is skipped on read with a count, not
raised -- half a line at the end of a file is what a killed process
leaves behind, and it must not cost you the two hundred turns above it.
"""

from __future__ import annotations

import json
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
        """
        return self.outcome != "end_turn" or any(not s.ok for s in self.steps)


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

    def __init__(self, path: Path | str, *, detail: str = SHAPE) -> None:
        if detail not in LEVELS:
            raise ConfigError(
                f"trace detail must be one of {'|'.join(LEVELS)}, got "
                f"{detail!r}; {SHAPE!r} keeps the task, the tool names and "
                f"the counts, {FULL!r} adds arguments, results and the "
                f"answer -- which is whatever the agent read")
        self.path = Path(path)
        self.detail = detail

    def record(self, trajectory: Trajectory) -> str:
        """Append one turn; return its id."""
        line = json.dumps(_as_json(trajectory), ensure_ascii=False)
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
    return payload


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
    )


def watch(task: str, events: Iterable[Any], sink: Callable[[Trajectory], Any],
          *, provider: str = "", model: str = "", detail: str = SHAPE,
          usd: float | None = None) -> Iterator[Any]:
    """Pass every event through, and hand the finished turn to ``sink``.

    A TEE rather than a consumer, the same shape the sub-agent stream
    tee uses (notes/08): a host that records turns must still be able to
    render them, and a recorder that swallowed the stream would make
    recording and showing mutually exclusive.

    The trajectory is handed over when the turn ENDS -- including when it
    ends badly. A turn that raises is recorded with ``outcome="crashed"``
    and the exception re-raised, because the turn somebody most wants a
    case for is the one that fell over.
    """
    started = time.monotonic()
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
        # a generator closed early (Ctrl-C at the terminal) is exactly the
        # shape of turn somebody wants to look at afterwards.
        trajectory.seconds = round(time.monotonic() - started, 3)
        trajectory.usd = usd
        sink(trajectory)
