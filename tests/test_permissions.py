"""Permission-gate tests: the shipped gates, and the gate-as-data contract.

A gate is just Callable[[PermissionRequest], bool] -- these tests pin the
three built-ins and prove a denial reaches the MODEL as an error result
(rather than raising), which is what lets it say "okay, different plan".
Approve-with-edits lives here too: a gate may REPLACE ``arguments``
before approving, and the loop adopts the edited form verbatim.
"""

from __future__ import annotations

import json

import pytest

from conftest import ScriptedProvider, assistant_text
from yantra.agent import Agent
from yantra.permissions import (
    PermissionRequest,
    SwitchableGate,
    allow_read_only,
    deny_all,
    yolo,
)
from yantra.tools.base import Tool, ToolRegistry
from yantra.types import Message, ModelResponse, ToolCall


class ReaderTool(Tool):
    name = "reader"
    description = "read-only"
    parameters = {"type": "object", "properties": {}}
    read_only = True

    def summary(self, args, ctx):
        return "reader()"

    def run(self, args, ctx):
        return "read ok"


class WriterTool(Tool):
    name = "writer"
    description = "mutates things"
    parameters = {"type": "object", "properties": {}}

    def summary(self, args, ctx):
        return "writer()"

    def run(self, args, ctx):
        return "wrote"


def request_for(tool_name: str) -> PermissionRequest:
    return PermissionRequest(
        tool_name=tool_name, arguments={}, summary=f"{tool_name}()",
        read_only=tool_name == "reader",
    )


# ---- the shipped gates ------------------------------------------------------


def test_allow_read_only_auto_approves_safe_tools_only():
    assert allow_read_only(request_for("reader")) is True
    assert allow_read_only(request_for("writer")) is False


def test_yolo_approves_everything():
    assert yolo(request_for("reader")) is True
    assert yolo(request_for("writer")) is True


def test_deny_all_denies_everything():
    assert deny_all(request_for("reader")) is False
    assert deny_all(request_for("writer")) is False


# ---- runtime mode switching (SwitchableGate) ---------------------------------


def test_switchable_gate_ask_mode_delegates_to_inner():
    gate = SwitchableGate(allow_read_only)
    assert gate.mode == "ask"
    assert gate(request_for("reader")) is True   # inner gate's verdict
    assert gate(request_for("writer")) is False


def test_switchable_gate_yolo_mode_approves_everything():
    gate = SwitchableGate(allow_read_only)
    gate.set_mode("yolo")
    assert gate(request_for("writer")) is True


def test_toggle_round_trips_both_ways():
    gate = SwitchableGate(deny_all)
    assert gate.toggle() == "yolo"
    # yolo wins over even deny_all while bypassed ...
    assert gate(request_for("reader")) is True
    assert gate.toggle() == "ask"
    # ... and the moment it flips back, the inner gate rules again
    assert gate(request_for("reader")) is False


def test_set_mode_rejects_unknown_names():
    gate = SwitchableGate(yolo)
    with pytest.raises(ValueError):
        gate.set_mode("YOLO")  # exact names only -- no case-folding guesses
    with pytest.raises(ValueError):
        gate.set_mode("plan")
    assert gate.mode == "ask"  # a failed set leaves the mode untouched


def test_permission_request_arguments_are_editable():
    """The deliberate mutability contract (approve-with-edits): a gate may
    REPLACE arguments before answering. The loop notices via identity."""
    req = request_for("writer")
    req.arguments = {"command": "ls -la"}
    assert req.arguments == {"command": "ls -la"}


# ---- gate -> model contract (one integration pass through the Agent) --------


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ReaderTool())
    reg.register(WriterTool())
    return reg


@pytest.fixture
def script() -> list[ModelResponse]:
    """One batch of two calls (reader + writer), then the final answer."""
    batch = ModelResponse(
        message=Message("assistant", [
            ToolCall(id="r1", name="reader", arguments={}),
            ToolCall(id="w1", name="writer", arguments={}),
        ]),
        stop_reason="tool_use",
    )
    return [batch, assistant_text("got it")]


def test_mixed_batch_allowed_and_denied_as_data(registry, script):
    from yantra.agent import ToolExecuted

    agent = Agent(
        ScriptedProvider(script), model="m",
        tools=registry, permissions=allow_read_only,
    )
    results = [
        e.result for e in agent.run_streaming("go") if isinstance(e, ToolExecuted)
    ]
    # reader auto-approved, writer denied by the gate -- both become data.
    assert [(r.tool_call_id, r.is_error) for r in results] == [("r1", False), ("w1", True)]
    assert results[0].content == "read ok"
    assert "denied" in results[1].content.lower()


# ---- approve-with-edits: gates may amend, loops adopt ------------------------


class EchoTool(Tool):
    """Echoes its arguments back -- proves WHICH args actually ran."""

    name = "echo"
    description = "echoes arguments"
    parameters = {"type": "object", "properties": {}}

    def summary(self, args, ctx):
        return "echo " + json.dumps(args, sort_keys=True)

    def run(self, args, ctx):
        return "ran " + json.dumps(args, sort_keys=True)


def _echo_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    return reg


def _echo_script(arguments: dict) -> list[ModelResponse]:
    batch = ModelResponse(
        message=Message("assistant", [
            ToolCall(id="e1", name="echo", arguments=arguments),
        ]),
        stop_reason="tool_use",
    )
    return [batch, assistant_text("done")]


