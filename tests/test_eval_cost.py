"""What a suite run cost, and the two ways a dollar figure lies.

The bias here is that money is the number people trust most and check
least. Two specific lies:

* **$0.00 for "we don't know".** A hosted model with no price entry has
  an unknown cost; a local server has a cost of zero. Rendering both as
  zero teaches exactly the wrong instinct about what a suite costs, so
  the tests below keep ``None`` and ``0.0`` apart at every layer --
  result, outcome, record, and the closing line.
* **History rewritten by a price change.** A report stores the figure it
  was written with and nothing recomputes it. The test for that prices a
  run, moves the table underneath it, and asserts the file did not move.

The third thing pinned here is that an old report -- written before this
key existed -- still loads, and says it has no figure rather than
claiming a free run.
"""

from __future__ import annotations

import json

from conftest import ScriptedProvider, assistant_text
from yantra.eval_report import CaseRecord, SuiteRun, read_report, write_report
from yantra.eval_suite import CASES, SUITE_DIR
from yantra.evals import CaseOutcome, EvalResult
from yantra.package import MANIFEST
from yantra.pricing import ModelPrice, cost_now
from yantra.types import Usage


def usage(inp=1000, out=500):
    return Usage(input_tokens=inp, output_tokens=out)


def result(usd=None, ran_model=True):
    return EvalResult(case_id="x", passed=True, failures=[], final_answer="",
                      tokens_used=10, iterations_used=1, tool_calls_seen=[],
                      duration_seconds=1.0, ran_model=ran_model, usd=usd)


def record(usd=None, ran_model=True):
    return CaseRecord(id="x", passed=True, attempts=1, passes=1,
                      min_pass_rate=1.0, tokens=10, seconds=1.0,
                      ran_model=ran_model, usd=usd)


def suite(*cases):
    return SuiteRun(suite="s", provider="p", model="m", at="t", repeat=1,
                    cases=list(cases), cases_in_suite=len(cases))


class TestPricingOneRun:
    def test_a_local_provider_costs_zero_and_says_so(self):
        """Free is a fact, not a missing price."""
        assert cost_now(usage(), "ollama", "qwen3.8:27b") == 0.0

    def test_an_unpriced_hosted_model_is_unknown_not_free(self):
        assert cost_now(usage(), "anthropic", "some-model-nobody-priced") is None

    def test_a_priced_model_is_the_weighted_sum(self, monkeypatch):
        import yantra.pricing as pricing
        monkeypatch.setitem(pricing._EXACT, "test-model",
                            ModelPrice(input_per_mtok=3.0, output_per_mtok=15.0))
        # 1000 in at $3/M + 500 out at $15/M = 0.003 + 0.0075
        assert round(cost_now(usage(), "anthropic", "test-model"), 6) == 0.0105


class TestAcrossRuns:
    def test_an_unpriced_run_contributes_nothing_rather_than_zero(self):
        o = CaseOutcome(case_id="x", runs=[result(usd=0.01), result(usd=None)])
        assert o.usd == 0.01

    def test_a_case_with_no_priced_run_at_all_has_no_figure(self):
        assert CaseOutcome(case_id="x", runs=[result(usd=None)]).usd is None

    def test_a_suite_is_fully_priced_only_if_every_paid_case_is(self):
        assert suite(record(usd=0.01), record(usd=None)).fully_priced is False
        assert suite(record(usd=0.01), record(usd=0.02)).fully_priced is True

    def test_a_roster_only_case_does_not_make_a_run_unpriced(self):
        """It reached no model, so it has nothing to price."""
        assert suite(record(usd=0.01),
                     record(usd=None, ran_model=False)).fully_priced is True


