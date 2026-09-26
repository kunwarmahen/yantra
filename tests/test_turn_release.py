"""A turn that is over lets go of what its tools kept alive (notes/92).

The case that asked for it: a headed browser opened to look up a flight
stayed on screen after the answer was written, because only the model
could close it and a model that has its answer does not tidy up. The
loop now calls every tool's ``turn_ended`` as the turn ends. What is
pinned here is WHEN:

* every way a turn ends counts -- an answer, the iteration cap, an
  error, a consumer that closes the stream, and ``run()``, which returns
  on TurnEnd without ever resuming the generator;
* a turn HELD for approval does not -- its resume still needs the page;
* a sub-agent's turn does not -- its tools are its parent's objects.
"""

from __future__ import annotations

import asyncio

import pytest

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent, TurnEnd
from yantra.async_agent import AsyncAgent
from yantra.permissions import PermissionRequest, hold, yolo
from yantra.tools.base import Tool
from yantra.tools.browser import BrowserSession


class Holder(Tool):
    """Stands in for the browser: something alive between calls."""

    name = "hold_thing"
    description = "keeps a thing open"
    parameters = {"type": "object", "properties": {},
                  "additionalProperties": False}

    def __init__(self) -> None:
        self.released = 0

    def summary(self, args, ctx):
        return "hold a thing"

    def run(self, args, ctx):
        return "holding"

    def turn_ended(self) -> None:
        self.released += 1


class Crashy(Holder):
    name = "crashy"

    def turn_ended(self) -> None:
        raise RuntimeError("tidying failed")


def agent(script, *, cls=Agent, permissions=yolo, tools=None):
    a = cls(ScriptedProvider(script), model="m", permissions=permissions)
    for tool in tools or [Holder()]:
        a.registry.register(tool)
    return a


def call_then_answer():
    return [assistant_tool_call("c1", "hold_thing", {}),
            assistant_text("done")]


def holder(a) -> Holder:
    return a.registry.get("hold_thing")


class TestEveryEndReleases:
    def test_an_answered_turn(self):
        a = agent(call_then_answer())
        events = list(a.run_streaming("go"))
        assert events[-1].reason == "end_turn"
        assert holder(a).released == 1

    def test_released_before_the_end_is_seen(self):
        # run() returns on TurnEnd and never resumes the generator, so a
        # release that waited for the generator to finish would wait for
        # garbage collection -- the window-left-open bug again
        a = agent(call_then_answer())
        for event in a.run_streaming("go"):
            if isinstance(event, TurnEnd):
                assert holder(a).released == 1
                break

    def test_run_releases(self):
        a = agent(call_then_answer())
        assert a.run("go").message.text() == "done"
        assert holder(a).released == 1

    def test_the_iteration_cap(self):
        a = agent([assistant_tool_call(f"c{i}", "hold_thing", {})
                   for i in range(3)])
        a.max_iterations = 2
        events = list(a.run_streaming("go"))
        assert events[-1].reason == "max_iterations"
        assert holder(a).released == 1

    def test_a_stream_closed_mid_turn(self):
        a = agent(call_then_answer())
        stream = a.run_streaming("go")
        next(stream)
        stream.close()
        assert holder(a).released == 1

    def test_a_provider_that_fails(self):
        a = agent([])          # the script runs dry on the first request
        with pytest.raises(AssertionError, match="script exhausted"):
            list(a.run_streaming("go"))
        assert holder(a).released == 1

    def test_one_tool_failing_to_let_go_costs_nobody_else(self):
        a = agent(call_then_answer(), tools=[Crashy(), Holder()])
        events = list(a.run_streaming("go"))
        assert events[-1].reason == "end_turn"
        assert holder(a).released == 1


class TestWhatKeepsItsState:
    def test_a_held_turn_keeps_it_until_the_resume_ends(self):
        def gate(request: PermissionRequest):
            return hold(request)
        a = agent(call_then_answer(), permissions=gate)
        events = list(a.run_streaming("go"))
        assert events[-1].reason == "held"
        assert holder(a).released == 0
        list(a.resume({"c1": True}))
        assert holder(a).released == 1

    def test_a_child_never_releases_its_parents_tools(self):
        a = agent(call_then_answer())
        a.clock_turn = "parent-turn"   # what the spawner stamps on a child
        list(a.run_streaming("go"))
        assert holder(a).released == 0


class TestTheAsyncTwin:
    def test_an_answered_turn(self):
        a = agent(call_then_answer(), cls=AsyncAgent)

        async def drive():
            return [e async for e in a.run_streaming("go")]
        events = asyncio.run(drive())
        assert events[-1].reason == "end_turn"
        assert holder(a).released == 1

    def test_a_held_turn_keeps_it(self):
        def gate(request: PermissionRequest):
            return hold(request)
        a = agent(call_then_answer(), cls=AsyncAgent, permissions=gate)

        async def drive():
            return [e async for e in a.run_streaming("go")]
        assert asyncio.run(drive())[-1].reason == "held"
        assert holder(a).released == 0


class TestTheBrowserLetsGo:
    def test_release_closes_an_open_page(self):
        class Page:
            closed = False

            def close(self):
                self.closed = True
        session = BrowserSession(profile=None, executable=None, headed=False)
        session._pw, session._browser = object(), object()
        session._page = page = Page()
        session.release()
        assert page.closed and session._page is None

    def test_release_with_nothing_open_starts_no_thread(self):
        session = BrowserSession(profile=None, executable=None, headed=False)
        session.release()
        assert session._exec is None
