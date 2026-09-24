"""Listing a recording so a person can choose what becomes a case.

The bias here is a tool that decides for the person. Nothing in Yantra
knows whether a turn that ended cleanly gave a wrong answer (notes/57),
so the listing must not pretend to. The tests pin:

* every turn is listed; ``--turns failed`` keeps the flagged ones;
* flagged means the cheap filter (ended badly, a tool or child failed)
  OR a grader that said no -- the one verdict a person wrote;
* each flag says WHY, so the reader does not have to open the JSONL;
* a suite's line carries its grader's verdict as a boolean, never the
  grader's words (which may quote the agent);
* the footer says an unflagged turn can still be wrong.
"""

from __future__ import annotations

import json

from yantra.cli.main import main
from yantra.trace import ChildRun, ToolStep, Trajectory, TrajectoryLog


def turn(tid, *, steps=(), outcome="end_turn", passed=None, case=None,
         children=()):
    return Trajectory(id=tid + "0" * 24, at="2026-09-24T12:00:00Z",
                      provider="ollama", model="qwen3.8:latest",
                      detail="shape", task=f"task {tid}", steps=list(steps),
                      outcome=outcome, passed=passed, case=case,
                      children=list(children))


def listing(tmp_path, capsys, *turns, which=None):
    path = tmp_path / "t.jsonl"
    log = TrajectoryLog(path)
    for t in turns:
        log.record(t)
    argv = ["--turns"] + ([which] if which else []) + ["--trace", str(path)]
    assert main(argv) == 0
    return capsys.readouterr().out


class TestWhatIsListed:
    def test_every_turn_by_default(self, tmp_path, capsys):
        out = listing(tmp_path, capsys, turn("aaaaaaaa"), turn("bbbbbbbb"))
        assert "aaaaaaaa" in out and "bbbbbbbb" in out
        assert "2 turn(s), 0 flagged" in out

    def test_failed_keeps_only_the_flagged(self, tmp_path, capsys):
        out = listing(tmp_path, capsys, turn("aaaaaaaa"),
                      turn("bbbbbbbb", outcome="max_iterations"),
                      which="failed")
        assert "aaaaaaaa" not in out.split("turn(s)")[0]
        assert "bbbbbbbb" in out and "ended max_iterations" in out


class TestWhyAFlag:
    def test_a_tool_that_errored(self, tmp_path, capsys):
        out = listing(tmp_path, capsys, turn(
            "aaaaaaaa", steps=[ToolStep("read_file", ok=False)]))
        assert "read_file (failed)" in out

    def test_a_refusal_names_its_code(self, tmp_path, capsys):
        out = listing(tmp_path, capsys, turn("aaaaaaaa", steps=[
            ToolStep("write_file", ok=False, refusal="policy")]))
        assert "write_file (refused: policy)" in out

    def test_a_child_that_failed(self, tmp_path, capsys):
        child = ChildRun(number=1, agent="fact_checker", model="m",
                         code="max_iterations")
        out = listing(tmp_path, capsys, turn("aaaaaaaa", children=[child]))
        assert "sub-agent #1 fact_checker: max_iterations" in out

    def test_a_clean_turn_its_grader_failed_is_flagged(self, tmp_path, capsys):
        """The case the cheap filter misses: ended fine, used no failing
        tool, and was wrong -- by the standard of a case a person wrote."""
        out = listing(tmp_path, capsys,
                      turn("aaaaaaaa", passed=False, case="x"), which="failed")
        assert "aaaaaaaa" in out and "red in its case" in out

    def test_every_reason_is_given_not_only_the_first(self, tmp_path, capsys):
        """The grader's no and a tool that failed are two different leads;
        the second one is where somebody starts reading."""
        out = listing(tmp_path, capsys, turn(
            "aaaaaaaa", passed=False, steps=[ToolStep("read_file", ok=False)]))
        assert "the grader said no; read_file (failed)" in out

    def test_the_footer_says_unflagged_is_not_right(self, tmp_path, capsys):
        out = " ".join(listing(tmp_path, capsys, turn("aaaaaaaa")).split())
        assert "an unflagged turn can still be wrong" in out
        assert "--fossil ID" in out


class TestTheRecordedVerdict:
    def test_it_round_trips_as_a_boolean(self, tmp_path):
        path = tmp_path / "t.jsonl"
        TrajectoryLog(path).record(turn("aaaaaaaa", passed=False, case="x"))
        assert json.loads(path.read_text())["passed"] is False
        (back,) = TrajectoryLog(path).read()
        assert back.passed is False

    def test_a_typed_turn_carries_no_verdict(self, tmp_path):
        path = tmp_path / "t.jsonl"
        TrajectoryLog(path).record(turn("aaaaaaaa"))
        assert "passed" not in json.loads(path.read_text())

    def test_a_suite_writes_what_its_grader_said(self, tmp_path, monkeypatch):
        from test_eval_cost import TestThroughTheGate
        gate = TestThroughTheGate()
        root = gate._suite_dir(tmp_path)
        (root / "evals" / "cases.toml").write_text(
            '[[case]]\nid = "x"\nuser_message = "hi"\n'
            'required_tools = ["read_file"]\n')
        trace = tmp_path / "t.jsonl"
        gate._run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                                "ollama", "--model", "m", "--trace",
                                str(trace)])
        (recorded,) = TrajectoryLog(trace).read()
        assert recorded.case == "x" and recorded.passed is False


class TestTheFlag:
    def test_it_needs_the_file(self, capsys):
        assert main(["--turns"]) == 2
        assert "--trace FILE" in capsys.readouterr().err

    def test_it_is_its_own_mode(self, tmp_path, capsys):
        assert main(["--turns", "--trace", str(tmp_path / "t.jsonl"),
                     "--eval"]) == 2


