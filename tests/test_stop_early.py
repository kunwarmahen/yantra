"""--repeat stops once a case cannot pass, and at no other time (notes/84).

The bias here is the careless sequential test. Stopping a run count
early is a real saving and a real way to fool yourself, and the two
differ only in WHICH side you stop on. So the tests pin:

* **It never changes a verdict.** A brute force over every pass/fail
  sequence up to four runs
  through the runner, and up to ten through the rule alone, at several claimed rates: whenever the
  runner stopped, every way the remaining runs could have gone was red.
* **It never stops on the green side.** A case on a winning streak runs
  to the end; the runs after a streak are what separate luck from a
  rate.
* **It never hides.** A stopped case says it stopped and how many runs
  it was set, and a pool that adds stopped counts says they lean low.
"""

from __future__ import annotations

import asyncio
import io
import itertools

import pytest
from rich.console import Console

from conftest import ScriptedProvider, assistant_text
from yantra.cli.main import _render_pools
from yantra.eval_report import (CaseRecord, SuiteRun, pool, record_run,
                                stopped_early)
from yantra.eval_suite import CASES, SUITE_DIR
from yantra.evals import (AsyncEvalRunner, CaseOutcome, EvalCase, EvalResult,
                          EvalRunner, out_of_reach, required_passes)
from yantra.package import MANIFEST
from yantra.permissions import yolo
from yantra.tools import ToolRegistry

PASS, FAIL = assistant_text("done"), assistant_text("nope")


def case(rate=1.0) -> EvalCase:
    return EvalCase(id="c", description="d", user_message="go",
                    check_answer=lambda a: "done" in a, min_pass_rate=rate)


def runner(script):
    provider = ScriptedProvider(list(script))
    return EvalRunner(provider, "m", tools=ToolRegistry(),
                      permissions=yolo), provider


def async_runner(script, concurrency):
    provider = ScriptedProvider(list(script))
    return AsyncEvalRunner(provider, "m", tools=ToolRegistry(),
                           permissions=yolo,
                           concurrency=concurrency), provider


class TestTheRule:
    def test_every_run_claim_stops_at_the_first_failure(self):
        r, provider = runner([FAIL] + [PASS] * 4)
        outcome = r.evaluate(case(), repeat=5)
        assert len(provider.requests) == 1
        assert outcome.stopped_early and outcome.planned == 5
        assert not outcome.passed and outcome.required_passes == 5

    def test_a_rate_claim_stops_when_the_rest_cannot_lift_it(self):
        """0.7 of 10 needs 7: after four failures, six runs are not enough."""
        r, provider = runner([FAIL] * 4 + [PASS] * 6)
        outcome = r.evaluate(case(0.7), repeat=10)
        assert len(provider.requests) == 4
        assert outcome.marks == "✗✗✗✗" and outcome.required_passes == 7

    def test_a_winning_streak_is_never_cut_short(self):
        r, provider = runner([PASS] * 9)
        outcome = r.evaluate(case(0.7), repeat=9)
        assert len(provider.requests) == 9 and not outcome.stopped_early

    def test_a_case_still_in_reach_keeps_running(self):
        r, provider = runner([FAIL, FAIL, FAIL] + [PASS] * 7)
        outcome = r.evaluate(case(0.7), repeat=10)
        assert len(provider.requests) == 10 and outcome.passed

    def test_all_runs_buys_every_one(self):
        r, provider = runner([FAIL] * 5)
        outcome = r.evaluate(case(), repeat=5, stop_early=False)
        assert len(provider.requests) == 5 and not outcome.stopped_early

    @pytest.mark.parametrize("rate", [1.0, 0.9, 0.7, 0.5, 0.34])
    @pytest.mark.parametrize("n", range(1, 5))
    def test_stopping_never_changes_a_verdict(self, rate, n):
        """Every sequence of n runs: if the runner stopped, no way the rest
        could have gone would have passed."""
        for seq in itertools.product([True, False], repeat=n):
            r, _ = runner([PASS if ok else FAIL for ok in seq])
            outcome = r.evaluate(case(rate), repeat=n)
            full = CaseOutcome(case_id="c", runs=[
                EvalResult(case_id="c", passed=ok, failures=[],
                           final_answer="", tokens_used=0, iterations_used=1,
                           tool_calls_seen=[], duration_seconds=0.0)
                for ok in seq], min_pass_rate=rate)
            assert outcome.passed == full.passed, (seq, rate)
            if outcome.stopped_early:
                done = seq[:outcome.attempts]
                best = sum(done) + (n - len(done))
                assert best < required_passes(rate, n), (seq, rate)

    @pytest.mark.parametrize("rate", [1.0, 0.95, 0.9, 0.7, 0.5, 0.34, 0.1])
    def test_the_rule_alone_never_stops_a_case_that_could_pass(self, rate):
        """The same property as above without the runner, out to ten runs:
        wherever out_of_reach says stop, the best case is still red."""
        for n in range(1, 11):
            for seq in itertools.product([True, False], repeat=n):
                for done in range(1, n + 1):
                    passes = sum(seq[:done])
                    if out_of_reach(rate, passes, done, n):
                        assert passes + (n - done) < required_passes(rate, n)
                        assert sum(seq) < required_passes(rate, n)

    def test_out_of_reach_is_futility_only(self):
        assert out_of_reach(0.7, passes=0, done=4, planned=10)
        assert not out_of_reach(0.7, passes=0, done=3, planned=10)
        # already passed is NOT a reason to stop
        assert not out_of_reach(0.7, passes=7, done=7, planned=10)


