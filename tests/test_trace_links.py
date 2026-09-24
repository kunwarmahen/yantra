"""A refusal inside a child, a suite's turns, and what a pile of reports cost.

The bias here is the record that has the answer and cannot say it. Four
such records existed:

* **A child refused by the gate looked like a child whose read failed.**
  Both were an error result in the child's history. The tests below make
  the gate turn a child's call away and assert that the code survives
  into the spawner's result, the parent's recorded line, the file, and
  the ``--fossil`` note.
* **A red case in a report had no way back to what the agent did.** The
  suite ran with ``--trace`` and wrote nothing. The tests pin that every
  run that reached a model is recorded under its case id, that the report
  names those turns, and that a run nobody paid for is not recorded --
  and that recording does not change a verdict.
* **The reports held every case's dollars and nothing added them up.**
  Pinned: per-run cost, oldest against newest, and a moved rate named
  rather than blamed on the agent.
* **The pool was printed and then gone.** ``--pool-json`` writes it, in
  a format of its own that ``--against`` will not mistake for a report.
"""

from __future__ import annotations

import asyncio
import json

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from test_evals import EchoTool, case
from test_trace_children import SPAWN
from yantra.agent import Agent
from yantra.cli.main import main
from yantra.eval_report import (POOL_FORMAT, CaseRecord, PriceRecord,
                                SuiteRun, pool, read_report, record_run,
                                write_pool, write_report)
from yantra.evals import AsyncEvalRunner, EvalRunner
from yantra.permissions import REFUSED_POLICY, refuse
from yantra.subagent import SPAWN_TOOL_NAME, SpawnSubagent, SubagentSpawner
from yantra.tools.base import ToolRegistry
from yantra.tools.fs import ReadFile
from yantra.trace import SHAPE, ChildRun, ToolStep, Trajectory, \
    TrajectoryLog, watch
from yantra.types import Usage


def no_reads(request):
    """Lets the spawn through and turns every read_file away, by policy."""
    if request.tool_name == "read_file":
        return refuse(request, "reads are off in this test")
    return True


def refused_child(tmp_path):
    (tmp_path / "notes.txt").write_text("the notes")
    script = [assistant_tool_call("p1", SPAWN_TOOL_NAME, SPAWN),
              assistant_tool_call("c1", "read_file", {"path": "notes.txt"}),
              assistant_text("could not read it"),
              assistant_text("the child could not read it")]
    agent = Agent(ScriptedProvider(script), model="m", permissions=no_reads,
                  cwd=tmp_path)
    agent.registry.register(ReadFile())
    spawner = SubagentSpawner(agent)
    agent.registry.register(SpawnSubagent(spawner))
    agent.subagents = spawner
    return agent, spawner


class TestARefusedChildSaysSo:
    def test_the_spawner_keeps_the_code(self, tmp_path):
        agent, spawner = refused_child(tmp_path)
        agent.run("go")
        assert spawner.results[0].steps == [("read_file", False,
                                             REFUSED_POLICY)]

    def test_a_failed_read_still_has_no_code(self, tmp_path):
        """The two cases the code exists to tell apart."""
        from test_trace_children import delegating_agent
        agent, spawner = delegating_agent(tmp_path, ["gone.txt"])
        agent.run("go")
        assert spawner.results[0].steps == [("read_file", False, None)]

    def test_the_parents_line_carries_it_to_disk_and_back(self, tmp_path):
        agent, spawner = refused_child(tmp_path)
        log = TrajectoryLog(tmp_path / "t.jsonl")
        for _ in watch("go", agent.run_streaming("go"), log.record,
                       spawner=spawner):
            pass
        (back,) = log.read()
        assert back.children[0].steps[0].refusal == REFUSED_POLICY

    def test_turn_refusals_start_empty_each_turn(self, tmp_path):
        agent, _ = refused_child(tmp_path)
        agent.run("go")
        assert agent.turn_refusals == {}   # the PARENT was refused nothing
        child = Agent(ScriptedProvider([
            assistant_tool_call("r1", "read_file", {"path": "notes.txt"}),
            assistant_text("no"), assistant_text("again")]),
            model="m", permissions=no_reads, cwd=tmp_path)
        child.registry.register(ReadFile())
        child.run("first")
        assert child.turn_refusals == {"r1": REFUSED_POLICY}
        child.run("second")
        assert child.turn_refusals == {}

    def test_fossil_names_the_refusal(self, tmp_path, capsys):
        log = TrajectoryLog(tmp_path / "t.jsonl")
        log.record(Trajectory(
            id="abcdef1234", at="2026-09-24T00:00:00Z", provider="ollama",
            model="m", detail=SHAPE, task="t",
            children=[ChildRun(number=1, agent="reader", model="m",
                               steps=[ToolStep("read_file", False,
                                               refusal="policy"),
                                      ToolStep("grep", False)])]))
        assert main(["--fossil", "abcdef", "--trace",
                     str(tmp_path / "t.jsonl")]) == 0
        err = capsys.readouterr().err
        assert "read_file (refused: policy)" in err
        assert "grep (failed)" in err


