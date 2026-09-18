"""Two runs of the same suite, and what a comparison may not quietly do.

The bias here is that a comparison is the easiest place in this repo to
build a lie that reads as reassurance. Three specific ones:

* **Intersecting the two runs.** A case that exists in one report and not
  the other is the single most important thing a comparison can say --
  somebody deleted a case, or a filter cut it out -- and taking the
  intersection is exactly the operation that hides it. So several tests
  below assert that a missing case SURVIVES into the output.
* **Turning counts into percentages.** "7 of 10" and "1 of 1" are not 70%
  against 100%; one is ten samples and one is a die roll. The tests read
  the tallies and the warning about differing sample sizes.
* **A baseline that could not be read being a shrug.** A comparison the
  operator asked for and did not get is the silent pass the whole eval
  format exists to prevent, so every unreadable report is an error with a
  sentence -- and it is discovered BEFORE the suite spends anything,
  which is asserted against an empty script.
"""

from __future__ import annotations

import json

import pytest

from conftest import ScriptedProvider, assistant_text
from yantra.errors import ConfigError
from yantra.eval_report import (
    FORMAT,
    CaseRecord,
    SuiteRun,
    compare,
    line_up,
    read_report,
    record_run,
    write_report,
)
from yantra.eval_suite import CASES, SUITE_DIR
from yantra.package import MANIFEST


# ---- fixtures ---------------------------------------------------------------


def case(id="x", passed=True, attempts=1, passes=1, tokens=100,
         rate=1.0, ran_model=True, failures=()):
    return CaseRecord(id=id, passed=passed, attempts=attempts, passes=passes,
                      min_pass_rate=rate, tokens=tokens, seconds=1.0,
                      ran_model=ran_model, failures=list(failures))


def run(*cases, model="m", provider="ollama", repeat=1, filtered=None,
        in_suite=None):
    return SuiteRun(suite="pkg 1.0", provider=provider, model=model,
                    at="2026-01-01T00:00:00Z", repeat=repeat,
                    cases=list(cases),
                    cases_in_suite=in_suite if in_suite is not None
                    else len(cases),
                    filtered=filtered)


def _suite(tmp_path, cases_text: str, *, package: str | None = None):
    root = tmp_path / "pkg"
    (root / SUITE_DIR).mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(package or '[agent]\nname = "pkg"\n')
    (root / SUITE_DIR / CASES).write_text(cases_text)
    return root


# ---- the file ---------------------------------------------------------------


class TestTheReportFile:
    def test_a_report_round_trips(self, tmp_path):
        original = run(case("a"), case("b", passed=False, passes=0,
                                       failures=["nope"]))
        path = tmp_path / "deep" / "r.json"
        write_report(path, original)          # creates the directory
        back = read_report(path)
        assert back.suite == original.suite
        assert [c.id for c in back.cases] == ["a", "b"]
        assert back.cases[1].failures == ["nope"]
        assert back.passed == 1 and back.tokens == 200

    def test_a_missing_report_says_how_to_make_one(self, tmp_path):
        with pytest.raises(ConfigError, match="--report"):
            read_report(tmp_path / "never-written.json")

    def test_an_unreadable_file_is_an_error_not_an_empty_baseline(self,
                                                                  tmp_path):
        path = tmp_path / "r.json"
        path.write_text("{not json")
        with pytest.raises(ConfigError, match="not a readable"):
            read_report(path)

    def test_a_format_this_build_does_not_know_is_refused_by_name(self,
                                                                  tmp_path):
        """Written by one version, read by another, months later, by a CI
        job nobody has looked at since."""
        path = tmp_path / "r.json"
        path.write_text(json.dumps({"format": "yantra.eval.v9", "cases": []}))
        with pytest.raises(ConfigError, match="yantra.eval.v9"):
            read_report(path)
        assert FORMAT in read_error(path)

    def test_a_truncated_report_says_what_is_missing(self, tmp_path):
        path = tmp_path / "r.json"
        path.write_text(json.dumps({"format": FORMAT, "suite": "x"}))
        with pytest.raises(ConfigError, match="missing part"):
            read_report(path)

    def test_the_record_reads_only_public_outcome_properties(self):
        """record_run takes CaseOutcomes; an outcome type that grows a
        field must not have to grow one here."""
        from yantra.evals import CaseOutcome, EvalResult

        outcome = CaseOutcome(
            case_id="a", min_pass_rate=0.5,
            runs=[EvalResult("a", True, [], "hi", 10, 1, [], 0.5),
                  EvalResult("a", False, ["no"], "", 5, 1, [], 0.25)])
        recorded = record_run([outcome], suite="pkg", provider="ollama",
                              model="m", repeat=2, cases_in_suite=3)
        (only,) = recorded.cases
        assert only.tally == "1/2" and only.passed is True
        assert only.tokens == 15
        assert recorded.cases_in_suite == 3


