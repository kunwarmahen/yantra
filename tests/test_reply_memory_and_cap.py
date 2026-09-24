"""A fresh agent that remembers, and a reply that cannot overspend.

Two biases, from the two halves of notes/76.

**A forecast that forgets.** Note 73 lets a turn's first call borrow the
previous turn's largest reply -- but only inside one agent, so every eval
case, one-shot run and new process started with nothing. Pinned: the
figure is kept on disk per package and model, read back by a fresh
budget, never written for a ceiling that is not metering, and never a
crash when the file is unreadable or unwritable.

**A cap that is not opt-in, or that throws the answer away.** Limiting a
reply to what the money can buy reverses "a final answer is never
discarded", so it must be off unless the operator asks. When on, the
provider is asked for no more than the money left can buy, a cut-off
answer is KEPT as far as it got, a half-written tool call is never run,
and the turn says it was the cap.
"""

from __future__ import annotations

import json

from conftest import ScriptedProvider, assistant_text
from yantra.agent import Agent, TurnEnd
from yantra.budget import Budget
from yantra.permissions import yolo
from yantra.types import Message, ModelResponse, TextBlock, ToolCall, Usage

PRICED = "claude-sonnet-5"                 # $3 in / $15 out per million


class Owner:
    pass


def remembering(path, ceiling=0.50, key="pkg|claude-sonnet-5"):
    budget, owner = Budget(ceiling), Owner()
    budget.remember(path, key)
    budget.begin_turn(owner)
    return budget, owner


