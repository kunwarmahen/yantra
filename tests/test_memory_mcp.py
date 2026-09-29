"""A memory store behind an MCP server (memory/mcp.py).

The server here is a fake session with the same ``call_tool`` surface as
mcp.MCPSession: no subprocess, no network. What is under test is Yantra's
half -- the verb map, what is sent, how answers are read, binding late,
and failing open.
"""

from __future__ import annotations

import json

import pytest

from conftest import ScriptedProvider
from yantra.agent import Agent
from yantra.errors import ConfigError
from yantra.mcp import MCPManager, MCPServerConfig, MCPToolInfo, MCPToolWrapper
from yantra.memory import MemoryStoreError, enable_memory
from yantra.memory.mcp import (DEFAULT_TOOLS, McpStore, bind_memory_server,
                               check_verbs)
from yantra.package import load_package
from yantra.spec import AgentSpec
from yantra.tools.base import ToolRegistry


class FakeServer:
    """A store's MCP server: one person-keyed dict, tools answering JSON
    the way Smritikosh's do."""

    def __init__(self, name="kosh", tools=("remember", "recall", "forget",
                                           "list_memories", "get_context")):
        self.config = MCPServerConfig(name=name, command="fake")
        self.tools = tools
        self.rows: dict[str, tuple[str, str]] = {}    # id -> (user, text)
        self.calls: list[tuple[str, dict]] = []
        self.down = False

    def healthy(self):
        return not self.down

    def close(self):
        pass

    def list_tools(self):
        return [MCPToolInfo(name=t, description=t, input_schema={"type": "object"})
                for t in self.tools]

    def call_tool(self, name, arguments, *, timeout=None):
        from yantra.mcp import MCPError
        if self.down:
            raise MCPError("server went away")
        self.calls.append((name, arguments))
        user = arguments.get("user_id")
        if name == "remember":
            new_id = f"evt-{len(self.rows) + 1}"
            self.rows[new_id] = (user, arguments["statement"])
            return json.dumps({"id": new_id, "statement": arguments["statement"]}), False
        if name == "recall":
            words = set(arguments["query"].lower().split())
            hits = [{"event_id": i, "raw_text": t, "hybrid_score": 0.9}
                    for i, (u, t) in self.rows.items()
                    if u == user and words & set(t.lower().split())]
            return json.dumps({"results": hits, "total": len(hits)}), False
        if name == "list_memories":
            rows = [{"id": i, "statement": t, "created_at": "2026-09-29"}
                    for i, (u, t) in reversed(self.rows.items()) if u == user]
            return json.dumps({"memories": rows}), False
        if name == "forget":
            row = self.rows.get(arguments["memory_id"])
            if row is None or row[0] != user:
                return json.dumps({"forgotten": False}), False
            del self.rows[arguments["memory_id"]]
            return json.dumps({"forgotten": True}), False
        return "no such tool", True


def _bound(server=None, verbs=None):
    server = server or FakeServer()
    store = McpStore(server.config.name, verbs)
    store.bind(lambda: server, set(server.tools))
    return store, server


# ---- the verb map --------------------------------------------------------------


class TestVerbs:
    def test_a_verb_left_out_is_a_tool_of_the_same_name(self):
        assert check_verbs(None) == DEFAULT_TOOLS
        assert DEFAULT_TOOLS["list"] == "list_memories"

    def test_the_map_renames_what_it_names(self):
        assert check_verbs({"remember": "store_statement"})["remember"] == "store_statement"

    def test_an_unknown_verb_is_refused(self):
        with pytest.raises(ValueError, match="unknown memory verb"):
            check_verbs({"ingest": "store_memory"})

    def test_a_package_can_map_its_store(self, tmp_path):
        (tmp_path / "agent.toml").write_text(
            '[memory]\nvia = "kosh"\nverbs = { remember = "store_statement" }\n')
        spec = load_package(tmp_path)
        assert spec.memory == "kosh"
        assert dict(spec.memory_verbs) == {"remember": "store_statement"}

    def test_a_package_with_a_bad_verb_fails_at_load(self, tmp_path):
        (tmp_path / "agent.toml").write_text(
            '[memory]\nvia = "kosh"\nverbs = { ingest = "x" }\n')
        with pytest.raises(ConfigError, match="unknown memory verb"):
            load_package(tmp_path)


# ---- the four verbs, over the wire -------------------------------------------