def read_error(path) -> str:
    try:
        read_report(path)
    except ConfigError as exc:
        return str(exc)
    raise AssertionError("expected an error")


# ---- the comparison ---------------------------------------------------------


class TestWhatMoved:
    def test_the_four_verdict_words(self):
        before = run(case("stays"), case("breaks"),
                     case("fixes", passed=False, passes=0), case("vanishes"))
        after = run(case("stays"), case("breaks", passed=False, passes=0),
                    case("fixes"), case("appears"))
        kinds = {d.id: d.kind for d in compare(before, after).deltas}
        assert kinds == {"stays": "same", "breaks": "broke",
                         "fixes": "fixed", "vanishes": "gone",
                         "appears": "added"}

    def test_a_case_that_disappeared_survives_the_comparison(self):
        """The one thing an intersection would hide, and the reason it is
        not an intersection."""
        cmp = compare(run(case("a"), case("deleted")), run(case("a")))
        gone = [d for d in cmp.deltas if d.kind == "gone"]
        assert [d.id for d in gone] == ["deleted"]
        assert gone[0].after is None

    def test_the_order_is_the_one_just_watched_go_past(self):
        before = run(case("a"), case("b"))
        after = run(case("b"), case("a"))
        assert [d.id for d in compare(before, after).deltas] == ["b", "a"]

    def test_a_pass_count_that_moved_without_the_verdict_is_reported(self):
        """9/10 to 6/10 is still green and is the most useful line on the
        page -- a comparison that only printed verdicts would lose it."""
        before = run(case("a", attempts=10, passes=9, rate=0.5))
        after = run(case("a", attempts=10, passes=6, rate=0.5))
        (delta,) = compare(before, after).deltas
        assert delta.kind == "same"
        assert delta.rate_moved is True
        assert delta.rate_direction == "down"

    def test_an_unchanged_case_is_not_reported_as_movement(self):
        (delta,) = compare(run(case("a")), run(case("a"))).deltas
        assert delta.kind == "same" and delta.rate_moved is False

    def test_rates_are_compared_as_fractions_not_counts(self):
        """2/3 against 7/10 is a genuine comparison of one claim at two
        sample sizes; 7 against 2 is not."""
        before = run(case("a", attempts=3, passes=2, rate=0.5), repeat=3)
        after = run(case("a", attempts=10, passes=7, rate=0.5), repeat=10)
        (delta,) = compare(before, after).deltas
        assert delta.rate_direction == "up"      # 0.70 > 0.67

    def test_two_runs_over_different_cases_are_not_comparable(self):
        assert compare(run(case("a")), run(case("b"))).comparable is False

    def test_two_runs_over_the_same_cases_are(self):
        assert compare(run(case("a")), run(case("a"))).comparable is True

    def test_a_subset_against_a_suite_is_not_comparable(self):
        before = run(case("a"), case("b"))
        after = run(case("a"), filtered=["a"], in_suite=2)
        assert compare(before, after).comparable is False

    def test_token_movement_is_the_difference_not_a_ratio(self):
        cmp = compare(run(case("a", tokens=1000)), run(case("a", tokens=400)))
        assert cmp.tokens_moved == -600


# ---- three or more ----------------------------------------------------------


