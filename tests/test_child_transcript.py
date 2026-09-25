"""A child kept at FULL exactly as its parent is (notes/85).

The bias here is the half-kept child. A FULL recording used to keep the
parent's reads verbatim and the child's as bare tool names, so a
delegation that went wrong said THAT it went wrong and never WHERE. Three
ways the fix could quietly be only half a fix, each pinned below:

* **The contents leak into SHAPE.** The spawner keeps every argument and
  result, because it cannot know what a recorder will keep. A default
  recording that let them through would be the privacy failure notes/57
  was built to prevent, one level down.
* **The scrubber forgets the new field.** ``--trace-redact`` scrubbed a
  child's task and answer before the child had steps worth scrubbing. A
  child step's result is where a child's reads land now, and a scrubber
  that skipped it would write exactly what the flag promised not to.
* **SHAPE strips the spawner's own copy.** The recorder must drop the
  contents from what it writes, not from ``spawner.results``, which a
  host may be reading for its own reasons.
"""

from __future__ import annotations

import json

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent
from yantra.permissions import yolo
from yantra.subagent import SPAWN_TOOL_NAME, SpawnSubagent, SubagentSpawner
from yantra.tools.fs import ReadFile
from yantra.trace import FULL, MAX_KEPT_CHARS, SHAPE, TrajectoryLog, watch

from test_trace_children import SPAWN, delegating_agent


def recorded_to_disk(tmp_path, agent, spawner, *, detail, redact=()):
    log = TrajectoryLog(tmp_path / "t.jsonl", detail=detail, redact=redact)
    for _ in watch("go", agent.run_streaming("go"), log.record,
                   detail=detail, spawner=spawner):
        pass
    return json.loads(log.path.read_text()), log.read()[0]


class TestTheSpawnerKeepsWhatTheChildDid:
    def test_each_step_has_its_arguments_and_result(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["notes.txt"])
        agent.run("go")
        (step,) = spawner.results[0].steps
        assert step.arguments == {"path": "notes.txt"}
        assert "the notes" in step.result

    def test_a_failed_read_keeps_the_error_it_got(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["gone.txt"])
        agent.run("go")
        (step,) = spawner.results[0].steps
        assert step.ok is False and "gone.txt" in step.result

    def test_a_long_result_is_clipped_before_it_is_kept(self, tmp_path):
        (tmp_path / "big.txt").write_text("x" * (MAX_KEPT_CHARS * 3))
        script = [
            assistant_tool_call("p1", SPAWN_TOOL_NAME, SPAWN),
            assistant_tool_call("c1", "read_file", {"path": "big.txt"}),
            assistant_text("big"), assistant_text("done"),
        ]
        agent = Agent(ScriptedProvider(script), model="m", permissions=yolo,
                      cwd=tmp_path)
        agent.registry.register(ReadFile())
        spawner = SubagentSpawner(agent)
        agent.registry.register(SpawnSubagent(spawner))
        agent.run("go")
        (step,) = spawner.results[0].steps
        assert len(step.result) < MAX_KEPT_CHARS + 100
        assert "more chars" in step.result


class TestFullKeepsItAndShapeDoesNot:
    def test_full_writes_the_childs_arguments_and_result(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["notes.txt"])
        raw, back = recorded_to_disk(tmp_path, agent, spawner, detail=FULL)
        (row,) = raw["children"][0]["steps"]
        assert row["arguments"] == {"path": "notes.txt"}
        assert "the notes" in row["result"]
        assert back.children[0].steps[0].arguments == {"path": "notes.txt"}

    def test_a_full_child_step_has_the_parents_row_shape(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["notes.txt"])
        raw, _ = recorded_to_disk(tmp_path, agent, spawner, detail=FULL)
        assert (set(raw["children"][0]["steps"][0])
                == set(raw["steps"][0]))

    def test_shape_writes_neither(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["notes.txt"])
        raw, _ = recorded_to_disk(tmp_path, agent, spawner, detail=SHAPE)
        assert raw["children"][0]["steps"] == [
            {"name": "read_file", "ok": True}]
        assert "the notes" not in json.dumps(raw)

    def test_shape_leaves_the_spawners_copy_alone(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["notes.txt"])
        recorded_to_disk(tmp_path, agent, spawner, detail=SHAPE)
        assert spawner.results[0].steps[0].arguments == {"path": "notes.txt"}


class TestTheScrubberReachesTheChild:
    def test_a_childs_read_is_scrubbed_and_counted(self, tmp_path):
        (tmp_path / "owner.txt").write_text("owner: ana@example.com")
        agent, spawner = delegating_agent(tmp_path, ["owner.txt"])
        raw, _ = recorded_to_disk(tmp_path, agent, spawner, detail=FULL,
                                  redact=["email"])
        (row,) = raw["children"][0]["steps"]
        assert "@example" not in json.dumps(raw)
        assert "[redacted]" in row["result"]
        assert raw["redacted"] >= 1

    def test_a_childs_arguments_are_scrubbed(self, tmp_path):
        agent, spawner = delegating_agent(tmp_path, ["ana@example.com"])
        raw, _ = recorded_to_disk(tmp_path, agent, spawner, detail=FULL,
                                  redact=["email"])
        (row,) = raw["children"][0]["steps"]
        assert row["arguments"] == {"path": "[redacted]"}
        assert row["name"] == "read_file"      # shape survives the scrub
