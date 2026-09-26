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
import time

import pytest

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent, TurnEnd
from yantra.config import browser_close_policy
from yantra.errors import ConfigError
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
        session = BrowserSession(profile=None, executable=None, headed=False,
                                 close_after=0.0)
        session._pw, session._browser = object(), object()
        session._page = page = Page()
        session.release()
        assert page.closed and session._page is None

    def test_release_with_nothing_open_starts_no_thread(self):
        session = BrowserSession(profile=None, executable=None, headed=False,
                                 close_after=0.0)
        session.release()
        assert session._exec is None


class _Page:
    closed = False

    def close(self):
        self.closed = True


def _open_session(close_after):
    """A session that believes it launched, around a page that records
    whether it was closed -- and a worker, as a real open leaves one."""
    session = BrowserSession(profile=None, executable=None, headed=False,
                             close_after=close_after)
    session._pw, session._browser = object(), object()
    session._page = page = _Page()
    session._call(lambda: None)        # starts the worker, as open() does
    return session, page


def _wait_for(check, seconds=2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return check()


class TestThePolicyIsYours:
    """YANTRA_BROWSER_CLOSE: closing at the turn's end leaves no window,
    and costs a follow-up its page. Neither is right for everyone."""

    @pytest.mark.parametrize("raw, expected", [
        ("", 0.0), ("turn", 0.0), ("model", None), ("never", None),
        ("300", 300.0), ("2.5", 2.5)])
    def test_the_setting_reads(self, monkeypatch, raw, expected):
        monkeypatch.setenv("YANTRA_BROWSER_CLOSE", raw)
        assert browser_close_policy() == expected

    @pytest.mark.parametrize("raw", ["0", "-5", "soon"])
    def test_a_setting_that_means_nothing_is_refused(self, monkeypatch, raw):
        monkeypatch.setenv("YANTRA_BROWSER_CLOSE", raw)
        with pytest.raises(ConfigError, match="YANTRA_BROWSER_CLOSE"):
            browser_close_policy()

    def test_model_leaves_it_open(self):
        session, page = _open_session(None)
        session.release()
        assert not page.closed and session._page is page

    def test_idle_keeps_the_page_for_the_next_turn(self):
        session, page = _open_session(60.0)
        session.release()
        assert not page.closed and session._page is page
        session._idle.cancel()

    def test_idle_closes_once_nobody_used_it(self):
        session, page = _open_session(0.05)
        session.release()
        assert _wait_for(lambda: page.closed)
        assert session._page is None

    def test_a_turn_that_uses_it_stops_the_idle_close(self):
        session, page = _open_session(0.05)
        session.release()
        session._call(lambda: None)    # the next turn's first verb
        time.sleep(0.2)
        assert not page.closed

    def test_an_idle_close_overtaken_on_the_worker_stands_down(self):
        # the timer checked, then a verb got in before its close ran:
        # the close must see it was overtaken rather than pull the page
        session, page = _open_session(60.0)
        session.release()
        armed = session._uses
        session._idle.cancel()
        session._call(lambda: None)
        session._idle_close(armed)
        session._call(lambda: None)    # drain the worker
        assert not page.closed
