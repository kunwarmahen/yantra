"""Clearing at once, the look back behind it (notes/131).

The BIAS these tests encode is against a look that costs the person a
wait, and against the two ways moving it behind could lose something:

* WAITING ON THE WORD -- /clear on the terminal and the page's clear
  must come back while the model is still looking, not after;
* A QUESTION IN THE WAY -- what the look finds waits for /keep (the
  page's tray), and nothing is kept without a yes;
* LOSING THE COPY -- a look that gives way to the person's next message
  leaves the conversation set aside, to be looked at the next time the
  model is free; and a skill look already running is not thrown away
  by /clear;
* MIXING THE TWO CONVERSATIONS -- a look over a set-aside copy must not
  mark any of the new conversation as looked at.
"""

from __future__ import annotations

import io
import threading

import pytest
from rich.console import Console

from conftest import ScriptedProvider, assistant_text
from test_learned_skills import enable_learning, make_agent, reply, solved_turn

from yantra.agent import Agent
from yantra.cli.repl import Repl
from yantra.memory import enable_memory
from yantra.memory.local import LocalStore
from yantra.memory.reflect import (LookFailed, look_back_ended, reflect,
                                   set_aside)
from yantra.skills.learn import Stopped
from yantra.tools.base import ToolRegistry

RDU = "fact: Lives near RDU (Raleigh-Durham airport)."
STATEMENT = "Lives near RDU (Raleigh-Durham airport)."


class Held(ScriptedProvider):
    """A scripted model whose streamed answers wait for ``go``, so a test
    can act while a look is still in the middle of its call."""

    def __init__(self, script, *, hold_from: int) -> None:
        super().__init__(script)
        self.go = threading.Event()
        self.asked = threading.Event()
        self._calls = 0
        self._hold_from = hold_from

    def stream(self, **kwargs):
        self._calls += 1
        held = self._calls >= self._hold_from
        events = super().stream(**kwargs)
        if held:
            self.asked.set()
            self.go.wait(5)
        yield from events


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "memory.sqlite")


def _agent(store, provider, *, mode="ask"):
    agent = Agent(provider, model="m", tools=ToolRegistry(),
                  permissions=lambda request: True)
    enable_memory(agent, store, user="asha")
    agent.memory.reflect = mode
    return agent


def _repl(agent, answers=()):
    replies = iter(answers)
    out = io.StringIO()
    repl = Repl(agent, Console(file=out, width=120),
                input_fn=lambda prompt: next(replies, ""))
    repl.behind = True
    return repl, out


# ---- the copy ------------------------------------------------------------------


class TestSetAside:
    def test_the_part_not_looked_at_is_copied_and_the_count_starts_over(self, store):
        agent = _agent(store, ScriptedProvider([assistant_text("ok")]))
        list(agent.run_streaming("flights from RDU?"))
        assert set_aside(agent) is True
        agent.history.clear()
        assert len(agent.memory.ended) == 1 and agent.memory.reviewed == 0
        assert "RDU" in agent.memory.ended[0][0].text()

    def test_off_and_nothing_said_set_nothing_aside(self, store):
        agent = _agent(store, ScriptedProvider([assistant_text("ok")]), mode="off")
        list(agent.run_streaming("flights from RDU?"))
        assert set_aside(agent) is False and agent.memory.ended == []
        agent.memory.reflect = "ask"
        agent.history.clear()
        assert set_aside(agent) is False

    def test_a_look_over_the_copy_leaves_the_new_conversation_unlooked_at(self, store):
        agent = _agent(store, ScriptedProvider([assistant_text("ok"),
                                                assistant_text(RDU),
                                                assistant_text("hi")]))
        list(agent.run_streaming("flights from RDU?"))
        set_aside(agent)
        agent.history.clear()
        found = look_back_ended(agent)
        list(agent.run_streaming("hello"))
        assert [c.statement for c in found] == [STATEMENT]
        assert agent.memory.ended == [] and agent.memory.reviewed == 0

    def test_stopped_the_copy_stays_set_aside(self, store):
        agent = _agent(store, ScriptedProvider([assistant_text("ok"),
                                                assistant_text(RDU)]))
        list(agent.run_streaming("flights from RDU?"))
        set_aside(agent)
        with pytest.raises(Stopped):
            look_back_ended(agent, stop=lambda: True)
        assert len(agent.memory.ended) == 1
        assert [c.statement for c in look_back_ended(agent)] == [STATEMENT]

    def test_one_that_fails_goes_and_the_others_are_still_found(self, store):
        agent = _agent(store, ScriptedProvider([assistant_text("ok"),
                                                assistant_text("ok")]))
        list(agent.run_streaming("flights from RDU?"))
        set_aside(agent)
        agent.history.clear()
        list(agent.run_streaming("and to Austin?"))
        set_aside(agent)
        agent.provider.script.append(assistant_text(RDU))   # the first look
        with pytest.raises(LookFailed) as failed:            # nothing for the second
            look_back_ended(agent)
        assert [c.statement for c in failed.value.found] == [STATEMENT]
        assert agent.memory.ended == []

    def test_the_streamed_look_is_closed_when_it_gives_way(self, store):
        agent = _agent(store, ScriptedProvider([assistant_text("ok"),
                                                assistant_text(RDU)]))
        list(agent.run_streaming("flights from RDU?"))
        with pytest.raises(Stopped):
            reflect(agent, stop=lambda: True)
        assert agent.memory.reviewed == 0   # nothing marked looked at


