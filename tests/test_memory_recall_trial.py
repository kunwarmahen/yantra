"""The two-session memory trial (examples/memory_recall_trial.py), scripted.

The trial's numbers are only worth what its grading and its plumbing are
worth, and both are easy to get quietly wrong. The bias these tests hold
against: a trial that reports "answered from memory" for an answer that
reached the fact some other way -- a lenient pattern, a second identity
that could see the first one's store, a session 2 that still carried
session 1's history. And for repeats: a rate that claims more than its
runs can say, repeats that share an identity (so run 2 finds run 1's
fact), or a pile of saved runs from different models graded as one.
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

from conftest import ScriptedProvider, assistant_text
from yantra.memory.local import LocalStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
import memory_recall_trial as trial  # noqa: E402

AIRPORT = {"id": "airport", "said": "Tips for flights from RDU to Denver?",
           "fact": "RDU", "later": "Find me flights to Austin.",
           "expect": "RDU"}
PACKAGES = {"id": "packages", "expect": "uv add", "rival": "pip install"}


def _trial(tmp_path, script, distractors=0, repeat=1):
    args = Namespace(provider="anthropic", model="m", store="local",
                     distractors=distractors, no_baseline=False, repeat=repeat)
    return trial.Trial(args, ScriptedProvider(script),
                       LocalStore(tmp_path / "memory.sqlite"))


class TestGrading:
    def test_the_remembered_answer_must_come_first(self):
        assert trial.grade(PACKAGES, "Run `uv add requests`.")
        assert not trial.grade(PACKAGES, "pip install requests -- or uv add requests")

    def test_an_answer_that_names_the_forbidden_thing_fails(self):
        case = {"expect": "vegetarian", "avoid": r"\bchicken\b"}
        assert not trial.grade(case, "A vegetarian twist on chicken curry")

    def test_a_missing_fact_fails(self):
        assert not trial.grade(AIRPORT, "Where are you flying from?")


class TestTwoSessions:
    def test_a_fact_said_in_passing_reaches_a_fresh_session(self, tmp_path):
        run = _trial(tmp_path, [
            assistant_text("Book early; RDU has good Denver fares."),  # session 1
            assistant_text("fact: Lives near RDU."),                   # look back
            assistant_text("From RDU, as before: AUS nonstops daily."),  # session 2
            assistant_text("Where are you flying from?"),              # other person
        ])
        row = run.run_case(AIRPORT, "t")
        assert row["kept"] == ["Lives near RDU."]
        assert row["in_prompt"] and row["passed"]
        assert row["isolated"] and row["baseline_passed"] is False
        assert row["recall_called"] is False
        # word overlap never finds it: it rides in on the recent top-up
        assert row["rank"] is None and row["stored"] == 1

    def test_buried_under_newer_memories_word_overlap_loses_it(self, tmp_path):
        run = _trial(tmp_path, [
            assistant_text("Sure."), assistant_text("fact: Lives near RDU."),
            assistant_text("Where from?"), assistant_text("Where from?"),
        ], distractors=25)
        row = run.run_case(AIRPORT, "t")
        assert row["kept_fact"] is True
        assert row["in_prompt"] is False     # oldest of 26, no word in common
        assert row["rank"] is None           # and search never offered it

    def test_something_true_for_a_week_is_a_false_keep(self, tmp_path):
        run = _trial(tmp_path, [assistant_text("Take BART."),
                                assistant_text("fact: Flying to SFO next Tuesday.")])
        row = run.run_case({"id": "trip", "said": "Flying to SFO Tuesday.",
                            "fact": "", "not_kept": "SFO", "later": "",
                            "expect": ""}, "t")
        assert row["false_keep"] is True
        assert "passed" not in row

    def test_a_store_that_is_down_still_gets_an_answer(self, tmp_path):
        run = _trial(tmp_path, [assistant_text("Where are you flying from?")])
        down = run.store_down(AIRPORT)
        assert down["answered"] and "store is down" in down["notice"]


class TestRepeats:
    def test_five_of_five_is_not_a_hundred_percent(self):
        lo, hi = trial.wilson(5, 5)
        assert 0.55 < lo < 0.58 and hi == 1.0
        assert trial.rate(5, 5, True) == "5/5 (57-100%)"
        assert trial.rate(5, 5, False) == "5/5"

    def test_each_repeat_is_a_different_person(self, tmp_path):
        """Run 2 must not pass by finding run 1's fact in the shared store."""
        run = _trial(tmp_path, [
            assistant_text("Sure."), assistant_text("fact: Lives near RDU."),
            assistant_text("From RDU: nonstops daily."),
            assistant_text("Where from?"),
            assistant_text("Sure."), assistant_text("nothing"),
            assistant_text("Where from?"), assistant_text("Where from?"),
        ], repeat=2)
        one, two = run.run_case(AIRPORT, "t", 1), run.run_case(AIRPORT, "t", 2)
        assert one["user"] != two["user"]
        assert one["passed"] and not two["passed"]
        assert two["prompt"] == []
        assert (one["rep"], two["rep"], one["model"]) == (1, 2, "m")

    def test_a_pile_of_runs_is_graded_per_setting(self, tmp_path, capsys):
        def row(model, passed, **extra):
            return {"id": "airport", "rep": 1, "kept": ["Lives near RDU"],
                    "later_answer": "From RDU" if passed else "Where from?",
                    "prompt": ["Lives near RDU"], "recall_called": False,
                    "remember_called": False, **extra,
                    **({"model": model, "provider": "ollama", "store": "local",
                        "distractors": 0} if model else {})}
        cases = tmp_path / "cases.jsonl"
        cases.write_text(json.dumps(AIRPORT) + "\n")
        a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
        a.write_text("".join(json.dumps(row("qwen", p)) + "\n"
                             for p in (True, True, False)))
        # saved before rows carried a setting: the command line names it
        b.write_text(json.dumps(row(None, True)) + "\n")
        args = Namespace(rescore=[a, b], cases=cases, provider="ollama",
                         model="gemma", store="local", distractors=0)
        assert trial.rescore(args) == 0
        out = capsys.readouterr().out
        assert "| | qwen, local | gemma, local |" in out
        assert "| answered from memory | 2/3 (21-94%) | 1/1 (21-100%) |" in out


