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

AND THE PRICES BEHIND THEM ARE WRITTEN DOWN TOO (notes/62). A figure
without its rates can say that a run got cheaper and never why: the agent
spending fewer tokens and the vendor cutting a price print the same
number. So a report carries the four rates it was priced at and where
they came from, once per run -- every figure in a run is priced by one
model's row, so one row is what was used.

A REPORT IS ALSO A SAMPLE (notes/62). Twenty runs of one case across
twenty days are twenty intervals, each too wide to say much; pooled, they
are one narrow one. The reports on disk ARE the store keyed by case --
nothing new is kept anywhere -- and how far back to look is the list of
files the operator names. What pooling refuses to do is add together runs
that were not samples of the same thing: a different model or a different
package version is a different rate, pooled on its own and never summed.
A version the author forgot to bump is caught by the package's
fingerprint, which every report carries (notes/68): one label, two
packages, two pools.
The same files hold every case's dollars, so a pooled case also says what
one run of it cost in the oldest report and the newest (notes/66) -- per
run, because ten repeats and three are not the same bill -- and the pool
can be written down as its own format, never mistaken for a report.
Tokens per run are pooled the same way (notes/67): dollars move with
prices and tokens do not, so the pair says whether it was the agent that
changed or the vendor -- and on a free road they are the only figure.

A REPORT NAMES THE TURNS BEHIND IT when the suite ran with ``--trace``
(notes/65): each case row lists the trace ids of its runs, so a red line
leads to what the agent actually did.

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
from yantra.pricing import is_free, price_source

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
    #: The recorded turns behind these runs, when the suite ran with
    #: ``--trace`` (notes/65): ids in the trace file, one per run that
    #: reached a model. Empty otherwise, and on every older report.
    traces: list[str] = field(default_factory=list)
    #: What the CASE was when this ran -- its table and grader module,
    #: hashed (notes/72). None on an older report, and unknown is not
    #: evidence that it changed.
    definition: str | None = None

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


