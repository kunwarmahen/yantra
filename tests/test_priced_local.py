"""A local model is free until you price it, and the meter says who spent.

The bias here is two numbers that disagreed about the same run. A price
for a local model in YANTRA_PRICES already made the budget METER it, but
the report and the cost line still called the run free, so a run could
be stopped by a ceiling it had supposedly never approached. The tests
below pin one rule for every caller: an operator's own price wins over
"local is free", and the built-in table never does.

The second bias is a bar that is right and still misleading: the whole
turn's spend in one number, when most of it went to a sub-agent. The
meter now keeps that share apart, and the tests pin that it is PART of
the total rather than added on top.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent
from yantra.budget import Budget
from yantra.eval_report import PriceRecord
from yantra.permissions import yolo
from yantra.pricing import ModelPrice, cost_now, is_free
from yantra.subagent import SPAWN_TOOL_NAME, SpawnSubagent, SubagentSpawner
from yantra.tools.fs import ReadFile
from yantra.trace import TrajectoryLog
from yantra.types import Usage
from yantra.web.server import cost_line

from test_web_server import make_session


@pytest.fixture
def priced_local(tmp_path, monkeypatch):
    """qwen3.8:latest priced by the operator, as the trial prices it."""
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"qwen3.8:latest":
                                  {"input": 1.0, "output": 5.0}}))
    monkeypatch.setenv("YANTRA_PRICES", str(prices))


class TestFreeUntilPriced:
    def test_a_local_model_is_free_by_default(self):
        assert is_free("ollama", "qwen3.8:latest") is True

    def test_your_own_price_makes_it_metered(self, priced_local):
        assert is_free("ollama", "qwen3.8:latest") is False
        assert cost_now(Usage(input_tokens=1_000_000), "ollama",
                        "qwen3.8:latest") == 1.0

    def test_a_built_in_row_never_prices_a_local_model(self):
        """A local tag that shares a prefix with a hosted model is not a
        bill: 'gpt-5-local' matches the built-in gpt-5 family."""
        assert is_free("ollama", "gpt-5-local") is True
        assert cost_now(Usage(input_tokens=1000), "ollama", "gpt-5-local") == 0.0

    def test_a_hosted_provider_is_never_free(self):
        assert is_free("anthropic", "anything") is False


class TestEveryCallerAgrees:
    def test_the_meter(self, priced_local):
        assert Budget.for_model(0.02, provider_name="ollama",
                                model="qwen3.8:latest").metered is True
        assert Budget.for_model(0.02, provider_name="ollama",
                                model="gpt-5-local").metered is False

    def test_the_report(self, priced_local):
        record = PriceRecord.for_model("ollama", "qwen3.8:latest")
        assert (record.source, record.input) == ("YANTRA_PRICES", 1.0)

    def test_the_browser_cost_line(self, priced_local):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="ollama"),
            usage_by_model={"qwen3.8:latest": Usage(input_tokens=1_000_000)})
        assert cost_line(agent) == "~$1.0000"

    def test_the_browser_cost_line_when_free(self):
        agent = SimpleNamespace(
            provider=SimpleNamespace(name="ollama"),
            usage_by_model={"qwen3.8:latest": Usage(input_tokens=1000)})
        assert cost_line(agent) == "$0.00 (local model)"


class TestWhoSpentIt:
    def test_the_owners_calls_are_not_delegated(self):
        owner = object()
        budget = Budget(1.0)
        budget.begin_turn(owner)
        budget.charge(Usage(input_tokens=1000), "claude-sonnet-4-5",
                      spender=owner)
        assert budget.spent > 0 and budget.delegated == 0.0

    def test_a_childs_calls_are_delegated_and_still_in_the_total(self):
        owner, child = object(), object()
        budget = Budget(1.0)
        budget.begin_turn(owner)
        budget.charge(Usage(input_tokens=1000), "claude-sonnet-4-5",
                      spender=owner)
        budget.charge(Usage(input_tokens=3000), "claude-sonnet-4-5",
                      spender=child)
        assert budget.delegated == pytest.approx(budget.spent * 0.75)

    def test_a_new_turn_clears_the_share(self):
        owner, child = object(), object()
        budget = Budget(1.0)
        budget.begin_turn(owner)
        budget.charge(Usage(input_tokens=1000), "claude-sonnet-4-5",
                      spender=child)
        budget.begin_turn(owner)
        assert budget.delegated == 0.0

    def test_a_real_sub_agent_turn_fills_it(self, monkeypatch):
        import yantra.pricing as pricing
        monkeypatch.setitem(pricing._EXACT, "m", ModelPrice(1.0, 5.0))
        spent = Usage(input_tokens=1000, output_tokens=100)
        script = [
            assistant_tool_call("p1", SPAWN_TOOL_NAME, {
                "objective": "say hi", "output_format": "one word",
                "tools_allowed": ["read_file"], "justification": "test"},
                usage=spent),
            assistant_text("hi", usage=spent),            # the child
            assistant_text("the child said hi", usage=spent),
        ]
        agent = Agent(ScriptedProvider(script), model="m", permissions=yolo,
                      budget=Budget(10.0))
        agent.registry.register(ReadFile())
        spawner = SubagentSpawner(agent)
        agent.registry.register(SpawnSubagent(spawner))
        agent.run("delegate")
        assert spawner.results and spawner.results[0].error is None
        assert agent.budget.delegated == pytest.approx(agent.budget.spent / 3)


class TestThePageSaysItIsRecording:
    def test_no_trace_no_chip(self):
        session, _ = make_session([])
        assert session.state()["recording"] is None

    def test_a_trace_is_on_the_page(self, tmp_path):
        session, _ = make_session([])
        session.trace = TrajectoryLog(tmp_path / "t.jsonl", detail="full")
        rec = session.state()["recording"]
        assert rec == {"path": str(tmp_path / "t.jsonl"), "detail": "full",
                       "redacting": 0}

    def test_the_page_says_it_is_scrubbing(self, tmp_path):
        session, _ = make_session([])
        session.trace = TrajectoryLog(tmp_path / "t.jsonl",
                                      redact=["email", "token"])
        assert session.state()["recording"]["redacting"] == 2
