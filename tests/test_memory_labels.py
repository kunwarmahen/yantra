"""A memory's topic, its slot, and what follows: replacing and lapsing.

The bias these tests encode: TWO MEMORIES THAT CANNOT BOTH BE TRUE MUST
NOT BOTH COME BACK, and NOTHING GOES SILENTLY OR WRONGLY. A new home city
replaces the old one; a fact with no key -- their stairs light, filed
under home -- replaces nothing; a second preference does not replace the first;
whatever was let go is said beside what was kept; a store that fails
half way keeps both rather than neither; a project nobody has used for
months goes, an account read every morning does not; and a model that
leaves the topic out of a look-back line still has its fact kept.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from yantra.memory import TOPICS, Memory, MemoryItem, label, slot
from yantra.memory.local import LocalStore
from yantra.memory.mcp import _items
from yantra.memory.reflect import Candidate, keep_lines, parse_reply
from yantra.memory.tools import Remember
from yantra.tools.base import ToolContext


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "memory.sqlite")


@pytest.fixture
def memory(store):
    return Memory(store, "asha")


class TestSlot:
    def test_only_a_key_makes_a_slot(self):
        assert slot("home") is None
        assert slot("home", key="city") == "home||city"
        assert slot("account", key="Brokerage") == "account||brokerage"

    def test_who_and_which_are_normalised(self):
        assert slot("contact", "Their daughter", "E-mail") == "contact|their_daughter|e_mail"

    def test_no_topic_or_an_unknown_one_has_no_slot(self):
        assert slot(None, key="city") is None
        assert slot("weather", key="today") is None

    def test_every_topic_has_a_lifetime_entry(self):
        assert TOPICS["home"] is None and TOPICS["project"] == 60

    def test_a_label_shows_topic_who_and_key(self):
        assert label(MemoryItem("1", "x", topic="contact", slot="contact|ravi|email")) \
            == "[contact/ravi.email] "
        assert label(MemoryItem("1", "x", topic="tool")) == "[tool] "
        assert label(MemoryItem("1", "x")) == ""


class TestReplacing:
    def test_a_new_home_replaces_the_old_and_says_so(self, memory, store):
        old = memory.remember("Lives in Cary, NC (Eastern time).", topic="home", key="city")
        new, replaced = memory.remember_replacing(
            "Lives in Austin, TX (Central time).", topic="home", key="city")
        assert [i.statement for i in replaced] == ["Lives in Cary, NC (Eastern time)."]
        assert [i.id for i in store.list("asha", 10)] == [new]
        assert old != new

    def test_a_home_fact_with_no_key_replaces_nothing(self, memory, store):
        memory.remember("Lives in Cary, NC.", topic="home", key="city")
        _, replaced = memory.remember_replacing(
            "Their stairs light is switch.lights_2 in Home Assistant.", topic="home")
        assert replaced == [] and len(store.list("asha", 10)) == 2

    def test_a_second_preference_with_no_key_sits_beside_the_first(self, memory, store):
        memory.remember("Prefers aisle seats on flights.", topic="preference")
        _, replaced = memory.remember_replacing("Prefers short answers.",
                                                topic="preference")
        assert replaced == [] and len(store.list("asha", 10)) == 2

    def test_a_key_makes_any_topic_replaceable(self, memory, store):
        memory.remember("Naina's email is a@example.com.", topic="contact",
                        about="Naina", key="email")
        _, replaced = memory.remember_replacing(
            "Naina's email is b@example.com.", topic="contact", about="naina",
            key="email")
        assert len(replaced) == 1 and len(store.list("asha", 10)) == 1

    def test_someone_elses_home_does_not_replace_theirs(self, memory, store):
        memory.remember("Lives in Cary, NC.", topic="home", key="city")
        _, replaced = memory.remember_replacing("Naina lives in Aligarh.",
                                                topic="home", about="Naina", key="city")
        assert replaced == [] and len(store.list("asha", 10)) == 2

    def test_a_store_that_cannot_forget_keeps_both_and_says_why(self, memory, store):
        memory.remember("Lives in Cary, NC.", topic="home", key="city")
        said = []
        memory.on_notice = said.append

        def broken(user, memory_id):
            raise RuntimeError("store down")
        store.forget = broken
        new, replaced = memory.remember_replacing("Lives in Austin, TX.", topic="home",
                                                  key="city")
        assert replaced == [] and len(store.list("asha", 10)) == 2
        assert "could not let go" in said[0]

    def test_an_unknown_topic_is_refused_before_anything_is_written(self, memory, store):
        from yantra.memory import MemoryStoreError
        with pytest.raises(MemoryStoreError, match="topic"):
            memory.remember("Likes rain.", topic="weather")
        assert store.list("asha", 10) == []

    def test_saying_it_again_with_a_topic_labels_the_old_row(self, memory, store):
        first = memory.remember("Lives in Cary, NC.")
        again = memory.remember("Lives in Cary, NC.", topic="home", key="city")
        item = store.list("asha", 10)[0]
        assert first == again and item.topic == "home" and item.slot == "home||city"


class TestLapsing:
    def _age(self, store, days, used_days=None):
        then = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        used = (None if used_days is None else
                (datetime.now(UTC) - timedelta(days=used_days)).isoformat(timespec="seconds"))
        store._db.execute("UPDATE memories SET created_at = ?, used_at = ?", (then, used))
        store._db.commit()

    def test_a_project_unused_past_its_lifetime_goes(self, memory, store):
        memory.remember("Working on the Q3 launch.", topic="project")
        self._age(store, 61)
        assert store.list("asha", 10) == []

    def test_one_in_use_stays_however_old(self, memory, store):
        memory.remember("Has a Robinhood brokerage account.", topic="account",
                        key="brokerage")
        self._age(store, 900, used_days=3)
        assert len(store.list("asha", 10)) == 1

    def test_home_has_no_lifetime(self, memory, store):
        memory.remember("Lives in Cary, NC.", topic="home")
        self._age(store, 3000)
        assert len(store.list("asha", 10)) == 1

    def test_an_older_file_gains_the_new_columns(self, tmp_path):
        path = tmp_path / "old.sqlite"
        db = sqlite3.connect(path)
        db.executescript("CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                         "user TEXT NOT NULL, statement TEXT NOT NULL, norm TEXT NOT NULL, "
                         "kind TEXT, package TEXT, created_at TEXT NOT NULL);")
        db.execute("INSERT INTO memories (user, statement, norm, created_at) "
                   "VALUES ('asha', 'Uses Neovim.', 'uses neovim.', '2026-01-01T00:00:00+00:00')")
        db.commit()
        db.close()
        items = LocalStore(path).list("asha", 10)
        assert items[0].statement == "Uses Neovim." and items[0].topic is None


class TestTheTool:
    def test_the_ask_shows_what_would_be_let_go(self, memory, tmp_path):
        memory.remember("Lives in Cary, NC.", topic="home", key="city")
        tool = Remember(memory)
        ask = tool.summary({"statement": "Lives in Austin, TX.", "topic": "home",
                            "key": "city"},
                           ToolContext(cwd=tmp_path))
        assert ask == "remember: Lives in Austin, TX. (instead of: Lives in Cary, NC.)"

    def test_the_kept_line_names_the_old_one(self, memory, tmp_path):
        memory.remember("Lives in Cary, NC.", topic="home", key="city")
        out = Remember(memory).run({"statement": "Lives in Austin, TX.",
                                    "topic": "home", "key": "city"},
                                   ToolContext(cwd=tmp_path))
        assert out.endswith("Lives in Austin, TX. (instead of: Lives in Cary, NC.)")
        assert memory.kept[0][1] == "Lives in Austin, TX. (instead of: Lives in Cary, NC.)"


class TestLookBack:
    def test_a_line_carries_its_topic_and_key(self):
        found = parse_reply("fact [home/city]: Lives in Cary, NC (Eastern time).", [])
        assert found == [Candidate("Lives in Cary, NC (Eastern time).", "fact", "home", "city")]

    def test_round_brackets_and_a_bold_kind_are_read_too(self):
        found = parse_reply("**preference** (tool / editor): Uses Neovim as their editor.", [])
        assert (found[0].topic, found[0].key) == ("tool", "editor")

    def test_a_topic_without_a_key_is_kept_with_no_slot(self):
        found = parse_reply("fact [home]: Their stairs light is switch.lights_2.", [])
        assert found == [Candidate("Their stairs light is switch.lights_2.", "fact", "home", None)]

    def test_a_line_without_a_bracket_is_still_kept(self):
        found = parse_reply("fact: Lives in Cary, NC.", [])
        assert found == [Candidate("Lives in Cary, NC.", "fact", None, None)]

    def test_an_unknown_topic_keeps_the_fact_without_it(self):
        found = parse_reply("fact [weather/today]: Likes the rain.", [])
        assert found == [Candidate("Likes the rain.", "fact", None, None)]

    def test_keeping_a_new_home_city_says_the_old_one(self, memory):
        memory.remember("Lives in Cary, NC.", topic="home", key="city")
        kept = keep_lines(memory, [Candidate("Lives in Austin, TX.", "fact", "home", "city")])
        assert kept[0][1] == "Lives in Austin, TX. (instead of: Lives in Cary, NC.)"

    def test_someone_elses_key_is_the_same_slot_the_tool_gives(self, memory, store):
        memory.remember("Ravi's email is a@example.org.", topic="contact", about="Ravi",
                        key="email")
        kept = keep_lines(memory, [Candidate("Ravi's email is b@example.org.", "fact",
                                             "contact", "ravi.email")])
        assert "(instead of: Ravi's email is a@example.org.)" in kept[0][1]
        assert len(store.list("asha", 10)) == 1

    def test_the_look_back_is_shown_each_facts_key(self, memory):
        from yantra.memory.reflect import build_prompt
        from yantra.memory import label
        memory.remember("Lives in Cary, NC.", topic="home", key="city")
        shown = [f"{label(i)}{i.statement}" for i in memory.list(10)]
        assert "- [home/city] Lives in Cary, NC." in build_prompt("…", shown)


class TestOverMcp:
    def test_a_rows_topic_and_slot_come_back_from_its_metadata(self):
        rows = {"memories": [{"id": "e1", "statement": "Lives in Cary, NC.",
                              "metadata": {"topic": "home", "slot": "home||"}}]}
        assert _items(rows) == [MemoryItem("e1", "Lives in Cary, NC.", topic="home",
                                           slot="home||")]
