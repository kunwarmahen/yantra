"""Facts and beliefs in the prompt: a store's ``profile`` fills the layer.

The bias these tests encode: WHAT THE STORE MADE OF THE MEMORIES REACHES
THE MODEL, NOTHING IS SAID TWICE, AND A GUESS IS NEVER PASSED OFF AS
SOMETHING SAID. Facts are headed by what they are about; beliefs come
under words that call them guesses; a memory a fact covers is left out;
a fact the answer leaned on is reported by its own id; and a store whose
profile fails still gives the memories, as before.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from yantra.memory import Belief, Fact, Memory, MemoryItem, Profile
from yantra.memory.local import LocalStore
from yantra.memory.mcp import McpStore


class ProfileStore:
    """A store with facts and beliefs, and the four verbs doing nothing."""

    name = "kosh"

    def __init__(self, profile=None, fails=False):
        self._profile = profile
        self.fails = fails
        self.used_ids: list[str] = []

    def profile(self, user, query):
        if self.fails:
            raise RuntimeError("graph is down")
        return self._profile

    def remember(self, user, statement, meta):
        return "1"

    def recall(self, user, query, limit):
        return [MemoryItem("m9", "Prefers aisle seats")]

    def forget(self, user, memory_id):
        return True

    def list(self, user, limit):
        return []

    def used(self, user, ids):
        self.used_ids += ids
        return len(ids)


def agent():
    return SimpleNamespace(registry={}, prompt_layers={})


PROFILE = Profile(
    facts=[Fact("fact:location/city", "Lives in Cary, NC; RDU is 12 minutes away",
                "location", True, ("m1",)),
           Fact("fact:finance/ira", "Their Robinhood IRA is 888026630", "finance", False,
                ("m2",))],
    beliefs=[Belief("belief:b1", "prefers a chart to a table", 0.7)],
    memories=[MemoryItem("m3", "Started a pottery class")])


def primed(store, message="flights to Denver"):
    memory = Memory(store, "mahen")
    a = agent()
    memory.prime(a, message)
    return memory, memory.layer_text(a)


class TestTheLayer:
    def test_facts_come_headed_by_what_they_are_about(self):
        _, text = primed(ProfileStore(PROFILE))
        assert "Where they live:\n- Lives in Cary, NC; RDU is 12 minutes away" in text
        assert "Accounts:\n- Their Robinhood IRA is 888026630" in text

    def test_a_belief_is_framed_as_a_guess_with_how_sure(self):
        _, text = primed(ProfileStore(PROFILE))
        assert "guesses drawn from many conversations, not things they said" in text
        assert "- prefers a chart to a table (70% sure)" in text

    def test_the_memories_no_fact_covers_come_after_the_facts(self):
        _, text = primed(ProfileStore(PROFILE))
        assert text.index("Where they live:") < text.index("Other things they told you:")
        assert "- Started a pottery class" in text

    def test_a_failed_profile_still_gives_the_memories_and_says_why(self):
        said = []
        memory = Memory(ProfileStore(fails=True), "mahen")
        memory.on_notice = said.append
        memory.prime(agent(), "seats")
        assert [i.statement for i in memory.in_prompt] == ["Prefers aisle seats"]
        assert memory.facts == [] and "graph is down" in said[0]

    def test_too_many_facts_are_cut_to_the_layers_room(self):
        many = Profile(facts=[Fact(f"fact:tool/t{i}", "x" * 200, "tool") for i in range(40)],
                       beliefs=[], memories=[])
        memory, _ = primed(ProfileStore(many))
        assert 0 < len(memory.facts) < 40


class TestUse:
    def test_a_fact_the_answer_leaned_on_is_reported_by_its_id(self):
        memory, _ = primed(ProfileStore(PROFILE))
        ids = memory.drawn_on("flights to Denver", "Flying out of RDU, 12 minutes from Cary")
        assert "fact:location/city" in ids and "fact:finance/ira" not in ids

    def test_a_memory_behind_a_shown_fact_counts_as_in_the_prompt(self):
        memory, _ = primed(ProfileStore(PROFILE))
        assert {"m1", "m2", "m3"} <= memory.shown_ids()


class TestTheBuiltInStore:
    @pytest.fixture
    def store(self, tmp_path):
        s = LocalStore(tmp_path / "m.sqlite")
        s.remember("asha", "Lives in Cary", {"topic": "home", "slot": "home||city"})
        s.remember("asha", "Brokerage is Fidelity",
                   {"topic": "account", "slot": "account||brokerage"})
        s.remember("asha", "Likes jazz", {})
        return s

    def test_a_memory_with_a_slot_is_a_fact_core_always_the_rest_when_asked(self, store):
        quiet = store.profile("asha", "weather tomorrow")
        assert [f.statement for f in quiet.facts] == ["Lives in Cary"]
        asked = store.profile("asha", "what is in my brokerage")
        assert {f.statement for f in asked.facts} == {"Lives in Cary", "Brokerage is Fidelity"}

    def test_its_memories_are_the_ones_with_no_slot_and_it_has_no_beliefs(self, store):
        got = store.profile("asha", "music")
        assert [m.statement for m in got.memories] == ["Likes jazz"] and got.beliefs == []


class TestOverMcp:
    def test_a_servers_profile_is_read_into_facts_beliefs_and_memories(self):
        answer = {"facts": [{"id": "fact:location/city", "value": "Lives in Cary",
                             "category": "location", "core": True, "sources": ["e1"]}],
                  "beliefs": [{"id": "belief:b1", "statement": "prefers charts",
                               "confidence": 0.7}],
                  "memories": [{"id": "e2", "statement": "Started pottery"}]}
        session = SimpleNamespace(call_tool=lambda name, args, timeout=None:
                                  (json.dumps(answer), False))
        store = McpStore("kosh")
        store.bind(lambda: session, {"profile"})
        got = store.profile("mahen", "hi")
        assert got.facts == [Fact("fact:location/city", "Lives in Cary", "location", True,
                                  ("e1",))]
        assert got.beliefs == [Belief("belief:b1", "prefers charts", 0.7)]
        assert [m.statement for m in got.memories] == ["Started pottery"]
