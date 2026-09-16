"""A gate that can wait, and a gate that can say why.

The bias here is two specific untruths the old contract could tell.

The first is silent and expensive: a coroutine object is TRUTHY, so a
loop that calls a gate and believes the answer would approve every
dangerous call in the session the moment someone handed the synchronous
agent an ``async def`` gate. Several tests below assert that the tool
did not RUN, rather than asserting on a verdict -- a test that only read
the ToolResult would keep passing the day the coroutine started being
treated as a yes.

The second is merely wrong: "Permission denied by user." is what the
model was told even when no user existed. These tests pin that a gate's
own reason reaches the model, and that the default gate -- which is what
an unattended run gets -- supplies one saying nobody was asked.

The third thing pinned here is the reason the first one matters at all:
an awaited gate must not stop the event loop. That is asserted as other
work MAKING PROGRESS while a gate is suspended, not as elapsed time.
"""

from __future__ import annotations

import asyncio
import warnings

import pytest

from yantra.agent import Agent, ToolExecuted
from yantra.async_agent import AsyncAgent
from yantra.permissions import (
    PermissionRequest,
    SwitchableGate,
    adecide,
    allow_read_only,
    decide,
    denial_text,
    deny_all,
    trust_sandbox,
)
from yantra.tools.base import Tool, ToolRegistry
from yantra.types import Message, ModelResponse, ToolCall

from conftest import ScriptedProvider, assistant_text


# ---- fixtures ---------------------------------------------------------------


class BombTool(Tool):
    """NOT read_only, and it records the fact that it ran.

    The class attribute is the point: "did the gate approve?" is a
    question about whether this list grew, not about what text came back.
    """

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
    """A turn that calls bomb() once, then says something."""
    return [
        ModelResponse(
            message=Message("assistant", [ToolCall(call_id, "bomb", {})]),
            stop_reason="tool_use",
        ),
        assistant_text("done"),
    ]


def sync_agent(gate) -> Agent:
    return Agent(ScriptedProvider(one_call()), model="m",
                 tools=registry(), permissions=gate)


def async_agent(gate, script=None) -> AsyncAgent:
    return AsyncAgent(ScriptedProvider(script or one_call()), model="m",
                      tools=registry(), permissions=gate)


async def drain(agent: AsyncAgent, text: str = "go") -> list[ToolExecuted]:
    return [e async for e in agent.run_streaming(text)
            if isinstance(e, ToolExecuted)]


# ---- an async gate answers, and the answer is obeyed ------------------------


def test_an_async_gate_that_approves_is_awaited_not_assumed():
    async def _scenario():
        async def gate(request: PermissionRequest) -> bool:
            await asyncio.sleep(0)  # a real suspension point
            return True

        executed = await drain(async_agent(gate))
        assert BombTool.detonations == ["boom"]
        assert executed[0].result.content == "detonated"

    asyncio.run(_scenario())


def test_an_async_gate_that_refuses_stops_the_call():
    async def _scenario():
        async def gate(request: PermissionRequest) -> bool:
            await asyncio.sleep(0)
            return False

        executed = await drain(async_agent(gate))
        assert BombTool.detonations == []
        assert executed[0].result.is_error is True

    asyncio.run(_scenario())


def test_a_plain_function_gate_still_works_on_the_async_agent():
    """Awaitable-TOLERANT, not async-only: every gate written against the
    old signature keeps working, which is what lets one gate serve both
    agents and both frontends."""
    async def _scenario():
        calls: list[str] = []

        def gate(request: PermissionRequest) -> bool:
            calls.append(request.tool_name)
            return True

        await drain(async_agent(gate))
        assert calls == ["bomb"]
        assert BombTool.detonations == ["boom"]

    asyncio.run(_scenario())

# ---- the expensive one: a coroutine is not a yes ----------------------------


