"""The house's dollars a day: a ceiling every program is held to together.

The bias: a ceiling that leaks. A billed model asked after the day's money
was gone. A local model stopped by a ceiling that's about dollars. An agent
that says "wait" slipping past the ceiling because it has no standby. A
total that counts yesterday, or a turn with no price as $0 spent. A file a
year long read whole before every call. And a stop nobody hears about: the
switch, and the turn that couldn't start, both say the ceiling in words.
"""

from __future__ import annotations

import json
import time

import pytest
from conftest import ScriptedProvider, assistant_text

from yantra import usage_log
from yantra.providers import standby
from yantra.providers.base import collect
from yantra.providers.standby import DaySpent, Standby, down_words


def provider(name: str, text: str = "hello") -> ScriptedProvider:
    p = ScriptedProvider([assistant_text(text)])
    p.name = name
    return p


def stamp(at: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(at))


def write(path, rows) -> None:
    path.write_text("".join(json.dumps({"format": usage_log.FORMAT, **r}) + "\n"
                            for r in rows))


@pytest.fixture
def house(monkeypatch, tmp_path):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("YANTRA_USAGE_FILE", str(path))
    monkeypatch.setenv(usage_log.DAY_USD, "2.00")
    return path


class TestTheTotal:
    def test_only_todays_priced_lines_count(self, house):
        now = time.time()
        yesterday = usage_log._midnight(now) - 3600
        write(house, [{"at": stamp(yesterday), "usd": 50.0},
                      {"at": stamp(now), "usd": 0.75},
                      {"at": stamp(now), "usd": None},       # no price: not $0, not counted
                      {"at": stamp(now), "usd": 0.5}])
        assert usage_log.spent_today(house, now) == 1.25

    def test_a_long_file_is_read_from_its_end(self, house):
        now = time.time()
        old = usage_log._midnight(now) - 86400
        line = json.dumps({"format": usage_log.FORMAT, "at": stamp(old), "usd": 1.0}) + "\n"
        house.write_text(line * 20000 + json.dumps(
            {"format": usage_log.FORMAT, "at": stamp(now), "usd": 0.25}) + "\n")
        started = time.monotonic()
        assert usage_log.spent_today(house, now) == 0.25
        assert time.monotonic() - started < 0.2

    def test_a_broken_line_or_no_file_is_nothing_spent(self, house, tmp_path):
        house.write_text("not json\n")
        assert usage_log.spent_today(house) == 0.0
        assert usage_log.spent_today(tmp_path / "none.jsonl") == 0.0

    @pytest.mark.parametrize("raw", ["", "0", "lots"])
    def test_no_ceiling_unless_one_is_set_in_dollars(self, monkeypatch, raw):
        monkeypatch.setenv(usage_log.DAY_USD, raw)
        assert usage_log.day_ceiling() is None
        assert usage_log.day_spent("anthropic") is None


class TestTheStop:
    def test_a_local_model_is_never_held_by_dollars(self, house):
        write(house, [{"at": stamp(time.time()), "usd": 9.0}])
        assert usage_log.day_spent("ollama") is None

    def test_under_the_ceiling_the_billed_model_answers(self, house):
        write(house, [{"at": stamp(time.time()), "usd": 1.99}])
        primary = provider("anthropic")
        wrapped = standby.wrap(primary, "claude-sonnet-5-5", [])
        collect(wrapped.stream(messages=[], system=None, tools=[], model="claude-sonnet-5-5"))
        assert len(primary.requests) == 1 and wrapped.take_switches() == ()

    def test_once_spent_a_local_standby_answers_and_says_why(self, house):
        write(house, [{"at": stamp(time.time()), "usd": 2.03}])
        primary, spare = provider("anthropic"), provider("ollama", "from the spare")
        wrapped = Standby(primary, "claude-sonnet-5-5", [(spare, "gemma4:12b")])
        response = collect(wrapped.stream(messages=[], system=None, tools=[],
                                          model="claude-sonnet-5-5"))
        assert response.message.text() == "from the spare"
        assert primary.requests == []
        assert wrapped.take_switches() == (
            "claude-sonnet-5-5 (anthropic) wasn't asked: the house's $2.00 for today is "
            "spent ($2.03 so far), so gemma4:12b (ollama) answered",)

    def test_with_nothing_standing_by_the_turn_ends_in_words(self, house):
        write(house, [{"at": stamp(time.time()), "usd": 2.0}])
        primary = provider("anthropic")
        wrapped = standby.wrap(primary, "claude-sonnet-5-5", [])
        assert isinstance(wrapped, Standby)
        with pytest.raises(DaySpent) as caught:
            collect(wrapped.stream(messages=[], system=None, tools=[],
                                   model="claude-sonnet-5-5"))
        assert primary.requests == []
        assert down_words(caught.value) == (
            "Nothing was done: the house's $2.00 for today is spent ($2.00 so far), and "
            "no local model stands by. Billed models answer again after midnight.")

    def test_an_agent_that_waits_is_held_too(self, house):
        write(house, [{"at": stamp(time.time()), "usd": 5.0}])
        primary, spare = provider("anthropic"), provider("ollama")
        wrapped = standby.wrap(primary, "claude", [(spare, "gemma4:12b")], "wait")
        assert isinstance(wrapped, Standby) and wrapped.standing_by == []
        with pytest.raises(DaySpent):
            collect(wrapped.stream(messages=[], system=None, tools=[], model="claude"))
        assert primary.requests == [] and spare.requests == []

    def test_the_async_twin_is_held_the_same(self, house):
        import asyncio

        write(house, [{"at": stamp(time.time()), "usd": 2.5}])
        primary = provider("anthropic")
        wrapped = standby.wrap(primary, "claude", [])

        async def go():
            async for _ in wrapped.astream(messages=[], system=None, tools=[], model="claude"):
                pass
        with pytest.raises(DaySpent):
            asyncio.run(go())
        assert primary.requests == []

    def test_without_a_ceiling_nothing_is_wrapped(self, monkeypatch):
        monkeypatch.delenv(usage_log.DAY_USD, raising=False)
        primary = provider("anthropic")
        assert standby.wrap(primary, "claude", []) is primary

    def test_the_usage_line_says_the_local_standby_that_answered_is_local(self, house):
        from yantra.agent import Agent

        write(house, [{"at": stamp(time.time()), "usd": 2.5}])
        spare = provider("ollama", "from the spare")
        agent = Agent(Standby(provider("anthropic"), "claude", [(spare, "gemma4:12b")]),
                      model="claude")
        list(agent.run_streaming("hi"))
        row = json.loads(house.read_text().splitlines()[-1])
        assert row["provider"] == "ollama" and row["local"] is True
        assert row["switched"][0].startswith("claude (anthropic) wasn't asked")
