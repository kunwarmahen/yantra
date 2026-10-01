"""The look back at a conversation's end (memory/reflect.py).

The BIAS these tests encode is against the ways a look back goes wrong
quietly:

* MISSING WHAT WAS SAID IN PASSING -- "flights from RDU" in session 1,
  with no ``remember`` call, must still reach session 2's prompt once
  the person keeps it;
* WRITING WITHOUT A YES -- under ``ask`` nothing is kept unless the
  person says so, and a bare Enter keeps nothing;
* NAGGING -- the same turns are not looked at twice, a dropped
  candidate is not offered again, and what is already remembered is
  not proposed;
* LEAKING -- a secret in the conversation never reaches the look back's
  prompt, and the trace's own redaction applies to it too;
* COSTING THE SESSION -- a store or provider that fails costs a line,
  never the conversation, and nothing to look at costs no model call.
"""

from __future__ import annotations

import asyncio
import io
import re

import pytest
from rich.console import Console

from conftest import ScriptedProvider, assistant_text, assistant_tool_call

from yantra.agent import Agent
from yantra.async_agent import AsyncAgent
from yantra.cli.repl import Repl, _chosen
from yantra.memory import enable_memory
from yantra.memory.local import LocalStore
from yantra.memory.reflect import (Candidate, before_compaction, keep,
                                   mark_reviewed, parse_reply, reflect,
                                   reflect_mode)
from yantra.tools.base import ToolRegistry
from yantra.types import ThinkingBlock, Usage

RDU = "fact: Lives near RDU (Raleigh-Durham airport)."


@pytest.fixture
def store(tmp_path):
    return LocalStore(tmp_path / "memory.sqlite")


def _agent(store, script, *, user="asha", mode="ask"):
    agent = Agent(ScriptedProvider(script), model="m", tools=ToolRegistry(),
                  permissions=lambda request: True)
    enable_memory(agent, store, user=user)
    agent.memory.reflect = mode
    return agent


def _repl(agent, answers=()):
    replies = iter(answers)
    out = io.StringIO()
    repl = Repl(agent, Console(file=out, width=120),
                input_fn=lambda prompt: next(replies, ""))
    return repl, out


def _prompt_of(provider) -> str:
    return provider.last_request()["messages"][0].content[0].text


def _transcript_of(provider) -> str:
    """The conversation part only: the prompt's own examples mention RDU."""
    return _prompt_of(provider).split("<<<", 1)[1]


# ---- reading the reply -------------------------------------------------------


class TestParse:
    def test_candidate_lines_are_read_and_everything_else_ignored(self):
        text = ("<think>they said RDU twice</think>\nHere is what I found:\n"
                "- fact: Lives near RDU.\n2. **preference**: Prefers short "
                "answers.\nNONE of the rest\n")
        assert parse_reply(text, []) == [
            Candidate("Lives near RDU.", "fact"),
            Candidate("Prefers short answers.", "preference")]

    def test_none_is_nothing(self):
        assert parse_reply("NONE", []) == []

    def test_what_is_already_remembered_is_not_proposed_again(self):
        remembered = ["Lives near RDU (Raleigh-Durham airport)"]
        assert parse_reply("fact: Lives near RDU.", remembered) == []
        assert parse_reply("fact: lives near rdu (raleigh-durham airport)",
                           remembered) == []

    def test_a_dropped_candidate_is_not_offered_again(self):
        assert parse_reply("fact: Uses vim.", [], {"uses vim."}) == []

    def test_a_candidate_with_a_hole_in_it_is_dropped(self):
        assert parse_reply("fact: Their API key is [redacted].", []) == []

    def test_at_most_five(self):
        text = "\n".join(f"fact: Fact number {n}." for n in range(9))
        assert len(parse_reply(text, [])) == 5


def test_the_mode_flag_beats_the_environment_beats_ask():
    assert reflect_mode(None, {}) == "ask"
    assert reflect_mode(None, {"YANTRA_REFLECT": "auto"}) == "auto"
    assert reflect_mode("off", {"YANTRA_REFLECT": "auto"}) == "off"
    with pytest.raises(ValueError, match="YANTRA_REFLECT"):
        reflect_mode(None, {"YANTRA_REFLECT": "maybe"})


def test_all_numbers_or_none():
    found = [Candidate("a"), Candidate("b"), Candidate("c")]
    assert _chosen("a", found) == found
    assert _chosen("1, 3 3 9", found) == [found[0], found[2]]
    assert _chosen("", found) == [] and _chosen("n", found) == []


# ---- the look back itself ------------------------------------------------------


