"""Every conversation kept to read again: the log, /chats, the History panel.

The bias these tests encode: a history is only worth having if it is
COMPLETE and if DELETE MEANS GONE. So they push on the two ways it lies --
a conversation that was had but is missing (one that ended badly, one
carried on that became a second row, a question nobody answered yet),
and a conversation that was deleted but is still there (in the search
index, in the file's freed pages, in a row the page writes again at the
end of the next turn). Search is tested against what a person types, not
FTS5 syntax: a stray quote or the word NEAR is a word, never an error.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from conftest import ScriptedProvider, assistant_text, assistant_tool_call

from yantra.agent import Agent
from yantra.cli.repl import Repl
from yantra.history import (ConversationLog, default_path, entries, match_of,
                            open_log)
from yantra.permissions import allow_read_only
from yantra.tools.base import Tool, ToolRegistry


class Echo(Tool):
    name = "echo"
    description = "echo text back"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    read_only = True

    def summary(self, args, ctx):
        return f"echo({args.get('text')!r})"

    def run(self, args, ctx):
        return f"echo:{args.get('text', '')}"


def agent_with(script) -> Agent:
    registry = ToolRegistry()
    registry.register(Echo())
    return Agent(ScriptedProvider(script), model="m", tools=registry,
                 permissions=allow_read_only)


def talked(log: ConversationLog, cid: str, question: str, answer: str) -> Agent:
    agent = agent_with([assistant_text(answer)])
    list(agent.run_streaming(question))
    log.record(cid, agent, provider_name="scripted", road="terminal", folder="/w")
    return agent


@pytest.fixture
def log(tmp_path):
    return ConversationLog(tmp_path / "conversations.sqlite")


# ---- the log ---------------------------------------------------------------------


def test_a_conversation_is_kept_with_its_first_words_as_its_title(log):
    talked(log, "a", "how do I reset the router?", "hold the button 10 s")
    [row] = log.listed()
    assert row.title == "how do I reset the router?"
    assert (row.road, row.folder, row.turns) == ("terminal", "/w", 1)


def test_each_turn_replaces_the_row_rather_than_adding_one(log):
    agent = agent_with([assistant_text("one"), assistant_text("two")])
    list(agent.run_streaming("first"))
    log.record("a", agent, provider_name="scripted", road="terminal")
    list(agent.run_streaming("second"))
    log.record("a", agent, provider_name="scripted", road="page")
    [row] = log.listed()
    assert row.turns == 2
    assert row.road == "page"          # where it is going on now
    assert row.title == "first"
    assert log.listed("second")        # the new turn's words are indexed


def test_nothing_is_kept_before_the_person_has_said_anything(log):
    agent = agent_with([])
    assert log.record("a", agent, provider_name="scripted", road="terminal") is False
    assert log.count() == 0


def test_the_newest_conversation_comes_first(log):
    talked(log, "old", "older question", "x")
    talked(log, "new", "newer question", "y")
    assert [r.id for r in log.listed()] == ["new", "old"]


def test_search_finds_words_in_the_answer_not_only_the_question(log):
    talked(log, "a", "what was that thing", "the router's password is on its back")
    talked(log, "b", "weather today", "sunny")
    [hit] = log.listed("password")
    assert hit.id == "a"
    assert "\x02password\x03" in hit.snippet


def test_search_wants_every_word_and_matches_the_start_of_a_word(log):
    talked(log, "a", "router reset", "done")
    talked(log, "b", "router price", "cheap")
    assert [r.id for r in log.listed("rout reset")] == ["a"]


def test_what_a_person_types_is_never_read_as_search_syntax(log):
    talked(log, "a", "near the station", "yes")
    assert match_of('"NEAR( OR *') == '"NEAR"* AND "OR"*'
    assert [r.id for r in log.listed('near "')] == ["a"]
    assert log.listed("((") == log.listed()   # no words: the whole list


def test_reading_one_shows_tools_with_their_results(log):
    agent = agent_with([assistant_tool_call("c1", "echo", {"text": "hi"}),
                        assistant_text("it said hi")])
    list(agent.run_streaming("echo hi please"))
    log.record("a", agent, provider_name="scripted", road="page")
    kept = log.read("a")
    assert [e["who"] for e in kept["entries"]] == ["you", "tool", "agent"]
    tool = kept["entries"][1]
    assert (tool["name"], tool["result"], tool["error"]) == ("echo", "echo:hi", False)


def test_a_long_tool_output_is_shortened_for_reading():
    payload = {"history": [
        {"role": "assistant", "content": [
            {"kind": "tool_call", "id": "1", "name": "fetch", "arguments": {}}]},
        {"role": "user", "content": [
            {"kind": "tool_result", "tool_call_id": "1", "content": "x" * 5000}]}]}
    [row] = entries(payload)
    assert len(row["result"]) == 400


def test_a_deleted_conversation_is_gone_from_the_list_and_the_search(log):
    talked(log, "a", "my secret plan", "noted")
    talked(log, "b", "other", "fine")
    assert log.delete("a") is True
    assert [r.id for r in log.listed()] == ["b"]
    assert log.listed("secret") == []
    assert log.read("a") is None and log.payload("a") is None
    assert log.delete("a") is False


def test_a_deleted_conversation_leaves_no_words_in_the_file(tmp_path):
    path = tmp_path / "c.sqlite"
    log = ConversationLog(path)
    talked(log, "a", "zebracorn-unique-word", "zebracorn-answer")
    talked(log, "b", "keep me", "kept")
    log.delete("a")
    log.close()
    assert b"zebracorn" not in path.read_bytes()


def test_the_log_is_off_when_asked_and_beside_memory_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("YANTRA_HISTORY", raising=False)
    assert default_path() == tmp_path / "yantra" / "conversations.sqlite"
    monkeypatch.setenv("YANTRA_HISTORY", "off")
    assert open_log() is None
    monkeypatch.setenv("YANTRA_HISTORY", str(tmp_path / "x.sqlite"))
    assert default_path() == tmp_path / "x.sqlite"


# ---- /chats in the terminal --------------------------------------------------------


def repl_with(log, lines, script) -> tuple[Repl, io.StringIO]:
    out = io.StringIO()
    feeder = iter(lines)
    repl = Repl(agent_with(script), Console(file=out, width=120),
                input_fn=lambda _prompt: next(feeder), log=log)
    return repl, out


def test_every_terminal_turn_is_kept_without_asking(log):
    repl, _ = repl_with(log, ["what is 2+2", "/quit"], [assistant_text("4")])
    repl.run()
    [row] = log.listed()
    assert row.title == "what is 2+2" and row.road == "terminal"


def test_clear_starts_a_second_conversation_and_keeps_the_first(log):
    repl, _ = repl_with(log, ["first talk", "/clear", "second talk", "/quit"],
                        [assistant_text("a"), assistant_text("b")])
    repl.run()
    assert [r.title for r in log.listed()] == ["second talk", "first talk"]


def test_chats_lists_searches_and_reads(log):
    talked(log, "x", "the router question", "hold reset for ten seconds")
    repl, out = repl_with(log, ["/chats", "/chats ten seconds", "/chats 1", "/quit"], [])
    repl.run()
    text = out.getvalue()
    assert "1. the router question" in text
    assert "[ten] [seconds]" in text
    assert "hold reset for ten seconds" in text


def test_chats_open_carries_it_on_in_the_same_row(log):
    talked(log, "x", "start of it", "first answer")
    repl, _ = repl_with(log, ["/chats", "/chats open 1", "and then?", "/quit"],
                        [assistant_text("second answer")])
    repl.run()
    [row] = log.listed()
    assert row.id == "x" and row.turns == 2


def test_chats_delete_asks_and_a_no_keeps_it(log):
    talked(log, "x", "keep this", "ok")
    repl, out = repl_with(log, ["/chats", "/chats delete 1", "n", "/quit"], [])
    repl.run()
    assert log.count() == 1 and "(kept)" in out.getvalue()


def test_chats_delete_with_a_yes_deletes_it_for_good(log):
    talked(log, "x", "forget this", "ok")
    repl, out = repl_with(log, ["/chats", "/chats delete 1", "y", "/chats 1", "/quit"], [])
    repl.run()
    assert log.count() == 0
    assert "deleted for good" in out.getvalue()
    assert "that conversation is gone" in out.getvalue()


def test_the_conversation_open_now_cannot_be_deleted_from_under_it(log):
    repl, out = repl_with(log, ["hello", "/chats", "/chats delete 1", "/quit"],
                          [assistant_text("hi")])
    repl.run()
    assert log.count() == 1
    assert "that is this conversation" in out.getvalue()


def test_chats_says_so_when_nothing_is_kept():
    repl, out = repl_with(None, ["/chats", "/quit"], [])
    repl.run()
    assert "not kept in this session" in out.getvalue()


# ---- the page's History panel -------------------------------------------------------

fastapi = pytest.importorskip("fastapi")


def web(log, script):
    from fastapi.testclient import TestClient
    from yantra.web.server import WebSession, make_app
    session = WebSession()
    agent = agent_with(script)
    agent.permissions = session.permission_gate()
    session.attach(agent, None)
    session.log = log
    return session, agent, TestClient(make_app(session))


def finish(client, ws, text):
    assert client.post("/api/message", json={"text": text}).status_code == 200
    while ws.receive_json()["type"] != "turn_done":
        pass


def test_a_page_turn_is_kept_and_listed_as_open_now(log):
    session, _, client = web(log, [assistant_text("hi there")])
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        finish(client, ws, "hello from the page")
    d = client.get("/api/chats").json()
    assert d["kept"] and d["total"] == 1
    assert d["rows"][0]["road"] == "page"
    assert d["current"] == d["rows"][0]["id"] == session.conversation_id


def test_the_page_searches_reads_and_deletes(log):
    talked(log, "old", "the boiler pressure", "1.5 bar is right")
    _, _, client = web(log, [])
    [hit] = client.get("/api/chats", params={"q": "bar"}).json()["rows"]
    assert hit["id"] == "old"
    read = client.get("/api/chats/old").json()
    assert read["entries"][1]["text"] == "1.5 bar is right"
    assert client.delete("/api/chats/old").status_code == 200
    assert client.get("/api/chats/old").status_code == 404
    assert client.get("/api/chats", params={"q": "boiler"}).json()["rows"] == []


def test_the_page_will_not_delete_the_conversation_open_now(log):
    session, _, client = web(log, [assistant_text("hi")])
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        finish(client, ws, "hello")
    assert client.delete(f"/api/chats/{session.conversation_id}").status_code == 409
    assert log.count() == 1


def test_carrying_one_on_from_the_page_grows_its_own_row(log):
    talked(log, "old", "plan the trip", "Lisbon in May")
    session, agent, client = web(log, [assistant_text("book the flights")])
    assert client.post("/api/chats/old/open").status_code == 200
    assert session.conversation_id == "old"
    assert len(agent.history) == 2
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        finish(client, ws, "what next?")
    [row] = log.listed()
    assert row.id == "old" and row.turns == 2


def test_clear_on_the_page_starts_a_new_row(log):
    session, _, client = web(log, [assistant_text("a"), assistant_text("b")])
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        finish(client, ws, "one")
        client.post("/api/clear")
        finish(client, ws, "two")
    assert [r.title for r in log.listed()] == ["two", "one"]


def test_the_page_says_when_nothing_is_kept():
    _, _, client = web(None, [])
    assert client.get("/api/chats").json() == {"kept": False, "rows": [],
                                                "current": None, "total": 0}
    assert client.get("/api/chats/x").status_code == 404