class TestTheStoredFigure:
    def test_a_price_change_does_not_rewrite_an_old_report(self, tmp_path,
                                                           monkeypatch):
        """The whole rule: a run costs what it cost on the day it ran."""
        import yantra.pricing as pricing
        path = tmp_path / "r.json"
        write_report(path, suite(record(usd=0.0105)))
        monkeypatch.setitem(pricing._EXACT, "m",
                            ModelPrice(input_per_mtok=300.0,
                                       output_per_mtok=1500.0))
        assert read_report(path).cases[0].usd == 0.0105

    def test_a_report_written_before_costs_were_kept_still_loads(self,
                                                                 tmp_path):
        path = tmp_path / "r.json"
        write_report(path, suite(record(usd=0.01)))
        raw = json.loads(path.read_text())
        del raw["cases"][0]["usd"]           # an older writer's file
        path.write_text(json.dumps(raw))
        back = read_report(path)
        assert back.cases[0].usd is None
        assert back.usd is None

    def test_a_figure_round_trips(self, tmp_path):
        path = tmp_path / "r.json"
        write_report(path, suite(record(usd=0.0105), record(usd=0.02)))
        assert round(read_report(path).usd, 6) == 0.0305


class TestThroughTheGate:
    def _suite_dir(self, tmp_path):
        root = tmp_path / "pkg"
        (root / SUITE_DIR).mkdir(parents=True, exist_ok=True)
        (root / MANIFEST).write_text('[agent]\nname = "pkg"\n')
        (root / SUITE_DIR / CASES).write_text(
            '[[case]]\nid = "x"\nuser_message = "hi"\n')
        return root

    def _run(self, monkeypatch, argv, spend=True):
        import yantra.cli.main as cli_main
        billed = Usage(input_tokens=1000, output_tokens=500) if spend else None
        provider = ScriptedProvider([assistant_text("done", usage=billed)] * 4)
        monkeypatch.setattr(cli_main, "guess_provider", lambda: "anthropic")
        monkeypatch.setattr(cli_main, "load_settings", lambda name: object())
        monkeypatch.setattr(cli_main, "get_provider", lambda *a, **k: provider)
        return cli_main.main(argv)

    def test_a_local_run_prints_no_dollar_figure(self, tmp_path, monkeypatch,
                                                 capsys):
        """$0.0000 beside a two-minute suite reads as a broken meter."""
        root = self._suite_dir(tmp_path)
        out_path = tmp_path / "r.json"
        self._run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                                "ollama", "--model", "qwen3.8:27b",
                                "--report", str(out_path)])
        assert "$" not in capsys.readouterr().out
        assert read_report(out_path).cases[0].usd == 0.0

    def test_a_priced_run_prints_what_it_cost(self, tmp_path, monkeypatch,
                                              capsys):
        import yantra.pricing as pricing
        monkeypatch.setitem(pricing._EXACT, "priced-model",
                            ModelPrice(input_per_mtok=3.0, output_per_mtok=15.0))
        root = self._suite_dir(tmp_path)
        self._run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                                "anthropic", "--model", "priced-model"])
        assert "$" in capsys.readouterr().out

    def test_a_comparison_reports_both_sides_as_they_were_priced(
            self, tmp_path, monkeypatch, capsys):
        import yantra.pricing as pricing
        monkeypatch.setitem(pricing._EXACT, "priced-model",
                            ModelPrice(input_per_mtok=3.0, output_per_mtok=15.0))
        root = self._suite_dir(tmp_path)
        base = tmp_path / "base.json"
        write_report(base, SuiteRun(suite="pkg", provider="anthropic",
                                    model="dearer-model", at="t", repeat=1,
                                    cases=[CaseRecord(
                                        id="x", passed=True, attempts=1,
                                        passes=1, min_pass_rate=1.0,
                                        tokens=10, seconds=1.0,
                                        ran_model=True, usd=1.25)],
                                    cases_in_suite=1))
        self._run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                                "anthropic", "--model", "priced-model",
                                "--against", str(base)])
        out = " ".join(capsys.readouterr().out.split())
        assert "cost: $1.2500" in out

    def test_a_comparison_against_a_report_with_no_figure_says_so(
            self, tmp_path, monkeypatch, capsys):
        import yantra.pricing as pricing
        monkeypatch.setitem(pricing._EXACT, "priced-model",
                            ModelPrice(input_per_mtok=3.0, output_per_mtok=15.0))
        root = self._suite_dir(tmp_path)
        base = tmp_path / "base.json"
        write_report(base, suite(record(usd=None)))
        self._run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                                "anthropic", "--model", "priced-model",
                                "--against", str(base)])
        out = " ".join(capsys.readouterr().out.split())
        assert "recorded no dollar figure" in out