class TestReflect:
    def test_it_looks_at_the_conversation_and_counts_what_it_spent(self, store):
        agent = _agent(store, [assistant_text("RDU to DEN: book early."),
                               assistant_text(RDU, usage=Usage(900, 20))])
        list(agent.run_streaming("flights from RDU to Denver next month?"))
        found = reflect(agent)
        assert found == [Candidate("Lives near RDU (Raleigh-Durham airport).")]
        prompt = _prompt_of(agent.provider)
        assert "flights from RDU to Denver" in prompt
        assert "(nothing yet)" in prompt
        assert agent.total_usage.input_tokens == 900
        assert agent.provider.requests[-1]["tools"] == []

    def test_the_models_reasoning_is_not_what_the_person_said(self, store):
        """The reasoning reads the system prompt, so a username in it is the
        machine's, not something the person told anyone."""
        agent = _agent(store, [assistant_text("Pin torch in pyproject."),
                               assistant_text("NONE")])
        list(agent.run_streaming("my uv sync fails on torch"))
        agent.history[-1].content.insert(0, ThinkingBlock(
            thinking="The user is mahen, working in /home/mahen/yantra.",
            signature=""))
        reflect(agent)
        seen = _transcript_of(agent.provider)
        assert "uv sync fails" in seen and "Pin torch" in seen
        assert "mahen" not in seen
        assert isinstance(agent.history[-1].content[0], ThinkingBlock)  # untouched

    def test_a_tool_calls_arguments_are_the_assistants_too(self, store):
        agent = _agent(store, [
            assistant_tool_call("c1", "bash", {"command": "cd /home/mahen/yantra"}),
            assistant_text("I can't run commands here."), assistant_text("NONE")])
        list(agent.run_streaming("my uv sync fails on torch"))
        reflect(agent)
        seen = _transcript_of(agent.provider)
        assert "tool_call: bash({})" in seen and "mahen" not in seen

    def test_turns_already_looked_at_are_not_sent_again(self, store):
        agent = _agent(store, [assistant_text("ok"), assistant_text("NONE"),
                               assistant_text("sure"), assistant_text("NONE")])
        list(agent.run_streaming("I live near RDU"))
        reflect(agent)
        list(agent.run_streaming("I use uv, not pip"))
        reflect(agent)
        seen = _transcript_of(agent.provider)
        assert "uv, not pip" in seen and "near RDU" not in seen

    def test_nothing_new_costs_no_model_call(self, store):
        agent = _agent(store, [assistant_text("ok"), assistant_text("NONE")])
        list(agent.run_streaming("hello"))
        reflect(agent)
        # the script is empty: a second call would fail the test
        assert reflect(agent) == []
        assert reflect(_agent(store, [])) == []

    def test_a_secret_never_reaches_the_look_back(self, store):
        agent = _agent(store, [assistant_text("noted"), assistant_text("NONE")])
        list(agent.run_streaming("my HA_TOKEN=abcd1234efgh5678 and I live in Cary"))
        reflect(agent)
        prompt = _prompt_of(agent.provider)
        assert "abcd1234efgh5678" not in prompt and "live in Cary" in prompt

    def test_the_traces_own_redaction_applies(self, store):
        agent = _agent(store, [assistant_text("ok"), assistant_text("NONE")])
        agent.memory.redact = re.compile(r"Priya")
        list(agent.run_streaming("my manager is Priya"))
        reflect(agent)
        assert "Priya" not in _transcript_of(agent.provider)

    def test_keeping_scrubs_once_more(self, store):
        agent = _agent(store, [])
        agent.memory.redact = re.compile(r"Priya")
        assert keep(agent.memory, [Candidate("Manager is Priya."),
                                   Candidate("Uses uv.")]) == ["1"]
        assert [i.statement for i in store.list("asha", 5)] == ["Uses uv."]

    def test_a_restored_conversation_counts_as_looked_at(self, store):
        agent = _agent(store, [assistant_text("ok")])
        list(agent.run_streaming("I live near RDU"))
        agent.memory.pending.append(Candidate("stale"))
        mark_reviewed(agent)
        assert agent.memory.reviewed == len(agent.history)
        assert agent.memory.pending == [] and reflect(agent) == []

    def test_a_new_conversation_starts_unlooked_at(self, store):
        agent = _agent(store, [assistant_text("ok"), assistant_text("ok")])
        list(agent.run_streaming("hi"))
        agent.memory.reviewed = 2
        agent.history.clear()
        list(agent.run_streaming("hello again"))
        assert agent.memory.reviewed == 0


# ---- before compaction ----------------------------------------------------------


