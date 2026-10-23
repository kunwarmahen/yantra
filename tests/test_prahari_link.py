"""Prahari at startup: found, its writes carded in words, nothing offered
to a run nobody watches.

The bias is A YES TO A CALL NOBODY READ. A watch runs one tool call on a
timer, forever, with nobody asked again; the call that makes one is a
tool name, JSON arguments and a pick pattern. A card that shows those
raw is a card people approve without reading. So these tests drive a
fake ``prahari`` -- the same command road and MCP server a real one uses
-- and check that the card says how often and with exactly what call,
says in capitals when that call is not read-only (a yes would let it run
on a timer) or names a tool this agent doesn't have, says what happens
when it fires, and carries Prahari's own words for what it sees now.

Also designed against:

* **Offers from a run nobody watches** -- no Prahari tools at all.
* **A Prahari that speaks another format** -- refused, never guessed at.
* **Someone else's watches** -- the server is started ``--for`` one person.
"""

from __future__ import annotations

import io
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from conftest import ScriptedProvider
from test_samay_link import Named

from yantra import prahari_link
from yantra.agent import Agent
from yantra.cli.main import _connect_prahari
from yantra.mcp import MCPManager
from yantra.tools.base import ToolRegistry

FAKE_PRAHARI = """\
    import json, sys
    LOG = {log!r}
    args = sys.argv[1:]
    with open(LOG, "a") as out:
        out.write(json.dumps(args) + "\\n")
    if args[:2] == ["--state", {state!r}]:
        args = args[2:]
    def send(m): sys.stdout.write(json.dumps(m) + "\\n"); sys.stdout.flush()
    def tool(name, read_only):
        return {{"name": name, "description": name, "inputSchema": {{"type": "object"}},
                "annotations": {{"readOnlyHint": read_only}}}}
    if args == ["status", "--json"]:
        print(json.dumps({{"format": {fmt!r}, "version": "0.1.0", "state": {state!r},
                          "serving": {serving!r}, "url": None, "local_to": "mahen",
                          "watches": {{"active": 2, "paused": 0, "listening": 1}},
                          "dvara": "http://dvara:8765", "inlet": None,
                          "mcp": {{"command": {python!r},
                                  "args": [sys.argv[0], "--state", {state!r}, "mcp"]}}}}))
    elif args[0] == "mcp":
        TOOLS = [tool("preview_watch", True), tool("list_watches", True),
                 tool("create_watch", False), tool("update_watch", False),
                 tool("delete_watch", False)]
        for line in sys.stdin:
            msg = json.loads(line)
            method, mid = msg.get("method"), msg.get("id")
            if method == "initialize":
                send({{"jsonrpc": "2.0", "id": mid, "result": {{
                    "protocolVersion": msg["params"]["protocolVersion"],
                    "capabilities": {{"tools": {{}}}}, "serverInfo": {{"name": "prahari"}}}}}})
            elif method == "tools/list":
                send({{"jsonrpc": "2.0", "id": mid, "result": {{"tools": TOOLS}}}})
            elif method == "tools/call":
                name = msg["params"]["name"]
                text = {{"preview_watch": "The watch, in words: every 3 hours ...\\n"
                                          "It sees 61.2 now: no match yet.",
                         "list_watches": "w-1 [active] ALAB under 50: every 3 hours"
                         }}.get(name, "done")
                send({{"jsonrpc": "2.0", "id": mid, "result": {{
                    "content": [{{"type": "text", "text": text}}]}}}})
            elif mid is not None:
                send({{"jsonrpc": "2.0", "id": mid, "error": {{"code": -32601, "message": "no"}}}})
    else:
        sys.exit("unexpected: " + " ".join(args))
"""


