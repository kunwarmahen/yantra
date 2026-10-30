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
* **A panel that acts behind the person's back, or on an option** -- the
  Watches panel on the page goes through the program's own verbs with an
  id that is an id, and its "seen" chip is only this person's looks that
  mattered (a quiet look, or somebody else's watch, never lights it).
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
    elif args == ["list", "--json"]:
        print(json.dumps([
            {{"id": "w-1", "owner": "local", "name": "ALAB under 50", "state": "active",
              "sentence": "every 3 hours: the number is below 50", "listen": "",
              "does": "You get a line saying what it saw; no model is asked",
              "last_look": None, "fired_today": 0}},
            {{"id": "w-2", "owner": "asha", "name": "Mum on WhatsApp", "state": "active",
              "sentence": "when whatsapp says Mum", "listen": "whatsapp",
              "does": "", "last_look": None, "fired_today": 0}}]))
    elif args[0] == "looks" and len(args) > 1 and args[1] == "--limit":
        print(json.dumps([
            {{"id": "l3", "watch": "w-1", "outcome": "fired", "value": "48.5",
              "found": ["the value is now 48.5"], "ended_at": "2026-10-24T09:00:00+00:00"}},
            {{"id": "l2", "watch": "w-1", "outcome": "quiet", "value": "61.2",
              "ended_at": "2026-10-24T06:00:00+00:00"}},
            {{"id": "l1", "watch": "w-2", "outcome": "fired", "found": ["Mum"],
              "ended_at": "2026-10-24T05:00:00+00:00"}}]))
    elif args[0] == "looks":
        print(json.dumps([{{"id": "l3", "watch": args[1], "outcome": "fired"}}]))
    elif args[0] in ("pause", "resume"):
        print(json.dumps({{"id": args[1], "state": "paused" if args[0] == "pause"
                                                  else "active"}}))
    elif args[0] == "rm":
        print(json.dumps({{"removed": args[1]}}))
    elif args[0] == "edit":
        print(json.dumps({{"id": args[1], "every": args[args.index("--every") + 1]}}))
    elif args[0] == "look":
        pass
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
                import os, time
                if os.path.exists({state!r} + "-slow"):    # a look at a slow page
                    time.sleep(1.0)
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


    def test_a_preview_may_take_as_long_as_a_look(self, fake, tmp_path):
        # A preview LOOKS: WhatsApp Web takes most of a minute, Prahari
        # gives a look 180s, and the 30s every MCP call gets cut it off.
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            preview = agent.registry.get("mcp__prahari__preview_watch")
            assert preview.timeout == prahari_link.LOOK_WAIT
            assert agent.registry.get("mcp__prahari__list_watches").timeout is None
            preview.session.timeout = 0.3          # the session's own wait, shrunk
            (tmp_path / "state-slow").touch()
            assert "It sees 61.2 now" in preview.run({}, agent.ctx)
            assert "now: It sees 61.2" in card(agent, "create_watch", PRICE)
        finally:
            manager.shutdown()

    def test_a_preview_first_lets_go_of_the_browser_it_will_look_with(self, fake,
                                                                       tmp_path):
        # The chat read WhatsApp to find the group, then asked for a try:
        # its own browser held the profile, and Prahari's look waited on it.
        from yantra.tools.base import Tool
        from yantra.tools.browser import BrowserSession

        class Held(BrowserSession):
            def __init__(self):
                self.closed = 0

            def let_go(self):
                self.closed += 1

        class Opener(Tool):
            def __init__(self, name):
                self.name, self.description, self.parameters = name, "reads", {}
                self.read_only, self.session = True, Held()

            def summary(self, args, ctx):
                return self.name

            def run(self, args, ctx):
                return "page"

        mine, other = Opener("whatsapp_open"), Opener("amazon_open")
        _, agent, manager, _ = start(fake[0], tmp_path, tools=(mine, other))
        try:
            look = {"check": {"tool": "whatsapp_open", "args": {}}, "every": "5m",
                    "pick": {"text": "Bad Company.*"}, "when": {"changed": True}}
            agent.registry.get("mcp__prahari__preview_watch").run(look, agent.ctx)
            assert (mine.session.closed, other.session.closed) == (1, 0)
            card(agent, "create_watch", look)          # the card's own preview too
            assert mine.session.closed == 2
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
        assert "to mahen's chat through Dvara, and to Yantra's page" in said

    def test_a_call_that_is_not_read_only_is_said_in_capitals(self):
        registry = ToolRegistry()
        registry.register(Named("web_fetch", read_only=False))
        said = prahari_link.explain_create(
            {**PRICE, "check": {"tool": "web_fetch", "args": {"url": "https://x"}}},
            registry=registry)
        assert ("web_fetch IS NOT READ-ONLY: saying yes lets Prahari run exactly this "
                "call every 3h") in said

    def test_the_card_says_what_it_is_for_in_the_persons_words(self):
        said = prahari_link.explain_create(
            {**PRICE, "asked": "tell me if ALAB  gets cheap", "thing": "ALAB's price"},
            registry=ToolRegistry())
        assert said.startswith('New watch: ALAB under 50\n  you asked: "tell me if ALAB '
                               'gets cheap"\n')
        assert "ALAB's price is below 50" in said

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


def test_a_watch_and_a_schedule_are_told_apart_in_both_layers():
    """Two programs both repeat things; the model must not offer the wrong one.
    A time is Samay's; waiting for something to happen is Prahari's."""
    from yantra import samay_link

    watches = prahari_link.prompt_text({"serving": True})
    schedules = samay_link.prompt_text({"serving": True})
    assert "is a schedule instead (`mcp__samay__`), not a watch" in watches
    assert "WAITING FOR SOMETHING TO HAPPEN is not a schedule" in schedules
    assert "Only without them" in schedules


class TestThePanel:
    def served(self, program, tmp_path):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app

        _, agent, manager, _ = start(program, tmp_path)
        session = WebSession()
        session.attach(agent, None, mcp=manager)
        return manager, TestClient(make_app(session))

    def test_the_page_state_says_prahari_is_here_and_where_news_goes(self, fake, tmp_path):
        manager, client = self.served(fake[0], tmp_path)
        try:
            prahari = client.get("/api/state").json()["prahari"]
            assert prahari["found"] is True and prahari["person"] == "local"
            assert prahari["counts"]["active"] == 2 and prahari["serving"] is False
            assert "through Dvara" in prahari["news"]
        finally:
            manager.shutdown()

    def test_the_list_looks_and_switches_go_through_the_program(self, fake, tmp_path):
        manager, client = self.served(fake[0], tmp_path)
        try:
            listed = client.get("/api/watches").json()
            assert [w["id"] for w in listed["watches"]] == ["w-1", "w-2"]
            assert client.get("/api/watches/w-1/looks").json()["looks"][0]["outcome"] == "fired"
            assert client.post("/api/watches/w-1/pause").json()["result"]["state"] == "paused"
            assert client.post("/api/watches/w-1/delete").json()["result"] == {
                "removed": "w-1"}
            assert client.post("/api/watches/w-1/look").json() == {"started": "w-1"}
            state = str(tmp_path / "state")
            made = calls(fake[1])
            assert ["--state", state, "pause", "w-1", "--json"] in made
            assert ["--state", state, "rm", "w-1", "--json"] in made
        finally:
            manager.shutdown()

    def test_how_often_goes_to_prahari_and_a_word_that_isnt_a_step_does_not(
            self, fake, tmp_path):
        manager, client = self.served(fake[0], tmp_path)
        try:
            got = client.post("/api/watches/w-1/every", json={"every": "30m"})
            assert got.status_code == 200 and got.json()["result"]["every"] == "30m"
            for wrong in ("", "soon", "30m; rm -rf /", "--state /x"):
                said = client.post("/api/watches/w-1/every", json={"every": wrong})
                assert said.status_code == 400 and "30m, 3h or 1d" in said.json()["detail"]
            assert ["--state", str(tmp_path / "state"), "edit", "w-1", "--every", "30m",
                    "--json"] in calls(fake[1])
        finally:
            manager.shutdown()

    def test_seen_is_this_persons_looks_that_mattered(self, fake, tmp_path):
        manager, client = self.served(fake[0], tmp_path)
        try:
            got = client.get("/api/watches/seen").json()["seen"]
            # the quiet look and asha's watch are left out
            assert [look["id"] for look in got] == ["l3"]
            assert got[0]["name"] == "ALAB under 50" and "below 50" in got[0]["sentence"]
        finally:
            manager.shutdown()

    def test_an_option_is_not_a_watch_id_and_an_unknown_action_is_refused(self, fake,
                                                                          tmp_path):
        manager, client = self.served(fake[0], tmp_path)
        try:
            assert client.post("/api/watches/--help/pause").status_code == 400
            assert client.post("/api/watches/--help/look").status_code == 400
            assert client.post("/api/watches/w-1/allow").status_code == 404
            assert not any("--help" in c for c in calls(fake[1]))
        finally:
            manager.shutdown()

    def test_with_no_prahari_the_panel_says_how_to_get_one(self, tmp_path, monkeypatch):
        monkeypatch.setattr(prahari_link.shutil, "which", lambda _name: None)
        monkeypatch.delenv(prahari_link.ENV, raising=False)
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app

        _, agent, manager, _ = start(None, tmp_path, flag="auto")
        session = WebSession()
        session.attach(agent, None, mcp=manager)
        client = TestClient(make_app(session))
        assert client.get("/api/state").json()["prahari"] is None
        answer = client.get("/api/watches")
        assert answer.status_code == 400 and "prahari" in answer.json()["detail"]

    def test_a_watch_made_in_the_turn_is_counted_when_the_turn_ends(self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            before = len([c for c in calls(fake[1]) if c[-2:] == ["status", "--json"]])
            agent.registry.get("mcp__prahari__delete_watch").run({"id": "w-1"}, agent.ctx)
            after = len([c for c in calls(fake[1]) if c[-2:] == ["status", "--json"]])
            assert after == before + 1
        finally:
            manager.shutdown()


def test_a_look_from_the_page_is_given_this_yantra_when_nothing_names_one(tmp_path,
                                                                           monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = prahari_link.looking_env({"PATH": "/usr/bin"})
    assert env["PRAHARI_YANTRA"].endswith("yantra")
    assert env["PRAHARI_YANTRA_HOME"] == str(tmp_path)
    # one already named is kept, home and all
    kept = prahari_link.looking_env({"PRAHARI_YANTRA": "uv run yantra",
                                     "PRAHARI_YANTRA_HOME": "/srv"})
    assert kept["PRAHARI_YANTRA"] == "uv run yantra" and kept["PRAHARI_YANTRA_HOME"] == "/srv"
