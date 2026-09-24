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
