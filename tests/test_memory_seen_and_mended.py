"""What a person sees of their memory, and how they put it right.

The bias these tests encode: A PERSON CAN SEE AND MEND WHAT THE AGENT IS
TOLD ABOUT THEM, AND A MEND NEVER LOSES THE FACT. Before this, a wrong
memory could only be forgotten, and the facts and beliefs that go into
every prompt could not be seen from a chat at all. So: the facts and
beliefs come back asked fresh, with how sure; one is turned down by its
whole id, never a shortened one guessed at; a correction lands in the old
memory's place before the old one goes, so a store that fails half way
keeps both, never neither.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from yantra.memory import Fact, Memory, MemoryStoreError
from yantra.memory.local import LocalStore
from yantra.memory.mcp import McpStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "memory.sqlite")


@pytest.fixture
def memory(store):
    return Memory(store, "asha")


class TestPutRight:
    def test_a_correction_takes_the_old_ones_place_and_its_slot(self, memory, store):
        old = memory.remember("Lives in Cary, NC.", topic="home", key="city")
        new, was = memory.correct(old[:8], "Lives in Apex, NC.")
        items = store.list("asha", 10)
        assert [i.statement for i in items] == ["Lives in Apex, NC."]
        assert was.statement == "Lives in Cary, NC." and items[0].id == new
        assert items[0].slot == "home||city" and items[0].kind == "correction"

    def test_a_memory_with_no_slot_is_replaced_too(self, memory, store):
        old = memory.remember("Likes jazz.")
        memory.correct(old, "Likes bluegrass, not jazz.")
        assert [i.statement for i in store.list("asha", 10)] == ["Likes bluegrass, not jazz."]

    def test_an_unknown_id_or_no_words_changes_nothing(self, memory, store):
        memory.remember("Likes jazz.")
        with pytest.raises(MemoryStoreError, match="no memory #zzz"):
            memory.correct("zzz", "x")
        only = store.list("asha", 10)[0].id
        with pytest.raises(MemoryStoreError, match="instead"):
            memory.correct(only, "  ")
        assert [i.statement for i in store.list("asha", 10)] == ["Likes jazz."]

    def test_when_the_old_one_wont_go_both_stay(self, memory, store, monkeypatch):
        old = memory.remember("Likes jazz.")
        monkeypatch.setattr(store, "forget", lambda user, mid: (_ for _ in ()).throw(
            RuntimeError("disk full")))
        with pytest.raises(RuntimeError):
            memory.correct(old, "Likes bluegrass.")
        assert {i.statement for i in store.list("asha", 10)} == {"Likes jazz.",
                                                                 "Likes bluegrass."}

    def test_someone_elses_memory_is_not_theirs_to_correct(self, store):
        theirs = Memory(store, "priya").remember("Lives in Pune.")
        with pytest.raises(MemoryStoreError):
            Memory(store, "asha").correct(theirs, "Lives in Delhi.")
        assert store.list("priya", 10)[0].statement == "Lives in Pune."


class TestSeen:
    def test_known_asks_the_store_now(self, memory):
        memory.remember("Lives in Cary.", topic="home", key="city")
        assert [f.statement for f in memory.known().facts] == ["Lives in Cary."]

    def test_a_store_with_no_profile_has_nothing_to_show(self):
        bare = SimpleNamespace(name="x", remember=None, recall=None, forget=None, list=None)
        assert Memory(bare, "asha").known() is None

    def test_a_built_in_fact_is_turned_down_by_its_memory_id(self, memory, store):
        memory.remember("Lives in Cary.", topic="home", key="city")
        fact = memory.known().facts[0]
        assert memory.turn_down(fact.id) and store.list("asha", 10) == []


class TestOverMcp:
    def test_how_sure_and_how_often_seen_come_through(self):
        answer = {"facts": [{"id": "fact:location/city", "value": "Lives in Cary",
                             "category": "location", "core": True, "sources": ["e1"],
                             "confidence": 0.85, "seen": 3}],
                  "beliefs": [], "memories": []}
        session = SimpleNamespace(call_tool=lambda name, args, timeout=None:
                                  (json.dumps(answer), False))
        store = McpStore("kosh")
        store.bind(lambda: session, {"profile"})
        got = store.profile("mahen", "")
        assert got.facts == [Fact("fact:location/city", "Lives in Cary", "location", True,
                                  ("e1",), 0.85, 3)]

    def test_a_fact_or_belief_id_goes_to_forget_whole(self):
        sent = []

        def call(name, args, timeout=None):
            sent.append((name, args))
            return json.dumps({"forgotten": True}), False
        store = McpStore("kosh")
        store.bind(lambda: SimpleNamespace(call_tool=call), {"forget"})
        assert Memory(store, "mahen").turn_down("fact:location/city")
        assert sent[0][1]["memory_id"] == "fact:location/city"