class TestLiningRunsUp:
    """A table, not a stack of differences.

    Same bias as the pair: nothing may be intersected away, and a run that
    did not grade a case must leave a visible hole rather than a silent
    one.
    """

    def test_the_last_run_sets_the_row_order(self):
        table = line_up([run(case("b"), case("a")), run(case("a"), case("b"))])
        assert table.ids == ["a", "b"]

    def test_a_case_only_an_earlier_run_had_is_kept(self):
        table = line_up([run(case("a"), case("dropped")), run(case("a"))])
        assert table.ids == ["a", "dropped"]
        assert table.cell("dropped", 1) is None
        assert table.comparable is False

    def test_runs_that_graded_the_same_cases_are_comparable(self):
        assert line_up([run(case("a")), run(case("a")),
                        run(case("a"))]).comparable is True

    def test_every_run_keeps_its_own_model(self):
        table = line_up([run(case("a"), model="qwen3.8:27b"),
                         run(case("a"), model="gemma4:12b"),
                         run(case("a"), model="qwen3.8:latest")])
        assert table.models == ["ollama/qwen3.8:27b", "ollama/gemma4:12b",
                                "ollama/qwen3.8:latest"]

    def test_a_row_reads_across_in_column_order(self):
        table = line_up([run(case("a")), run(case("a", passed=False)),
                         run(case("a"))])
        (_, row), = table.rows
        assert [c.passed for c in row] == [True, False, True]


# ---- through the CLI --------------------------------------------------------


