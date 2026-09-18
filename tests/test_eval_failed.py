"""Re-running only what was red, and the four ways that could lie.

The bias here is that selection driven by a FILE is selection the
operator cannot see. Every test below is aimed at one way a smaller run
could be mistaken for the suite:

* **A silently empty run.** A report whose red cases have all been
  renamed selects nothing, and a run of nothing passes everything. That
  is an error with a sentence, not a green line.
* **A case that left the suite.** The report names ids; the suite owns
  cases; an id in one and not the other is reported rather than
  reconciled, because a rename is the commonest reason and the operator
  is the only one who can tell a rename from a deletion.
* **A subset reported as a gate.** A ``--failed`` run is a subset like
  any other, so the header says what restricted it, the verdict word is
  SUBSET, and the report it writes records the restriction.
* **A green report costing a run.** Nothing to re-run is exit 0 and no
  provider at all -- asserted against an empty script, so any model call
  would raise.
"""

from __future__ import annotations

from conftest import ScriptedProvider, assistant_text
from yantra.eval_report import CaseRecord, SuiteRun, read_report, write_report
from yantra.eval_suite import CASES, SUITE_DIR
from yantra.package import MANIFEST


def case(id="x", passed=True):
    return CaseRecord(id=id, passed=passed, attempts=1, passes=1 if passed
                      else 0, min_pass_rate=1.0, tokens=100, seconds=1.0,
                      ran_model=True, failures=[] if passed else ["nope"])


def report(path, *cases):
    write_report(path, SuiteRun(suite="pkg", provider="ollama", model="m",
                                at="2026-01-01T00:00:00Z", repeat=1,
                                cases=list(cases), cases_in_suite=len(cases)))
    return path


TWO_CASES = ('[[case]]\nid = "reads"\nuser_message = "hi"\n'
             '[[case]]\nid = "writes"\nuser_message = "hi"\n')


def _suite(tmp_path, cases_text: str):
    root = tmp_path / "pkg"
    (root / SUITE_DIR).mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text('[agent]\nname = "pkg"\n')
    (root / SUITE_DIR / CASES).write_text(cases_text)
    return root


class TestTheCasesThatWereRed:
    def _run(self, monkeypatch, argv, script=None):
        import yantra.cli.main as cli_main
        provider = ScriptedProvider(script if script is not None
                                    else [assistant_text("done")] * 8)
        self.provider = provider
        monkeypatch.setattr(cli_main, "guess_provider", lambda: "anthropic")
        monkeypatch.setattr(cli_main, "load_settings", lambda name: object())
        monkeypatch.setattr(cli_main, "get_provider", lambda *a, **k: provider)
        return cli_main.main(argv)

    def test_only_the_red_case_runs(self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, TWO_CASES)
        prior = report(tmp_path / "r.json", case("reads"),
                       case("writes", passed=False))
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--failed", str(prior)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "1 case(s)" in out
        assert "writes" in out and "reads" not in out.split("cwd")[0]

    def test_a_green_report_costs_nothing_to_discover(self, tmp_path,
                                                      monkeypatch, capsys):
        """No provider, no request, exit 0 -- asserted against an empty
        script, so a single model call would raise."""
        root = _suite(tmp_path, TWO_CASES)
        prior = report(tmp_path / "r.json", case("reads"), case("writes"))
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--failed", str(prior)], script=[])
        assert rc == 0
        assert self.provider.requests == []
        assert "nothing to re-run" in capsys.readouterr().out

    def test_a_case_that_left_the_suite_is_named_and_the_rest_still_run(
            self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, TWO_CASES)
        prior = report(tmp_path / "r.json", case("writes", passed=False),
                       case("renamed-since", passed=False))
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--failed", str(prior)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "not in this suite any more" in out
        assert "renamed-since" in out
        assert "1 case(s)" in out

    def test_a_report_whose_red_cases_all_vanished_is_an_error(
            self, tmp_path, monkeypatch, capsys):
        """A run of nothing passes everything, which is the one verdict
        this may never print."""
        root = _suite(tmp_path, TWO_CASES)
        prior = report(tmp_path / "r.json", case("gone-a", passed=False),
                       case("gone-b", passed=False))
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--failed", str(prior)], script=[])
        assert rc == 2
        assert self.provider.requests == []
        assert "nothing to re-run" in capsys.readouterr().err

    def test_the_run_reports_as_a_subset_and_says_what_restricted_it(
            self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, TWO_CASES)
        prior = report(tmp_path / "r.json", case("writes", passed=False))
        out_path = tmp_path / "again.json"
        self._run(monkeypatch, ["--agent", str(root), "--eval", "--failed",
                                str(prior), "--report", str(out_path)])
        out = capsys.readouterr().out
        assert "SUBSET GREEN" in out and "SUITE GREEN" not in out
        assert "filtered:" in out and "red in" in out
        assert read_report(out_path).filtered == [f"red in {prior}"]

    def test_no_file_means_the_one_against_names(self, tmp_path, monkeypatch,
                                                 capsys):
        """The two flags are the same file read at both ends of a run."""
        root = _suite(tmp_path, TWO_CASES)
        prior = report(tmp_path / "r.json", case("reads"),
                       case("writes", passed=False))
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--failed", "--against", str(prior)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "1 case(s)" in out
        assert "against" in out              # the comparison still happened

    def test_no_file_and_no_against_is_an_error_not_a_whole_suite(
            self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, TWO_CASES)
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--failed"], script=[])
        assert rc == 2
        assert self.provider.requests == []
        assert "--against" in capsys.readouterr().err

    def test_an_unreadable_report_is_an_error_before_anything_runs(
            self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, TWO_CASES)
        bad = tmp_path / "r.json"
        bad.write_text("{not json")
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--failed", str(bad)], script=[])
        assert rc == 2
        assert self.provider.requests == []
        assert "not a readable" in capsys.readouterr().err

    def test_a_pattern_narrows_the_red_cases_further(self, tmp_path,
                                                     monkeypatch, capsys):
        root = _suite(tmp_path, TWO_CASES)
        prior = report(tmp_path / "r.json", case("reads", passed=False),
                       case("writes", passed=False))
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval",
                                     "--failed", str(prior), "--case", "writ*"])
        out = capsys.readouterr().out
        assert rc == 0 and "1 case(s)" in out

    def test_a_pattern_matching_no_red_case_says_which_were_red(
            self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, TWO_CASES)
        prior = report(tmp_path / "r.json", case("writes", passed=False))
        rc = self._run(monkeypatch, ["--agent", str(root), "--eval", "--failed",
                                     str(prior), "--case", "reads"],
                       script=[])
        err = capsys.readouterr().err
        assert rc == 2
        assert "were red are: writes" in err

    def test_failed_belongs_to_eval(self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, TWO_CASES)
        rc = self._run(monkeypatch, ["--agent", str(root), "--prompt", "hi",
                                     "--failed", str(tmp_path / "r.json")],
                       script=[])
        assert rc == 2
        assert "--failed" in capsys.readouterr().err
