"""Memory about the person: what outlives one conversation (memory/).

The BIAS these tests encode is against the four ways person-memory goes
wrong quietly:

* LEAKING -- one identity's facts reaching another's session, or an
  agent somebody else wrote (a package, an eval) reading them without
  having asked;
* NOT BEING THERE WHEN IT MATTERS -- "flights to Austin" shares no word
  with "lives near RDU", and the fact must reach the prompt anyway,
  without the model having to think of looking;
* COSTING THE TURN -- a store that is down must cost the memory layer,
  never the conversation, and must say so;
* COSTING THE CACHE -- the layer is filled once per conversation, so a
  memory written mid-conversation does not rewrite the prefix under
  the turns that follow.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from rich.console import Console

from conftest import ScriptedProvider, assistant_text, assistant_tool_call

from yantra.agent import Agent
from yantra.async_agent import AsyncAgent
from yantra.cli.repl import Repl
from yantra.errors import ConfigError
from yantra.memory import (Memory, MemoryItem, MemoryStoreError,
                           enable_memory, memory_mode, resolve_user)
from yantra.memory.local import LocalStore, default_path
from yantra.package import load_package
from yantra.permissions import allow_read_only
from yantra.spec import AgentSpec
from yantra.tools.base import ToolRegistry


def allow_all(request) -> bool:
    return True


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "memory.sqlite")


def _agent(store, script, *, user="asha", permissions=allow_all):
    agent = Agent(ScriptedProvider(script), model="m", tools=ToolRegistry(),
                  permissions=permissions)
    enable_memory(agent, store, user=user)
    return agent


class BrokenStore:
    name = "broken"

    def remember(self, *args):
        raise ConnectionError("store is down")

    recall = list = forget = remember


# ---- the local store ---------------------------------------------------------


class TestLocalStore:
    def test_what_is_remembered_comes_back_newest_first(self, store):
        store.remember("asha", "Lives near RDU", {})
        store.remember("asha", "Uses uv, not pip", {"kind": "preference"})
        items = store.list("asha", 10)
        assert [i.statement for i in items] == ["Uses uv, not pip", "Lives near RDU"]
        assert items[0].kind == "preference"

    def test_one_person_never_sees_or_deletes_anothers_memories(self, store):
        mine = store.remember("asha", "Lives near RDU", {})
        assert store.list("ben", 10) == []
        assert store.recall("ben", "RDU", 10) == []
        assert store.forget("ben", mine) is False
        assert len(store.list("asha", 10)) == 1

    def test_saying_it_twice_stores_it_once(self, store):
        first = store.remember("asha", "Lives near RDU", {})
        again = store.remember("asha", "  lives near   rdu ", {})
        assert first == again
        assert len(store.list("asha", 10)) == 1

    def test_recall_matches_words_not_filler(self, store):
        store.remember("asha", "Lives near RDU (Raleigh-Durham airport)", {})
        store.remember("asha", "Vegetarian", {})
        assert [i.statement for i in store.recall("asha", "flights from rdu", 5)] == [
            "Lives near RDU (Raleigh-Durham airport)"]
        # "what", "is", "my" say nothing about which memory is meant
        assert store.recall("asha", "what is my", 5) == []

    def test_forget_removes_it(self, store):
        memory_id = store.remember("asha", "Lives near RDU", {})
        assert store.forget("asha", memory_id) is True
        assert store.list("asha", 10) == []

    def test_the_file_lives_under_the_state_home_not_the_project(self, tmp_path,
                                                               monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        assert default_path() == tmp_path / "state" / "yantra" / "memory.sqlite"


# ---- identity and modes ------------------------------------------------------


class TestWhoAndWhether:
    def test_yantra_user_names_whose_memories_these_are(self):
        assert resolve_user({"YANTRA_USER": " priya "}) == "priya"

    def test_no_identity_means_no_memory_and_no_tools(self, store, monkeypatch):
        monkeypatch.setattr("yantra.memory.resolve_user", lambda: None)
        agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
        assert enable_memory(agent, store) is None
        assert agent.memory is None
        assert "remember" not in agent.registry

    def test_flag_beats_environment_beats_local(self):
        assert memory_mode(None, env={}) == "local"
        assert memory_mode(None, env={"YANTRA_MEMORY": "off"}) == "off"
        assert memory_mode("local", env={"YANTRA_MEMORY": "off"}) == "local"

    def test_an_unknown_mode_is_refused_by_name(self):
        with pytest.raises(ValueError, match="YANTRA_MEMORY='mem0'"):
            memory_mode(None, env={"YANTRA_MEMORY": "mem0"})

    def test_a_package_that_says_nothing_gets_no_memory(self, tmp_path):
        (tmp_path / "agent.toml").write_text('[agent]\nname = "x"\n')
        assert load_package(tmp_path).memory == "off"

    def test_a_package_can_ask_for_it(self, tmp_path):
        (tmp_path / "agent.toml").write_text(
            '[agent]\nname = "x"\n[memory]\nvia = "local"\n')
        assert load_package(tmp_path).memory == "local"

    def test_a_package_naming_a_store_that_is_not_there_fails_at_load(self, tmp_path):
        (tmp_path / "agent.toml").write_text('[memory]\nvia = "somewhere"\n')
        with pytest.raises(ConfigError, match="memory 'somewhere'"):
            load_package(tmp_path)

    def test_the_command_line_wins_over_the_package(self):
        package = AgentSpec(memory="off", root=Path("/pkg"))
        assert package.merge(AgentSpec(memory="local")).memory == "local"


class TestBuild:
    def test_a_spec_asking_for_memory_gets_the_handle_and_both_tools(self, monkeypatch):
        monkeypatch.setenv("YANTRA_USER", "asha")
        agent = AgentSpec(memory="local").build(provider=ScriptedProvider([]),
                                                provider_name="anthropic", model="m")
        assert agent.memory.user == "asha"
        assert {"remember", "recall_memory"} <= set(agent.registry.names())

    def test_a_library_build_that_did_not_say_reads_nothing(self):
        agent = AgentSpec().build(provider=ScriptedProvider([]),
                                  provider_name="anthropic", model="m")
        assert getattr(agent, "memory", None) is None
        assert "remember" not in agent.registry

    def test_the_package_allow_list_governs_the_tools_too(self, monkeypatch):
        monkeypatch.setenv("YANTRA_USER", "asha")
        agent = AgentSpec(memory="local", tool_allow=("read_file",)).build(
            provider=ScriptedProvider([]), provider_name="anthropic", model="m")
        assert agent.memory is not None      # the prompt layer still works
        assert "remember" not in agent.registry
        assert "remember" in agent.registry.refused_names()

    def test_an_eval_never_touches_the_persons_memory(self, monkeypatch):
        from yantra.evals import EvalCase, _agent_for
        monkeypatch.setenv("YANTRA_USER", "asha")
        agent = _agent_for(
            EvalCase(id="c", description="d", user_message="hi"), [], agent_cls=Agent,
            spec=AgentSpec(memory="local"), provider=ScriptedProvider([]),
            model="m", provider_name="anthropic", base_tools=ToolRegistry(),
            permissions=allow_read_only, max_iterations=3,
            context_window=None, cwd=None)
        assert getattr(agent, "memory", None) is None


# ---- the prompt layer --------------------------------------------------------


class TestPromptLayer:
    def test_a_fact_the_first_message_never_mentions_is_there_anyway(self, store):
        # the RDU case: nothing in "flights to Austin" points at an origin
        store.remember("asha", "Lives near RDU (Raleigh-Durham airport)", {})
        agent = _agent(store, [assistant_text("From RDU, as before.")])
        agent.run("find me flights to Austin")
        system = agent.provider.last_request()["system"]
        assert "Lives near RDU" in system
        assert "the current message wins" in system

    def test_what_matches_the_first_message_comes_first(self, store):
        store.remember("asha", "Airport: RDU", {})
        for n in range(3):
            store.remember("asha", f"unrelated fact {n}", {})
        memory = Memory(store, "asha")
        agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
        memory.prime(agent, "which airport?")
        assert memory.in_prompt[0].statement == "Airport: RDU"
        assert len(memory.in_prompt) == 4

    def test_filled_once_so_the_prefix_survives_a_mid_conversation_write(self, store):
        agent = _agent(store, [assistant_text("a"), assistant_text("b")])
        agent.run("hello")
        first = agent.provider.last_request()["system"]
        store.remember("asha", "Lives near RDU", {})
        agent.run("and again")
        assert agent.provider.last_request()["system"] == first

    def test_a_new_conversation_sees_what_the_last_one_kept(self, store):
        agent = _agent(store, [assistant_text("a"), assistant_text("b")])
        agent.run("hello")
        store.remember("asha", "Lives near RDU", {})
        agent.history.clear()                     # /clear
        agent.run("flights to Denver")
        assert "Lives near RDU" in agent.provider.last_request()["system"]

    def test_nothing_remembered_still_says_how_to_remember(self, store):
        agent = _agent(store, [assistant_text("hi")])
        agent.run("hello")
        system = agent.provider.last_request()["system"]
        assert "Nothing yet." in system and "`remember`" in system

    def test_no_memories_and_no_remember_tool_means_no_layer(self, store):
        memory = Memory(store, "asha")
        agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
        memory.prime(agent, "hello")
        assert agent.prompt.get("memory") is None

    def test_a_store_that_is_down_costs_the_layer_not_the_turn(self):
        agent = Agent(ScriptedProvider([assistant_text("fine")]), model="m",
                      tools=ToolRegistry())
        told: list[str] = []
        memory = enable_memory(agent, BrokenStore(), user="asha")
        memory.on_notice = told.append
        assert agent.run("hello").message.text() == "fine"
        assert told and "store is down" in told[0]
        assert "continuing without it" in memory.notice
        assert agent.prompt.get("memory") is None

    def test_the_async_agent_fills_it_too(self, store):
        store.remember("asha", "Lives near RDU", {})
        agent = AsyncAgent(ScriptedProvider([assistant_text("ok")]), model="m",
                           tools=ToolRegistry())
        enable_memory(agent, store, user="asha")
        asyncio.run(agent.run("flights to Denver"))
        assert "Lives near RDU" in agent.provider.last_request()["system"]


# ---- the tools ---------------------------------------------------------------


class TestTools:
    def test_remember_writes_the_statement_for_this_person_only(self, store):
        agent = _agent(store, [
            assistant_tool_call("c1", "remember",
                                {"statement": "Lives near RDU", "kind": "fact"}),
            assistant_text("noted"),
        ])
        agent.run("I live near RDU, remember that")
        assert [i.statement for i in store.list("asha", 5)] == ["Lives near RDU"]
        assert store.list("ben", 5) == []

    def test_remember_asks_first(self, store):
        agent = _agent(store, [
            assistant_tool_call("c1", "remember", {"statement": "Lives near RDU"}),
            assistant_text("ok"),
        ], permissions=allow_read_only)
        agent.run("I live near RDU")
        assert store.list("asha", 5) == []
        assert agent.registry.get("remember").read_only is False

    def test_recall_memory_searches_and_says_when_nothing_is_there(self, store):
        from yantra.memory.tools import RecallMemory
        store.remember("asha", "Vegetarian", {})
        tool = RecallMemory(Memory(store, "asha"))
        assert tool.run({"query": "vegetarian"}, None) == "- Vegetarian"
        assert "ask them" in tool.run({"query": "airport"}, None)

    def test_a_memory_is_a_sentence_not_a_document(self, store):
        memory = Memory(store, "asha")
        with pytest.raises(MemoryStoreError, match="too long"):
            memory.remember("x" * 400)
        with pytest.raises(MemoryStoreError, match="empty"):
            memory.remember("   ")


# ---- /memory in the terminal -------------------------------------------------


class TestReplCommand:
    def _repl(self, store):
        agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
        enable_memory(agent, store, user="asha")
        out = io.StringIO()
        return Repl(agent, Console(file=out, width=120),
                    input_fn=lambda prompt: ""), out

    def test_list_add_find_and_forget_without_a_model_turn(self, store):
        repl, out = self._repl(store)
        repl._command("/memory add I live near RDU")
        repl._command("/memory")
        assert "#1  I live near RDU" in out.getvalue()
        repl._command("/memory find rdu")
        assert "1 match 'rdu'" in out.getvalue()
        repl._command("/memory forget 1")
        assert store.list("asha", 5) == []
        assert "forgot #1" in out.getvalue()

    def test_memory_off_says_why(self):
        agent = Agent(ScriptedProvider([]), model="m")
        out = io.StringIO()
        Repl(agent, Console(file=out, width=120),
             input_fn=lambda p: "")._command("/memory")
        assert "memory is off" in out.getvalue()


# ---- the page ----------------------------------------------------------------


class TestWebPanel:
    @pytest.fixture
    def client(self, store):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app
        session = WebSession()
        agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
        enable_memory(agent, store, user="asha")
        session.attach(agent, None)
        return TestClient(make_app(session))

    def test_the_page_lists_adds_and_forgets(self, client):
        assert client.get("/api/memory").json()["items"] == []
        added = client.post("/api/memory/add",
                            json={"statement": "Lives near RDU"}).json()
        assert [i["statement"] for i in added["items"]] == ["Lives near RDU"]
        memory_id = added["items"][0]["id"]
        gone = client.post("/api/memory/forget", json={"id": memory_id}).json()
        assert gone["items"] == []
        assert client.post("/api/memory/forget",
                           json={"id": memory_id}).status_code == 404

    def test_the_state_carries_the_chip(self, client):
        state = client.get("/api/state").json()
        assert state["memory"]["store"] == "local"
        assert state["memory"]["user"] == "asha"

    def test_an_empty_statement_is_refused_plainly(self, client):
        res = client.post("/api/memory/add", json={"statement": " "})
        assert res.status_code == 400 and "empty" in res.json()["detail"]


def test_memory_item_is_a_plain_record():
    item = MemoryItem("1", "Lives near RDU")
    assert (item.package, item.kind) == (None, None)