def runner(script, tmp_path, *, cls=EvalRunner):
    registry = ToolRegistry()
    registry.register(EchoTool())
    log = TrajectoryLog(tmp_path / "suite.jsonl")
    return cls(ScriptedProvider(script), "m", tools=registry,
               permissions=lambda request: True, trace=log), log


class TestASuiteWritesItsTurnsDown:
    def test_each_run_is_recorded_under_its_case(self, tmp_path):
        run, log = runner([assistant_tool_call("t1", "echo", {"text": "hi"}),
                           assistant_text("done")], tmp_path)
        result = run.run_case(case(id="echoes"))
        (turn,) = log.read()
        assert turn.case == "echoes" and result.trace == turn.id
        assert turn.tools_used == ["echo"] and turn.task == "do the thing"

    def test_recording_does_not_change_the_verdict(self, tmp_path):
        script = [assistant_text("done")]
        plain = EvalRunner(ScriptedProvider(list(script)), "m",
                           tools=ToolRegistry(),
                           permissions=lambda r: True).run_case(
            case(required_tools=["echo"]))
        traced = runner(list(script), tmp_path)[0].run_case(
            case(required_tools=["echo"]))
        assert (plain.passed, plain.failures) == (traced.passed,
                                                   traced.failures)

    def test_a_crashed_run_is_recorded_with_why(self, tmp_path):
        run, log = runner([assistant_tool_call(f"t{i}", "echo", {"text": "x"})
                           for i in range(5)], tmp_path)
        run.max_iterations = 2
        result = run.run_case(case())
        assert not result.passed
        assert log.read()[0].outcome == "max_iterations"

    def test_a_run_nobody_paid_for_is_not_a_turn(self, tmp_path):
        run, log = runner([], tmp_path)
        result = run.run_case(case(user_message="", has_tools=["echo"]))
        assert result.trace is None
        assert not (tmp_path / "suite.jsonl").exists()

    def test_the_async_twin_records_the_same_way(self, tmp_path):
        run, log = runner([assistant_text("done")], tmp_path,
                          cls=AsyncEvalRunner)
        result = asyncio.run(run.run_case(case(id="a")))
        assert log.read()[0].case == "a" and result.trace

    def test_the_report_names_the_turns_and_old_reports_still_read(
            self, tmp_path):
        run, _ = runner([assistant_text("done")] * 3, tmp_path)
        outcomes = run.run_suite([case(id="x")], repeat=3)
        record = record_run(outcomes, suite="s", provider="p", model="m",
                            repeat=3, cases_in_suite=1)
        path = tmp_path / "r.json"
        write_report(path, record)
        assert read_report(path).cases[0].traces == outcomes[0].traces
        assert len(outcomes[0].traces) == 3
        raw = json.loads(path.read_text())
        del raw["cases"][0]["traces"]             # an older writer's file
        path.write_text(json.dumps(raw))
        assert read_report(path).cases[0].traces == []

    def test_through_the_cli_a_red_case_names_its_turn(
            self, tmp_path, monkeypatch, capsys):
        from test_eval_cost import TestThroughTheGate
        gate = TestThroughTheGate()
        root = gate._suite_dir(tmp_path)
        (root / "evals" / "cases.toml").write_text(
            '[[case]]\nid = "x"\nuser_message = "hi"\n'
            'required_tools = ["read_file"]\n')
        trace, report = tmp_path / "t.jsonl", tmp_path / "r.json"
        code = gate._run(monkeypatch, [
            "--agent", str(root), "--eval", "--provider", "ollama",
            "--model", "qwen3.8:latest", "--trace", str(trace),
            "--report", str(report)])
        out = " ".join(capsys.readouterr().out.split())
        (turn,) = TrajectoryLog(trace).read()
        assert code == 1
        assert f"turn: {turn.id[:8]}" in out
        assert f"--fossil ID --trace {trace}" in out
        assert read_report(report).cases[0].traces == [turn.id]

    def test_an_unwritable_trace_stops_before_any_run(
            self, tmp_path, monkeypatch, capsys):
        from test_eval_cost import TestThroughTheGate
        gate = TestThroughTheGate()
        root = gate._suite_dir(tmp_path)
        blocker = tmp_path / "file"
        blocker.write_text("")
        code = gate._run(monkeypatch, [
            "--agent", str(root), "--eval", "--provider", "ollama",
            "--model", "m", "--trace", str(blocker / "t.jsonl")])
        assert code == 2
        assert "cannot write trace" in capsys.readouterr().err


