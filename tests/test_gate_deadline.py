"""A clock on a gate, and a word for why a call was refused.

The bias here is two ways a harness can lie about a refusal.

The first is a deadline with an opinion. Every harness that grows a
timeout grows a default with it, and the default is always "unanswered
means no" -- which is right for a deploy and wrong for the overnight
batch whose owner set the deadline precisely so it would go ahead. So
several tests below assert that the wrapper CANNOT BE BUILT without
being told, and that the allow branch really allows: a policy nobody
chose is the failure being designed against.

The second is prose standing in for a token. "Nobody answered in time"
and "your policy forbids this" are the same shape of English and demand
opposite handling, and a caller reduced to matching on a sentence breaks
silently the day the sentence improves. The tests here read the CODE off
the event stream and separately assert the model never sees it -- two
readers, two registers, and a test for each.

Where a call was refused, these tests assert the tool DID NOT RUN rather
than reading a verdict: a test that only inspected the ToolResult would
keep passing the day a timeout started being treated as a yes.
"""

from __future__ import annotations

import asyncio

import pytest

from yantra.agent import Agent, ToolExecuted
from yantra.async_agent import AsyncAgent
from yantra.permissions import (
    REFUSED_POLICY,
    REFUSED_TIMEOUT,
    REFUSED_UNATTENDED,
    REFUSED_UNSPECIFIED,
    REFUSED_USER,
    PermissionRequest,
    SwitchableGate,
    allow_read_only,
    denial_code,
    deny_all,
    refuse,
    trust_sandbox,
    with_deadline,
)
from yantra.tools.base import Tool, ToolRegistry
from yantra.types import Message, ModelResponse, ToolCall

from conftest import ScriptedProvider, assistant_text


# ---- fixtures ---------------------------------------------------------------


class BombTool(Tool):
    """NOT read_only, and it records that it ran. "Was this refused?" is a
    question about whether this list grew."""

    name = "bomb"
    description = "mutates things"
    parameters = {"type": "object", "properties": {}}
    detonations: list[str] = []

    def summary(self, args, ctx):
        return "bomb()"

    def run(self, args, ctx):
        BombTool.detonations.append("boom")
        return "detonated"


@pytest.fixture(autouse=True)
def _clear_detonations():
    BombTool.detonations.clear()
    yield
    BombTool.detonations.clear()


def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(BombTool())
    return reg


def one_call(call_id: str = "b1") -> list[ModelResponse]:
    return [
        ModelResponse(
            message=Message("assistant", [ToolCall(call_id, "bomb", {})]),
            stop_reason="tool_use",
        ),
        assistant_text("done"),
    ]


def request(tool_name: str = "bomb") -> PermissionRequest:
    return PermissionRequest(tool_name=tool_name, arguments={},
                             summary=f"{tool_name}()", read_only=False)


def sync_agent(gate) -> Agent:
    return Agent(ScriptedProvider(one_call()), model="m",
                 tools=registry(), permissions=gate)


def async_agent(gate, script=None) -> AsyncAgent:
    return AsyncAgent(ScriptedProvider(script or one_call()), model="m",
                      tools=registry(), permissions=gate)


def run_sync(gate) -> list[ToolExecuted]:
    return [e for e in sync_agent(gate).run_streaming("go")
            if isinstance(e, ToolExecuted)]


async def drain(agent: AsyncAgent, text: str = "go") -> list[ToolExecuted]:
    return [e async for e in agent.run_streaming(text)
            if isinstance(e, ToolExecuted)]


def never_answers(asked: asyncio.Event | None = None):
    """A gate that reaches a person who is not at their desk."""
    async def gate(req: PermissionRequest) -> bool:
        if asked is not None:
            asked.set()
        await asyncio.sleep(3600)
        return True
    return gate


# ---- the deadline has no opinion of its own ---------------------------------


def test_the_wrapper_cannot_be_built_without_saying_what_silence_means():
    """The design in one assertion: a stopwatch is mechanism, a verdict on
    silence is policy, and this module owns only the first."""
    with pytest.raises(TypeError):
        with_deadline(allow_read_only, 5)  # type: ignore[call-arg]


def test_an_unknown_verdict_is_a_loud_error_not_a_quiet_deny():
    with pytest.raises(ValueError, match="on_timeout"):
        with_deadline(allow_read_only, 5, on_timeout="maybe")


