"""A suite run, written down -- so the next one has something to answer.

Everything about a gate up to here has been a verdict about NOW: the
cases pass or they do not, and the exit code says which (notes/33). That
is the whole of what CI needs and about half of what a person needs,
because the question a person asks second is never "did it pass". It is
**did that get better or worse**, and answering it needs a run to have
left something behind.

    yantra --agent . --eval --report runs/today.json
    yantra --agent . --eval --report runs/tomorrow.json --against runs/today.json

A REPORT IS A RECORD, NOT A BASELINE. Comparing changes no verdict and no
exit code: this run's cases passed or they did not, and a run that was
worse than yesterday's and still green is green. The alternative -- a
gate that goes red because a number moved -- sounds attractive for about
a day, until the first honest improvement that costs two tokens more than
last time has to be argued with a script.

COUNTS, NEVER PERCENTAGES ALONE. A case is "7 of 10" or "1 of 1", and
those two are not comparable as 70% against 100%: one is ten samples and
the other is a single die roll (notes/35). So a comparison prints both
sides' counts, and says out loud when the sample sizes differ rather than
quietly dividing them into a number that looks like an answer.

TWO MODELS IS THE POINT, not an error. The most useful comparison this
can do is "the same suite, a cheaper model" -- so a report carries the
provider and model it ran against, the comparison names both sides, and
nothing refuses to compare across them. What IS named loudly is a
comparison against a run that graded a different set of cases: a subset
(``--case``) or a suite somebody has since edited.

DOLLARS ARE WRITTEN DOWN, NEVER RECOMPUTED. A run costs what it cost on
the day it ran, so the figure is priced at write time and stored beside
the tokens (notes/48). Re-pricing an old report against today's table
would rewrite history every time a vendor moves a number, and would do it
silently, in the one file somebody keeps precisely because it does not
change. A report from before this key existed has no figure, which reads
correctly as "nobody wrote one down".

JSON, one object, with a format tag. A report is written by one version
of this program and read by another -- possibly months later, by a CI job
nobody has looked at since -- so an unreadable file has to say so rather
than be half-understood. Unknown future formats are refused by name.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yantra.confidence import overlaps, wilson_bounds
from yantra.errors import ConfigError

#: Bumped only when an OLD reader would misread a NEW file. Adding a key
#: that readers may ignore does not bump it; changing what a key means
#: does. The reader refuses anything it does not know, by name.
FORMAT = "yantra.eval.v1"


@dataclass(slots=True)
class CaseRecord:
    """One case's outcome, flattened to the numbers a later run can use."""

    id: str
    passed: bool
    attempts: int
    passes: int
    min_pass_rate: float
    tokens: int
    seconds: float
    ran_model: bool
    failures: list[str] = field(default_factory=list)
    #: Dollars, priced on the day this ran (notes/48). ``None`` means the
    #: model had no known price -- or that the report predates this key,
    #: which reads the same way and correctly: nobody wrote a figure down.
    usd: float | None = None

    @property
    def tally(self) -> str:
        """"7/10" -- the honest shape, because 0.7 is not what happened."""
        return f"{self.passes}/{self.attempts}"

    @property
    def confidence(self) -> tuple[float, float] | None:
        """What this record's counts are evidence of (notes/47), or ``None``
        for a roster-only case, which rolled no die.

        Derived rather than stored: ``passes`` and ``attempts`` are already
        in every report ever written, so old files answer this question
        too and the format did not have to move.
        """
        if not self.ran_model or not self.attempts:
            return None
        return wilson_bounds(self.passes, self.attempts)


@dataclass(slots=True)
class SuiteRun:
    """One whole ``--eval`` run, as it will be written to disk."""

    suite: str
    provider: str
    model: str
    at: str                     # ISO-8601 UTC, to the second
    repeat: int
    cases: list[CaseRecord]
    cases_in_suite: int         # how many the file HAS, filtered or not
    filtered: list[str] | None = None

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def tokens(self) -> int:
        return sum(c.tokens for c in self.cases)

    @property
    def usd(self) -> float | None:
        """What the run cost, or None when no case carried a figure."""
        priced = [c.usd for c in self.cases if c.usd is not None]
        return sum(priced) if priced else None

    @property
    def fully_priced(self) -> bool:
        """Whether every case that reached a model carried a figure.

        False is the honest half-answer: a suite run across a priced model
        and an unpriced one has a total that is real and incomplete, and
        saying so beats both hiding it and implying it is everything.
        """
        return all(c.usd is not None for c in self.cases if c.ran_model)

    @property
    def green(self) -> bool:
        return bool(self.cases) and self.passed == len(self.cases)

    @property
    def where(self) -> str:
        """"ollama/qwen3.8:latest" -- what this run was actually about."""
        return f"{self.provider}/{self.model}"