class TestTheMemory:
    def test_a_fresh_agent_starts_from_the_last_turn_anybody_ran(self, tmp_path):
        path = tmp_path / ".yantra" / "replies.json"
        first, owner = remembering(path)
        first.charge(Usage(output_tokens=40_000), PRICED, spender=owner)
        second, owner2 = remembering(path)        # a new process, say
        assert second.last_turn_reply == 40_000
        advice = second.take_warning(owner2, next_input_tokens=50_000,
                                     model=PRICED)
        assert "replies last turn have run to ~40,000 tokens" in advice

    def test_each_package_and_model_is_its_own(self, tmp_path):
        path = tmp_path / "replies.json"
        a, owner = remembering(path, key="a|m")
        a.charge(Usage(output_tokens=9_000), PRICED, spender=owner)
        b, _ = remembering(path, key="b|m")
        assert b.last_turn_reply == 0

    def test_only_the_latest_turn_is_kept(self, tmp_path):
        path = tmp_path / "replies.json"
        budget, owner = remembering(path, ceiling=100.0)
        budget.charge(Usage(output_tokens=40_000), PRICED, spender=owner)
        budget.begin_turn(owner)
        budget.charge(Usage(output_tokens=300), PRICED, spender=owner)
        assert json.loads(path.read_text())["pkg|claude-sonnet-5"] == 300

    def test_an_inert_ceiling_writes_nothing(self, tmp_path):
        path = tmp_path / "replies.json"
        budget, owner = Budget(0.5, metered=False), Owner()
        budget.remember(path, "k")
        budget.begin_turn(owner)
        budget.charge(Usage(output_tokens=40_000), PRICED, spender=owner)
        assert not path.exists()

    def test_a_broken_file_is_nothing_remembered(self, tmp_path):
        path = tmp_path / "replies.json"
        path.write_text("{not json")
        budget, owner = remembering(path)
        assert budget.last_turn_reply == 0
        budget.charge(Usage(output_tokens=10), PRICED, spender=owner)
        assert json.loads(path.read_text()) == {"pkg|claude-sonnet-5": 10}

    def test_an_unwritable_place_is_not_a_crash(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("")
        budget, owner = remembering(blocker / "replies.json")
        budget.charge(Usage(output_tokens=10), PRICED, spender=owner)
        assert budget.largest_reply == 10


class TestTheCapIsOptIn:
    def test_off_it_asks_for_what_was_asked(self):
        budget = Budget(0.01)
        assert budget.reply_cap(next_input_tokens=1_000, model=PRICED,
                                asked=16_384) == 16_384

    def test_on_it_asks_for_what_the_money_left_buys(self):
        budget = Budget(0.10)
        budget.cap_reply = True
        # $0.10 - $0.003 of input leaves $0.097: 6,466 tokens at $15/M.
        assert budget.reply_cap(next_input_tokens=1_000, model=PRICED,
                                asked=16_384) == 6_466

    def test_never_less_than_one_token(self):
        budget = Budget(0.001)
        budget.cap_reply = True
        assert budget.reply_cap(next_input_tokens=1_000_000, model=PRICED,
                                asked=16_384) == 1

    def test_the_startup_line_says_so(self):
        budget = Budget(0.10)
        budget.cap_reply = True
        assert "replies capped at what is left" in budget.describe()


def cut_off(*blocks) -> ModelResponse:
    return ModelResponse(message=Message("assistant", list(blocks)),
                         stop_reason="max_tokens", model=PRICED,
                         usage=Usage(input_tokens=1_000, output_tokens=6_466))


class TestACappedTurn:
    def agent(self, response, *, cap=True):
        provider = ScriptedProvider([response, assistant_text("more")])
        budget = Budget(0.10)
        budget.cap_reply = cap
        return Agent(provider, model=PRICED, budget=budget, permissions=yolo,
                     max_tokens=16_384), provider

    def test_the_provider_is_asked_for_no_more_than_the_money_buys(self):
        agent, provider = self.agent(cut_off(TextBlock("partial")))
        list(agent.run_streaming("go"))
        assert provider.requests[0]["max_tokens"] < 16_384

    def test_a_cut_off_answer_is_kept_and_the_turn_says_why(self):
        agent, _ = self.agent(cut_off(TextBlock("the answer so fa")))
        (end,) = [e for e in agent.run_streaming("go")
                  if isinstance(e, TurnEnd)]
        assert end.reason == "over_budget"
        assert end.response.message.text() == "the answer so fa"
        assert "cut off" in end.detail and "--budget-cap-reply" in end.detail

    def test_a_half_written_tool_call_is_never_run(self):
        ran = []

        from yantra.tools.base import Tool, ToolRegistry

        class Boom(Tool):
            name = "boom"
            description = "boom"
            parameters = {"type": "object", "properties": {}}

            def summary(self, args, ctx):
                return "boom()"

            def run(self, args, ctx):
                ran.append(args)
                return "ran"

        agent, _ = self.agent(cut_off(ToolCall("c1", "boom", {})))
        agent.registry = ToolRegistry()
        agent.registry.register(Boom())
        (end,) = [e for e in agent.run_streaming("go")
                  if isinstance(e, TurnEnd)]
        assert end.reason == "over_budget" and ran == []

    def test_off_a_max_tokens_stop_is_what_it_always_was(self):
        agent, provider = self.agent(cut_off(TextBlock("long")), cap=False)
        (end,) = [e for e in agent.run_streaming("go")
                  if isinstance(e, TurnEnd)]
        assert end.reason == "end_turn"
        assert provider.requests[0]["max_tokens"] == 16_384


def test_the_async_loop_caps_the_same_way():
    import asyncio

    from yantra.async_agent import AsyncAgent

    async def scenario():
        provider = ScriptedProvider([cut_off(TextBlock("half"))])
        budget = Budget(0.10)
        budget.cap_reply = True
        agent = AsyncAgent(provider, model=PRICED, budget=budget,
                           permissions=yolo, max_tokens=16_384)
        ends = [e async for e in agent.run_streaming("go")
                if isinstance(e, TurnEnd)]
        assert ends[0].reason == "over_budget"
        assert provider.requests[0]["max_tokens"] < 16_384

    asyncio.run(scenario())


def test_a_fresh_agents_first_call_counts_its_system_prompt_and_tools():
    """Found in note 76's receipt: "~19 tokens of context" for a request
    that carried a system prompt and a dozen tool schemas."""
    from yantra.tools import default_registry
    agent = Agent(ScriptedProvider([]), model=PRICED, system="x" * 4_000,
                  tools=default_registry())
    messages = [Message("user", [TextBlock("hi")])]
    assert agent._forecast_tokens(messages) > 1_000


def test_the_terminal_says_why_a_capped_reply_stops(capsys):
    """Found in note 76's receipt: an over_budget end that carried a
    response matched no renderer case, and the terminal said nothing."""
    from rich.console import Console

    from yantra.cli.render import Renderer
    console = Console(force_terminal=False, width=200)
    renderer = Renderer(console)
    agent, _ = TestACappedTurn().agent(cut_off(TextBlock("half")))
    for event in agent.run_streaming("go"):
        renderer(event)
    out = capsys.readouterr().out
    assert "turn ended: over_budget" in out and "cut off" in out