def test_an_async_gate_on_the_sync_agent_refuses_rather_than_approves():
    """The whole reason ``decide`` exists. bool(coroutine) is True."""
    async def gate(request: PermissionRequest) -> bool:
        return False  # would deny -- if anyone ever awaited it

    executed = [e for e in sync_agent(gate).run_streaming("go")
                if isinstance(e, ToolExecuted)]
    assert BombTool.detonations == []          # the point
    assert executed[0].result.is_error is True
    assert "permission gate failed" in executed[0].result.content
    assert "AsyncAgent" in executed[0].result.content  # says what to do


def test_refusing_an_async_gate_leaves_no_un_awaited_coroutine():
    """One error, not an error plus a RuntimeWarning nobody can act on."""
    async def gate(request: PermissionRequest) -> bool:
        return True

    request = PermissionRequest(tool_name="bomb", arguments={},
                                summary="bomb()", read_only=False)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        with pytest.raises(TypeError, match="awaitable"):
            decide(gate, request)


def test_decide_accepts_every_gate_written_before_this_change():
    request = PermissionRequest(tool_name="bomb", arguments={},
                                summary="bomb()", read_only=False)
    assert decide(lambda r: True, request) is True
    assert decide(allow_read_only, request) is False


# ---- the blocker: an awaited gate does not stop the world -------------------


def test_a_suspended_gate_does_not_block_the_event_loop():
    """dvara's whole reason for asking. Asserted as OTHER WORK making
    progress while the gate waits -- not as wall-clock time, which would
    pass just as well on a blocked loop that happened to be slow."""
    async def _scenario():
        asked = asyncio.Event()
        answer: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        ticks = 0

        async def gate(request: PermissionRequest) -> bool:
            asked.set()
            return await answer  # a human, somewhere else, taking their time

        async def someone_else() -> None:
            nonlocal ticks
            while not answer.done():
                ticks += 1
                await asyncio.sleep(0.001)

        agent = async_agent(gate)
        turn = asyncio.ensure_future(drain(agent))
        other = asyncio.ensure_future(someone_else())

        await asyncio.wait_for(asked.wait(), timeout=2)
        await asyncio.sleep(0.02)   # the loop keeps running while the gate waits
        assert ticks > 0            # somebody else got served
        answer.set_result(True)

        executed = await asyncio.wait_for(turn, timeout=2)
        await other
        assert BombTool.detonations == ["boom"]
        assert executed[0].result.content == "detonated"

    asyncio.run(_scenario())


def test_gates_in_one_batch_are_awaited_one_at_a_time():
    """Three questions arriving at once in a chat window is not a
    permission prompt, it is a pile."""
    async def _scenario():
        overlap = 0
        live = 0

        async def gate(request: PermissionRequest) -> bool:
            nonlocal overlap, live
            live += 1
            overlap = max(overlap, live)
            await asyncio.sleep(0.01)
            live -= 1
            return True

        batch = ModelResponse(
            message=Message("assistant", [ToolCall(f"b{i}", "bomb", {})
                                          for i in range(3)]),
            stop_reason="tool_use",
        )
        await drain(async_agent(gate, [batch, assistant_text("done")]))
        assert overlap == 1
        assert len(BombTool.detonations) == 3

    asyncio.run(_scenario())


def test_a_cancelled_turn_does_not_become_a_denial():
    """A dropped connection while a gate waits is not the human saying no
    -- it must propagate, or history records a refusal nobody made."""
    async def _scenario():
        asked = asyncio.Event()

        async def gate(request: PermissionRequest) -> bool:
            asked.set()
            await asyncio.sleep(10)
            return True

        agent = async_agent(gate)
        turn = asyncio.ensure_future(drain(agent))
        await asyncio.wait_for(asked.wait(), timeout=2)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        assert BombTool.detonations == []

    asyncio.run(_scenario())

# ---- a refusal that says why ------------------------------------------------


def test_a_gates_reason_reaches_the_model_instead_of_the_default():
    def gate(request: PermissionRequest) -> bool:
        request.reason = "your owner allows bomb only on weekdays."
        return False

    executed = [e for e in sync_agent(gate).run_streaming("go")
                if isinstance(e, ToolExecuted)]
    assert executed[0].result.content == "your owner allows bomb only on weekdays."
    assert executed[0].result.is_error is True


