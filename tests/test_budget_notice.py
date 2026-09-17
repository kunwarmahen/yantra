"""Telling the AGENT it is nearly out of budget -- and what it is not told.

Note 36 built the heads-up for the operator and deliberately stopped
there, naming the risk: a model that knows it is metered starts
optimising for the meter. That risk is the bias of this whole file.

So the tests below are mostly about ABSENCE. No dollar figure reaches the
model; no ceiling, no spend, no "you have N tokens left". What reaches it
is a deadline -- stop soon, say what you missed -- because a deadline is a
constraint a model can act on and a number is a quantity it can game.

The second bias is that history must not learn about it. A budget notice
is true of one moment, and history gets replayed, resumed and
checkpointed -- so a notice written into it would be read back next week
by a turn with a full meter, and read back by the model as something the
USER said. Several tests here assert on the provider's REQUEST rather
than on the agent's history, precisely because those two must differ.
"""

from __future__ import annotations

import asyncio


from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent, TurnEnd
from yantra.async_agent import AsyncAgent
from yantra.budget import Budget
from yantra.permissions import yolo
from yantra.tools.base import Tool, ToolRegistry
from yantra.types import TextBlock, Usage

PRICED = "claude-sonnet-5"
#: 100k input tokens of claude-sonnet-5 is exactly thirty cents.
THIRTY_CENTS = Usage(input_tokens=100_000)


class EchoTool(Tool):
    name = "echo"
    description = "echo"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    read_only = True

    def summary(self, args, ctx):
        return "echo()"

    def run(self, args, ctx):
        return "echoed"


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(EchoTool())
    return reg


def _call(cid="c"):
    return assistant_tool_call(cid, "echo", {"text": "hi"},
                               usage=THIRTY_CENTS, model=PRICED)


def _agent(script, *, max_usd=0.40, notify=True, cls=Agent):
    """A turn whose first call spends 30c of a 40c ceiling -- so the
    second iteration is warned."""
    budget = Budget(max_usd, notify_agent=notify)
    provider = ScriptedProvider(script)
    agent = cls(provider, model=PRICED, budget=budget, tools=_registry(),
                permissions=yolo)
    return agent, provider


def _warned_turn(**kwargs):
    """Two iterations: the first spends most of the ceiling, the second is
    the one that gets the notice."""
    return _agent([_call(), assistant_text("done")], **kwargs)


def _sent_text(provider) -> list[str]:
    """Every text block in the LAST request, flattened."""
    return [b.text for m in provider.last_request()["messages"]
            for b in m.content if isinstance(b, TextBlock)]


def run(agent):
    return [e for e in agent.run_streaming("go")]


# ---- what the model is told -----------------------------------------------


def test_a_warned_turn_tells_the_model_to_finish():
    agent, provider = _warned_turn()
    run(agent)
    notice = [t for t in _sent_text(provider) if "budget notice" in t]
    assert len(notice) == 1
    assert "best answer now" in notice[0]
    assert "not start new work" in notice[0].replace("Do ", "")


def test_the_model_is_never_told_a_number():
    """The whole design. A model handed a figure to optimise optimises for
    it, and the two ways it does that are both worse than the work."""
    agent, provider = _warned_turn()
    run(agent)
    (notice,) = [t for t in _sent_text(provider) if "budget notice" in t]
    assert "$" not in notice
    assert "0.40" not in notice and "0.30" not in notice
    for digit in "0123456789":
        assert digit not in notice


def test_the_notice_says_not_to_shorten_the_answer():
    """The obvious wrong reading of 'you are nearly out of budget' is 'be
    brief', which costs the user the one thing they were paying for."""
    agent, provider = _warned_turn()
    run(agent)
    (notice,) = [t for t in _sent_text(provider) if "budget notice" in t]
    assert "not shorten the answer" in notice


def test_nothing_is_told_by_default():
    """Off unless the operator asked: this changes how the model behaves,
    and it is not the package author's call to make."""
    agent, provider = _warned_turn(notify=False)
    events = run(agent)
    assert [t for t in _sent_text(provider) if "budget notice" in t] == []
    # ... and the operator still got theirs
    assert any(type(e).__name__ == "BudgetWarning" for e in events)