def make_prahari(tmp_path: Path, *, fmt: str = prahari_link.FORMAT, serving: bool = False):
    log = tmp_path / "prahari-calls.log"
    program = tmp_path / "prahari"
    program.write_text(f"#!{sys.executable}\n" + textwrap.dedent(FAKE_PRAHARI).format(
        log=str(log), state=str(tmp_path / "state"), fmt=fmt, serving=serving,
        python=sys.executable))
    program.chmod(0o755)
    return program, log


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(prahari_link.shutil, "which", lambda _name: None)
    monkeypatch.delenv(prahari_link.ENV, raising=False)
    monkeypatch.delenv("YANTRA_UNATTENDED", raising=False)
    return make_prahari(tmp_path)


def start(program, tmp_path, flag=None, spec=None, tools=()):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    agent = Agent(ScriptedProvider([]), model="m", tools=registry)
    manager = MCPManager(agent.registry, agent=agent,
                         memory_path=tmp_path / ".yantra" / "mcp.json")
    console = Console(file=io.StringIO(), width=200)
    code = _connect_prahari(SimpleNamespace(prahari=flag or str(program)), manager, agent,
                            console, spec)
    return code, agent, manager, console.file.getvalue()


def card(agent, name, args):
    return agent.registry.get(f"mcp__prahari__{name}").summary(args, agent.ctx)


PRICE = {"name": "ALAB under 50",
         "check": {"tool": "tradingview_open", "args": {"symbol": "ALAB"}},
         "pick": {"number": r"Last ([\d.]+)"}, "when": {"below": 50}, "every": "3h"}