# ---- the terminal ----------------------------------------------------------------


class TestTerminal:
    def test_clear_comes_back_while_the_model_is_still_looking(self, store):
        provider = Held([assistant_text("ok"), assistant_text(RDU)], hold_from=2)
        agent = _agent(store, provider)
        repl, out = _repl(agent, ["a"])
        repl.run_turn("flights from RDU?")
        repl._command("/clear")
        assert provider.asked.wait(5)
        assert agent.history == [] and "history cleared" in out.getvalue()
        assert repl._look is not None and store.list("asha", 5) == []
        provider.go.set()
        repl._wait_look()
        repl._say_found()
        assert "a thing worth remembering about you -- /keep" in out.getvalue()
        assert store.list("asha", 5) == []          # no yes yet
        repl._command("/keep")
        assert [i.statement for i in store.list("asha", 5)] == [STATEMENT]

    def test_a_message_sent_meanwhile_stops_the_look_and_the_copy_waits(self, store):
        provider = Held([assistant_text("ok"), assistant_text(RDU),
                         assistant_text("hi"), assistant_text(RDU)], hold_from=2)
        agent = _agent(store, provider)
        repl, _ = _repl(agent, ["a"])
        repl.run_turn("flights from RDU?")
        repl._command("/clear")
        assert provider.asked.wait(5)
        provider.go.set()
        repl._stop_look()                    # what typing a message does
        assert len(agent.memory.ended) == 1 and agent.memory.pending == []
        repl.run_turn("hello")               # the model is free again after it
        repl._wait_look()
        assert agent.memory.ended == []
        assert [c.statement for c in agent.memory.pending] == [STATEMENT]

    def test_the_skill_question_waits_for_keep(self, tmp_path):
        agent = make_agent(tmp_path, [*solved_turn(), assistant_text(reply())])
        enable_learning(agent, "ask", home=tmp_path / "home")
        repl, out = _repl(agent, ["s"])
        repl.run_turn("greet world")
        repl._wait_look()
        repl._say_found()
        text = out.getvalue()
        assert "Save this as a skill?" not in text
        assert "found a skill worth keeping: greet-someone -- /keep" in text
        repl._command("/keep")
        assert agent.skills.get("greet-someone") is not None

    def test_clear_leaves_a_running_skill_look_alone(self, tmp_path):
        provider_script = [*solved_turn(), assistant_text(reply())]
        agent = make_agent(tmp_path, provider_script)
        enable_learning(agent, "ask", home=tmp_path / "home")
        held = Held(provider_script, hold_from=6)
        agent.provider = held
        repl, _ = _repl(agent, ["s"])
        repl.run_turn("greet world")
        assert held.asked.wait(5)
        repl._command("/clear")
        held.go.set()
        repl._wait_look()
        assert [o.draft.name for _, o in repl._found_skills] == ["greet-someone"]

    def test_quit_waits_for_the_look_and_asks_what_it_found(self, store):
        provider = Held([assistant_text("ok"), assistant_text(RDU)], hold_from=2)
        agent = _agent(store, provider)
        repl, out = _repl(agent, ["a"])
        repl.run_turn("flights from RDU?")
        repl._command("/clear")
        provider.go.set()
        assert repl._command("/quit") is True
        assert "worth remembering about you, for later" in out.getvalue()
        assert "/keep to see" not in out.getvalue()   # asked, not pointed at
        assert [i.statement for i in store.list("asha", 5)] == [STATEMENT]

    def test_auto_keeps_behind_and_says_so_at_the_prompt(self, store):
        agent = _agent(store, ScriptedProvider([assistant_text("ok"),
                                                assistant_text(RDU)]), mode="auto")
        repl, out = _repl(agent)
        repl.run_turn("flights from RDU?")
        repl._command("/clear")
        repl._wait_look()
        repl._say_found()
        assert "remembered 1 about you" in out.getvalue()
        assert len(store.list("asha", 5)) == 1

    def test_keep_with_nothing_waiting_says_so(self, store):
        agent = _agent(store, ScriptedProvider([]))
        repl, out = _repl(agent)
        repl._command("/keep")
        assert "nothing waiting" in out.getvalue()


# ---- the page ---------------------------------------------------------------------


class TestPage:
    @pytest.fixture
    def setup(self, store):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app
        provider = Held([assistant_text("ok"), assistant_text(RDU)], hold_from=2)
        session = WebSession()
        agent = _agent(store, provider)
        session.attach(agent, None)
        list(agent.run_streaming("flights from RDU?"))
        return TestClient(make_app(session)), session, agent, provider

    def test_clear_comes_back_and_the_tray_fills_behind(self, setup, store):
        client, session, agent, provider = setup
        assert client.post("/api/clear").status_code == 200
        assert provider.asked.wait(5)
        assert agent.history == [] and session.looking
        provider.go.set()
        look = session._look
        if look is not None:
            look.join(5)
        waiting = client.get("/api/kept").json()["memories"]
        assert waiting == [{"statement": STATEMENT, "kind": "fact"}]
        assert store.list("asha", 5) == []
