"""Forecasting the reply from the turn's own replies.

The bias here is the unannounced stop. Measured on a thinking-heavy
local model, eight of twenty turns went from comfortably under the
ceiling to over it in ONE call and were never warned: the forecast priced
the request, and the money went out in the reply. The tests pin:

* a turn whose replies run long is warned a call earlier than the input
  alone would say -- including the exact turn that used to be stopped
  without a word;
* the first call of a FRESH agent is forecast on its input alone (there
  is nothing to go on), so nothing is invented -- but a later turn's
  first call uses the previous turn's largest reply (notes/73), and only
  the previous turn's, so one huge answer does not haunt a session;
* a sub-agent's replies do not set the owner's forecast;
* a fresh turn starts with no reply on record;
* the sentence says the replies were counted.
"""

from __future__ import annotations

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent, BudgetWarning, TurnEnd
from yantra.budget import Budget
from yantra.permissions import yolo
from yantra.tools.base import Tool, ToolRegistry
from yantra.types import Usage

PRICED = "claude-sonnet-5"                 # $3 in / $15 out per million
#: $0.03 of input and $0.60 of reply: a turn that spends in its replies.
LONG_REPLY = Usage(input_tokens=10_000, output_tokens=40_000)


class Owner:
    pass


def metered(ceiling=1.00):
    budget, owner = Budget(ceiling), Owner()
    budget.begin_turn(owner)
    return budget, owner


class TestTheMeter:
    def test_a_long_reply_is_expected_again(self):
        """$0.63 spent, $0.15 of context next: $0.78 fits a $1 ceiling on
        the input alone. Another $0.60 reply does not."""
        budget, owner = metered()
        budget.charge(LONG_REPLY, PRICED, spender=owner)
        advice = budget.take_warning(owner, next_input_tokens=50_000,
                                     model=PRICED)
        assert advice is not None
        assert "replies this turn have run to ~40,000 tokens" in advice

    def test_the_first_call_is_forecast_on_its_input_alone(self):
        budget, owner = metered()
        assert budget.largest_reply == 0
        assert budget.take_warning(owner, next_input_tokens=50_000,
                                   model=PRICED) is None

    def test_the_largest_reply_is_kept_not_the_last(self):
        """Under-forecasting is the direction that loses warnings."""
        budget, owner = metered(ceiling=100.0)
        budget.charge(Usage(output_tokens=9_000), PRICED, spender=owner)
        budget.charge(Usage(output_tokens=200), PRICED, spender=owner)
        assert budget.largest_reply == 9_000

    def test_a_sub_agents_replies_are_its_own(self):
        budget, owner = metered()
        budget.charge(LONG_REPLY, PRICED, spender=Owner())   # a child
        assert budget.largest_reply == 0

    def test_a_fresh_turn_has_no_reply_on_record(self):
        budget, owner = metered(ceiling=100.0)
        budget.charge(LONG_REPLY, PRICED, spender=owner)
        budget.begin_turn(owner)
        assert budget.largest_reply == 0

    def test_a_turns_first_call_expects_last_turns_reply(self):
        """notes/73: the gap note 69 left -- the first call of a turn.
        A fresh meter: $0.15 of context fits $0.50; another $0.60 reply
        like last turn's does not."""
        budget, owner = metered(ceiling=0.50)
        budget.charge(LONG_REPLY, PRICED, spender=owner)
        budget.begin_turn(owner)                        # a new turn
        assert budget.largest_reply == 0
        advice = budget.take_warning(owner, next_input_tokens=50_000,
                                     model=PRICED)
        assert advice is not None
        assert "replies last turn have run to ~40,000 tokens" in advice

    def test_this_turns_own_reply_replaces_last_turns(self):
        budget, owner = metered(ceiling=100.0)
        budget.charge(LONG_REPLY, PRICED, spender=owner)
        budget.begin_turn(owner)
        budget.charge(Usage(output_tokens=100), PRICED, spender=owner)
        advice_reply = budget.largest_reply or budget.last_turn_reply
        assert advice_reply == 100

    def test_only_the_previous_turn_not_the_whole_session(self):
        budget, owner = metered(ceiling=100.0)
        budget.charge(LONG_REPLY, PRICED, spender=owner)      # turn 1: huge
        budget.begin_turn(owner)
        budget.charge(Usage(output_tokens=500), PRICED, spender=owner)
        budget.begin_turn(owner)                              # turn 3
        assert budget.last_turn_reply == 500

    def test_without_a_reply_the_sentence_is_the_old_one(self):
        budget, owner = metered(ceiling=0.10)
        advice = budget.take_warning(owner, next_input_tokens=50_000,
                                     model=PRICED)
        assert "before the reply" in advice


class EchoTool(Tool):
    name = "echo"
    description = "echo"
    parameters = {"type": "object", "properties": {}}
    read_only = True

    def summary(self, args, ctx):
        return "echo()"

    def run(self, args, ctx):
        return "echoed"


def test_the_turn_that_used_to_be_stopped_unannounced_is_warned_first():
    """One long reply, then a second like it: $0.63, then $1.26 against a
    $1 ceiling. Priced on input alone, the second call looked like $0.66
    and nobody was told before the stop."""
    registry = ToolRegistry()
    registry.register(EchoTool())
    provider = ScriptedProvider([
        assistant_tool_call("a", "echo", {}, usage=LONG_REPLY, model=PRICED),
        assistant_tool_call("b", "echo", {}, usage=LONG_REPLY, model=PRICED),
        assistant_text("done"),
    ])
    agent = Agent(provider, model=PRICED, budget=Budget(1.00),
                  tools=registry, permissions=yolo)
    events = list(agent.run_streaming("go"))
    warnings = [e for e in events if isinstance(e, BudgetWarning)]
    (end,) = [e for e in events if isinstance(e, TurnEnd)]
    assert end.reason == "over_budget"
    assert len(warnings) == 1 and warnings[0].iterations == 2
    assert events.index(warnings[0]) < events.index(end)


def test_a_ceiling_between_whole_cents_is_said_as_written():
    """Found in note 73's receipt: "~$0.0120 is left of the $0.01 ceiling"
    for a $0.012 ceiling -- two decimals misstated it."""
    budget, owner = metered(ceiling=0.012)
    advice = budget.take_warning(owner, next_input_tokens=50_000,
                                 model=PRICED)
    assert "$0.012 ceiling" in advice
    assert "$0.25 per turn" in Budget(0.25).describe()