class TestBeforeCompaction:
    def test_compaction_looks_back_first_and_the_offer_waits(self, store):
        agent = _agent(store, [assistant_text("ok"), assistant_text(RDU)])
        list(agent.run_streaming("flights from RDU?"))
        agent.compact()
        assert agent.memory.pending == [
            Candidate("Lives near RDU (Raleigh-Durham airport).")]
        assert agent.memory.reviewed == len(agent.history)
        assert store.list("asha", 5) == []      # asked, not written

    def test_auto_keeps_without_waiting(self, store):
        agent = _agent(store, [assistant_text("ok"), assistant_text(RDU)],
                       mode="auto")
        list(agent.run_streaming("flights from RDU?"))
        agent.compact()
        assert agent.memory.pending == []
        assert len(store.list("asha", 5)) == 1

    def test_off_never_looks(self, store):
        agent = _agent(store, [assistant_text("ok")], mode="off")
        list(agent.run_streaming("flights from RDU?"))
        agent.compact()     # a look back would exhaust the script

    def test_a_failure_costs_a_notice_not_the_turn(self, store):
        agent = _agent(store, [assistant_text("ok")])   # no reply for the look
        list(agent.run_streaming("flights from RDU?"))
        before_compaction(agent)
        assert "looking back before compaction failed" in agent.memory.notice

    def test_the_async_twin_looks_back_too(self, store):
        agent = AsyncAgent(ScriptedProvider([assistant_text("ok"),
                                             assistant_text(RDU)]),
                           model="m", tools=ToolRegistry())
        enable_memory(agent, store, user="asha")

        async def go():
            async for _ in agent.run_streaming("flights from RDU?"):
                pass
            await agent.compact()
        asyncio.run(go())
        assert len(agent.memory.pending) == 1


# ---- the terminal ------------------------------------------------------------------


class TestRepl:
    def test_quit_looks_back_and_asks_and_a_yes_keeps(self, store):
        agent = _agent(store, [assistant_text("book early"), assistant_text(RDU)])
        repl, out = _repl(agent, ["a"])
        repl.run_turn("flights from RDU to Denver?")
        assert repl._command("/quit") is True
        assert "worth remembering about you" in out.getvalue()
        assert [i.statement for i in store.list("asha", 5)] == [
            "Lives near RDU (Raleigh-Durham airport)."]

    def test_a_bare_enter_keeps_nothing_and_it_is_not_asked_again(self, store):
        agent = _agent(store, [assistant_text("ok"), assistant_text(RDU),
                               assistant_text("ok"), assistant_text(RDU)])
        repl, out = _repl(agent, [""])
        repl.run_turn("flights from RDU?")
        repl._command("/remember")
        assert store.list("asha", 5) == [] and "nothing kept" in out.getvalue()
        repl.run_turn("and back again?")
        repl._command("/remember")
        assert "nothing new worth remembering" in out.getvalue()

    def test_clear_looks_back_before_the_history_goes(self, store):
        agent = _agent(store, [assistant_text("ok"), assistant_text(RDU)])
        repl, _ = _repl(agent, ["1"])
        repl.run_turn("flights from RDU?")
        repl._command("/clear")
        assert agent.history == [] and len(store.list("asha", 5)) == 1

    def test_off_never_looks_but_remember_still_does(self, store):
        agent = _agent(store, [assistant_text("ok"), assistant_text(RDU)],
                       mode="off")
        repl, _ = _repl(agent, ["a"])
        repl.run_turn("flights from RDU?")
        repl._command("/clear")           # would exhaust nothing: no look
        assert store.list("asha", 5) == []
        agent.history.clear()
        agent.provider.script[:0] = [assistant_text("ok")]
        repl.run_turn("flights from RDU?")
        repl._command("/remember")
        assert len(store.list("asha", 5)) == 1

    def test_auto_keeps_without_asking(self, store):
        agent = _agent(store, [assistant_text("ok"), assistant_text(RDU)],
                       mode="auto")
        repl, out = _repl(agent, [])
        repl.run_turn("flights from RDU?")
        repl.end_conversation()
        assert "worth remembering" not in out.getvalue()
        assert "remembered 1" in out.getvalue()

    def test_what_compaction_found_is_offered_when_the_turn_ends(self, store):
        agent = _agent(store, [assistant_text("ok")])
        agent.memory.pending.append(Candidate("Lives near RDU."))
        repl, out = _repl(agent, ["a"])
        repl.run_turn("hi")
        assert "Lives near RDU." in out.getvalue()
        assert len(store.list("asha", 5)) == 1

    def test_a_broken_provider_costs_one_line(self, store):
        agent = _agent(store, [assistant_text("ok")])
        repl, out = _repl(agent, [])
        repl.run_turn("flights from RDU?")
        repl.end_conversation()
        assert "looking back skipped" in out.getvalue()

    def test_memory_off_means_nothing_to_look_for(self):
        agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
        repl, out = _repl(agent)
        repl._command("/remember")
        assert "memory is off" in out.getvalue()


