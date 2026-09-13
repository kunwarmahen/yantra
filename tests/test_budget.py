"""The per-turn dollar ceiling, and the ways it could quietly not be one.

The bias behind every test here: a ceiling that fails to stop something
is worse than no ceiling at all, because the operator who set it has
stopped watching. So most of this file is about silent non-enforcement --
a model nobody can price, a sub-agent spending outside the meter, a
second turn inheriting a meter that was never cleared, a manifest key
that looks configured and is ignored.

The second bias is that stopping must not cost anything already paid
for. A turn that crosses the line ON ITS FINAL ANSWER keeps the answer:
the money is spent either way, and throwing the reply away turns a
budget into a way to waste one.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent, ToolExecuted, TurnEnd
from yantra.async_agent import AsyncAgent
from yantra.budget import Budget
from yantra.errors import ConfigError
from yantra.package import load_package
from yantra.permissions import yolo
from yantra.spec import AgentSpec
from yantra.tools.base import Tool, ToolRegistry
from yantra.types import Usage

#: claude-sonnet-5 lists at $3.00 per 1M input tokens, so 100k input
#: tokens is exactly 30 cents -- round numbers make the assertions below
#: readable as arithmetic rather than as magic.
PRICED = "claude-sonnet-5"
THIRTY_CENTS = Usage(input_tokens=100_000)


class CountingTool(Tool):
    """Records every execution, so 'the tools did not run' is testable."""

    name = "echo"
    description = "echo the text back"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    read_only = True

    def __init__(self) -> None:
        self.runs = 0

    def summary(self, args, ctx):
        return "echo()"

    def run(self, args, ctx):
        self.runs += 1
        return "echoed"


def _registry(tool: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(tool)
    return registry


def _agent(script, *, budget, tool=None, cls=Agent, **kwargs):
    return cls(
        ScriptedProvider(script), model=PRICED, budget=budget,
        tools=_registry(tool) if tool is not None else None,
        permissions=yolo, **kwargs,
    )


def _call(usage: Usage) -> object:
    return assistant_tool_call("c", "echo", {"text": "hi"},
                               usage=usage, model=PRICED)


# ---- the meter ------------------------------------------------------------


def test_charging_a_priced_model_accumulates_list_price_dollars():
    budget = Budget(1.00)
    budget.charge(THIRTY_CENTS, PRICED)
    budget.charge(THIRTY_CENTS, PRICED)
    assert budget.spent == pytest.approx(0.60)
    assert not budget.exceeded()


def test_the_ceiling_trips_once_spending_reaches_it_not_after():
    budget = Budget(0.60)
    budget.charge(THIRTY_CENTS, PRICED)
    assert not budget.exceeded()
    budget.charge(THIRTY_CENTS, PRICED)
    assert budget.exceeded()


def test_a_model_with_no_list_price_stops_the_turn_rather_than_counting_zero():
    # The failure this prevents: a mid-session /model switch to an
    # unpriced slug, after which the meter reads $0.00 forever and the
    # ceiling silently stops existing.
    budget = Budget(10.00)
    budget.charge(Usage(input_tokens=999_999), "some-gateway/mystery-model")
    assert budget.exceeded()
    assert "mystery-model" in budget.explain()


def test_an_inert_ceiling_never_trips_because_a_local_model_bills_nothing():
    budget = Budget(0.01, metered=False)
    budget.charge(Usage(input_tokens=50_000_000), "qwen3:8b")
    assert budget.spent == 0.0
    assert not budget.exceeded()
    assert "local model" in budget.describe()


def test_a_ceiling_of_zero_is_refused_as_a_typo_not_accepted_as_a_budget():
    with pytest.raises(ConfigError, match="refusal to run"):
        Budget(0)
    with pytest.raises(ConfigError, match="refusal to run"):
        Budget(-1.5)


def test_explain_names_both_numbers_so_over_budget_is_an_answer():
    budget = Budget(0.50)
    budget.charge(THIRTY_CENTS, PRICED)
    said = budget.explain()
    assert "$0.3000" in said and "$0.50" in said


# ---- ownership: who is allowed to clear the meter -------------------------


def test_only_the_owning_agent_clears_the_meter_between_turns():
    budget = Budget(1.00)
    parent, child = object(), object()
    budget.begin_turn(parent)          # parent claims it
    budget.charge(THIRTY_CENTS, PRICED)

    budget.begin_turn(child)           # a sub-agent starting its own turn
    assert budget.spent == pytest.approx(0.30), (
        "a sub-agent must not be able to zero its parent's spend")

    budget.begin_turn(parent)          # the parent's NEXT turn
    assert budget.spent == 0.0


def test_a_blind_meter_recovers_when_the_owner_starts_a_fresh_turn():
    budget = Budget(1.00)
    owner = object()
    budget.begin_turn(owner)
    budget.charge(THIRTY_CENTS, "mystery-model")
    assert budget.exceeded()
    budget.begin_turn(owner)
    assert not budget.exceeded()


# ---- construction: what may carry a ceiling at all ------------------------


def test_for_model_meters_a_model_with_a_known_list_price():
    budget = Budget.for_model(0.50, provider_name="anthropic", model=PRICED)
    assert budget.metered


def test_for_model_keeps_an_inert_ceiling_for_a_local_provider():
    # Not an error: an Ollama turn really does cost nothing, and an
    # author's cloud-shaped budget must not make their package
    # unrunnable on somebody else's hardware.
    budget = Budget.for_model(0.50, provider_name="ollama", model="qwen3:8b")
    assert not budget.metered
    assert not budget.exceeded()


def test_a_price_you_wrote_yourself_meters_even_a_local_model(tmp_path,
                                                              monkeypatch):
    """How a ceiling gets rehearsed without pointing it at a real card."""
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"qwen3:8b": {"input": 3.0, "output": 15.0}}))
    monkeypatch.setenv("YANTRA_PRICES", str(prices))
    budget = Budget.for_model(0.50, provider_name="ollama", model="qwen3:8b")
    assert budget.metered
    budget.charge(THIRTY_CENTS, "qwen3:8b")
    assert budget.spent == pytest.approx(0.30)


def test_for_model_refuses_a_metered_model_it_cannot_price():
    with pytest.raises(ConfigError, match="YANTRA_PRICES"):
        Budget.for_model(0.50, provider_name="openai", model="gizmo-9")


def test_a_price_override_makes_an_unknown_model_budgetable(tmp_path,
                                                            monkeypatch):
    """The escape hatch the refusal points at actually works."""
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"gizmo-9": {"input": 2.0, "output": 8.0}}))
    monkeypatch.setenv("YANTRA_PRICES", str(prices))
    budget = Budget.for_model(0.50, provider_name="openai", model="gizmo-9")
    assert budget.metered
    budget.charge(Usage(input_tokens=100_000), "gizmo-9")
    assert budget.spent == pytest.approx(0.20)


# ---- the loop -------------------------------------------------------------


def test_the_loop_stops_between_iterations_once_the_turn_costs_too_much():
    tool = CountingTool()
    agent = _agent([_call(THIRTY_CENTS), _call(THIRTY_CENTS),
                    assistant_text("never reached")],
                   budget=Budget(0.50), tool=tool)
    ends = [e for e in agent.run_streaming("go") if isinstance(e, TurnEnd)]

    assert len(ends) == 1
    assert ends[0].reason == "over_budget"
    assert ends[0].response is None
    assert ends[0].iterations == 2
    assert "$0.6000" in ends[0].detail and "$0.50" in ends[0].detail


def test_the_tools_of_the_iteration_that_broke_the_ceiling_never_run():
    # Stopping AFTER executing them would spend wall-clock time and touch
    # the world for results nothing was going to read.
    tool = CountingTool()
    agent = _agent([_call(THIRTY_CENTS), _call(THIRTY_CENTS)],
                   budget=Budget(0.50), tool=tool)
    events = list(agent.run_streaming("go"))

    assert tool.runs == 1, "only the under-budget iteration's tool ran"
    assert sum(isinstance(e, ToolExecuted) for e in events) == 1


def test_a_budget_stop_leaves_history_valid_for_the_next_request():
    """Same invariant the iteration cap maintains: no unanswered call id."""
    agent = _agent([_call(THIRTY_CENTS), _call(THIRTY_CENTS)],
                   budget=Budget(0.50), tool=CountingTool())
    list(agent.run_streaming("go"))

    called = {c.id for m in agent.history for c in m.tool_calls()}
    answered = {b.tool_call_id for m in agent.history for b in m.content
                if hasattr(b, "tool_call_id")}
    assert called and called <= answered


def test_a_final_answer_is_kept_even_when_it_crosses_the_ceiling():
    """THE rule that keeps a budget from wasting money instead of saving it."""
    agent = _agent([_call(THIRTY_CENTS),
                    assistant_text("here it is", usage=Usage(input_tokens=200_000),
                                   model=PRICED)],
                   budget=Budget(0.50), tool=CountingTool())
    ends = [e for e in agent.run_streaming("go") if isinstance(e, TurnEnd)]

    assert ends[0].reason == "end_turn"
    assert ends[0].response.message.text() == "here it is"
    assert agent.budget.spent == pytest.approx(0.90)


def test_the_meter_is_per_turn_so_a_second_question_starts_clean():
    agent = _agent([assistant_text("one", usage=THIRTY_CENTS, model=PRICED),
                    assistant_text("two", usage=THIRTY_CENTS, model=PRICED)],
                   budget=Budget(0.50))
    agent.run("first")
    assert agent.budget.spent == pytest.approx(0.30)
    agent.run("second")
    assert agent.budget.spent == pytest.approx(0.30), "not 0.60 -- per TURN"


def test_run_surfaces_the_dollars_in_the_error_a_budget_stop_raises():
    agent = _agent([_call(THIRTY_CENTS), _call(THIRTY_CENTS)],
                   budget=Budget(0.50), tool=CountingTool())
    with pytest.raises(RuntimeError, match=r"over_budget.*\$0\.6000"):
        agent.run("go")


def test_without_a_budget_the_loop_behaves_exactly_as_before():
    tool = CountingTool()
    agent = _agent([_call(Usage(input_tokens=5_000_000)),
                    assistant_text("done")], budget=None, tool=tool)
    ends = [e for e in agent.run_streaming("go") if isinstance(e, TurnEnd)]
    assert ends[0].reason == "end_turn"
    assert tool.runs == 1


def test_the_async_loop_stops_on_the_same_ceiling():
    tool = CountingTool()
    agent = _agent([_call(THIRTY_CENTS), _call(THIRTY_CENTS)],
                   budget=Budget(0.50), tool=tool, cls=AsyncAgent)

    async def _collect():
        return [e async for e in agent.run_streaming("go")
                if isinstance(e, TurnEnd)]

    ends = asyncio.run(_collect())
    assert ends[0].reason == "over_budget"
    assert ends[0].iterations == 2
    assert tool.runs == 1


# ---- sub-agents: the obvious way around a ceiling -------------------------


def test_a_sub_agent_charges_its_parents_meter_rather_than_a_fresh_one():
    """Delegation must not be the cheap way around the one real limit."""
    from yantra.subagent import SubagentSpawner

    # One scripted reply, and the CHILD is the one who consumes it.
    parent = _agent([assistant_text("child answer", usage=THIRTY_CENTS,
                                    model=PRICED)],
                    budget=Budget(10.00), tool=CountingTool())
    parent.budget.begin_turn(parent)
    parent.budget.charge(THIRTY_CENTS, PRICED)

    SubagentSpawner(parent).spawn({
        "objective": "look something up",
        "output_format": "one line",
        "justification": "a self-contained lookup",
        "tools_allowed": ["echo"],
    })

    assert parent.budget.spent == pytest.approx(0.60), (
        "0.30 means the child cleared the parent's turn; 0.30 with the "
        "child's own spend missing means it billed a meter nobody reads")


# ---- the manifest ---------------------------------------------------------


def _package(tmp_path, manifest: str):
    (tmp_path / "agent.toml").write_text(manifest)
    return tmp_path


def test_a_manifest_can_declare_a_ceiling():
    spec = AgentSpec(max_usd_per_turn=0.5)
    spec.validate()
    assert spec.max_usd_per_turn == 0.5


def test_the_budget_table_loads_from_agent_toml(tmp_path):
    spec = load_package(_package(tmp_path, '[agent]\nname = "x"\n\n'
                                           '[budget]\nmax_usd_per_turn = 0.25\n'))
    assert spec.max_usd_per_turn == 0.25


def test_a_whole_dollar_written_as_an_integer_is_a_whole_dollar(tmp_path):
    spec = load_package(_package(tmp_path, '[budget]\nmax_usd_per_turn = 2\n'))
    assert spec.max_usd_per_turn == 2.0


def test_a_misspelled_budget_key_is_an_error_not_a_ceiling_that_does_nothing(
        tmp_path):
    with pytest.raises(ConfigError, match="unknown key"):
        load_package(_package(tmp_path,
                              '[budget]\nmax_usd_per_run = 0.25\n'))


def test_a_ceiling_that_is_not_a_number_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="must be a number"):
        load_package(_package(tmp_path,
                              '[budget]\nmax_usd_per_turn = "fifty cents"\n'))


def test_a_zero_or_negative_ceiling_is_refused_against_the_file(tmp_path):
    with pytest.raises(ConfigError, match="greater than zero"):
        load_package(_package(tmp_path, '[budget]\nmax_usd_per_turn = 0\n'))


def test_the_operator_may_raise_the_authors_estimate(tmp_path):
    """Unlike tools.deny, a budget is a guard rail and not a boundary: the
    person paying gets the last word on how much they will pay."""
    package = load_package(_package(tmp_path,
                                    '[budget]\nmax_usd_per_turn = 0.25\n'))
    merged = package.merge(AgentSpec(max_usd_per_turn=2.0))
    assert merged.max_usd_per_turn == 2.0


# ---- the build ------------------------------------------------------------


def test_build_wires_the_ceiling_onto_the_agent():
    agent = AgentSpec(max_usd_per_turn=0.5).build(
        provider=ScriptedProvider([]), provider_name="anthropic", model=PRICED)
    assert agent.budget is not None
    assert agent.budget.max_usd == 0.5


def test_build_refuses_a_ceiling_on_a_model_nobody_can_price():
    with pytest.raises(ConfigError, match="no list price"):
        AgentSpec(max_usd_per_turn=0.5).build(
            provider=ScriptedProvider([]), provider_name="openai",
            model="gizmo-9")


def test_build_against_a_local_provider_keeps_the_ceiling_inert():
    agent = AgentSpec(max_usd_per_turn=0.5).build(
        provider=ScriptedProvider([]), provider_name="ollama",
        model="qwen3:8b")
    assert agent.budget is not None and not agent.budget.metered


def test_no_ceiling_asked_for_means_no_meter_at_all():
    agent = AgentSpec().build(provider=ScriptedProvider([]),
                              provider_name="anthropic", model=PRICED)
    assert agent.budget is None