class TestAPersonsMark:
    """notes/74: Yantra does not decide; a person can write it down."""

    def recording(self, tmp_path, *turns):
        path = tmp_path / "t.jsonl"
        log = TrajectoryLog(path)
        for t in turns:
            log.record(t)
        return path

    def test_a_bad_mark_flags_a_clean_turn_with_the_persons_words(
            self, tmp_path, capsys):
        path = self.recording(tmp_path, turn("aaaaaaaa"))
        assert main(["--mark", "aaaa", "bad", "--why", "wrong file",
                     "--trace", str(path)]) == 0
        main(["--turns", "failed", "--trace", str(path)])
        out = capsys.readouterr().out
        assert "marked bad by a person: wrong file" in out

    def test_a_good_mark_outranks_the_cheap_filter_and_the_grader(
            self, tmp_path, capsys):
        path = self.recording(tmp_path, turn(
            "aaaaaaaa", passed=False, case="x",
            steps=[ToolStep("read_file", ok=False)]))
        main(["--mark", "aaaa", "good", "--trace", str(path)])
        main(["--turns", "failed", "--trace", str(path)])
        assert "1 turn(s), 0 flagged" in " ".join(
            capsys.readouterr().out.split())

    def test_the_line_is_rewritten_not_appended(self, tmp_path):
        path = self.recording(tmp_path, turn("aaaaaaaa"), turn("bbbbbbbb"))
        TrajectoryLog(path).mark("bbbb", passed=False, why="no")
        lines = path.read_text().splitlines()
        assert len(lines) == 2
        raw = json.loads(lines[1])
        assert (raw["passed"], raw["judged_by"], raw["why"]) == \
            (False, "person", "no")
        log = TrajectoryLog(path)
        assert len(log.read()) == 2 and log.unreadable == 0

    def test_fossil_takes_the_persons_reason(self, tmp_path, capsys):
        path = self.recording(tmp_path, turn("aaaaaaaa"))
        main(["--mark", "aaaa", "bad", "--why", "cited nothing",
              "--trace", str(path)])
        capsys.readouterr()
        main(["--fossil", "aaaa", "--trace", str(path)])
        assert 'description = "marked bad: cited nothing' in \
            capsys.readouterr().out

    def test_a_suite_turns_verdict_says_it_was_the_grader(self, tmp_path):
        from yantra.trace import from_history

        class Nothing:
            history = []
        recorded = from_history(Nothing(), "t", case="x", passed=True)
        assert recorded.judged_by == "grader"

    def test_the_verdict_must_be_good_or_bad(self, tmp_path, capsys):
        path = self.recording(tmp_path, turn("aaaaaaaa"))
        assert main(["--mark", "aaaa", "meh", "--trace", str(path)]) == 2

    def test_why_needs_a_mark(self, capsys):
        assert main(["--why", "x"]) == 2


class TestTakingAMarkBack:
    """notes/77: a mark can be cleared, and clearing gives back exactly
    what was there before -- including a grader's verdict it replaced."""

    def recording(self, tmp_path, *turns):
        path = tmp_path / "t.jsonl"
        log = TrajectoryLog(path)
        for t in turns:
            log.record(t)
        return path

    def test_clearing_a_mark_on_a_typed_turn_leaves_no_verdict(
            self, tmp_path):
        path = self.recording(tmp_path, turn("aaaaaaaa"))
        log = TrajectoryLog(path)
        log.mark("aaaa", passed=False, why="wrong")
        log.mark("aaaa", passed=None)
        raw = json.loads(path.read_text())
        assert not {"passed", "judged_by", "why", "graded"} & raw.keys()

    def test_clearing_gives_the_graders_verdict_back(self, tmp_path):
        path = self.recording(tmp_path, turn("aaaaaaaa", passed=False,
                                             case="x"))
        log = TrajectoryLog(path)
        log.mark("aaaa", passed=True)
        log.mark("aaaa", passed=False)     # a second mark keeps the first's
        cleared = log.mark("aaaa", passed=None)
        assert (cleared.passed, cleared.judged_by) == (False, "grader")
        assert "graded" not in json.loads(path.read_text())

    def test_a_line_from_before_judged_by_counts_its_verdict_as_the_graders(
            self, tmp_path):
        path = tmp_path / "t.jsonl"
        TrajectoryLog(path).record(turn("aaaaaaaa", passed=True, case="x"))
        raw = json.loads(path.read_text())
        raw.pop("judged_by", None)          # as note 70 wrote it
        path.write_text(json.dumps(raw) + "\n")
        log = TrajectoryLog(path)
        log.mark("aaaa", passed=False)
        assert log.mark("aaaa", passed=None).passed is True

    def test_the_command_says_what_is_left(self, tmp_path, capsys):
        path = self.recording(tmp_path, turn("aaaaaaaa", passed=False,
                                             case="x"))
        main(["--mark", "aaaa", "good", "--trace", str(path)])
        assert main(["--mark", "aaaa", "clear", "--trace", str(path)]) == 0
        out = " ".join(capsys.readouterr().out.split())
        assert "mark cleared, back to its grader's fail" in out
        main(["--turns", "failed", "--trace", str(path)])
        assert "red in its case" in capsys.readouterr().out

    def test_clear_takes_no_reason(self, tmp_path, capsys):
        path = self.recording(tmp_path, turn("aaaaaaaa"))
        assert main(["--mark", "aaaa", "clear", "--why", "x",
                     "--trace", str(path)]) == 2