class TestSavedAsItGoes:
    """A long run that dies -- the store's server stopped forty minutes in
    -- must keep the rows it already graded. The BIAS is against a trial
    that writes --out only at the end and loses everything to one crash."""

    def test_rows_graded_before_a_crash_are_on_disk(self, tmp_path, monkeypatch):
        import pytest

        cases = tmp_path / "cases.jsonl"
        second = {**AIRPORT, "id": "airport2"}
        cases.write_text(json.dumps(AIRPORT) + "\n" + json.dumps(second) + "\n")
        out = tmp_path / "rows.jsonl"
        provider = ScriptedProvider([
            assistant_text("RDU fares are good."), assistant_text("fact: Lives near RDU."),
            assistant_text("From RDU: nonstops daily."), assistant_text("Where from?"),
        ])
        real = trial.Trial.run_case

        def dies_second(self, case, *a, **k):
            if case["id"] == "airport2":   # what a stopped store's server did
                raise ConnectionError("All connection attempts failed")
            return real(self, case, *a, **k)

        monkeypatch.setattr(trial.Trial, "run_case", dies_second)
        monkeypatch.setattr(trial, "get_provider", lambda *a, **k: provider)
        monkeypatch.setattr(trial, "load_settings", lambda *a, **k: None)
        monkeypatch.setattr(sys, "argv", ["trial", "--cases", str(cases),
                                          "--out", str(out)])
        with pytest.raises(ConnectionError):
            trial.main()
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        assert [r["id"] for r in rows] == ["airport"] and rows[0]["passed"]