def record_run(outcomes: Sequence[Any], *, suite: str, provider: str,
               model: str, repeat: int, cases_in_suite: int,
               filtered: list[str] | None = None) -> SuiteRun:
    """``CaseOutcome``s -> the record. Reads only the public properties, so
    an outcome type that grows a field does not have to grow one here."""
    return SuiteRun(
        suite=suite, provider=provider, model=model,
        at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        repeat=repeat, cases_in_suite=cases_in_suite,
        filtered=list(filtered) if filtered else None,
        cases=[CaseRecord(
            id=o.case_id, passed=o.passed, attempts=o.attempts,
            passes=o.passes, min_pass_rate=o.min_pass_rate,
            tokens=o.tokens_used, seconds=round(o.duration_seconds, 3),
            ran_model=o.ran_model, failures=list(o.failures),
            usd=getattr(o, "usd", None),
        ) for o in outcomes],
    )


def write_report(path: Path, run: SuiteRun) -> None:
    """Write the record, creating the directory if it is not there.

    Written whether the suite was green or red, because a red run is
    exactly the one somebody will want to compare against tomorrow.
    """
    payload = {
        "format": FORMAT,
        "suite": run.suite,
        "provider": run.provider,
        "model": run.model,
        "at": run.at,
        "repeat": run.repeat,
        "cases_in_suite": run.cases_in_suite,
        "filtered": run.filtered,
        "passed": run.passed,
        "tokens": run.tokens,
        "cases": [{
            "id": c.id, "passed": c.passed, "attempts": c.attempts,
            "passes": c.passes, "min_pass_rate": c.min_pass_rate,
            "tokens": c.tokens, "seconds": c.seconds,
            "ran_model": c.ran_model, "failures": c.failures,
            "usd": c.usd,
        } for c in run.cases],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot write eval report {path}: {exc}") from None


def read_report(path: Path) -> SuiteRun:
    """Read one back, or say precisely why it cannot be read.

    A comparison the operator ASKED FOR and did not get is the silent
    pass this whole area exists to prevent, so every failure here is an
    error with a sentence, never a shrug and a run that carries on.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            f"no eval report at {path}; write one first with: "
            f"--eval --report {path}") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not a readable eval report: "
                          f"{exc}") from None
    except OSError as exc:
        raise ConfigError(f"cannot read eval report {path}: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} is not an eval report (expected a JSON "
                          f"object)")
    found = raw.get("format")
    if found != FORMAT:
        raise ConfigError(
            f"{path} is format {found!r}, and this build reads {FORMAT!r}; "
            f"write a fresh report rather than comparing against one this "
            f"version may misread")
    try:
        return SuiteRun(
            suite=raw["suite"], provider=raw["provider"], model=raw["model"],
            at=raw["at"], repeat=raw["repeat"],
            cases_in_suite=raw["cases_in_suite"],
            filtered=raw.get("filtered"),
            cases=[CaseRecord(
                id=c["id"], passed=c["passed"], attempts=c["attempts"],
                passes=c["passes"], min_pass_rate=c["min_pass_rate"],
                tokens=c["tokens"], seconds=c["seconds"],
                ran_model=c["ran_model"], failures=list(c.get("failures", [])),
                usd=c.get("usd"),
            ) for c in raw["cases"]],
        )
    except (KeyError, TypeError) as exc:
        raise ConfigError(f"{path} is missing part of an eval report: "
                          f"{exc}") from None


#: What happened to one case between two runs. ``kind`` is the word the
#: reader acts on; everything else is the evidence for it.
@dataclass(slots=True)
class CaseDelta:
    """One case, then and now."""

    id: str
    kind: str                       # fixed | broke | same | added | gone
    before: CaseRecord | None
    after: CaseRecord | None

    @property
    def rate_moved(self) -> bool:
        """The pass COUNT changed while the verdict did not -- a case going
        from 9/10 to 6/10 is still green and is still the most useful line
        in the report."""
        return (self.kind == "same" and self.before is not None
                and self.after is not None
                and (self.before.passes, self.before.attempts)
                != (self.after.passes, self.after.attempts))

    @property
    def rate_direction(self) -> str:
        """"up" or "down", comparing FRACTIONS rather than counts.

        The one place a rate is the right unit: 7/10 against 2/3 is a
        genuine comparison of the same claim at two sample sizes, and the
        header has already warned that the sizes differ.
        """
        if self.before is None or self.after is None:
            return "up"
        was = self.before.passes / self.before.attempts if self.before.attempts else 0.0
        now = self.after.passes / self.after.attempts if self.after.attempts else 0.0
        return "up" if now >= was else "down"

    @property
    def tokens_moved(self) -> int:
        if self.before is None or self.after is None:
            return 0
        return self.after.tokens - self.before.tokens

    @property
    def usd_moved(self) -> float | None:
        """The difference in dollars, or None when either side has none."""
        if self.before is None or self.after is None:
            return None
        if self.before.usd is None or self.after.usd is None:
            return None
        return self.after.usd - self.before.usd

    @property
    def movement_is_evidence(self) -> bool:
        """Whether the pass counts moved by more than sampling noise.

        9/10 then 6/10 LOOKS like a regression and is not evidence of one:
        both counts are consistent with the same underlying rate, so the
        two intervals overlap (notes/47). The line still prints -- the
        movement happened -- and this says what it is worth, which is the
        difference between a report and an alarm.
        """
        if self.before is None or self.after is None:
            return False
        was, now = self.before.confidence, self.after.confidence
        if was is None or now is None:
            return True          # deterministic: a change IS the evidence
        return not overlaps(was, now)


@dataclass(slots=True)
class Comparison:
    """Two runs, case by case, in the second run's order.

    Cases only one side has are KEPT (``added`` / ``gone``) rather than
    intersected away: a case that disappeared between two runs is the
    single most important thing a comparison can tell you, and an
    intersection is precisely the operation that hides it.
    """

    before: SuiteRun
    after: SuiteRun
    deltas: list[CaseDelta]

    @property
    def fixed(self) -> list[CaseDelta]:
        return [d for d in self.deltas if d.kind == "fixed"]

    @property
    def broke(self) -> list[CaseDelta]:
        return [d for d in self.deltas if d.kind == "broke"]

    @property
    def comparable(self) -> bool:
        """Whether the two runs graded the same set of cases.

        False does not mean "refuse" -- it means the header has to say so,
        because a subset compared against a suite produces a page of
        'gone' lines that mean nothing went missing.
        """
        return (self.before.filtered == self.after.filtered
                and {d.id for d in self.deltas if d.before is None} == set()
                and {d.id for d in self.deltas if d.after is None} == set())

    @property
    def tokens_moved(self) -> int:
        return self.after.tokens - self.before.tokens

    @property
    def usd_moved(self) -> float | None:
        """Dollars then against dollars now, or None when one side has no
        figure to compare -- an unpriced model, or a report written before
        anybody wrote costs down."""
        if self.before.usd is None or self.after.usd is None:
            return None
        return self.after.usd - self.before.usd


@dataclass(slots=True)
class Matrix:
    """Three or more runs of one suite, lined up case by case.

    Two runs are a DIFFERENCE and three are a TABLE -- the same data, and
    a different question. "Did this get worse?" has a before and an after;
    "which of these three models should we use?" has no before at all, and
    rendering it as two differences makes the reader do the join in their
    head.

    The last run is the one that just happened, and the columns are in the
    order the operator named them, because that is the order they are
    holding in mind.
    """

    runs: list[SuiteRun]
    ids: list[str]

    def cell(self, case_id: str, column: int) -> CaseRecord | None:
        """One case in one run, or None when that run did not grade it."""
        for case in self.runs[column].cases:
            if case.id == case_id:
                return case
        return None

    @property
    def rows(self) -> list[tuple[str, list[CaseRecord | None]]]:
        return [(case_id, [self.cell(case_id, i)
                           for i in range(len(self.runs))])
                for case_id in self.ids]

    @property
    def comparable(self) -> bool:
        """Whether every run graded every case. False is a header line, not
        a refusal -- the same rule a pair follows."""
        return all(cell is not None for _, row in self.rows for cell in row)

    @property
    def models(self) -> list[str]:
        return [run.where for run in self.runs]


def line_up(runs: Sequence[SuiteRun]) -> Matrix:
    """Several runs -> one table, keeping every case any of them graded.

    Row order is the LAST run's -- the one just watched go past -- with
    cases only the earlier runs have appended in the order they first
    appear. Same rule as ``compare``: nothing is intersected away, because
    a case that one run graded and another did not is the most important
    thing a comparison can say.
    """
    ordered = list(runs)
    ids: list[str] = [c.id for c in ordered[-1].cases] if ordered else []
    for run in ordered[:-1]:
        for case in run.cases:
            if case.id not in ids:
                ids.append(case.id)
    return Matrix(runs=ordered, ids=ids)


def compare(before: SuiteRun, after: SuiteRun) -> Comparison:
    """What changed, in the LATER run's order.

    The later run's order because that is the order the operator just
    watched go past; a report sorted by severity reads well and matches
    nothing on screen.
    """
    old = {c.id: c for c in before.cases}
    new = {c.id: c for c in after.cases}
    deltas: list[CaseDelta] = []
    for case in after.cases:
        was = old.get(case.id)
        if was is None:
            kind = "added"
        elif was.passed == case.passed:
            kind = "same"
        else:
            kind = "fixed" if case.passed else "broke"
        deltas.append(CaseDelta(id=case.id, kind=kind, before=was,
                                after=case))
    for case in before.cases:
        if case.id not in new:
            deltas.append(CaseDelta(id=case.id, kind="gone", before=case,
                                    after=None))
    return Comparison(before=before, after=after, deltas=deltas)