class TestStore:
    def test_round_trip(self):
        store, _ = _bound()
        memory_id = store.remember("asha", "Lives near RDU", {"kind": "fact"})
        assert memory_id == "evt-1"
        assert [i.statement for i in store.recall("asha", "flights from RDU", 5)] \
            == ["Lives near RDU"]
        assert [i.id for i in store.list("asha", 10)] == ["evt-1"]
        assert store.forget("asha", memory_id) is True
        assert store.list("asha", 10) == []

    def test_whose_memory_is_always_sent(self):
        store, server = _bound()
        store.remember("asha", "Uses uv", {"kind": "preference", "package": None})
        store.recall("asha", "uv", 5)
        store.list("asha", 5)
        store.forget("asha", "evt-1")
        assert all(args["user_id"] == "asha" for _, args in server.calls)
        # None-valued metadata is not sent: the server's schema may refuse it
        assert server.calls[0][1]["metadata"] == {"kind": "preference"}

    def test_one_person_never_sees_anothers(self):
        store, _ = _bound()
        store.remember("asha", "Lives near RDU", {})
        assert store.list("priya", 10) == []
        assert store.forget("priya", "evt-1") is False

    def test_the_map_decides_which_tool_is_called(self):
        server = FakeServer(tools=("store_statement", "recall", "forget",
                                   "list_memories"))
        store, _ = _bound(server, {"remember": "store_statement"})
        server.call_tool = lambda name, args, timeout=None: (
            server.calls.append((name, args)) or (json.dumps({"event_id": "e9"}), False))
        assert store.remember("asha", "Diet: vegetarian", {}) == "e9"
        assert server.calls[-1][0] == "store_statement"

    def test_a_tool_error_is_a_store_error(self):
        store, server = _bound()
        server.call_tool = lambda *a, **k: ("quota exceeded", True)
        with pytest.raises(MemoryStoreError, match="quota exceeded"):
            store.remember("asha", "x", {})

    def test_a_dropped_server_is_a_store_error(self):
        store, server = _bound()
        server.down = True
        with pytest.raises(MemoryStoreError, match="server went away"):
            store.list("asha", 5)


# ---- a server without every verb -----------------------------------------------


class TestMissingVerbs:
    def test_a_verb_the_server_lacks_is_unsupported_not_fatal(self):
        store, _ = _bound(FakeServer(tools=("remember", "recall")))
        assert store.missing == {"forget", "list"}
        with pytest.raises(MemoryStoreError, match="no tool for 'forget'"):
            store.forget("asha", "evt-1")

    def test_the_prompt_layer_fills_from_what_it_can(self):
        store, server = _bound(FakeServer(tools=("remember", "recall")))
        store.remember("asha", "Lives near RDU", {})
        agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
        memory = enable_memory(agent, store, user="asha")
        memory.prime(agent, "flights from RDU to Denver")
        assert memory.notice is None
        assert [i.statement for i in memory.in_prompt] == ["Lives near RDU"]
        assert memory.cannot() == ["forget", "list"]
        assert memory.describe()["cannot"] == ["forget", "list"]


# ---- binding, late, in the host -------------------------------------------------


def _manager_for(agent, server):
    def connector(config, timeout=30.0):
        return server, [MCPToolWrapper(server, info) for info in server.list_tools()]
    return MCPManager(agent.registry, agent=agent, connector=connector)


class TestBinding:
    def _agent(self, monkeypatch, verbs=()):
        monkeypatch.setenv("YANTRA_USER", "asha")
        return AgentSpec(memory="kosh", memory_verbs=verbs).build(
            provider=ScriptedProvider([]), provider_name="anthropic", model="m")

    def test_unbound_it_fails_open_with_a_notice(self, monkeypatch):
        agent = self._agent(monkeypatch)
        assert isinstance(agent.memory.store, McpStore)
        agent.memory.prime(agent, "hello")
        assert "not connected" in agent.memory.notice

    def test_it_binds_to_the_server_the_host_connected(self, monkeypatch):
        agent = self._agent(monkeypatch)
        server = FakeServer()
        manager = _manager_for(agent, server)
        manager.connect(server.config)
        assert bind_memory_server(agent, manager) is None
        agent.memory.remember("Lives near RDU")
        assert server.rows == {"evt-1": ("asha", "Lives near RDU")}

    def test_binding_takes_the_mapped_tools_away_from_the_model(self, monkeypatch):
        agent = self._agent(monkeypatch)
        server = FakeServer()
        manager = _manager_for(agent, server)
        manager.connect(server.config)
        bind_memory_server(agent, manager)
        names = set(agent.registry.names())
        assert not {"mcp__kosh__remember", "mcp__kosh__recall",
                    "mcp__kosh__forget", "mcp__kosh__list_memories"} & names
        assert "mcp__kosh__get_context" in names     # not a verb: stays
        assert {"remember", "recall_memory"} <= names  # Yantra's own road
        manager.shutdown()                             # still closes cleanly

    def test_a_server_nobody_declared_is_said_plainly(self, monkeypatch):
        agent = self._agent(monkeypatch)
        problem = bind_memory_server(agent, _manager_for(agent, FakeServer()))
        assert "'kosh' is not connected" in problem

    def test_a_local_store_is_left_alone(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.setenv("YANTRA_USER", "asha")
        agent = AgentSpec(memory="local").build(
            provider=ScriptedProvider([]), provider_name="anthropic", model="m")
        assert bind_memory_server(agent, _manager_for(agent, FakeServer())) is None
