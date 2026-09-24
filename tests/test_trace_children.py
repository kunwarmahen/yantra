"""A recording that keeps the sub-agents, and one the browser writes too.

The bias here is the recording with a hole in it that nobody can see.
Two such holes existed:

* **A delegation that failed inside the child.** The parent's recording
  showed one call to the spawn tool, and the child's three failed reads
  were gone when the turn ended. The tests below make a child fail
  inside, and assert that the parent's line says where, and that the
  turn counts as failed even though the parent carried on.
* **A frontend that was never recorded.** ``--trace`` taught the
  terminal to write turns down; the browser runs the same agent through
  its own loop and wrote nothing. A test drives a turn through the web
  session and reads the file back.

The privacy rule from notes/57 applies to children as well. The task a
parent gives its child was written by the parent model out of what it
had read, so it is content, and a default (SHAPE) recording must not
contain it. That gets its own assertion.
"""

from __future__ import annotations

import json
import threading

from fastapi.testclient import TestClient

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent
from yantra.cli.main import main
from yantra.permissions import yolo
from yantra.subagent import SPAWN_TOOL_NAME, SpawnSubagent, SubagentSpawner
from yantra.tools.fs import ReadFile
from yantra.trace import FULL, SHAPE, ChildRun, ToolStep, Trajectory, \
    TrajectoryLog, watch
from yantra.web.server import make_app

from test_web_server import drain_until, make_session

SPAWN = {
    "objective": "Read notes.txt and say what it is about.",
    "output_format": "One line.",
    "tools_allowed": ["read_file"],
    "justification": "Isolated read.",
}


def delegating_agent(tmp_path, child_reads: list[str]):
    """A parent that spawns ONE child; the child reads each path in turn,
    then answers. A path that does not exist is a failed read."""
    (tmp_path / "notes.txt").write_text("the notes")
    script = [assistant_tool_call("p1", SPAWN_TOOL_NAME, SPAWN)]
    script += [assistant_tool_call(f"c{i}", "read_file", {"path": path})
               for i, path in enumerate(child_reads)]
    script += [assistant_text("they are notes"),       # the child's answer
               assistant_text("the child says: notes")]  # the parent's
    agent = Agent(ScriptedProvider(script), model="m", permissions=yolo,
                  cwd=tmp_path)
    agent.registry.register(ReadFile())
    spawner = SubagentSpawner(agent)
    agent.registry.register(SpawnSubagent(spawner))
    agent.subagents = spawner
    return agent, spawner


def recorded(agent, spawner, detail=SHAPE, task="what is in notes.txt?"):
    kept: list[Trajectory] = []
    for _ in watch(task, agent.run_streaming(task), kept.append,
                   model="m", detail=detail, spawner=spawner):
        pass
    return kept[0]


class TestTheSpawnerKeepsTheChildsSteps:
    def test_steps_come_back_in_order_with_what_worked(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["notes.txt", "gone.txt"])
        agent.run("go")
        (result,) = spawner.results
        assert result.steps == [("read_file", True), ("read_file", False)]
        assert (result.number, result.agent, result.model) == (
            1, SPAWN_TOOL_NAME, "m")

    def test_parallel_children_never_share_a_number(self):
        """The number is taken inside the budget lock, not read after it."""
        spawner = SubagentSpawner(Agent(ScriptedProvider([]), model="m"),
                                  max_per_session=50)
        numbers: list[int] = []
        threads = [threading.Thread(
            target=lambda: numbers.append(spawner._charge_one()))
            for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(numbers) == list(range(1, 41))


class TestTheParentsLineCarriesItsChildren:
    def test_a_child_is_recorded_with_its_steps(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["notes.txt"])
        trace = recorded(agent, spawner)
        (child,) = trace.children
        assert [s.name for s in child.steps] == ["read_file"]
        assert child.code is None and child.tokens >= 0

    def test_a_failure_inside_the_child_makes_the_turn_failed(self, tmp_path):
        """The parent carried on and answered; the turn still had trouble."""
        agent, spawner = delegating_agent(tmp_path, ["gone.txt"])
        trace = recorded(agent, spawner)
        assert all(step.ok for step in trace.steps)      # the parent's view
        assert trace.children[0].steps[0].ok is False
        assert trace.failed is True

    def test_the_task_a_parent_wrote_is_content_not_shape(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["notes.txt"])
        child = recorded(agent, spawner).children[0]
        assert child.task is None and child.answer is None

    def test_full_keeps_the_task_and_the_answer(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["notes.txt"])
        child = recorded(agent, spawner, detail=FULL).children[0]
        assert child.task == SPAWN["objective"]
        assert child.answer == "they are notes"

    def test_only_this_turns_children_are_this_turns(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["notes.txt"])
        agent.run("an earlier turn that spawned")
        agent.provider = ScriptedProvider([assistant_text("no delegation")])
        assert recorded(agent, spawner).children == []

    def test_a_host_with_no_spawner_records_as_before(self):
        kept: list[Trajectory] = []
        list(watch("t", iter(()), kept.append, spawner=object()))
        assert kept[0].children == []


class TestOnDisk:
    def _with_child(self):
        return Trajectory(
            id="abc123", at="2026-09-24T00:00:00Z", provider="ollama",
            model="m", detail=SHAPE, task="t",
            children=[ChildRun(number=1, agent="fact_checker", model="m",
                               steps=[ToolStep("read_file", False)],
                               iterations=2, tokens=40)])

    def test_children_round_trip(self, tmp_path):
        log = TrajectoryLog(tmp_path / "t.jsonl")
        log.record(self._with_child())
        (back,) = log.read()
        assert back.children[0].agent == "fact_checker"
        assert back.children[0].steps[0].ok is False

    def test_a_turn_without_children_writes_no_key(self, tmp_path):
        """So a line that delegated nothing reads exactly as it always did."""
        log = TrajectoryLog(tmp_path / "t.jsonl")
        trace = self._with_child()
        trace.children = []
        log.record(trace)
        assert "children" not in json.loads(log.path.read_text())

    def test_fossil_names_the_children_without_asserting_them(self, tmp_path,
                                                              capsys):
        log = TrajectoryLog(tmp_path / "t.jsonl")
        log.record(self._with_child())
        assert main(["--fossil", "abc", "--trace", str(log.path)]) == 0
        out = capsys.readouterr()
        assert "sub-agent #1 fact_checker" in out.err
        assert "read_file (failed)" in out.err
        assert "fact_checker" not in out.out          # not in the case itself


class TestTheBrowserRecordsToo:
    def test_a_web_turn_lands_in_the_trace(self, tmp_path):
        session, _ = make_session([
            assistant_tool_call("e1", "echo", {"text": "hi"}),
            assistant_text("done"),
        ])
        session.trace = TrajectoryLog(tmp_path / "web.jsonl")
        client = TestClient(make_app(session))
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "state"
            assert client.post("/api/message",
                               json={"text": "say hi"}).status_code == 200
            drain_until(ws, {"turn_done"})
        (trace,) = session.trace.read()
        assert trace.task == "say hi"
        assert trace.tools_used == ["echo"]
        assert trace.outcome == "end_turn"
