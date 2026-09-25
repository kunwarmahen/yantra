"""Telling the MODEL how much of a turn's approval time is left.

Notes 51 and 78 gave a turn an allowance for waiting on approvals, and
the model learned about it only by being refused. The bias here is the
refusal as the first news: a model that knew twelve seconds were left
could have asked for the one write it needed instead of three.

The second bias is noise. A notice on every request would be a paragraph
in every turn for the sake of the few that ask for approval, so the tests
assert SILENCE as often as speech: nothing before any waiting, nothing
repeated while the number has not moved, nothing without a clock.

Like the budget notice, it is sent and never stored -- history is
replayed, and a clock from last week read back as something the user
said is worse than no clock.

And a sub-agent (notes/91). Its prompts keep the same person waiting as
its parent's, so the bias there is two clocks: a child told nothing
because the notice was wired to the parent only, or a child whose own
turn id looks to the wrapper like a fresh turn and refills the
allowance its parent had spent.
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent
from yantra.async_agent import AsyncAgent
from yantra.permissions import (PermissionRequest, adecide, approval_notice,
                                with_wait_budget, yolo)
from yantra.tools.base import Tool, ToolRegistry


class Echo(Tool):
    name = "echo"
    description = "echo"
    parameters = {"type": "object", "properties": {}}
    read_only = True

    def summary(self, args, ctx):
        return "echo()"

    def run(self, args, ctx):
        return "echoed"


def _texts(request: dict) -> list[str]:
    return [b.text for m in request["messages"] for b in m.content
            if hasattr(b, "text")]


def _told(request: dict) -> list[str]:
    return [t for t in _texts(request) if t.startswith("[approval notice]")]


class TestTheSentence:
    def test_a_full_allowance_says_nothing(self):
        assert approval_notice(60, 60) is None

    def test_it_gives_the_seconds_rounded_up(self):
        said = approval_notice(11.2, 60)
        assert "12 of this turn's 60 seconds" in said
        assert "the one you need most" in said

    def test_a_fraction_of_a_second_is_not_called_zero(self):
        assert "1 of this turn's" in approval_notice(0.3, 60)

    def test_none_left_says_what_still_works(self):
        said = approval_notice(0, 60)
        assert "used all 60 seconds" in said
        assert "Read-only tools still work" in said


class TestTheLibraryWrapper:
    def request(self, turn="t1"):
        return PermissionRequest(tool_name="bomb", arguments={},
                                 summary="bomb()", read_only=False,
                                 turn_id=turn)

    def test_a_turn_it_has_not_seen_has_spent_nothing(self):
        gate = with_wait_budget(yolo, 10, on_timeout="deny")
        assert gate.approval_notice("t1") is None

    def test_a_wait_is_reported_to_the_turn_that_spent_it(self):
        async def slow(request):
            await asyncio.sleep(0.05)
            return True
        gate = with_wait_budget(slow, 10, on_timeout="deny")
        assert asyncio.run(adecide(gate, self.request("t1"))) is True
        assert "10 of this turn's 10 seconds" in gate.approval_notice("t1")
        assert gate.approval_notice("t2") is None


def _agent(script, clock, cls=Agent):
    reg = ToolRegistry()
    reg.register(Echo())
    provider = ScriptedProvider(script)
    agent = cls(provider, model="m", tools=reg, permissions=yolo)
    agent.approval_notice = clock
    return agent, provider


def _three_calls():
    return [assistant_tool_call("a", "echo", {}),
            assistant_tool_call("b", "echo", {}),
            assistant_text("done")]


class TestTheLoop:
    def test_the_model_is_told_when_the_sentence_changes_and_only_then(self):
        said = iter(["[approval notice] 40 left", "[approval notice] 40 left",
                     "[approval notice] 12 left"])
        agent, provider = _agent(_three_calls(), lambda turn: next(said))
        agent.run("go")
        told = [_told(r) for r in provider.requests]
        assert told == [["[approval notice] 40 left"], [],
                        ["[approval notice] 12 left"]]

    def test_no_clock_says_nothing(self):
        agent, provider = _agent(_three_calls(), None)
        agent.run("go")
        assert not any(_told(r) for r in provider.requests)

    def test_it_is_sent_and_never_stored(self):
        agent, provider = _agent([assistant_text("done")],
                                 lambda turn: "[approval notice] 5 left")
        agent.run("go")
        assert _told(provider.last_request())
        assert not any(getattr(b, "text", "").startswith("[approval")
                       for m in agent.history for b in m.content)

    def test_the_clock_is_asked_about_this_turn(self):
        seen = []
        agent, _ = _agent([assistant_text("a"), assistant_text("b")],
                          lambda turn: seen.append(turn))
        agent.run("one")
        agent.run("two")
        assert len(set(seen)) == 2 and "" not in seen

    def test_the_same_sentence_is_told_again_in_a_new_turn(self):
        agent, provider = _agent([assistant_text("a"), assistant_text("b")],
                                 lambda turn: "[approval notice] 0 left")
        agent.run("one")
        agent.run("two")
        assert all(_told(r) for r in provider.requests)

    def test_the_async_loop_tells_the_model_the_same_thing(self):
        said = iter(["[approval notice] 40 left", "[approval notice] 40 left",
                     "[approval notice] 12 left"])
        agent, provider = _agent(_three_calls(), lambda turn: next(said),
                                 cls=AsyncAgent)
        asyncio.run(agent.run("go"))
        assert [_told(r) for r in provider.requests] == [
            ["[approval notice] 40 left"], [], ["[approval notice] 12 left"]]


class TestTheBrowser:
    def test_a_withdrawn_prompt_is_followed_by_the_news(self):
        pytest.importorskip("fastapi")
        from test_web_wait_budget import run, two_writes_and_a_read
        from test_web_server import make_session
        session, agent = make_session(two_writes_and_a_read())
        session.set_wait_budget(0.3, on_timeout="deny")
        run(session)
        told = [_told(r) for r in agent.provider.requests]
        assert told[0] == []                          # nothing spent yet
        assert "used all 0.3 seconds" in told[1][0]   # after the timeout
        assert told[2:] == [[], []]                   # not repeated

    def test_without_a_budget_the_model_hears_nothing(self):
        pytest.importorskip("fastapi")
        from test_web_wait_budget import run
        from test_web_server import make_session
        session, agent = make_session(
            [assistant_tool_call("r1", "echo", {"text": "c"}),
             assistant_text("done")])
        run(session)
        assert not any(_told(r) for r in agent.provider.requests)
        assert session.approval_notice("anything") is None


class Poke(Tool):
    """A tool that needs approval, so asking for it spends the clock."""
    name = "poke"
    description = "poke"
    parameters = {"type": "object", "properties": {}}
    read_only = False

    def summary(self, args, ctx):
        return "poke()"

    def run(self, args, ctx):
        return "poked"


def _parent_with_a_child(script, *, seconds=10, answer_after=0.05):
    """An async parent that can spawn, under a wrapper clock of
    ``seconds`` whose every prompt takes ``answer_after`` to answer."""
    from yantra.subagent import SpawnSubagent, SubagentSpawner
    asked: list[PermissionRequest] = []

    async def slow(request):
        asked.append(request)
        await asyncio.sleep(answer_after)
        return True
    gate = with_wait_budget(slow, seconds, on_timeout="deny")
    provider = ScriptedProvider(script)
    parent = AsyncAgent(provider, model="m", permissions=gate)
    parent.approval_notice = gate.approval_notice
    parent.registry.register(Poke())
    parent.registry.register(SpawnSubagent(SubagentSpawner(parent)))
    return parent, provider, asked


def _spawn_a_poker():
    from yantra.subagent import SPAWN_TOOL_NAME
    return assistant_tool_call("s", SPAWN_TOOL_NAME, {
        "objective": "poke once", "output_format": "one word",
        "tools_allowed": ["poke"], "justification": "isolated"})


class TestASubAgent:
    def test_its_prompts_spend_the_parents_turn(self):
        parent, _, asked = _parent_with_a_child([
            _spawn_a_poker(),
            assistant_tool_call("c", "poke", {}),   # the child asks
            assistant_text("poked"),                # the child finishes
            assistant_text("done")])
        asyncio.run(parent.run("go"))
        assert [r.tool_name for r in asked] == ["spawn_subagent", "poke"]
        assert {r.turn_id for r in asked} == {parent._turn_id}

    def test_it_is_told_what_its_parent_already_spent(self):
        parent, provider, _ = _parent_with_a_child([
            _spawn_a_poker(),
            assistant_tool_call("c", "poke", {}),
            assistant_text("poked"),
            assistant_text("done")])
        asyncio.run(parent.run("go"))
        # requests: parent, child, child, parent. The spawn prompt spent
        # time before the child's first request, so the child hears it
        # at once -- from the same clock, in the same words.
        child_first = _told(provider.requests[1])
        assert child_first and "of this turn's 10 seconds" in child_first[0]

    def test_a_child_does_not_refill_the_allowance(self):
        parent, _, _ = _parent_with_a_child([
            _spawn_a_poker(),
            assistant_tool_call("c", "poke", {}),
            assistant_text("poked"),
            assistant_tool_call("p", "poke", {}),   # the parent asks again
            assistant_text("done")], seconds=0.25, answer_after=0.1)
        asyncio.run(parent.run("go"))
        # 0.1s for the spawn, 0.1s for the child's poke: 0.05s is left
        # when the parent asks again, and it runs out. Had the child's
        # own turn id reset the clock, the parent's prompt would have
        # found a full 0.25s and been approved.
        assert parent.turn_refusals == {"p": "timeout"}

    def test_the_spawner_hands_the_child_the_parents_clock(self):
        from yantra.subagent import SubagentSpawner
        parent = Agent(ScriptedProvider([]), model="m")
        parent.approval_notice = lambda turn: None
        parent._turn_id = "parent-turn"
        child = SubagentSpawner(parent)._build(
            system="s", allowed=[], max_iterations=2, number=1)
        assert child.clock_turn == "parent-turn"
        assert child.approval_notice is parent.approval_notice