def test_a_zero_deadline_is_refused_rather_than_read_as_no_deadline():
    """0 means "off" in half the config files in the world; here it would
    mean every question expires before it is asked."""
    with pytest.raises(ValueError, match="does not mean"):
        with_deadline(allow_read_only, 0, on_timeout="deny")
    with pytest.raises(ValueError, match="positive"):
        with_deadline(allow_read_only, -1, on_timeout="deny")


def test_a_deadline_told_to_deny_refuses_and_the_tool_never_runs():
    async def _scenario():
        gate = with_deadline(never_answers(), 0.01, on_timeout="deny")
        executed = await drain(async_agent(gate))
        assert BombTool.detonations == []
        assert executed[0].result.is_error is True
        assert executed[0].refusal == REFUSED_TIMEOUT

    asyncio.run(_scenario())


def test_a_deadline_told_to_allow_goes_ahead_when_nobody_answers():
    """The branch a defaulting library would have taken away: an
    unattended batch whose owner decided silence means proceed."""
    async def _scenario():
        gate = with_deadline(never_answers(), 0.01, on_timeout="allow")
        executed = await drain(async_agent(gate))
        assert BombTool.detonations == ["boom"]
        assert executed[0].result.content == "detonated"
        assert executed[0].refusal is None

    asyncio.run(_scenario())


def test_an_answer_that_arrives_in_time_is_the_gates_own():
    async def _scenario():
        async def gate(req: PermissionRequest) -> bool:
            await asyncio.sleep(0)
            return False

        executed = await drain(
            async_agent(with_deadline(gate, 5, on_timeout="allow")))
        assert BombTool.detonations == []           # the gate's no, not the clock's
        assert executed[0].refusal == REFUSED_UNSPECIFIED

    asyncio.run(_scenario())


def test_a_gate_that_answers_inline_passes_straight_through():
    """A DEADLINE ONLY BINDS A GATE THAT SUSPENDS. A plain function has
    already answered by the time the wrapper sees it -- pinned so the
    limitation cannot quietly become something else."""
    gate = with_deadline(lambda req: True, 0.001, on_timeout="deny")
    assert gate(request()) is True
    executed = run_sync(gate)
    assert BombTool.detonations == ["boom"]
    assert executed[0].refusal is None


# ---- what expiry does to the person who was asked ---------------------------


def test_an_expired_question_is_cancelled_so_the_desk_can_withdraw_it():
    """A chat window must not keep offering a button for a call that can
    no longer happen."""
    async def _scenario():
        withdrawn: list[str] = []

        async def gate(req: PermissionRequest) -> bool:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                withdrawn.append(req.tool_name)
                raise
            return True

        await drain(async_agent(with_deadline(gate, 0.01, on_timeout="deny")))
        assert withdrawn == ["bomb"]

    asyncio.run(_scenario())


def test_a_cancelled_turn_under_a_deadline_is_still_not_a_denial():
    """The dropped connection and the expired clock look alike from here
    and mean opposite things: one is nobody's decision at all."""
    async def _scenario():
        asked = asyncio.Event()
        agent = async_agent(
            with_deadline(never_answers(asked), 30, on_timeout="deny"))
        turn = asyncio.ensure_future(drain(agent))
        await asyncio.wait_for(asked.wait(), timeout=2)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        assert BombTool.detonations == []

    asyncio.run(_scenario())


def test_a_waiting_deadline_does_not_stop_the_event_loop():
    """Asserted as other work MAKING PROGRESS, not as elapsed time -- a
    blocked loop that happens to be slow would pass a clock test."""
    async def _scenario():
        ticks = 0
        done = asyncio.Event()

        async def someone_else() -> None:
            nonlocal ticks
            while not done.is_set():
                ticks += 1
                await asyncio.sleep(0.001)

        other = asyncio.ensure_future(someone_else())
        gate = with_deadline(never_answers(), 0.05, on_timeout="deny")
        executed = await drain(async_agent(gate))
        done.set()
        await other
        assert ticks > 0
        assert executed[0].refusal == REFUSED_TIMEOUT

    asyncio.run(_scenario())


def test_the_timed_out_call_tells_the_model_what_to_do_next():
    """Not politeness: a model told a person refused will argue with the
    person, and a model told nobody answered goes and finds another way."""
    async def _scenario():
        gate = with_deadline(never_answers(), 0.01, on_timeout="deny")
        executed = await drain(async_agent(gate))
        said = executed[0].result.content
        assert "unanswered" in said
        assert "Nobody refused it" in said
        assert REFUSED_TIMEOUT not in said  # the token is not for the model

    asyncio.run(_scenario())


