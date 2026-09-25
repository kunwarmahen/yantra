"""A turn that stops for an approval and carries on when it comes
(notes/88).

The bias here is a broken conversation. Holding deliberately leaves a
tool call without a result, which is the one state every provider
rejects with a 400, so every path out of a hold is pinned to leave
history valid:

* **Resumed:** approved calls run, refused ones say so in the person's
  words, and ONE batch goes back in the order the model asked.
* **Set aside:** a new message answers the waiting calls as abandoned.
* **Interrupted:** a consumer that closes the stream mid-hold, or a
  resume that is interrupted, leaves no call unanswered.

And three that would each be a quiet lie:

* **Batch-mates approved beside a held call are not lost** -- they ran,
  and their results go back with the resumed ones.
* **A bad resume changes nothing** -- a missing or extra answer raises
  before a single call runs.
* **A child never holds** -- it cannot end its parent's turn, so its
  hold becomes a refusal it can read.
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import ScriptedProvider, assistant_text
from yantra.agent import Agent, ToolExecuted, TurnEnd
from yantra.async_agent import AsyncAgent
from yantra.hold import ABANDONED, TurnHeld
from yantra.permissions import (HELD, HELD_IN_CHILD, REFUSED_USER,
                                PermissionRequest, hold, refuse,
                                with_wait_budget)
from yantra.tools.base import Tool
from yantra.trace import SHAPE, TrajectoryLog, flagged, watch
from yantra.types import Message, ModelResponse, ToolCall, ToolResult, Usage


class Write(Tool):
    name = "write_note"
    description = "write a note"
    parameters = {"type": "object",
                  "properties": {"text": {"type": "string"}},
                  "required": ["text"], "additionalProperties": False}

    def __init__(self):
        self.written: list[str] = []

    def summary(self, args, ctx):
        return f"write_note({args.get('text')!r})"

    def run(self, args, ctx):
        self.written.append(args["text"])
        return f"wrote {args['text']}"


def calls(*pairs) -> ModelResponse:
    """One assistant message asking for several tool calls at once."""
    return ModelResponse(
        message=Message("assistant", [ToolCall(id=i, name="write_note",
                                               arguments={"text": t})
                                      for i, t in pairs]),
        stop_reason="tool_use", usage=Usage())


def hold_on(*texts):
    """A gate that holds the calls whose text is listed and approves the
    rest -- 'the person answered two of three in time'."""
    def gate(request: PermissionRequest) -> bool:
        if request.arguments["text"] in texts:
            return hold(request)
        return True
    return gate


def agent_with(script, gate, cls=Agent):
    tool = Write()
    agent = cls(ScriptedProvider(script), model="m", permissions=gate)
    agent.registry.register(tool)
    return agent, tool


def valid(history) -> bool:
    """Every tool call has a result before the next message."""
    for i, message in enumerate(history):
        if message.role == "assistant" and message.tool_calls():
            if i + 1 >= len(history):
                return False
            answered = {b.tool_call_id for b in history[i + 1].content
                        if isinstance(b, ToolResult)}
            if {c.id for c in message.tool_calls()} - answered:
                return False
    return True


class TestAHeldTurnStops:
    def test_the_turn_ends_held_and_says_what_waits(self):
        agent, tool = agent_with([calls(("a", "one"), ("b", "two"))],
                                 hold_on("two"))
        events = list(agent.run_streaming("go"))
        end = events[-1]
        assert isinstance(end, TurnEnd) and end.reason == "held"
        assert [r.call_id for r in end.waiting] == ["b"]
        assert agent.held.ids == ["b"]

    def test_the_approved_batch_mate_ran(self):
        agent, tool = agent_with([calls(("a", "one"), ("b", "two"))],
                                 hold_on("two"))
        events = list(agent.run_streaming("go"))
        assert tool.written == ["one"]
        assert [e.call.id for e in events if isinstance(e, ToolExecuted)] \
            == ["a"]

    def test_history_ends_on_the_question_on_purpose(self):
        agent, _ = agent_with([calls(("b", "two"))], hold_on("two"))
        list(agent.run_streaming("go"))
        assert agent.history[-1].role == "assistant"

    def test_run_raises_rather_than_inventing_a_reply(self):
        agent, _ = agent_with([calls(("b", "two"))], hold_on("two"))
        with pytest.raises(TurnHeld) as caught:
            agent.run("go")
        assert caught.value.held.ids == ["b"]


class TestResume:
    def script(self):
        return [calls(("a", "one"), ("b", "two"), ("c", "three")),
                assistant_text("done")]

    def test_approved_runs_refused_says_so_and_the_turn_finishes(self):
        agent, tool = agent_with(self.script(), hold_on("two", "three"))
        list(agent.run_streaming("go"))
        events = list(agent.resume({"b": True, "c": "not that file"}))
        assert tool.written == ["one", "two"]
        refused = [e for e in events if isinstance(e, ToolExecuted)
                   and e.refusal == REFUSED_USER]
        assert [e.call.id for e in refused] == ["c"]
        assert "The person said: not that file" in refused[0].result.content
        assert events[-1].reason == "end_turn"
        assert agent.held is None and valid(agent.history)

    def test_one_batch_goes_back_in_the_order_asked(self):
        agent, _ = agent_with(self.script(), hold_on("two", "three"))
        list(agent.run_streaming("go"))
        list(agent.resume({"b": True, "c": False}))
        batch = agent.history[2]
        assert [b.tool_call_id for b in batch.content] == ["a", "b", "c"]

    def test_edited_arguments_are_what_runs(self):
        agent, tool = agent_with(self.script(), hold_on("two", "three"))
        list(agent.run_streaming("go"))
        list(agent.resume({"b": {"text": "TWO"}, "c": True}))
        assert tool.written == ["one", "TWO", "three"]

    def test_a_resume_is_a_new_turn(self):
        agent, _ = agent_with(self.script(), hold_on("two", "three"))
        list(agent.run_streaming("go"))
        before = agent._turn_id
        list(agent.resume({"b": True, "c": True}))
        assert agent._turn_id != before

    @pytest.mark.parametrize("answers", [{"b": True}, {"b": True, "c": True,
                                                        "z": True},
                                         {"b": True, "c": 3}])
    def test_a_bad_answer_changes_nothing(self, answers):
        agent, tool = agent_with(self.script(), hold_on("two", "three"))
        list(agent.run_streaming("go"))
        length = len(agent.history)
        with pytest.raises(ValueError):
            list(agent.resume(answers))
        assert tool.written == ["one"] and len(agent.history) == length
        assert agent.held is not None

    def test_nothing_held_nothing_to_resume(self):
        agent, _ = agent_with([], hold_on())
        with pytest.raises(ValueError, match="nothing is held"):
            list(agent.resume({}))


class TestSetAside:
    def test_a_new_message_abandons_the_hold(self):
        agent, tool = agent_with([calls(("a", "one"), ("b", "two")),
                                  assistant_text("ok")], hold_on("two"))
        list(agent.run_streaming("go"))
        agent.run("never mind")
        batch = agent.history[2]
        assert [b.content for b in batch.content][1] == ABANDONED
        assert batch.content[0].content == "wrote one"   # the real result
        assert agent.held is None and valid(agent.history)

    def test_compaction_sets_it_aside_first(self):
        agent, _ = agent_with([calls(("b", "two"))], hold_on("two"))
        list(agent.run_streaming("go"))
        assert agent.abandon_held() is True
        assert valid(agent.history) and agent.abandon_held() is False

    def test_a_cleared_history_cannot_be_resumed_into(self):
        agent, _ = agent_with([calls(("b", "two"))], hold_on("two"))
        list(agent.run_streaming("go"))
        agent.history.clear()
        with pytest.raises(ValueError, match="moved on"):
            list(agent.resume({"b": True}))


class TestInterrupted:
    def test_closing_the_stream_on_the_hold_leaves_history_valid(self):
        agent, _ = agent_with([calls(("a", "one"), ("b", "two"))],
                              hold_on("two"))
        stream = agent.run_streaming("go")
        for event in stream:
            if isinstance(event, ToolExecuted):
                stream.close()
                break
        assert agent.held is None and valid(agent.history)


class TestTheClockWrappers:
    def test_on_timeout_hold_is_accepted_and_still_required(self):
        with pytest.raises(TypeError):
            with_wait_budget(lambda r: True, 5)   # no default, still
        with_wait_budget(lambda r: True, 5, on_timeout="hold")

    def test_an_unanswered_question_is_held_not_refused(self):
        async def never(request):
            await asyncio.sleep(10)
            return True
        gate = with_wait_budget(never, 0.05, on_timeout="hold")
        agent, tool = agent_with(
            [calls(("a", "one"), ("b", "two")), assistant_text("done")],
            gate, cls=AsyncAgent)

        async def drive():
            events = [e async for e in agent.run_streaming("go")]
            assert events[-1].reason == "held"
            # Both held: the first timed out, the second was never asked.
            assert agent.held.ids == ["a", "b"]
            assert tool.written == []
            events = [e async for e in agent.resume({"a": True, "b": True})]
            return events
        events = asyncio.run(drive())
        assert events[-1].reason == "end_turn" and tool.written == ["one",
                                                                    "two"]
        assert valid(agent.history)

    def test_hold_is_a_false_that_older_loops_read_as_a_refusal(self):
        request = PermissionRequest("x", {}, "x()", read_only=False)
        assert hold(request) is False and request.code == HELD


class TestAChildNeverHolds:
    def test_its_hold_becomes_a_refusal_it_can_read(self):
        agent, tool = agent_with([calls(("b", "two")), assistant_text("ok")],
                                 hold_on("two"))
        agent.can_hold = False
        agent.run("go")
        assert agent.turn_refusals == {"b": HELD_IN_CHILD}
        assert agent.held is None and tool.written == []

    def test_the_spawner_builds_children_that_cannot_hold(self):
        from yantra.subagent import SubagentSpawner
        parent = Agent(ScriptedProvider([]), model="m")
        child = SubagentSpawner(parent)._build(
            system="s", allowed=[], max_iterations=2, number=1)
        assert child.can_hold is False


class TestTheRecording:
    def test_a_held_turn_is_recorded_held_and_not_flagged(self, tmp_path):
        agent, _ = agent_with([calls(("a", "one"), ("b", "two"))],
                              hold_on("two"))
        log = TrajectoryLog(tmp_path / "t.jsonl")
        for _ in watch("go", agent.run_streaming("go"), log.record,
                       detail=SHAPE):
            pass
        (turn,) = log.read()
        assert turn.outcome == "held"
        assert [(s.name, s.refusal) for s in turn.steps] == [
            ("write_note", None), ("write_note", "held")]
        assert not flagged(turn)

    def test_the_resumed_turn_names_the_one_it_continues(self, tmp_path):
        agent, _ = agent_with([calls(("b", "two")), assistant_text("done")],
                              hold_on("two"))
        log = TrajectoryLog(tmp_path / "t.jsonl")
        for _ in watch("go", agent.run_streaming("go"), log.record):
            pass
        held_id = log.read()[0].id
        for _ in watch("go", agent.resume({"b": True}), log.record,
                       resumes=held_id):
            pass
        assert log.read()[1].resumes == held_id

    def test_a_refused_step_still_flags(self, tmp_path):
        """Held is excused; a real refusal is not."""
        agent, _ = agent_with([calls(("b", "two")), assistant_text("ok")],
                              lambda r: refuse(r, "no"))
        log = TrajectoryLog(tmp_path / "t.jsonl")
        for _ in watch("go", agent.run_streaming("go"), log.record):
            pass
        assert flagged(log.read()[0])


class TestAcrossARestart:
    """A held session is saved as the turn before it asked, plus the
    question on the side (session.py) -- so an OLD reader still loads a
    valid conversation, and this one resumes it in a new process."""

    def held_and_saved(self, tmp_path, script):
        from yantra.session import SessionStore
        agent, tool = agent_with(script, hold_on("two", "three"))
        list(agent.run_streaming("go"))
        store = SessionStore(tmp_path / "s.db")
        store.save(agent, provider_name="anthropic")
        return store.load_latest(), tool

    def script(self):
        return [calls(("a", "one"), ("b", "two"), ("c", "three")),
                assistant_text("done")]

    def test_the_history_on_disk_is_valid_without_the_block(self, tmp_path):
        from yantra.session import _load_message
        payload, _ = self.held_and_saved(tmp_path, self.script())
        history = [_load_message(m) for m in payload["history"]]
        assert valid(history) and history[-1].role == "user"
        assert [w["call_id"] for w in payload["held"]["waiting"]] == ["b", "c"]

    def test_a_new_process_resumes_it(self, tmp_path):
        from yantra.session import apply_payload
        payload, _ = self.held_and_saved(tmp_path, self.script())
        fresh, tool = agent_with([assistant_text("done")], hold_on())
        apply_payload(fresh, payload, history_only=True)
        assert fresh.held.ids == ["b", "c"]
        events = list(fresh.resume({"b": True, "c": {"text": "THREE"}}))
        assert tool.written == ["two", "THREE"]
        assert events[-1].reason == "end_turn" and valid(fresh.history)
        batch = fresh.history[2]
        assert batch.content[0].content == "wrote one"   # ran before saving
        # The edit is what history records as having been called.
        assert fresh.history[1].tool_calls()[2].arguments == {"text": "THREE"}

    def test_loading_a_plain_session_clears_a_stale_hold(self, tmp_path):
        from yantra.session import SessionStore, apply_payload
        plain, _ = agent_with([assistant_text("hi")], hold_on())
        plain.run("hi")
        store = SessionStore(tmp_path / "p.db")
        store.save(plain, provider_name="anthropic")
        agent, _ = agent_with([calls(("b", "two"))], hold_on("two"))
        list(agent.run_streaming("go"))
        apply_payload(agent, store.load_latest(), history_only=True)
        assert agent.held is None and valid(agent.history)