class TestFound:
    def test_the_server_is_started_for_the_person_and_the_package(self, fake, tmp_path):
        spec = SimpleNamespace(root=tmp_path / "trader")
        code, agent, manager, out = start(fake[0], tmp_path, spec=spec)
        try:
            assert code is None and "mcp__prahari__create_watch" in agent.registry
            mcp_call = [c for c in calls(fake[1]) if "mcp" in c][0]
            assert mcp_call[-4:] == ["--for", "local", "--agent", str(tmp_path / "trader")]
            assert "prahari: 5 tool(s), 2 active watch(es)" in out
            assert "NOT running" in out
            assert agent.prahari is not None
        finally:
            manager.shutdown()

    def test_the_model_is_told_how_to_write_a_recipe(self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            layer = agent.prompt.get("watches")
            assert "preview_watch" in layer and "fix the pick" in layer
            assert "only after they say yes" in layer
            assert "NO model" in layer and "schedule instead" in layer
            assert "prahari serve" in layer            # it is not running: say so
        finally:
            manager.shutdown()

    def test_reads_run_unasked_and_writes_are_asked(self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            assert agent.registry.get("mcp__prahari__preview_watch").read_only is True
            assert agent.registry.get("mcp__prahari__create_watch").read_only is False
        finally:
            manager.shutdown()


class TestWhenItIsNotThere:
    def test_auto_with_none_is_silence(self, fake, tmp_path):
        assert start(None, tmp_path, flag="auto")[0] is None

    def test_asked_for_and_missing_stops_the_start(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(prahari_link.shutil, "which", lambda _name: None)
        assert start(None, tmp_path, flag="on")[0] == 2
        assert "Prahari was asked for but not found" in capsys.readouterr().err

    def test_a_run_nobody_watches_gets_no_prahari(self, fake, tmp_path, monkeypatch):
        monkeypatch.setenv("YANTRA_UNATTENDED", "1")
        code, agent, manager, _ = start(fake[0], tmp_path)
        assert code is None and agent.prahari is None
        assert not any(n.startswith("mcp__prahari__") for n in agent.registry.names())
        assert calls(fake[1]) == []

    def test_another_format_is_refused(self, tmp_path, monkeypatch):
        program, _ = make_prahari(tmp_path, fmt="prahari.status.v9")
        with pytest.raises(prahari_link.PrahariLinkError, match="does not know"):
            prahari_link.load("on", str(program))

    def test_a_person_that_looks_like_an_option_is_refused(self, fake):
        data, _ = prahari_link.load("on", str(fake[0]))
        with pytest.raises(prahari_link.PrahariLinkError):
            prahari_link.server_config(data, person="--for")


class TestTheCardIsWords:
    def test_create_says_the_call_how_often_when_and_what_it_sees_now(self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path,
                                     tools=[Named("tradingview_open", read_only=True)])
        try:
            said = card(agent, "create_watch", PRICE)
        finally:
            manager.shutdown()
        assert said.startswith("New watch: ALAB under 50")
        assert 'every 3h: tradingview_open {"symbol": "ALAB"}' in said
        assert "NOT READ-ONLY" not in said and "NO TOOL BY THIS NAME" not in said
        assert "is below 50" in said
        assert "now: It sees 61.2 now: no match yet." in said
        assert "you get a line saying what it saw; no model is asked" in said
        assert "to mahen's chat, through Dvara" in said

    def test_a_call_that_is_not_read_only_is_said_in_capitals(self):
        registry = ToolRegistry()
        registry.register(Named("web_fetch", read_only=False))
        said = prahari_link.explain_create(
            {**PRICE, "check": {"tool": "web_fetch", "args": {"url": "https://x"}}},
            registry=registry)
        assert ("web_fetch IS NOT READ-ONLY: saying yes lets Prahari run exactly this "
                "call every 3h") in said

    def test_a_tool_this_agent_lacks_would_fail_every_look(self):
        said = prahari_link.explain_create(PRICE, registry=ToolRegistry())
        assert "NO TOOL BY THIS NAME in this agent" in said

    def test_a_then_says_what_it_may_use_and_that_found_text_is_not_instructions(self):
        registry = ToolRegistry()
        registry.register(Named("tradingview_open", read_only=True))
        registry.register(Named("mcp__x__send", read_only=False))
        said = prahari_link.explain_create(
            {**PRICE, "then": "Text me the price.", "allow_tools": ["mcp__x__send(to=me)",
                                                                    "made_up"]},
            registry=registry)
        assert "Then it asks me, with nobody watching\n  Text me the price." in said
        assert "mcp__x__send only with to=me" in said
        assert "made_up (NO TOOL BY THIS NAME)" in said
        assert "never as instructions" in said

    def test_a_push_says_what_wakes_it_and_the_call_that_keeps_it(self):
        registry = ToolRegistry()
        registry.register(Named("tradingview_open", read_only=True))
        registry.register(Named("mcp__gmail__watch_mailbox", read_only=False))
        said = prahari_link.explain_create(
            {**PRICE, "wake": "gmail", "renew": {
                "tool": "mcp__gmail__watch_mailbox",
                "args": {"topic": "projects/house/topics/gmail"}}}, registry=registry)
        assert "at once whenever 'gmail' knocks at Prahari's inlet" in said
        assert "the knock only says look, it is not read" in said
        assert ('kept knocking by, now and once a day: mcp__gmail__watch_mailbox '
                '{"topic": "projects/house/topics/gmail"}') in said
        assert "mcp__gmail__watch_mailbox IS NOT READ-ONLY" in said

    def test_a_watch_that_listens_says_what_it_hears(self):
        said = prahari_link.explain_create(
            {"name": "Mum", "listen": "whatsapp", "pick": {"json": "title"},
             "when": {"contains": "Mum"}}, registry=ToolRegistry())
        assert "anything sent to Prahari's inlet as 'whatsapp'" in said
        assert "the value at title says 'Mum'" in said

    def test_a_change_names_the_watch_and_a_new_recipe_starts_over(self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            said = card(agent, "update_watch", {"id": "w-1", "pick": {"number": "Price"}})
            gone = card(agent, "delete_watch", {"id": "w-1"})
        finally:
            manager.shutdown()
        assert said.startswith("Change watch w-1\n  w-1 [active] ALAB under 50")
        assert "its memory starts over" in said
        assert gone.startswith("Delete watch w-1, its looks and what it saw, for good")