def test_gate_edited_arguments_are_what_execute():
    from yantra.agent import ToolExecuted

    def editing_gate(request: PermissionRequest) -> bool:
        request.arguments = {"amended": True}  # the human's edit
        return True

    agent = Agent(
        ScriptedProvider(_echo_script({"original": True})), model="m",
        tools=_echo_registry(), permissions=editing_gate,
    )
    results = [
        e.result for e in agent.run_streaming("go") if isinstance(e, ToolExecuted)
    ]
    # The EDITED args executed, not the model's original ones.
    assert results[0].content == 'ran {"amended": true}'
    assert results[0].is_error is False
    # History records the approved (edited) form -- what runs is what's kept.
    call = agent.history[1].tool_calls()[0]
    assert call.arguments == {"amended": True}


def test_gate_edit_then_deny_executes_nothing():
    from yantra.agent import ToolExecuted

    def fickle_gate(request: PermissionRequest) -> bool:
        request.arguments = {"amended": True}
        return False  # ...but ultimately says no

    agent = Agent(
        ScriptedProvider(_echo_script({"original": True})), model="m",
        tools=_echo_registry(), permissions=fickle_gate,
    )
    executed = [
        e for e in agent.run_streaming("go") if isinstance(e, ToolExecuted)
    ]
    assert executed[0].result.is_error is True
    assert "denied" in executed[0].result.content.lower()


def test_summarize_is_supplied_and_rebinds_previews():
    """The loop pre-binds tool.summary into every request so an editing
    UI can re-render the preview without knowing the tool."""
    seen: list[PermissionRequest] = []

    def capturing_gate(request: PermissionRequest) -> bool:
        seen.append(request)
        if request.summarize is not None:
            request.summary = request.summarize(request.arguments)
        return True

    agent = Agent(
        ScriptedProvider(_echo_script({"q": 1})), model="m",
        tools=_echo_registry(), permissions=capturing_gate,
    )
    list(agent.run_streaming("go"))
    assert seen[0].summarize is not None
    assert seen[0].summarize({"q": 2}) == 'echo {"q": 2}'
    assert seen[0].summary == 'echo {"q": 1}'


# ---- the call behind the request --------------------------------------------


def test_a_gate_is_told_which_call_it_is_deciding(registry):
    """The seam a host that RECORDS decisions should not do without.

    Approving or refusing needs nothing from the id. Writing down "you
    approved this call, from your phone, at 14:02" needs it — and the
    alternative is counting, which works right up until it does not:
    gates being sequential, one gate per call, and results arriving in
    submission order are three properties of this loop that no caller was
    ever promised, and a drift in any of them files one person's approval
    against a different call without raising anything.
    """
    seen = []

    def gate(request: PermissionRequest) -> bool:
        seen.append((request.tool_name, request.call_id))
        return True

    provider = ScriptedProvider([
        ModelResponse(
            message=Message("assistant", [
                ToolCall("call-a", "writer", {"text": "one"}),
                ToolCall("call-b", "writer", {"text": "two"}),
            ]),
            stop_reason="tool_use",
        ),
        assistant_text("done"),
    ])
    agent = Agent(provider, model="m", tools=registry, permissions=gate)
    agent.run("write twice")

    assert seen == [("writer", "call-a"), ("writer", "call-b")]


def test_the_id_matches_the_result_the_decision_produced(registry):
    """One call in, one result out, and the id is the thread between them."""
    decided = {}

    def gate(request: PermissionRequest) -> bool:
        decided[request.call_id] = request.tool_name
        return False

    provider = ScriptedProvider([
        ModelResponse(
            message=Message("assistant",
                            [ToolCall("c7", "writer", {"text": "x"})]),
            stop_reason="tool_use",
        ),
        assistant_text("refused, then."),
    ])
    agent = Agent(provider, model="m", tools=registry, permissions=gate)
    agent.run("write")

    results = [b for m in agent.history for b in (m.content or [])
               if type(b).__name__ == "ToolResult"]
    assert [r.tool_call_id for r in results] == list(decided)


def test_a_gate_is_told_which_turn_is_asking(registry):
    """The other thing a stream of questions does not say.

    A gate budgeting how long it may keep somebody waiting has to know
    where one turn ends and the next begins, and the alternatives are
    inference: a gap in timing, a count of calls, a guess. Every one of
    those resets the budget at the wrong moment and none of them raises.
    """
    turns = []

    def gate(request: PermissionRequest) -> bool:
        turns.append(request.turn_id)
        return True

    def script():
        return [
            ModelResponse(
                message=Message("assistant", [
                    ToolCall("a", "writer", {"text": "one"}),
                    ToolCall("b", "writer", {"text": "two"}),
                ]),
                stop_reason="tool_use",
            ),
            assistant_text("done"),
        ]

    agent = Agent(ScriptedProvider(script() + script()), model="m",
                  tools=registry, permissions=gate)
    agent.run("write twice")
    agent.run("and again")

    assert len(turns) == 4
    assert turns[0] == turns[1]           # one turn, two calls
    assert turns[2] == turns[3]
    assert turns[0] != turns[2]           # two turns
    assert all(turns)                     # and never empty inside a turn


def test_two_agents_are_never_the_same_turn(registry):
    """A sub-agent's turn is not its parent's: a budget that confused the
    two would let a child spend the person's patience for them."""
    seen = []

    def gate(request: PermissionRequest) -> bool:
        seen.append(request.turn_id)
        return True

    def one_call():
        return [
            ModelResponse(
                message=Message("assistant",
                                [ToolCall("a", "writer", {"text": "x"})]),
                stop_reason="tool_use",
            ),
            assistant_text("done"),
        ]

    for _ in range(2):
        Agent(ScriptedProvider(one_call()), model="m", tools=registry,
              permissions=gate).run("write")
    assert seen[0] != seen[1]


def test_a_request_built_by_hand_still_works():
    """Defaulted, not required: a host driving a gate directly has no call
    and belongs to no turn."""
    assert request_for("reader").call_id == ""
    assert request_for("reader").turn_id == ""
