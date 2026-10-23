"""A memory in use does not age (memory/__init__.py, ``used``).

The BIAS these tests encode is against two quiet failures:

* AGEING WHAT IS USED -- "lives near RDU", written long ago and leaned on
  every week, ranked and listed like a memory nobody needs, until it
  falls out of the twenty a conversation starts with;
* CALLING SHOWN "USED" -- every fact in the layer is shown on every
  turn; reporting them all would make ``used`` mean nothing, so only
  what the answer (or a tool's arguments) carried past the person's own
  words counts.

And against the old leaks in a new place: one person's ``used`` touching
another's rows, and a store that cannot say ``used`` costing the turn.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from conftest import ScriptedProvider, assistant_text, assistant_tool_call

from yantra.agent import Agent
from yantra.memory import drew_on, enable_memory, note_used, supports
from yantra.memory.local import LocalStore
from yantra.memory.mcp import McpStore
from yantra.tools.base import Tool, ToolRegistry


def allow_all(request) -> bool:
    return True


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "memory.sqlite")


def _wait_for_reports() -> None:
    for thread in threading.enumerate():
        if thread.name == "memory-used":
            thread.join(5)


class SearchFlights(Tool):
    name = "search_flights"
    description = "search flights"
    parameters = {"type": "object", "properties": {"origin": {"type": "string"},
                                                   "to": {"type": "string"}}}
    read_only = True

    def summary(self, args, ctx):
        return "search flights"

    def run(self, args, ctx):
        return "3 flights"


def _agent(store, script, *, user="asha"):
    registry = ToolRegistry()
    registry.register(SearchFlights())
    agent = Agent(ScriptedProvider(script), model="m", tools=registry,
                  permissions=allow_all)
    enable_memory(agent, store, user=user)
    return agent


# ---- what counts as drawn on -----------------------------------------------------


class TestDrewOn:
    def test_a_name_from_the_memory_in_the_answer_counts(self):
        assert drew_on("Lives near RDU", "flights to Denver?",
                       "From RDU, as before -- say if not.")

    def test_a_name_the_person_said_themselves_does_not(self):
        assert not drew_on("Lives near RDU", "flights from RDU to Denver?",
                           "Three flights from RDU.")

    def test_two_plain_words_count_and_one_does_not(self):
        assert drew_on("Prefers aisle seats", "book it", "I picked an aisle seat for you.")
        assert not drew_on("Prefers aisle seats", "book it", "Your seat is 14C.")

    def test_a_word_the_person_said_leaves_the_rest_to_decide(self):
        # the live run on qwen3.8: "which seat should I ask for?"
        assert drew_on("Prefers aisle seats", "which seat should I ask for?",
                       "Ask for an aisle seat, as you prefer.")
        assert not drew_on("Prefers aisle seats", "which aisle seat?", "The aisle seat.")

    def test_a_memory_of_one_word_needs_only_that_word(self):
        assert drew_on("Is vegetarian", "dinner ideas?", "Here are vegetarian options.")

    def test_the_verbs_every_memory_starts_with_say_nothing(self):
        assert not drew_on("Uses uv, not pip", "hi", "The tool uses a cache.")

    def test_a_number_is_a_name(self):
        assert drew_on("Runs Ollama on a 4090", "is my gpu enough?",
                       "Your 4090 has 24 GB.")


# ---- the turn reports what it drew on, behind the answer --------------------------


class TestReported:
    def test_the_answer_reports_only_the_memory_it_leaned_on(self, store):
        rdu = store.remember("asha", "Lives near RDU", {})
        store.remember("asha", "Prefers aisle seats", {})
        agent = _agent(store, [assistant_text("From RDU, as before -- three flights.")])

        agent.run("flights to Denver")
        _wait_for_reports()

        used = {item.id: item.used for item in store.list("asha", 10)}
        assert used[rdu] != ""
        assert sum(1 for when in used.values() if when) == 1

    def test_a_tool_call_that_carries_it_counts(self, store):
        rdu = store.remember("asha", "Lives near RDU", {})
        agent = _agent(store, [
            assistant_tool_call("c1", "search_flights", {"origin": "RDU", "to": "DEN"}),
            assistant_text("Three flights tomorrow morning."),
        ])

        agent.run("flights to Denver")
        _wait_for_reports()

        assert store.list("asha", 1)[0].id == rdu
        assert store.list("asha", 1)[0].used

    def test_what_recall_memory_found_counts_too(self, store):
        for i in range(25):                      # more than the layer carries
            store.remember("asha", f"Filler fact number {i}", {})
        store.remember("asha", "Allergic to peanuts", {})
        agent = _agent(store, [
            assistant_tool_call("c1", "recall_memory", {"query": "allergies"}),
            assistant_text("Skip the satay: you're allergic to peanuts."),
        ])
        agent.memory.in_prompt = []              # as if the layer had missed it
        agent.memory.primed = True

        agent.run("what should I order?")
        _wait_for_reports()

        top = store.list("asha", 1)[0]
        assert top.statement == "Allergic to peanuts" and top.used

    def test_one_report_per_memory_per_conversation(self, store):
        store.remember("asha", "Lives near RDU", {})
        agent = _agent(store, [assistant_text("From RDU."), assistant_text("RDU again.")])
        calls = []
        real = store.used
        store.used = lambda user, ids: calls.append(ids) or real(user, ids)

        agent.run("flights to Denver")
        _wait_for_reports()
        agent.run("and to Austin?")
        _wait_for_reports()

        assert len(calls) == 1

    def test_a_store_without_used_costs_nothing(self):
        class FourVerbs:
            name = "four"
            def remember(self, user, statement, meta): return "1"
            def recall(self, user, query, limit): return []
            def forget(self, user, memory_id): return True
            def list(self, user, limit): return []

        agent = Agent(ScriptedProvider([assistant_text("ok")]), model="m",
                      tools=ToolRegistry())
        enable_memory(agent, FourVerbs(), user="asha")

        assert not supports(agent.memory.store, "used")
        assert agent.run("hi").message.text() == "ok"
        assert note_used(agent) is None

    def test_a_failing_used_is_said_once_and_the_turn_goes_on(self, store):
        store.remember("asha", "Lives near RDU", {})
        agent = _agent(store, [assistant_text("From RDU."), assistant_text("RDU.")])
        told = []
        agent.memory.on_notice = told.append

        def broken(user, ids):
            raise RuntimeError("store is down")
        store.used = broken

        agent.run("flights to Denver")
        _wait_for_reports()
        agent.memory.marked.clear()              # let it try again
        agent.run("and Austin?")
        _wait_for_reports()

        assert len(told) == 1 and "store is down" in told[0]


# ---- the built-in store ------------------------------------------------------------


class TestLocalStore:
    def test_a_used_memory_is_listed_first_however_old(self, store):
        old = store.remember("asha", "Lives near RDU", {})
        newer = store.remember("asha", "Started a pottery class", {})
        for memory_id, written in ((old, "2025-01-01"), (newer, "2026-09-01")):
            store._db.execute("UPDATE memories SET created_at = ? WHERE id = ?",
                              (f"{written}T00:00:00+00:00", memory_id))

        assert store.used("asha", [old]) == 1

        assert [i.id for i in store.list("asha", 2)][0] == old

    def test_another_persons_ids_are_not_touched(self, store):
        theirs = store.remember("ravi", "Lives in Pune", {})

        assert store.used("asha", [theirs]) == 0
        assert store.list("ravi", 1)[0].used == ""

    def test_a_file_from_before_gains_the_column(self, tmp_path):
        path = tmp_path / "old.sqlite"
        db = sqlite3.connect(path)
        db.executescript(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT "
            "NOT NULL, statement TEXT NOT NULL, norm TEXT NOT NULL, kind TEXT, "
            "package TEXT, created_at TEXT NOT NULL);"
            "INSERT INTO memories (user, statement, norm, created_at) VALUES "
            "('asha', 'Lives near RDU', 'lives near rdu', '2025-01-01T00:00:00+00:00');")
        db.commit()
        db.close()

        store = LocalStore(path)

        assert store.used("asha", ["1"]) == 1
        assert store.list("asha", 1)[0].used


# ---- over MCP ------------------------------------------------------------------------


class UsedServer:
    def __init__(self):
        self.calls = []

    def call_tool(self, name, arguments, *, timeout=None):
        import json
        self.calls.append((name, arguments))
        if name == "list_memories":
            return json.dumps({"memories": [{"id": "evt-1", "statement": "Lives near RDU",
                                             "created_at": "2025-01-01",
                                             "last_used_at": "2026-10-20T08:00:00"}]}), False
        return json.dumps({"marked": len(arguments["memory_ids"])}), False


class TestOverMcp:
    def test_used_sends_the_ids_and_whose_they_are(self):
        server = UsedServer()
        store = McpStore("kosh")
        store.bind(lambda: server, {"remember", "recall", "forget", "list_memories", "used"})

        assert store.used("asha", ["evt-1", "evt-2"]) == 2
        assert server.calls[-1] == ("used", {"memory_ids": ["evt-1", "evt-2"],
                                             "user_id": "asha"})

    def test_a_listed_memory_carries_when_it_was_last_used(self):
        server = UsedServer()
        store = McpStore("kosh")
        store.bind(lambda: server, {"list_memories"})

        assert store.list("asha", 5)[0].used == "2026-10-20T08:00:00"

    def test_a_server_without_used_is_simply_not_told(self):
        store = McpStore("kosh")
        store.bind(lambda: UsedServer(), {"remember", "recall", "forget", "list_memories"})

        assert not supports(store, "used")