def test_the_wrappers_compose_in_either_order():
    """Deadlines and the existing wrappers are both pass-through, which is
    the payoff for gates having stayed plain functions."""
    async def _scenario():
        inner = SwitchableGate(ask=never_answers())
        gate = with_deadline(inner, 0.01, on_timeout="deny")
        executed = await drain(async_agent(gate))
        assert executed[0].refusal == REFUSED_TIMEOUT

        BombTool.detonations.clear()
        sandbox = type("S", (), {"confined": False})()
        gate = trust_sandbox(
            with_deadline(never_answers(), 0.01, on_timeout="deny"), sandbox)
        executed = await drain(async_agent(gate))
        assert executed[0].refusal == REFUSED_TIMEOUT

    asyncio.run(_scenario())


# ---- the code is a token, and it is for the caller --------------------------


def test_refuse_always_answers_no():
    """The signature is the safety rail: a gate ends `return refuse(...)`
    and cannot write a reason and then approve by accident."""
    req = request()
    assert refuse(req, "not today") is False
    assert req.reason == "not today"
    assert req.code == REFUSED_POLICY


def test_a_sentence_in_the_code_field_is_a_loud_error():
    """Invisible otherwise: it surfaces as a caller whose == never
    matches, months later, in someone else's service."""
    with pytest.raises(ValueError, match="not a token"):
        refuse(request(), "no", code="The user said no.")
    with pytest.raises(ValueError, match="not a token"):
        refuse(request(), "no", code="TIMEOUT")


def test_a_gate_that_names_no_cause_says_so_rather_than_guessing():
    req = request()
    req.reason = "because."
    assert denial_code(req) == REFUSED_UNSPECIFIED


def test_the_built_in_gates_name_their_own_cause():
    unattended = request()
    allow_read_only(unattended)
    assert unattended.code == REFUSED_UNATTENDED

    everything = request()
    deny_all(everything)
    assert everything.code == REFUSED_POLICY


def test_the_terminal_gate_is_the_one_refusal_with_a_person_behind_it():
    """The only gate in the tree where "denied by user" was ever true --
    and it still carries a code, so a caller counting refusals never has
    to guess which of them had a human behind them."""
    import io
    import unittest.mock as mock

    from rich.console import Console
    from rich.prompt import Prompt

    from yantra.cli.repl import confirm_gate

    gate = confirm_gate(Console(file=io.StringIO(), width=200),
                        editor=lambda args: None)
    req = request()
    with mock.patch.object(Prompt, "ask", return_value="n"):
        assert gate(req) is False
    assert req.code == REFUSED_USER
    assert "you said no" in (req.reason or "")


def test_an_approved_call_carries_no_code_anywhere():
    executed = run_sync(lambda req: True)
    assert executed[0].refusal is None
    assert BombTool.detonations == ["boom"]


def test_the_code_reaches_a_consumer_of_the_sync_stream_too():
    """Both twins or neither: a host that switched agents must not lose
    its refusal accounting."""
    executed = run_sync(deny_all)
    assert executed[0].refusal == REFUSED_POLICY
    assert BombTool.detonations == []


def test_one_batchs_refusals_do_not_leak_into_the_next():
    """Two iterations, one refused and one approved. A stale code here
    would report a call that ran as a call that was turned away."""
    seen: list[bool] = []

    def gate(req: PermissionRequest) -> bool:
        seen.append(True)
        if len(seen) == 1:
            return refuse(req, "not the first one", code="first_call")
        return True

    script = [
        ModelResponse(message=Message("assistant", [ToolCall("b1", "bomb", {})]),
                      stop_reason="tool_use"),
        ModelResponse(message=Message("assistant", [ToolCall("b2", "bomb", {})]),
                      stop_reason="tool_use"),
        assistant_text("done"),
    ]
    agent = Agent(ScriptedProvider(script), model="m", tools=registry(),
                  permissions=gate)
    executed = [e for e in agent.run_streaming("go")
                if isinstance(e, ToolExecuted)]
    assert [e.refusal for e in executed] == ["first_call", None]
    assert BombTool.detonations == ["boom"]


def test_a_host_may_name_refusals_the_harness_never_heard_of():
    """The field is a plain str on purpose: a closed enum would mean a
    service cannot name its own refusals without patching this repo."""
    executed = run_sync(
        lambda req: refuse(req, "your plan is out of credit",
                           code="quota_exhausted"))
    assert executed[0].refusal == "quota_exhausted"
    assert executed[0].result.content == "your plan is out of credit"
