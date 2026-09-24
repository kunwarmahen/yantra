"""Reports read back without a run: compared, priced, and pooled.

The bias here is that a pile of reports is easy to over-read. Three
specific over-readings:

* **Adding runs that were not samples of one thing.** 9/10 on one model
  and 2/10 on another is not 11/20 of anything, so the tests below pin
  that a different model or package version is a separate pool, and that
  two runs of one case that disagree outright are flagged rather than
  averaged in silence.
* **A cost change blamed on the agent that was the vendor's.** A report
  now carries the rates behind its figures; the tests pin that a moved
  rate is named, that the same rates say so, and that an older report
  with no rates says it cannot tell.
* **A reading mode that grows into a gate.** ``--reports`` exits 0
  whatever the reports say. Only a file it cannot read is an error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yantra.cli.main import main
from yantra.eval_report import (CaseRecord, PriceRecord, SuiteRun, compare,
                                pool, read_report, write_report)
from yantra.pricing import TABLE, ModelPrice, price_source


def case(id="c", passes=1, attempts=1, rate=1.0, ran_model=True, usd=None):
    return CaseRecord(id=id, passed=passes / attempts >= rate if attempts
                      else True, attempts=attempts, passes=passes,
                      min_pass_rate=rate, tokens=10, seconds=1.0,
                      ran_model=ran_model, usd=usd)


def run(*cases, at="2026-09-01T00:00:00Z", model="m", suite="pkg 0.1",
        pricing=None):
    return SuiteRun(suite=suite, provider="p", model=model, at=at, repeat=1,
                    cases=list(cases), cases_in_suite=len(cases),
                    pricing=pricing)


def write(tmp_path, name, suite_run):
    path = tmp_path / f"{name}.json"
    write_report(path, suite_run)
    return str(path)


class TestWhereAPriceCameFrom:
    def test_a_built_in_row_names_the_table(self):
        price, origin = price_source("claude-sonnet-4-5")
        assert price is not None and origin == TABLE

    def test_an_override_names_the_override(self, tmp_path, monkeypatch):
        prices = tmp_path / "prices.json"
        prices.write_text(json.dumps({"claude-sonnet-4-5":
                                      {"input": 2.0, "output": 9.0}}))
        monkeypatch.setenv("YANTRA_PRICES", str(prices))
        price, origin = price_source("claude-sonnet-4-5")
        assert origin == "YANTRA_PRICES" and price.input_per_mtok == 2.0

    def test_an_unknown_model_has_no_origin_either(self):
        assert price_source("nobody-priced-this") == (None, None)

    def test_a_local_provider_records_free_not_unpriced(self):
        assert PriceRecord.for_model("ollama", "qwen3.8:latest").source == "free"
        assert PriceRecord.for_model("anthropic",
                                     "nobody-priced-this").source == "unpriced"


class TestTheRatesAreWrittenDown:
    def test_the_rates_round_trip(self, tmp_path):
        rates = PriceRecord(source=TABLE, input=3.0, output=15.0,
                            cache_read=0.3, cache_write=3.75)
        back = read_report(Path(write(tmp_path, "r", run(case(),
                                                         pricing=rates))))
        assert back.pricing == rates

    def test_a_report_from_before_rates_were_kept_still_loads(self, tmp_path):
        path = write(tmp_path, "r", run(case()))
        raw = json.loads(Path(path).read_text())
        del raw["pricing"]                        # an older writer's file
        Path(path).write_text(json.dumps(raw))
        assert read_report(Path(path)).pricing is None

    def test_rates_are_read_at_run_time_not_at_read_time(self, tmp_path,
                                                         monkeypatch):
        """Same rule as the figure: a table that moves later changes
        nothing already on disk."""
        import yantra.pricing as pricing
        monkeypatch.setitem(pricing._EXACT, "m", ModelPrice(3.0, 15.0))
        path = write(tmp_path, "r", run(case(), pricing=PriceRecord.for_model(
            "anthropic", "m")))
        monkeypatch.setitem(pricing._EXACT, "m", ModelPrice(300.0, 1500.0))
        assert read_report(Path(path)).pricing.input == 3.0


class TestWhetherThePriceMoved:
    def test_different_rates_are_a_moved_price(self):
        a = run(case(), pricing=PriceRecord(TABLE, 3.0, 15.0))
        b = run(case(), pricing=PriceRecord("YANTRA_PRICES", 2.0, 15.0))
        assert compare(a, b).prices_moved is True

    def test_the_same_rates_are_not(self):
        a = run(case(), pricing=PriceRecord(TABLE, 3.0, 15.0))
        assert compare(a, run(case(), pricing=PriceRecord(TABLE, 3.0,
                                                          15.0))).prices_moved is False

    def test_a_side_with_no_rates_cannot_answer(self):
        a = run(case(), pricing=PriceRecord(TABLE, 3.0, 15.0))
        assert compare(run(case()), a).prices_moved is None


class TestPooling:
    def test_counts_add_up_across_runs_of_the_same_thing(self):
        (only,) = pool([run(case(passes=9, attempts=10, rate=0.7)),
                        run(case(passes=8, attempts=10, rate=0.7),
                            at="2026-09-02T00:00:00Z")])
        (c,) = only.cases
        assert (c.passes, c.attempts, c.runs) == (17, 20, 2)

    def test_a_different_model_is_a_different_pool(self):
        """9/10 on one model and 2/10 on another is not 11/20 of anything."""
        pools = pool([run(case(passes=9, attempts=10)),
                      run(case(passes=2, attempts=10), model="other")])
        assert len(pools) == 2
        assert [p.cases[0].tally for p in pools] == ["9/10", "2/10"]

    def test_a_different_package_version_is_a_different_pool(self):
        assert len(pool([run(case()), run(case(), suite="pkg 0.2")])) == 2

    def test_runs_that_disagree_outright_are_flagged(self):
        (only,) = pool([run(case(passes=10, attempts=10)),
                        run(case(passes=0, attempts=10))])
        assert only.cases[0].disagree is True

    def test_runs_that_merely_wobble_are_not(self):
        (only,) = pool([run(case(passes=9, attempts=10, rate=0.7)),
                        run(case(passes=6, attempts=10, rate=0.7))])
        assert only.cases[0].disagree is False

    def test_a_roster_only_case_is_named_not_pooled(self):
        (only,) = pool([run(case(id="r", ran_model=False), case(id="t"))])
        assert only.roster_only == ["r"]
        assert [c.id for c in only.cases] == ["t"]

    def test_the_newest_claim_is_the_one_held_and_a_change_is_said(self):
        (only,) = pool([run(case(rate=0.9), at="2026-09-02T00:00:00Z"),
                        run(case(rate=0.5), at="2026-09-01T00:00:00Z")])
        assert only.cases[0].min_pass_rate == 0.9
        assert only.cases[0].claim_changed is True

    @pytest.mark.parametrize("passes,attempts,claim,word", [
        (60, 60, 0.9, "holds"),       # low end 0.94 clears 0.9
        (3, 3, 0.7, "unsettled"),     # 0.44..1.00 spans it
        (2, 20, 0.7, "below"),        # high end 0.30 misses it
        (40, 40, 1.0, "holds"),       # every-time: no run failed
        (39, 40, 1.0, "below"),       # every-time: one failure breaks it
    ])
    def test_standing_against_the_claim(self, passes, attempts, claim, word):
        (only,) = pool([run(case(passes=passes, attempts=attempts,
                                 rate=claim))])
        assert only.cases[0].standing == word


class TestFromTheCommandLine:
    def test_two_reports_compare_without_a_package_or_a_key(self, tmp_path,
                                                            monkeypatch,
                                                            capsys):
        monkeypatch.chdir(tmp_path)               # no agent.toml here
        a = write(tmp_path, "a", run(case(passes=1)))
        b = write(tmp_path, "b", run(case(passes=0)))
        assert main(["--reports", a, b]) == 0
        assert "broke" in capsys.readouterr().out

    def test_three_reports_are_a_table(self, tmp_path, capsys):
        paths = [write(tmp_path, n, run(case())) for n in ("x", "y", "z")]
        assert main(["--reports", *paths]) == 0
        assert "across 3 runs" in capsys.readouterr().out

    def test_a_red_report_does_not_make_reading_it_fail(self, tmp_path):
        """Reading is not judging."""
        path = write(tmp_path, "red", run(case(passes=0)))
        assert main(["--reports", path, "--pool"]) == 0

    def test_one_report_without_pool_is_refused(self, tmp_path, capsys):
        assert main(["--reports", write(tmp_path, "a", run(case()))]) == 2
        assert "--pool" in capsys.readouterr().err

    def test_the_same_file_twice_is_counted_once(self, tmp_path, capsys):
        path = write(tmp_path, "a", run(case(passes=1)))
        assert main(["--reports", path, path, "--pool"]) == 0
        out = " ".join(capsys.readouterr().out.split())   # undo rich's wrap
        assert "named twice" in out and "1/1 over 1 run(s)" in out

    def test_an_unreadable_report_is_an_error(self, tmp_path, capsys):
        good = write(tmp_path, "a", run(case()))
        assert main(["--reports", good, str(tmp_path / "missing.json")]) == 2

    def test_pool_without_reports_is_refused(self, capsys):
        assert main(["--pool"]) == 2
        assert "--reports" in capsys.readouterr().err

    def test_reports_with_eval_is_refused(self, tmp_path, capsys):
        path = write(tmp_path, "a", run(case()))
        assert main(["--reports", path, "--eval"]) == 2
        assert "--against" in capsys.readouterr().err

    def test_a_moved_price_is_named_in_the_comparison(self, tmp_path, capsys):
        a = write(tmp_path, "a", run(case(usd=0.02),
                                     pricing=PriceRecord(TABLE, 3.0, 15.0)))
        b = write(tmp_path, "b", run(case(usd=0.01),
                                     pricing=PriceRecord("YANTRA_PRICES",
                                                         1.5, 7.5)))
        assert main(["--reports", a, b]) == 0
        assert "part of the cost change is the price" in capsys.readouterr().out

    def test_the_same_price_says_the_change_is_the_agents(self, tmp_path,
                                                          capsys):
        rates = PriceRecord(TABLE, 3.0, 15.0)
        a = write(tmp_path, "a", run(case(usd=0.02), pricing=rates))
        b = write(tmp_path, "b", run(case(usd=0.01), pricing=rates))
        assert main(["--reports", a, b]) == 0
        assert "the cost change is the agent's" in capsys.readouterr().out