class TestTheAsyncTwin:
    def test_a_queued_run_is_never_started(self):
        r, provider = async_runner([FAIL] + [PASS] * 3, concurrency=1)
        outcome = asyncio.run(r.evaluate(case(), repeat=4))
        assert len(provider.requests) == 1 and outcome.stopped_early

    def test_a_run_already_in_flight_finishes_and_counts(self):
        """Its tokens are spent either way; a result thrown away is one the
        report cannot show."""
        class Slow(ScriptedProvider):
            async def astream(self, **kwargs):
                await asyncio.sleep(0.02)       # so the three truly overlap
                async for event in super().astream(**kwargs):
                    yield event

        provider = Slow([FAIL] * 3)
        r = AsyncEvalRunner(provider, "m", tools=ToolRegistry(),
                            permissions=yolo, concurrency=3)
        outcome = asyncio.run(r.evaluate(case(), repeat=3))
        assert len(provider.requests) == 3 and outcome.attempts == 3
        assert not outcome.stopped_early and not outcome.passed

    def test_all_runs_buys_every_one(self):
        r, provider = async_runner([FAIL] * 4, concurrency=1)
        outcome = asyncio.run(r.evaluate(case(), repeat=4, stop_early=False))
        assert len(provider.requests) == 4 and outcome.attempts == 4


class TestItSaysSo:
    def _suite(self, tmp_path):
        root = tmp_path / "pkg"
        (root / SUITE_DIR).mkdir(parents=True)
        (root / MANIFEST).write_text('[agent]\nname = "pkg"\n')
        (root / SUITE_DIR / CASES).write_text(
            '[[case]]\nid = "x"\nuser_message = "hi"\n'
            'required_tools = ["read_file"]\n')
        return root

    def _run(self, monkeypatch, argv, script):
        import yantra.cli.main as cli_main
        provider = ScriptedProvider(script)
        monkeypatch.setattr(cli_main, "guess_provider", lambda: "anthropic")
        monkeypatch.setattr(cli_main, "load_settings", lambda name: object())
        monkeypatch.setattr(cli_main, "get_provider", lambda *a, **k: provider)
        return cli_main.main(argv), provider

    def test_the_line_names_the_runs_it_was_set(self, tmp_path, monkeypatch,
                                                capsys):
        root = self._suite(tmp_path)
        rc, provider = self._run(monkeypatch, [
            "--agent", str(root), "--eval", "--provider", "anthropic",
            "--repeat", "5"], [assistant_text("no tool")] * 5)
        out = " ".join(capsys.readouterr().out.split())
        assert rc == 1 and len(provider.requests) == 1
        assert "✗ 0/1 runs of 5 · stopped, out of reach" in out
        assert "1 case(s) stopped early, 4 run(s) not bought" in out
        assert "a case stops once it can no longer reach its rate" in out

    def test_all_runs_is_said_and_obeyed(self, tmp_path, monkeypatch, capsys):
        root = self._suite(tmp_path)
        rc, provider = self._run(monkeypatch, [
            "--agent", str(root), "--eval", "--provider", "anthropic",
            "--repeat", "3", "--all-runs"], [assistant_text("no tool")] * 3)
        out = " ".join(capsys.readouterr().out.split())
        assert rc == 1 and len(provider.requests) == 3
        assert "every run is bought (--all-runs)" in out
        assert "stopped" not in out.replace("stops", "")

    def test_all_runs_outside_eval_is_refused(self, capsys):
        import yantra.cli.main as cli_main
        assert cli_main.main(["--all-runs", "--prompt", "hi"]) == 2
        assert "--all-runs" in capsys.readouterr().err


class TestInAReport:
    def _record(self, attempts, repeat, ran_model=True):
        return SuiteRun(suite="pkg 0.1", provider="p", model="m",
                        at="2026-09-01T00:00:00Z", repeat=repeat,
                        cases=[CaseRecord(id="x", passed=False,
                                          attempts=attempts, passes=0,
                                          min_pass_rate=1.0, tokens=10,
                                          seconds=1.0, ran_model=ran_model)],
                        cases_in_suite=1)

    def test_a_stopped_case_is_visible_without_a_new_key(self):
        run = self._record(attempts=1, repeat=5)
        assert stopped_early(run, run.cases[0])
        full = self._record(attempts=5, repeat=5)
        assert not stopped_early(full, full.cases[0])
        roster = self._record(attempts=1, repeat=5, ran_model=False)
        assert not stopped_early(roster, roster.cases[0])

    def test_the_record_keeps_the_short_count(self):
        r, _ = runner([FAIL] * 5)
        outcome = r.evaluate(case(), repeat=5)
        run = record_run([outcome], suite="s", provider="p", model="m",
                         repeat=5, cases_in_suite=1)
        assert run.cases[0].attempts == 1 and stopped_early(run, run.cases[0])

    def test_a_pool_of_stopped_counts_says_they_lean_low(self):
        pools = pool([self._record(1, 5), self._record(5, 5)])
        assert pools[0].cases[0].stopped == 1
        buf = io.StringIO()
        _render_pools(Console(file=buf, width=200), pools)
        assert "stopped early in 1 report(s)" in buf.getvalue()
        assert "leans low" in buf.getvalue()