def priced(case_id, usd, attempts=1, at="2026-09-01T00:00:00Z", rates=None):
    return SuiteRun(
        suite="pkg 0.1", provider="p", model="m", at=at, repeat=attempts,
        cases_in_suite=1, pricing=rates,
        cases=[CaseRecord(id=case_id, passed=True, attempts=attempts,
                          passes=attempts, min_pass_rate=1.0, tokens=10,
                          seconds=1.0, ran_model=True, usd=usd)])


class TestWhatEachCaseCostOverTime:
    def test_cost_is_per_run_oldest_against_newest(self):
        """Ten repeats and three are not the same bill."""
        (group,) = pool([priced("x", 0.10, attempts=10),
                         priced("x", 0.09, attempts=3,
                                at="2026-09-20T00:00:00Z")])
        (c,) = group.cases
        assert round(c.usd_first, 6) == 0.01
        assert round(c.usd_last, 6) == 0.03
        assert round(c.usd_growth, 6) == 3.0

    def test_a_moved_rate_is_named(self):
        a = PriceRecord(source="t", input=3.0, output=15.0)
        b = PriceRecord(source="t", input=6.0, output=15.0)
        (group,) = pool([priced("x", 0.01, rates=a),
                         priced("x", 0.02, at="2026-09-20T00:00:00Z",
                                rates=b)])
        assert group.cases[0].price_moved is True

    def test_an_unpriced_case_has_no_figure_not_zero(self):
        (group,) = pool([priced("x", None)])
        assert group.cases[0].usd_first is None
        assert group.cases[0].usd_growth is None

    def test_the_pool_prints_the_dearest_move(self, tmp_path, capsys):
        paths = []
        for name, usd, at in (("a", 0.01, "2026-09-01T00:00:00Z"),
                              ("b", 0.04, "2026-09-20T00:00:00Z")):
            paths.append(str(tmp_path / f"{name}.json"))
            write_report(tmp_path / f"{name}.json", priced("x", usd, at=at))
        assert main(["--reports", *paths, "--pool"]) == 0
        out = " ".join(capsys.readouterr().out.split())
        assert "$0.0100 → $0.0400 per run (x4.0)" in out
        assert "dearest move: x costs x4.0 per run" in out

    def test_a_free_case_prints_no_dollars(self, tmp_path, capsys):
        path = tmp_path / "a.json"
        write_report(path, priced("x", 0.0))
        main(["--reports", str(path), "--pool"])
        assert "$" not in capsys.readouterr().out


class TestThePoolIsWrittenDown:
    def test_the_file_has_its_own_format_and_every_figure(self, tmp_path):
        path = tmp_path / "pool.json"
        write_pool(path, pool([priced("x", 0.01)]))
        raw = json.loads(path.read_text())
        assert raw["format"] == POOL_FORMAT
        (c,) = raw["pools"][0]["cases"]
        assert c["standing"] == "holds" and c["usd_first"] == 0.01
        assert 0 < c["low"] <= c["high"] <= 1

    def test_against_refuses_a_pool_by_name(self, tmp_path):
        path = tmp_path / "pool.json"
        write_pool(path, pool([priced("x", 0.01)]))
        try:
            read_report(path)
        except Exception as exc:          # ConfigError, naming the format
            assert POOL_FORMAT in str(exc)
        else:
            raise AssertionError("a pool was read as a report")

    def test_the_flag_pools_and_writes(self, tmp_path, capsys):
        report, out = tmp_path / "a.json", tmp_path / "pool.json"
        write_report(report, priced("x", 0.01))
        assert main(["--reports", str(report), "--pool-json", str(out)]) == 0
        assert "pooled" in capsys.readouterr().out
        assert json.loads(out.read_text())["pools"][0]["suite"] == "pkg 0.1"

    def test_the_flag_needs_reports(self, capsys):
        assert main(["--pool-json", "x.json"]) == 2
        assert "--reports" in capsys.readouterr().err


def test_usage_is_what_the_recording_counts(tmp_path):
    run, log = runner([assistant_text(
        "done", usage=Usage(input_tokens=30, output_tokens=12))], tmp_path)
    run.run_case(case())
    assert log.read()[0].tokens == 42