def test_an_unwarned_turn_is_told_nothing():
    agent, provider = _agent([assistant_text("done")], max_usd=100.0)
    run(agent)
    assert [t for t in _sent_text(provider) if "budget notice" in t] == []


# ---- and what history is told (nothing) ------------------------------------


def test_the_notice_is_sent_and_never_stored():
    """APPENDED TO WHAT IS SENT, NEVER TO HISTORY. A notice in history is
    read back next week by a turn with a full meter, as something the
    user said."""
    agent, provider = _warned_turn()
    run(agent)
    assert any("budget notice" in t for t in _sent_text(provider))
    stored = [b.text for m in agent.history for b in m.content
              if isinstance(b, TextBlock)]
    assert not any("budget notice" in t for t in stored)


def test_the_notice_rides_in_the_last_user_message_not_a_new_one():
    """Two user messages in a row is a shape some wires reject; the notice
    is one more block in the message already going out."""
    agent, provider = _warned_turn()
    run(agent)
    roles = [m.role for m in provider.last_request()["messages"]]
    assert roles[-1] == "user"
    assert roles.count("user") == len([r for r in roles if r == "user"])
    last = provider.last_request()["messages"][-1]
    assert any("budget notice" in b.text for b in last.content
               if isinstance(b, TextBlock))


def test_a_second_turn_re_sends_nothing_from_the_first():
    """The one-shot re-arms per turn (note 36), and the notice follows it
    -- a fresh turn under a fresh meter says nothing."""
    budget = Budget(100.0, notify_agent=True)
    provider = ScriptedProvider([assistant_text("one"), assistant_text("two")])
    agent = Agent(provider, model=PRICED, budget=budget, tools=_registry(),
                  permissions=yolo)
    agent.run("first")
    agent.run("second")
    assert [t for t in _sent_text(provider) if "budget notice" in t] == []


# ---- the async twin --------------------------------------------------------


def test_the_async_loop_tells_the_model_the_same_thing():
    async def _scenario():
        agent, provider = _warned_turn(cls=AsyncAgent)
        async for _ in agent.run_streaming("go"):
            pass
        (notice,) = [t for t in _sent_text(provider) if "budget notice" in t]
        assert "$" not in notice
        stored = [b.text for m in agent.history for b in m.content
                  if isinstance(b, TextBlock)]
        assert not any("budget notice" in t for t in stored)

    asyncio.run(_scenario())


# ---- the meter, unchanged --------------------------------------------------


def test_telling_the_model_does_not_change_what_the_loop_does():
    """A warned turn goes ahead and crosses anyway (note 36). This feature
    changes what the model KNOWS, never what the loop DOES."""
    told, _ = _agent([_call("a"), _call("b"), assistant_text("done")],
                     max_usd=0.40, notify=True)
    quiet, _ = _agent([_call("a"), _call("b"), assistant_text("done")],
                      max_usd=0.40, notify=False)
    ends_told = [e for e in run(told) if isinstance(e, TurnEnd)]
    ends_quiet = [e for e in run(quiet) if isinstance(e, TurnEnd)]
    assert ends_told[0].reason == ends_quiet[0].reason == "over_budget"


def test_an_inert_ceiling_tells_nobody_anything():
    """A local model bills nothing, so there is nothing to approach --
    and the model must not be told to hurry for no reason."""
    budget = Budget(0.40, metered=False, notify_agent=True)
    provider = ScriptedProvider([_call(), assistant_text("done")])
    agent = Agent(provider, model=PRICED, budget=budget, tools=_registry(),
                  permissions=yolo)
    run(agent)
    assert [t for t in _sent_text(provider) if "budget notice" in t] == []


def test_the_startup_line_says_when_the_agent_is_in_on_it():
    """An operator must not learn that their model was being coached by
    reading a transcript."""
    assert "the agent is told too" in Budget(1.0, notify_agent=True).describe()
    assert "the agent is told" not in Budget(1.0).describe()


def test_for_model_carries_the_flag_through():
    budget = Budget.for_model(1.0, provider_name="anthropic", model=PRICED,
                              notify_agent=True)
    assert budget.notify_agent is True
    assert budget.notice() is not None