def test_the_reason_survives_the_async_path_too():
    async def _scenario():
        async def gate(request: PermissionRequest) -> bool:
            request.reason = "nobody answered within 60s, so this was refused."
            return False

        executed = await drain(async_agent(gate))
        assert executed[0].result.content.startswith("nobody answered")

    asyncio.run(_scenario())


def test_a_refusal_with_no_reason_still_says_the_old_thing():
    """The default is unchanged for gates that have a user behind them."""
    executed = [e for e in sync_agent(lambda r: False).run_streaming("go")
                if isinstance(e, ToolExecuted)]
    assert executed[0].result.content == "Permission denied by user."


def test_the_default_gate_does_not_blame_a_user_who_was_never_there():
    """allow_read_only is what an UNATTENDED run gets. It used to report a
    human decision that had not happened."""
    request = PermissionRequest(tool_name="bomb", arguments={},
                                summary="bomb()", read_only=False)
    assert allow_read_only(request) is False
    assert "bomb" in denial_text(request)
    assert "nobody is available to ask" in denial_text(request).lower()


def test_an_approved_call_carries_no_reason_anywhere():
    """A reason set on an approval is ignored: only a refusal has one."""
    def gate(request: PermissionRequest) -> bool:
        request.reason = "allowed, and here is a sentence nobody should see"
        return True

    executed = [e for e in sync_agent(gate).run_streaming("go")
                if isinstance(e, ToolExecuted)]
    assert executed[0].result.is_error is False
    assert executed[0].result.content == "detonated"


def test_deny_all_says_what_it_is():
    request = PermissionRequest(tool_name="bomb", arguments={},
                                summary="bomb()", read_only=True)
    assert deny_all(request) is False
    assert "denies every tool call" in denial_text(request)


# ---- the wrappers pass an awaitable through untouched -----------------------


def test_switchable_gate_passes_an_async_ask_through():
    async def _scenario():
        async def ask(request: PermissionRequest) -> bool:
            await asyncio.sleep(0)
            return False

        gate = SwitchableGate(ask=ask)
        request = PermissionRequest(tool_name="bomb", arguments={},
                                    summary="bomb()", read_only=False)
        assert await adecide(gate, request) is False
        gate.set_mode("yolo")           # yolo answers inline, no await needed
        assert await adecide(gate, request) is True

    asyncio.run(_scenario())


def test_trust_sandbox_passes_an_async_inner_through():
    async def _scenario():
        class NotConfined:
            confined = False

        async def inner(request: PermissionRequest) -> bool:
            await asyncio.sleep(0)
            return True

        gate = trust_sandbox(inner, NotConfined())
        request = PermissionRequest(tool_name="bomb", arguments={},
                                    summary="bomb()", read_only=False)
        assert await adecide(gate, request) is True

    asyncio.run(_scenario())

# ---- approve-with-edits still works through a suspension --------------------


def test_an_async_gate_may_still_amend_arguments_before_approving():
    async def _scenario():
        seen: list[dict] = []

        class Echo(Tool):
            name = "echo"
            description = "echoes"
            parameters = {"type": "object", "properties": {"q": {"type": "string"}}}

            def summary(self, args, ctx):
                return f"echo {args}"

            def run(self, args, ctx):
                seen.append(dict(args))
                return "ok"

        reg = ToolRegistry()
        reg.register(Echo())

        async def gate(request: PermissionRequest) -> bool:
            await asyncio.sleep(0)
            request.arguments = {"q": "amended"}
            return True

        script = [
            ModelResponse(
                message=Message("assistant", [ToolCall("e1", "echo", {"q": "original"})]),
                stop_reason="tool_use",
            ),
            assistant_text("done"),
        ]
        agent = AsyncAgent(ScriptedProvider(script), model="m",
                           tools=reg, permissions=gate)
        await drain(agent)
        assert seen == [{"q": "amended"}]

    asyncio.run(_scenario())
