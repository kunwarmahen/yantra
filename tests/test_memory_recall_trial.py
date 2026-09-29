"""The two-session memory trial (examples/memory_recall_trial.py), scripted.

The trial's numbers are only worth what its grading and its plumbing are
worth, and both are easy to get quietly wrong. The bias these tests hold
against: a trial that reports "answered from memory" for an answer that
reached the fact some other way -- a lenient pattern, a second identity
that could see the first one's store, a session 2 that still carried
session 1's history.
"""

from __future__ import annotations

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


def _trial(tmp_path, script, distractors=0):
    args = Namespace(provider="anthropic", model="m", distractors=distractors,
                     no_baseline=False)
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