@dataclass(frozen=True, slots=True)
class PriceRecord:
    """The rates a run's figures were priced at, and where they came from.

    ``source`` is one of the pricing table's names (``pricing.TABLE``),
    ``"YANTRA_PRICES"`` for an operator's override file, ``"free"`` for a
    provider that bills nothing, or ``"unpriced"`` for a model nobody has
    a row for. Rates are dollars per million tokens, None where the row
    has no separate rate (cached tokens then bill at the input rate).
    """

    source: str
    input: float | None = None
    output: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None

    @classmethod
    def for_model(cls, provider_name: str | None, model: str) -> PriceRecord:
        """What ``cost_now`` will use for this run, read the same way."""
        if is_free(provider_name, model):
            return cls(source="free")
        price, origin = price_source(model)
        if price is None or origin is None:
            return cls(source="unpriced")
        return cls(source=origin, input=price.input_per_mtok,
                   output=price.output_per_mtok,
                   cache_read=price.cache_read_per_mtok,
                   cache_write=price.cache_write_per_mtok)

    @property
    def rates(self) -> tuple[float | None, ...]:
        return (self.input, self.output, self.cache_read, self.cache_write)

    @property
    def describe(self) -> str:
        """"$3.00 in / $15.00 out per Mtok (built-in 2026-08)"."""
        if self.input is None or self.output is None:
            return self.source
        return (f"${self.input:.2f} in / ${self.output:.2f} out per Mtok "
                f"({self.source})")

    def to_json(self) -> dict[str, Any]:
        return {"source": self.source, "input": self.input,
                "output": self.output, "cache_read": self.cache_read,
                "cache_write": self.cache_write}

    @classmethod
    def from_json(cls, raw: Any) -> PriceRecord | None:
        if not isinstance(raw, dict) or "source" not in raw:
            return None
        return cls(source=raw["source"], input=raw.get("input"),
                   output=raw.get("output"), cache_read=raw.get("cache_read"),
                   cache_write=raw.get("cache_write"))


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
    #: The rates behind every ``usd`` in this run (notes/62). None on a
    #: report written before prices were kept.
    pricing: PriceRecord | None = None
    #: What the package WAS when this ran (fingerprint.py, notes/68): a
    #: hash of the files it is built from. None on an older report, or
    #: for an agent with no package directory -- unknown, not "same".
    package: str | None = None
    #: The digest of the weights behind a local model tag (notes/72), so a
    #: re-pulled ``qwen3.8:latest`` is not pooled as the same model. None
    #: on a cloud provider, which does not say, and on older reports.
    weights: str | None = None

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
               filtered: list[str] | None = None,
               pricing: PriceRecord | None = None,
               package: str | None = None,
               weights: str | None = None,
               definitions: dict[str, str | None] | None = None
               ) -> SuiteRun:
    """``CaseOutcome``s -> the record. Reads only the public properties, so
    an outcome type that grows a field does not have to grow one here."""
    return SuiteRun(
        suite=suite, provider=provider, model=model,
        at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        repeat=repeat, cases_in_suite=cases_in_suite,
        filtered=list(filtered) if filtered else None,
        pricing=pricing, package=package, weights=weights,
        cases=[CaseRecord(
            id=o.case_id, passed=o.passed, attempts=o.attempts,
            passes=o.passes, min_pass_rate=o.min_pass_rate,
            tokens=o.tokens_used, seconds=round(o.duration_seconds, 3),
            ran_model=o.ran_model, failures=list(o.failures),
            usd=getattr(o, "usd", None),
            traces=list(getattr(o, "traces", [])),
            definition=(definitions or {}).get(o.case_id),
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
        "pricing": run.pricing.to_json() if run.pricing else None,
        "package": run.package,
        "weights": run.weights,
        "passed": run.passed,
        "tokens": run.tokens,
        "cases": [_case_json(c) for c in run.cases],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot write eval report {path}: {exc}") from None


def _case_json(case: CaseRecord) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": case.id, "passed": case.passed, "attempts": case.attempts,
        "passes": case.passes, "min_pass_rate": case.min_pass_rate,
        "tokens": case.tokens, "seconds": case.seconds,
        "ran_model": case.ran_model, "failures": case.failures,
        "usd": case.usd,
    }
    if case.definition is not None:
        row["definition"] = case.definition
    if case.traces:
        # Only when a trace file was kept, so a report from a run that
        # recorded nothing reads exactly as it always did.
        row["traces"] = case.traces
    return row


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
            pricing=PriceRecord.from_json(raw.get("pricing")),
            package=raw.get("package"),
            weights=raw.get("weights"),
            cases=[CaseRecord(
                id=c["id"], passed=c["passed"], attempts=c["attempts"],
                passes=c["passes"], min_pass_rate=c["min_pass_rate"],
                tokens=c["tokens"], seconds=c["seconds"],
                ran_model=c["ran_model"], failures=list(c.get("failures", [])),
                usd=c.get("usd"),
                traces=list(c.get("traces", [])),
                definition=c.get("definition"),
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
    def definition_changed(self) -> bool:
        """This case was edited between the two runs (notes/72), so a
        moved verdict may be the edit. False when either side is unknown."""
        return (self.before is not None and self.after is not None
                and self.before.definition is not None
                and self.after.definition is not None
                and self.before.definition != self.after.definition)

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

    @property
    def prices_moved(self) -> bool | None:
        """Whether the two runs were priced at different rates, or None
        when one of them did not write its rates down.

        True is the sentence notes/48 could not say: part of the cost line
        is the vendor, not the agent. Across two models it is simply
        expected, and still worth the line.
        """
        if self.before.pricing is None or self.after.pricing is None:
            return None
        return self.before.pricing.rates != self.after.pricing.rates

    @property
    def weights_changed(self) -> bool:
        """The same tag on both sides, and different weights behind it
        (notes/72): the model was re-pulled between the runs."""
        return (self.before.where == self.after.where
                and self.before.weights is not None
                and self.after.weights is not None
                and self.before.weights != self.after.weights)

    @property
    def package_changed(self) -> bool:
        """The same suite label on both sides, and two different packages
        behind it (notes/68): the version was not bumped, and "fixed" or
        "broke" below may be the edit rather than the model's luck.

        False when either side has no fingerprint -- an older report is
        unknown, and unknown is not evidence of a change.
        """
        return (self.before.suite == self.after.suite
                and self.before.package is not None
                and self.after.package is not None
                and self.before.package != self.after.package)


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

    def disagrees(self, case_id: str) -> bool:
        """Whether the runs that graded this case reached different verdicts.

        A blank is not a verdict: a case one run never had is a hole in the
        table (notes/49), not a vote against the runs that did.
        """
        verdicts = {cell.passed for cell in
                    (self.cell(case_id, i) for i in range(len(self.runs)))
                    if cell is not None}
        return len(verdicts) > 1

    def reds(self, case_id: str) -> int:
        """How many runs graded this case red."""
        return sum(1 for i in range(len(self.runs))
                   if (cell := self.cell(case_id, i)) is not None
                   and not cell.passed)

    def sorted_by(self, key: str) -> Matrix:
        """The same table, rows reordered (notes/83). Never filtered.

        ``disagree`` puts the cases the runs split on first -- the rows a
        table is for. ``red`` puts the most red cells first. ``id`` is
        alphabetical, for finding one case in forty. Each sort is STABLE,
        so rows that tie keep the last run's order rather than a new one
        nobody asked for.
        """
        if key not in SORTS:
            raise ValueError(f"unknown sort {key!r}; known: "
                             f"{', '.join(SORTS)}")
        if key == "id":
            ids = sorted(self.ids)
        elif key == "red":
            ids = sorted(self.ids, key=lambda i: -self.reds(i))
        else:
            ids = sorted(self.ids, key=lambda i: not self.disagrees(i))
        return Matrix(runs=self.runs, ids=ids)


#: What ``--sort`` accepts (notes/83). The default is no key at all: the
#: last run's order, which is the order the operator just watched.
SORTS = ("disagree", "red", "id")


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


@dataclass(slots=True)
class PooledCase:
    """One case's counts, summed over every run that rolled its die."""

    id: str
    passes: int
    attempts: int
    runs: int                   # how many reports contributed
    min_pass_rate: float        # the NEWEST report's claim
    claim_changed: bool         # an older report claimed something else
    disagree: bool              # two runs' own intervals do not overlap
    #: Dollars PER RUN in the oldest and newest report that priced this
    #: case (notes/66), or None when none did. Per run, because
    #: a report of ten repeats and one of three are not the same bill.
    usd_first: float | None = None
    usd_last: float | None = None
    #: Whether those two reports were priced at different rates: when
    #: True, part of the move is the vendor's, not the agent's (notes/62).
    price_moved: bool = False
    #: Tokens PER RUN in the oldest and newest report that counted any
    #: (notes/67). Dollars move with prices and tokens do not, so these
    #: say whether the AGENT changed -- on a free road, and in reports
    #: written before rates were kept, they are the only figure there is.
    tokens_first: float | None = None
    tokens_last: float | None = None
    #: The case definition this row pools (notes/72), and how many
    #: definitions of this case the runs held when that is more than one
    #: -- an edited case is pooled apart, one row per definition.
    definition: str | None = None
    definitions: int = 0

    @property
    def tally(self) -> str:
        return f"{self.passes}/{self.attempts}"

    @property
    def confidence(self) -> tuple[float, float]:
        return wilson_bounds(self.passes, self.attempts)

    @property
    def usd_growth(self) -> float | None:
        """Newest per-run cost over the oldest, or None when there is no
        pair of priced, non-zero figures to divide."""
        if not self.usd_first or self.usd_last is None:
            return None
        return self.usd_last / self.usd_first

    @property
    def tokens_growth(self) -> float | None:
        """Newest per-run token count over the oldest, or None without two
        reports that counted any."""
        if not self.tokens_first or self.tokens_last is None:
            return None
        return self.tokens_last / self.tokens_first

    @property
    def standing(self) -> str:
        """``holds``, ``below`` or ``unsettled`` against the case's claim.

        ``holds`` when even the low end of the pooled interval reaches the
        claim; ``below`` when even the high end misses it; ``unsettled``
        is everything in between -- the honest word for most cases on
        most evidence, and the one that says more runs would help.

        A claim of 1.0 is read the way the gate reads it: one failure
        breaks it, and no number of greens can lift an interval's low end
        all the way to 1, so "no run failed" is what ``holds`` means there.
        """
        if self.min_pass_rate >= 1:
            return "holds" if self.passes == self.attempts else "below"
        lo, hi = self.confidence
        if lo >= self.min_pass_rate:
            return "holds"
        if hi < self.min_pass_rate:
            return "below"
        return "unsettled"


@dataclass(slots=True)
class Pool:
    """Every named run of ONE suite version on ONE model, pooled by case.

    ``runs`` are oldest first. ``roster_only`` names cases that never
    reached a model in any of them: they roll no die, so there is nothing
    to pool, and they are named rather than silently missing.
    """

    suite: str
    where: str
    runs: list[SuiteRun]
    cases: list[PooledCase]
    roster_only: list[str]
    #: The fingerprint every known run in this pool shares (notes/68), or
    #: None when no run in it recorded one.
    package: str | None = None
    #: Set when ONE suite label on one model held several packages, so
    #: this pool is one of those split apart. The count of packages found.
    split_from: int = 0
    #: Runs pooled here with no fingerprint (older reports). They join the
    #: one known package when there is exactly one, as they always did.
    unknown: int = 0
    #: The model weights every known run here shares (notes/72), and how
    #: many different weights one tag held when this pool is one of them.
    weights: str | None = None
    weights_split: int = 0

    @property
    def span(self) -> str:
        """"2026-09-01T..Z .. 2026-09-20T..Z", or the one time."""
        first, last = self.runs[0].at, self.runs[-1].at
        return first if first == last else f"{first} .. {last}"


def pool(runs: Sequence[SuiteRun]) -> list[Pool]:
    """Several reports -> one pool per (suite, model), cases summed.

    SAMPLES OF THE SAME THING OR NOTHING. A report is keyed by the suite
    label (the package's name AND version) and the provider/model it ran
    against; runs that differ in either are separate pools, because 9/10
    on one model and 2/10 on another is not 11/20 of anything. The version
    is only as good as the author's habit of bumping it, so a report also
    carries the package's FINGERPRINT (notes/68), and one label holding
    two fingerprints is split into a pool per package. A report too old to
    have one is unknown, not different: it joins the only known package
    when there is one, and pools apart when there are several. What is
    left for ``disagree`` is the edit nobody fingerprinted -- two runs of
    one case whose intervals do not even overlap are evidence that
    something moved between them anyway.

    Only runs that reached a model count. A run that stopped at a failed
    roster assertion is a verdict about the tool list, not a die roll.
    """
    pools: list[Pool] = []
    for (suite, where), members in _by_label(runs).items():
        members = sorted(members, key=lambda r: r.at)
        by_package = _split(members, lambda r: r.package)
        for package, of_package in by_package:
            by_weights = _split(of_package, lambda r: r.weights)
            for weights, group in by_weights:
                pools.append(_pooled(
                    suite, where, group, package=package,
                    split_from=len(by_package) if len(by_package) > 1 else 0,
                    weights=weights,
                    weights_split=(len(by_weights) if len(by_weights) > 1
                                   else 0)))
    return pools


def _split(items: Sequence[Any], key) -> list[tuple[Any, list[Any]]]:
    """Items grouped by ``key``, where None is UNKNOWN, NOT DIFFERENT.

    The one rule every fingerprint in a pool follows (notes/68, notes/72):
    no known value, or exactly one, and everything is one group under it
    -- an older report joins the only thing it could have been. Several
    known values, and each is its own group, with the unknowns together
    in a last group of their own, because they cannot be placed. Groups
    come in the order their value first appears, which for runs sorted
    by time is oldest first.
    """
    known = list(dict.fromkeys(key(i) for i in items if key(i) is not None))
    if len(known) <= 1:
        return [(known[0] if known else None, list(items))]
    groups = [(value, [i for i in items if key(i) == value])
              for value in known]
    unplaced = [i for i in items if key(i) is None]
    if unplaced:
        groups.append((None, unplaced))
    return groups


def _pooled(suite: str, where: str, members: list[SuiteRun], *,
            package: str | None, split_from: int, weights: str | None,
            weights_split: int) -> Pool:
    """One pool's worth of runs, summed case by case."""
    members = sorted(members, key=lambda r: r.at)
    ids: list[str] = []
    for run in reversed(members):             # newest run's order first
        for case in run.cases:
            if case.id not in ids:
                ids.append(case.id)
    cases: list[PooledCase] = []
    roster_only: list[str] = []
    for case_id in ids:
        rolled_runs = [(run, c) for run in members for c in run.cases
                       if c.id == case_id and c.ran_model and c.attempts]
        if not rolled_runs:
            roster_only.append(case_id)
            continue
        # An edited case is a different question under the same id: its
        # runs pool apart, one row per definition (notes/72).
        by_definition = _split(rolled_runs, lambda pair: pair[1].definition)
        for definition, pairs in by_definition:
            cases.append(_pooled_case(
                case_id, pairs, definition=definition,
                definitions=(len(by_definition) if len(by_definition) > 1
                             else 0)))
    return Pool(suite=suite, where=where, runs=members, cases=cases,
                roster_only=roster_only, package=package,
                split_from=split_from,
                unknown=sum(1 for r in members if r.package is None),
                weights=weights, weights_split=weights_split)


def _pooled_case(case_id: str, pairs: list[tuple[SuiteRun, CaseRecord]], *,
                 definition: str | None, definitions: int) -> PooledCase:
    rolled = [c for _, c in pairs]
    intervals = [wilson_bounds(c.passes, c.attempts) for c in rolled]
    claims = {c.min_pass_rate for c in rolled}
    # The same files already hold every run's dollars; which case got
    # expensive is the same kind of sum as which got flaky.
    priced = [(run, c) for run, c in pairs if c.usd is not None]
    first = priced[0] if priced else None
    last = priced[-1] if priced else None
    # A run that reached a model and counted no tokens is a provider that
    # reported no usage -- an unknown, not a zero.
    counted = [c for c in rolled if c.tokens]
    return PooledCase(
        id=case_id,
        passes=sum(c.passes for c in rolled),
        attempts=sum(c.attempts for c in rolled),
        runs=len(rolled),
        min_pass_rate=rolled[-1].min_pass_rate,
        claim_changed=len(claims) > 1,
        disagree=(max(lo for lo, _ in intervals)
                  > min(hi for _, hi in intervals)),
        usd_first=(first[1].usd / first[1].attempts if first else None),
        usd_last=(last[1].usd / last[1].attempts if last else None),
        price_moved=bool(
            first and last and first[0] is not last[0]
            and first[0].pricing is not None
            and last[0].pricing is not None
            and first[0].pricing.rates != last[0].pricing.rates),
        tokens_first=(counted[0].tokens / counted[0].attempts
                      if counted else None),
        tokens_last=(counted[-1].tokens / counted[-1].attempts
                     if counted else None),
        definition=definition, definitions=definitions,
    )


def _by_label(runs: Sequence[SuiteRun]) -> dict[tuple[str, str],
                                                list[SuiteRun]]:
    groups: dict[tuple[str, str], list[SuiteRun]] = {}
    for run in runs:
        groups.setdefault((run.suite, run.where), []).append(run)
    return groups


#: The pooled file's own tag. A pool is not a report -- it has no verdict
#: and no single run behind it -- so it gets a format of its own rather
#: than a report that ``--against`` would try, and fail, to compare with.
POOL_FORMAT = "yantra.pool.v1"


def write_pool(path: Path, pools: Sequence[Pool]) -> None:
    """Write what ``--pool`` printed, as JSON (notes/66).

    For whatever tracks the pooled range over time -- a dashboard, a
    weekly job -- which should not have to scrape a terminal. Everything
    derived is written as well as the counts it came from (the interval,
    the standing), because the reader is a program that should not need
    this module to know what "unsettled" means.
    """
    payload = {
        "format": POOL_FORMAT,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pools": [{
            "suite": group.suite,
            "where": group.where,
            "package": group.package,
            "split_from": group.split_from,
            "weights": group.weights,
            "weights_split": group.weights_split,
            "runs": [run.at for run in group.runs],
            "roster_only": group.roster_only,
            "cases": [{
                "id": c.id, "passes": c.passes, "attempts": c.attempts,
                "runs": c.runs, "low": round(c.confidence[0], 4),
                "high": round(c.confidence[1], 4),
                "min_pass_rate": c.min_pass_rate, "standing": c.standing,
                "claim_changed": c.claim_changed, "disagree": c.disagree,
                "usd_first": c.usd_first, "usd_last": c.usd_last,
                "price_moved": c.price_moved,
                "definition": c.definition, "definitions": c.definitions,
                "tokens_first": _rounded(c.tokens_first),
                "tokens_last": _rounded(c.tokens_last),
            } for c in group.cases],
        } for group in pools],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot write pool {path}: {exc}") from None


def _rounded(tokens: float | None) -> float | None:
    """A per-run token count to one decimal: 1234.333... is not a figure
    anybody measured."""
    return None if tokens is None else round(tokens, 1)
