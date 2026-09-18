"""What a pass count is evidence of, and the two lies about it.

The bias here is that a number printed beside a verdict gets believed.
Two specific ways that goes wrong, one in each direction:

* **Certainty from three coin flips.** The textbook normal interval
  computes a width of ZERO at 3/3 and at 0/5, which is where an eval
  suite actually lives. Several tests below pin the ends, because an
  interval that collapses there is worse than no interval at all.
* **A statistic that reddens a gate.** Nothing in this module may change
  a verdict. ``claim_is_supported`` is False on cases that PASSED, and
  the tests assert both halves of that sentence together, since a future
  edit that quietly wires it into ``passed`` would still look green here
  otherwise.

The third bias is arithmetic: the numbers are checked against values
computed by hand from the Wilson formula, not against this module's own
output, so a sign error cannot ratify itself.
"""

from __future__ import annotations

from yantra.confidence import (
    Z_95,
    describe,
    overlaps,
    perfect_runs_needed,
    wilson_bounds,
)
from yantra.eval_report import CaseRecord, compare
from yantra.evals import CaseOutcome, EvalResult


def result(passed=True, ran_model=True):
    return EvalResult(case_id="x", passed=passed, failures=[] if passed
                      else ["nope"], final_answer="", tool_calls_seen=[],
                      iterations_used=1, tokens_used=10, duration_seconds=1.0,
                      ran_model=ran_model)


def outcome(*runs, rate=1.0):
    return CaseOutcome(case_id="x", runs=list(runs), min_pass_rate=rate)


def record(passes, attempts, rate=1.0, ran_model=True):
    return CaseRecord(id="x", passed=True, attempts=attempts, passes=passes,
                      min_pass_rate=rate, tokens=1, seconds=1.0,
                      ran_model=ran_model)


class TestTheInterval:
    def test_a_perfect_record_is_not_certainty(self):
        """3/3 is consistent with a case that holds 44% of the time."""
        lo, hi = wilson_bounds(3, 3)
        assert hi == 1.0
        assert 0.43 < lo < 0.45

    def test_a_perfect_failure_is_not_certainty_either(self):
        lo, hi = wilson_bounds(0, 5)
        assert lo == 0.0
        assert 0.42 < hi < 0.44

    def test_one_green_run_is_consistent_with_one_in_five(self):
        """The sentence notes/35 could not say."""
        lo, _ = wilson_bounds(1, 1)
        assert 0.20 < lo < 0.22

    def test_the_interval_narrows_as_runs_are_bought(self):
        wide = wilson_bounds(7, 10)
        narrow = wilson_bounds(70, 100)
        assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])

    def test_seven_of_ten_spans_forty_to_eighty_nine_percent(self):
        lo, hi = wilson_bounds(7, 10)
        assert round(lo, 2) == 0.40 and round(hi, 2) == 0.89

    def test_no_samples_is_the_whole_range(self):
        assert wilson_bounds(0, 0) == (0.0, 1.0)

    def test_the_bounds_never_leave_zero_to_one(self):
        for attempts in range(1, 30):
            for passes in range(attempts + 1):
                lo, hi = wilson_bounds(passes, attempts)
                assert 0.0 <= lo <= hi <= 1.0

    def test_the_description_names_its_confidence_level(self):
        assert describe(7, 10) == "0.40-0.89 at 95%"

    def test_a_wider_z_gives_a_wider_interval(self):
        tight = wilson_bounds(7, 10, z=1.0)
        loose = wilson_bounds(7, 10, z=Z_95)
        assert loose[0] < tight[0] and loose[1] > tight[1]


class TestHowManyRuns:
    def test_a_claim_of_seven_tenths_needs_nine_perfect_runs(self):
        assert perfect_runs_needed(0.7) == 9
        assert wilson_bounds(9, 9)[0] >= 0.7
        assert wilson_bounds(8, 8)[0] < 0.7

    def test_the_answer_is_a_floor_not_a_promise(self):
        """It assumes every run is green; a case that fails one needs
        more, and a case whose true rate is under its claim never gets
        there."""
        for rate in (0.5, 0.75, 0.85, 0.9, 0.95):
            n = perfect_runs_needed(rate)
            assert wilson_bounds(n, n)[0] >= rate
            assert wilson_bounds(n - 1, n - 1)[0] < rate

    def test_a_claim_of_one_has_no_number(self):
        """n/(n+z²) approaches 1 and never reaches it; a large integer
        would read as advice."""
        assert perfect_runs_needed(1.0) == 0


class TestOverlap:
    def test_nine_of_ten_against_six_of_ten_is_not_evidence(self):
        assert overlaps(wilson_bounds(9, 10), wilson_bounds(6, 10)) is True

    def test_a_real_collapse_is(self):
        assert overlaps(wilson_bounds(20, 20), wilson_bounds(2, 20)) is False


class TestOnAnOutcome:
    def test_a_roster_only_case_gets_no_interval(self):
        """Deterministic: the same list, graded the same way. An interval
        over it would be arithmetic about nothing."""
        assert outcome(result(ran_model=False)).confidence is None

    def test_a_case_that_reached_a_model_gets_one(self):
        assert outcome(result(), result()).confidence is not None

    def test_a_case_can_pass_and_still_not_support_its_claim(self):
        """Both halves in one assertion: the day this reddens a gate is
        the day this test fails."""
        o = outcome(result(), result(), result(), rate=0.7)
        assert o.passed is True
        assert o.claim_is_supported is False

    def test_enough_green_runs_do_support_it(self):
        o = outcome(*[result() for _ in range(9)], rate=0.7)
        assert o.passed is True and o.claim_is_supported is True

    def test_a_case_claiming_certainty_is_never_reported_as_thin(self):
        """1.0 has no n, so "unsupported" would be permanent and useless."""
        assert outcome(result(), rate=1.0).claim_is_supported is True

    def test_a_roster_case_is_never_reported_as_thin(self):
        assert outcome(result(ran_model=False),
                       rate=0.7).claim_is_supported is True


class TestOnAComparison:
    def _delta(self, before, after):
        from yantra.eval_report import SuiteRun

        def run(rec):
            return SuiteRun(suite="s", provider="p", model="m", at="t",
                            repeat=rec.attempts, cases=[rec],
                            cases_in_suite=1)
        return compare(run(before), run(after)).deltas[0]

    def test_movement_inside_the_noise_says_so(self):
        d = self._delta(record(9, 10), record(6, 10))
        assert d.rate_moved is True
        assert d.movement_is_evidence is False

    def test_movement_outside_it_is_evidence(self):
        d = self._delta(record(20, 20), record(2, 20))
        assert d.movement_is_evidence is True

    def test_a_deterministic_case_that_changed_is_always_evidence(self):
        """A roster case has no die to blame: if it moved, something in
        the package moved."""
        d = self._delta(record(1, 1, ran_model=False),
                        record(0, 1, ran_model=False))
        assert d.movement_is_evidence is True

    def test_a_case_in_only_one_run_is_not_a_movement_to_judge(self):
        from yantra.eval_report import CaseDelta
        assert CaseDelta(id="x", kind="gone", before=record(1, 1),
                         after=None).movement_is_evidence is False