# ---- the receipt, end to end -------------------------------------------------------


def test_said_in_passing_is_there_next_session(store):
    """Session 1 never calls remember; the look back proposes RDU and the
    person keeps it; session 2 -- a new agent -- has it in the prompt."""
    first = _agent(store, [assistant_text("Book 6-8 weeks out."),
                           assistant_text(RDU)])
    repl, _ = _repl(first, ["a"])
    repl.run_turn("What should I think about for flights from RDU to Denver?")
    repl._command("/quit")

    second = _agent(store, [assistant_text("From RDU, as before.")])
    list(second.run_streaming("find me flights to Austin"))
    system = second.provider.last_request()["system"]
    assert "Lives near RDU (Raleigh-Durham airport)." in system

    other = _agent(store, [assistant_text("Where from?")], user="ben")
    list(other.run_streaming("find me flights to Austin"))
    assert "RDU" not in (other.provider.last_request()["system"] or "")


# ---- the page ------------------------------------------------------------------------


class TestWeb:
    @pytest.fixture
    def setup(self, store):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app
        session = WebSession()
        agent = _agent(store, [assistant_text("ok"), assistant_text(RDU)])
        session.attach(agent, None)
        list(agent.run_streaming("flights from RDU?"))
        return TestClient(make_app(session)), agent

    def test_the_button_looks_and_nothing_is_kept_until_asked(self, setup, store):
        client, _ = setup
        got = client.post("/api/memory/reflect", json={}).json()
        assert got["candidates"] == [
            {"statement": "Lives near RDU (Raleigh-Durham airport).",
             "kind": "fact"}]
        assert store.list("asha", 5) == []
        kept = client.post("/api/memory/keep",
                           json={"keep": got["candidates"], "drop": []}).json()
        assert kept["kept"] == 1 and len(kept["items"]) == 1

    def test_ending_follows_the_mode(self, setup, store):
        client, agent = setup
        agent.memory.reflect = "off"
        assert client.post("/api/memory/reflect",
                           json={"ending": True}).json()["candidates"] == []
        agent.memory.reflect = "auto"
        got = client.post("/api/memory/reflect", json={"ending": True}).json()
        assert got["kept"] == 1 and len(store.list("asha", 5)) == 1

    def test_a_dropped_one_is_declined(self, setup):
        client, agent = setup
        got = client.post("/api/memory/reflect", json={}).json()
        client.post("/api/memory/keep", json={"keep": [], "drop": got["candidates"]})
        assert "lives near rdu (raleigh-durham airport)." in agent.memory.declined


class TestWording:
    """A kept fact must be findable later, which means it says what it is
    about (notes/105: a bare "Uses Neovim." was never found by meaning
    search). The BIAS here is against the prompt teaching to the test: its
    examples must not be the trial's own answers, or the trial would
    measure copying, not the rule."""

    def test_the_rule_is_in_both_ways_a_fact_is_kept(self):
        from yantra.memory.reflect import PROMPT
        from yantra.memory.tools import Remember

        assert "say what the thing IS" in PROMPT
        assert "says what the thing IS" in Remember.description

    def test_the_line_format_is_said_right_before_its_examples(self):
        # A wording rule placed between "one line per fact" and the example
        # lines made qwen3.8 and gemma4:12b drop the "fact:" prefix: every
        # candidate was then unparseable, and the trial kept 0 of 35.
        from yantra.memory.reflect import PROMPT

        rule, examples = PROMPT.split("Start every line with its kind", 1)
        assert "say what the thing IS" in rule
        assert examples.split("\n", 2)[1].startswith("fact: ")

    def test_its_examples_are_not_the_trials_answers(self):
        import json
        from pathlib import Path

        from yantra.memory.reflect import PROMPT
        from yantra.memory.tools import Remember

        cases = Path(__file__).parent.parent / "examples" / "memory_recall_cases.jsonl"
        answers = [json.loads(line)["fact"] for line in cases.read_text().splitlines()
                   if line.strip()]
        taught = PROMPT.split("The conversation:")[0] + Remember.description
        # RDU is the prompt's one deliberate example and predates the trial's
        # scoring of it; every other answer must be absent.
        for answer in answers:
            if answer and "RDU" not in answer:
                assert not re.search(answer, taught, re.IGNORECASE), answer