class TestThroughTheGate:
    def _run(self, monkeypatch, argv, script=None):
        import yantra.cli.main as cli_main
        provider = ScriptedProvider(script or [assistant_text("done")])
        self.provider = provider
        monkeypatch.setattr(cli_main, "guess_provider", lambda: "anthropic")
        monkeypatch.setattr(cli_main, "load_settings", lambda name: object())
        monkeypatch.setattr(cli_main, "get_provider", lambda *a, **k: provider)
        return cli_main.main(argv)

    def test_a_report_is_written_for_a_red_run_too(self, tmp_path,
                                                   monkeypatch, capsys):
        """The red one is exactly what somebody compares against
        tomorrow."""
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n'
                                'required_tools = ["read_file"]\n')
        out = tmp_path / "r.json"
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--report", str(out)])
        assert rc == 1
        written = read_report(out)
        assert written.green is False
        assert written.cases[0].failures

    def test_a_broken_baseline_costs_no_tokens_to_discover(self, tmp_path,
                                                           monkeypatch,
                                                           capsys):
        """Read before the suite runs, the same rule graders get: an empty
        script means any model call at all would raise."""
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        rc = self._run(monkeypatch,
                       ["--agent", str(root), "--eval", "--against",
                        str(tmp_path / "nope.json")], script=[])
        assert rc == 2
        assert self.provider.requests == []
        assert "--report" in capsys.readouterr().err

    def test_a_comparison_changes_no_verdict(self, tmp_path, monkeypatch,
                                             capsys):
        """A run that got worse and is still green is still green -- and a
        run that got BETTER and is red is still red."""
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n'
                                'required_tools = ["read_file"]\n')
        baseline = tmp_path / "base.json"
        write_report(baseline, run(case("x", passed=False, passes=0)))
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--against", str(baseline)])
        assert rc == 1                       # this run's own verdict

    def test_the_comparison_names_both_models_rather_than_refusing(
            self, tmp_path, monkeypatch, capsys):
        """The most useful comparison this does is 'the same suite, a
        cheaper model'."""
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        baseline = tmp_path / "base.json"
        write_report(baseline, run(case("x"), provider="ollama",
                                   model="qwen3.8:latest"))
        self._run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                                "anthropic", "--against", str(baseline)])
        out = capsys.readouterr().out
        assert "different model" in out
        assert "ollama/qwen3.8:latest" in out

    def test_differing_sample_sizes_are_called_out(self, tmp_path,
                                                   monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        baseline = tmp_path / "base.json"
        write_report(baseline, run(case("x", attempts=5, passes=5), repeat=5))
        self._run(monkeypatch, ["--agent", str(root), "--eval",
                                "--against", str(baseline)])
        assert "not rates" in capsys.readouterr().out

    def test_a_round_trip_through_the_gate_reports_no_movement(self, tmp_path,
                                                               monkeypatch,
                                                               capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        first = tmp_path / "a.json"
        self._run(monkeypatch, ["--agent", str(root), "--eval",
                                "--report", str(first)])
        capsys.readouterr()
        self._run(monkeypatch, ["--agent", str(root), "--eval",
                                "--against", str(first)])
        assert "no case changed" in capsys.readouterr().out

    def test_three_reports_line_up_as_a_table(self, tmp_path, monkeypatch,
                                              capsys):
        """Two runs are a difference; three are a table, and rendering a
        table as two differences makes the reader do the join."""
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        a, b = tmp_path / "qwen.json", tmp_path / "gemma.json"
        write_report(a, run(case("x"), model="qwen3.8:27b"))
        write_report(b, run(case("x", passed=False), model="gemma4:12b"))
        self._run(monkeypatch, ["--agent", str(root), "--eval", "--against",
                                str(a), "--against", str(b)])
        out = capsys.readouterr().out
        assert "across 3 runs" in out
        assert "qwen" in out and "gemma" in out and "this run" in out
        assert "→" not in out                 # not a pair rendering

    def test_a_pair_is_still_a_difference(self, tmp_path, monkeypatch,
                                          capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        a = tmp_path / "qwen.json"
        write_report(a, run(case("x", passed=False)))
        self._run(monkeypatch, ["--agent", str(root), "--eval", "--against",
                                str(a)])
        out = capsys.readouterr().out
        assert "across" not in out and "fixed" in out

    def test_a_table_says_which_run_did_not_grade_a_case(self, tmp_path,
                                                         monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        a, b = tmp_path / "a.json", tmp_path / "b.json"
        write_report(a, run(case("x"), case("gone-since")))
        write_report(b, run(case("x")))
        self._run(monkeypatch, ["--agent", str(root), "--eval", "--against",
                                str(a), "--against", str(b)])
        out = capsys.readouterr().out
        assert "not every run graded every case" in out
        assert "gone-since" in out and "--" in out

    def test_a_table_changes_no_verdict(self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n'
                                'required_tools = ["read_file"]\n')
        a, b = tmp_path / "a.json", tmp_path / "b.json"
        write_report(a, run(case("x")))
        write_report(b, run(case("x")))
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--against", str(a), "--against", str(b)])
        assert rc == 1                       # this run's own verdict, alone

    def test_an_unreadable_report_among_several_is_still_an_error(
            self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        a, b = tmp_path / "a.json", tmp_path / "b.json"
        write_report(a, run(case("x")))
        b.write_text("{not json")
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--against", str(a), "--against", str(b)],
                       script=[])
        assert rc == 2
        assert self.provider.requests == []

    def test_bare_failed_refuses_to_guess_among_several_reports(
            self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        a, b = tmp_path / "a.json", tmp_path / "b.json"
        write_report(a, run(case("x", passed=False)))
        write_report(b, run(case("x", passed=False)))
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--failed", "--against", str(a),
                                     "--against", str(b)], script=[])
        assert rc == 2
        assert "are 2 of them" in capsys.readouterr().err

    def test_a_case_deleted_between_runs_shows_up_as_gone(self, tmp_path,
                                                          monkeypatch,
                                                          capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        baseline = tmp_path / "base.json"
        write_report(baseline, run(case("x"), case("removed-since")))
        self._run(monkeypatch, ["--agent", str(root), "--eval",
                                "--against", str(baseline)])
        out = capsys.readouterr().out
        assert "gone" in out and "removed-since" in out
        assert "did not grade the same cases" in out

    def test_report_and_against_belong_to_the_gate(self, tmp_path,
                                                   monkeypatch, capsys):
        rc = self._run(monkeypatch, ["--cwd", str(tmp_path), "--report",
                                     "x.json", "--provider", "anthropic",
                                     "hello"])
        assert rc == 2
        assert "belong to --eval" in capsys.readouterr().err
