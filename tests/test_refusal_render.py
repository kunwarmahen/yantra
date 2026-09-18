"""A refusal is not a crash, and both frontends used to say it was.

The bias: a denied call reaches a consumer as an error ToolResult -- on
purpose, so nothing downstream has to learn a second shape (notes/37) --
and both of this repo's frontends took that at face value and drew a red
"error". A person reading their own transcript was told their tool broke
when what happened is that they said no.

The tests below pin three distinctions that a single red panel collapses:

* refused versus failed (the tool never ran at all),
* refused BY WHOM -- "user" and "timeout" are the difference between a
  decision and an absence,
* and a REPLAYED call, where no code was ever recorded, which must show
  the error result the model actually saw rather than inventing a cause.
"""

from __future__ import annotations

import io

from rich.console import Console

from yantra.agent import ToolExecuted, TurnEnd
from yantra.types import StartEvent, ToolCall, ToolResult
from yantra.cli.render import Renderer


def executed(refusal=None, is_error=True, name="bomb"):
    return ToolExecuted(
        call=ToolCall("c1", name, {}),
        result=ToolResult("c1", "Permission denied by user.", is_error=is_error),
        refusal=refusal)


def render(*events) -> str:
    console = Console(file=io.StringIO(), width=200)
    view = Renderer(console)
    for event in events:
        view(event)
    return console.file.getvalue()


class TestTheTerminal:
    def test_a_refused_call_is_not_labelled_an_error(self):
        out = render(executed(refusal="user"))
        assert "refused: user" in out
        assert "[error]" not in out

    def test_a_crash_is_still_labelled_an_error(self):
        """The distinction only means something if the other half holds."""
        out = render(executed(refusal=None))
        assert "error" in out and "refused" not in out

    def test_the_code_says_which_kind_of_refusal(self):
        """"You said no" and "nobody answered in time" are a decision and
        an absence, and they read identically without this."""
        assert "refused: timeout" in render(executed(refusal="timeout"))
        assert "refused: policy" in render(executed(refusal="policy"))

    def test_a_turn_tallies_its_refusals_by_cause(self):
        out = render(StartEvent(model="m"),
                     executed(refusal="user"),
                     executed(refusal="user"),
                     executed(refusal="timeout"),
                     TurnEnd(response=None, reason="end_turn", iterations=1))
        assert "3 call(s) refused" in out
        assert "timeout 1" in out and "user 2" in out

    def test_a_turn_with_nothing_refused_says_nothing(self):
        out = render(StartEvent(model="m"),
                     executed(refusal=None, is_error=False),
                     TurnEnd(response=None, reason="end_turn", iterations=1))
        assert "refused" not in out

    def test_the_tally_does_not_leak_into_the_next_turn(self):
        out = render(StartEvent(model="m"),
                     executed(refusal="user"),
                     TurnEnd(response=None, reason="end_turn", iterations=1),
                     StartEvent(model="m"),
                     TurnEnd(response=None, reason="end_turn", iterations=1))
        assert out.count("call(s) refused") == 1

    def test_a_turn_that_ended_badly_still_reports_its_refusals(self):
        """over_budget after three refusals is exactly when somebody wants
        to know whether the refusals were theirs."""
        out = render(StartEvent(model="m"),
                     executed(refusal="out_of_time"),
                     TurnEnd(response=None, reason="over_budget",
                             iterations=2, detail="spent $1.00"))
        assert "out_of_time 1" in out


class TestTheBrowser:
    def _sent(self, refusal):
        """Drive the real handler with a fake set of clients: what the
        browser gets is what it can draw with."""
        from yantra.agent import Agent
        from yantra.tools.base import ToolRegistry
        from yantra.web.server import WebSession

        from conftest import ScriptedProvider

        session = WebSession()
        session.attach(Agent(ScriptedProvider([]), model="m",
                             tools=ToolRegistry()), None)
        sent: list[dict] = []
        session.broadcast = sent.append          # type: ignore[method-assign]
        session._emit(executed(refusal=refusal))
        return sent[0]

    def test_the_code_reaches_the_browser(self):
        """A card that cannot tell a refusal from a crash draws a broken
        tool; the payload is where that distinction has to arrive."""
        assert self._sent("timeout")["refusal"] == "timeout"
        assert self._sent("timeout")["is_error"] is True   # unchanged

    def test_a_call_that_really_failed_carries_no_code(self):
        assert self._sent(None)["refusal"] is None

    def test_a_replayed_call_carries_no_invented_code(self):
        """History holds the error result, never the gate's reason for
        it. A reconnect that guessed a cause would be worse than one that
        shows what the model actually saw."""
        import inspect

        from yantra.web import server
        source = inspect.getsource(server.WebSession)
        replay = source.split('"type": "tool_result", "name": call.name')[1]
        assert "refusal" not in replay.split("return out")[0]
