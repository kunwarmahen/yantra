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
* **A model that doesn't know a reading is already kept** -- it calls
  the weather tool again. The prompt names each indriya the person may
  read (what and how often, never the value, which would be stale), says
  to read it first, and is written again after one is made in the turn.
  Making one gets a card that says the call, whether it only reads, and
  in capitals when the whole house may read its value.
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
        flags = dict(a[2:].split("=", 1) for a in args[2:] if "=" in a)
        if "--every" in args:
            flags["every"] = args[args.index("--every") + 1]
        print(json.dumps({{"id": args[1], **flags}}))
    elif args[0] == "add":
        flags = dict(a[2:].split("=", 1) for a in args[1:] if "=" in a)
        print(json.dumps({{"id": "w-9", "name": args[-1], **flags,
                          "sentence": "every 1 hour: " + flags.get("when", "")}}))
    elif args[0] == "preview":
        flags = dict(a[2:].split("=", 1) for a in args[1:] if "=" in a)
        print(json.dumps({{"sentence": "looks with " + flags.get("check", "?"),
                          "said": "It sees 61.2 now: no match yet."}}))
    elif args[0] == "look":
        pass
    elif args[:2] == ["indriya", "list"]:
        import os
        kept = {state!r} + "-readings"
        print(open(kept).read() if os.path.exists(kept) else json.dumps([
            {{"id": "i-1", "name": "outside-temp", "owner": "local", "share": "mine",
              "state": "active", "value": "56.9", "age": "4 min ago",
              "read_at": "2026-11-08T17:23:00+00:00",
              "sentence": "reads the outside temperature every 15 minutes: looks at "
                          "api.open-meteo.com/v1/forecast (with web_fetch)",
              "sensed_by": [{{"id": "w-7", "name": "Heat the guestroom"}}]}},
            {{"id": "i-2", "name": "steps", "owner": "asha", "share": "house",
              "state": "paused", "paused_because": "3 readings failed",
              "value": "", "age": "", "read_at": None,
              "sentence": "reads the steps every 1 hour: looks with fit_steps",
              "sensed_by": []}}]))
    elif args[:2] in (["indriya", "pause"], ["indriya", "resume"]):
        print(json.dumps({{"name": args[2], "state": "paused" if args[1] == "pause"
                                                    else "active"}}))
    elif args[:2] == ["indriya", "look"]:
        pass
    elif args[:2] == ["indriya", "readings"]:
        print(json.dumps([
            {{"id": "r2", "at": "2026-11-09T14:00:00+00:00", "outcome": "read",
              "value": "49.2", "came_by": "clock", "came_words": "on its clock",
              "why": "Read 49.2", "detail": "", "said": "{{}}", "others": 1,
              "decided": [{{"watch": "w-7", "name": "Heat the guestroom",
                            "outcome": "fired", "why": "49.2 is below 60",
                            "notified": True}}]}}]))
    elif args[0] == "mcp":
        TOOLS = [tool("preview_watch", True), tool("list_watches", True),
                 tool("create_watch", False), tool("update_watch", False),
                 tool("delete_watch", False), tool("list_indriyas", True),
                 tool("read_indriya", True), tool("create_indriya", False),
                 tool("update_indriya", False), tool("delete_indriya", False),
                 tool("indriya_templates", True), tool("install_indriya_template", False)]
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
                                          "It sees 61.2 now: no match yet."
                                          + ("\\nReading already kept: It goes by the "
                                             "reading outside-temp, which already makes "
                                             "this call, instead of calling again. "
                                             "(look_itself: true makes it call on its "
                                             "own instead.)"
                                             if os.path.exists({state!r} + "-reader")
                                             else ""),
                         "list_watches": "w-1 [active] ALAB under 50: every 3 hours",
                         "list_indriyas": "outside-temp: 56.9 (4 min ago) -- reads the "
                                          "outside temperature every 15 minutes",
                         "indriya_templates": "air-quality: Air quality (US AQI) -- calls "
                                              "web_fetch {{\\"url\\": \\"https://aq.example/"
                                              "[latitude]\\"}} every 1h, with no model"
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
            assert "prahari: 12 tool(s), 2 active watch(es)" in out
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

    def test_the_card_says_when_it_starts_and_stops(self):
        said = prahari_link.explain_create(
            {**PRICE, "thing": "the fare", "starts": "2099-01-01T07:00",
             "ends": "2099-01-03T00:00", "stop": {"contains": "Landed"}},
            registry=ToolRegistry())
        assert ("How long\n  from Thu 1 Jan 07:00\n  until Sat 3 Jan 00:00, then it ends "
                "by itself\n  ends once the fare says 'Landed'") in said
        assert "How long" not in prahari_link.explain_create(PRICE, registry=ToolRegistry())

    def test_the_card_says_its_days_on_the_persons_own_clock(self):
        said = prahari_link.explain_create(
            {**PRICE, "ends": "2099-07-01T17:00+00:00"}, registry=ToolRegistry(),
            zone="Europe/London")
        assert "until Wed 1 Jul 18:00, then it ends by itself" in said
        naive = prahari_link.explain_create(
            {**PRICE, "ends": "2099-07-01T18:00"}, registry=ToolRegistry(),
            zone="Europe/London")
        assert "until Wed 1 Jul 18:00" in naive         # on their clock, as Prahari takes it

    def test_the_persons_zone_goes_to_prahari_and_none_adds_nothing(self):
        data = {"mcp": {"command": "prahari", "args": ["mcp"]}}
        cfg = prahari_link.server_config(data, person="asha", zone="Europe/London")
        assert cfg.args[-2:] == ["--zone", "Europe/London"]
        assert "--zone" not in prahari_link.server_config(data, person="asha").args

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


def test_changing_a_watch_s_line_changes_its_words_in_the_same_call():
    said = prahari_link.prompt_text({"serving": True})
    assert "TO CHANGE A WATCH'S LINE" in said and "in the same call" in said


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

    def test_a_watch_written_by_hand_goes_to_prahari_add_as_typed(self, fake, tmp_path):
        manager, client = self.served(fake[0], tmp_path)
        form = {"name": "ALAB under 50", "asked": "tell me if ALAB is cheap",
                "tool": "web_fetch", "args": '{"url": "https://example.com"}',
                "pick": "number", "when": "below 50", "every": "3h", "then": ""}
        try:
            tried = client.post("/api/watches/preview", json=form).json()["preview"]
            assert tried["sentence"] == "looks with web_fetch"
            made = client.post("/api/watches", json=form).json()["result"]
            assert made["id"] == "w-9" and made["name"] == "ALAB under 50"
            state = str(tmp_path / "state")
            assert ["--state", state, "add", "--check=web_fetch",
                    '--args={"url": "https://example.com"}', "--pick=number",
                    "--when=below 50", "--every=3h", "--asked=tell me if ALAB is cheap",
                    "--json", "--", "ALAB under 50"] in calls(fake[1])
            # a name that looks like an option is still the name
            assert client.post("/api/watches", json={**form, "name": "--help"}
                               ).json()["result"]["name"] == "--help"
        finally:
            manager.shutdown()

    def test_a_form_missing_its_tool_or_test_or_with_bad_arguments_is_said(self, fake,
                                                                          tmp_path):
        manager, client = self.served(fake[0], tmp_path)
        base = {"name": "x", "tool": "web_fetch", "args": "{}", "when": "new"}
        try:
            for wrong, said in (({"name": ""}, "needs a name"),
                                ({"tool": ""}, "which tool"),
                                ({"when": ""}, "when it matters"),
                                ({"args": "[1]"}, "one JSON object"),
                                ({"args": "{url"}, "one JSON object")):
                got = client.post("/api/watches", json={**base, **wrong})
                assert got.status_code == 400 and said in got.json()["detail"]
            assert not any(c[2:3] == ["add"] for c in calls(fake[1]))
        finally:
            manager.shutdown()

    def test_mending_sends_only_what_changed(self, fake, tmp_path):
        manager, client = self.served(fake[0], tmp_path)
        try:
            got = client.post("/api/watches/w-1/edit", json={"name": "ALAB cheap",
                                                              "then": ""})
            assert got.status_code == 200
            state = str(tmp_path / "state")
            assert ["--state", state, "edit", "w-1", "--then=", "--name=ALAB cheap",
                    "--json"] in calls(fake[1])
            client.post("/api/watches/w-1/edit", json={"when": "below 40"})
            assert ["--state", state, "edit", "w-1", "--when=below 40",
                    "--json"] in calls(fake[1])
            assert client.post("/api/watches/--help/edit", json={}).status_code == 400
        finally:
            manager.shutdown()

    def test_a_new_line_goes_with_its_words_in_one_mend(self, fake, tmp_path):
        """70 -> 80 on the panel: the form carries the number into the name
        and the words, and they go in the same ``prahari edit``."""
        manager, client = self.served(fake[0], tmp_path)
        try:
            client.post("/api/watches/w-1/edit", json={
                "when": "above 80", "name": "Cool the guestroom above 80",
                "asked": "AC on above 80", "thing": "guestroom at 80"})
            sent = [c for c in calls(fake[1]) if c[2:4] == ["edit", "w-1"]][-1]
            assert "--when=above 80" in sent and "--name=Cool the guestroom above 80" in sent
            assert "--asked=AC on above 80" in sent and "--thing=guestroom at 80" in sent
        finally:
            manager.shutdown()

    def test_a_watch_that_listens_is_mended_with_no_check_and_no_every(self, fake,
                                                                        tmp_path):
        # the form has no tool, args or every box for one: the word stays
        manager, client = self.served(fake[0], tmp_path)
        try:
            got = client.post("/api/watches/w-1/edit", json={
                "pick": "json:sender", "when": "contains Mum", "then": "Tell me what she said."})
            assert got.status_code == 200
            sent = [c for c in calls(fake[1]) if c[2:4] == ["edit", "w-1"]][-1]
            assert not any(a.startswith(("--check", "--args", "--every")) for a in sent)
            assert {"--pick=json:sender", "--when=contains Mum",
                    "--then=Tell me what she said."} <= set(sent)
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


OUTSIDE = {"name": "outside-temp", "thing": "the outside temperature",
           "check": {"tool": "web_fetch", "args": {"url": "https://api.open-meteo.com/x"}},
           "pick": {"json": "current.temperature_2m"}, "every": "15m"}


class TestReadings:
    def test_the_model_is_told_which_readings_are_kept_and_never_their_value(
            self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            layer = agent.prompt.get("watches")
        finally:
            manager.shutdown()
        assert "Readings Prahari already keeps" in layer
        assert "`mcp__prahari__read_indriya` instead of calling the tool" in layer
        assert "- outside-temp: reads the outside temperature every 15 minutes" in layer
        assert ("- steps: reads the steps every 1 hour: looks with fit_steps (asha's, "
                "shared with the house) [paused: 3 readings failed]") in layer
        assert "56.9" not in layer          # a value in the prompt is stale in minutes

    def test_with_no_readings_only_the_how_is_said(self):
        layer = prahari_link.prompt_text({"serving": True},
                                         readings=prahari_link.readings_text([]))
        assert "create_indriya" in layer and "`sense`" in layer
        assert "Readings Prahari already keeps" not in layer

    def test_many_readings_are_named_up_to_a_limit(self):
        cards = [{"name": f"r{n:02d}", "owner": "local", "sentence": "reads x"}
                 for n in range(prahari_link.READINGS_NAMED + 3)]
        said = prahari_link.readings_text(cards)
        assert "- r11: reads x" in said and "r12" not in said
        assert "and 3 more (`mcp__prahari__list_indriyas`)" in said

    def test_at_the_computer_ones_made_from_the_owners_chat_are_theirs(self):
        cards = [{"name": "outside-temp", "owner": "mahen", "sentence": "reads x"}]
        assert "mahen's" not in prahari_link.readings_text(cards, also_mine="mahen")
        assert "(mahen's, shared" in prahari_link.readings_text(cards)
        assert "(mahen's, shared" in prahari_link.readings_text(cards, "asha", "mahen")

    def test_another_persons_readings_are_asked_for_as_them(self, fake):
        data, program = prahari_link.load("on", str(fake[0]))
        link = prahari_link.Prahari(mode="on", data=data, program=program, person="asha")
        assert [c["name"] for c in link.readings()] == ["outside-temp", "steps"]
        assert calls(fake[1])[-1][-4:] == ["indriya", "list", "--as=asha", "--json"]
        mine = prahari_link.Prahari(mode="on", data=data, program=program)
        mine.readings()
        assert calls(fake[1])[-1][-3:] == ["indriya", "list", "--json"]

    def test_a_prahari_with_no_readings_is_no_readings_not_a_failed_turn(self, fake,
                                                                         tmp_path):
        (tmp_path / "state-readings").write_text("not json")
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            layer = agent.prompt.get("watches")
        finally:
            manager.shutdown()
        assert "preview_watch" in layer and "Readings Prahari" not in layer

    def test_a_new_reading_says_the_call_and_whether_it_only_reads(self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path,
                                     tools=[Named("web_fetch", read_only=False)])
        try:
            said = card(agent, "create_indriya", OUTSIDE)
            house = card(agent, "create_indriya", {**OUTSIDE, "share": "house"})
        finally:
            manager.shutdown()
        assert said.startswith("New reading kept up to date: outside-temp")
        assert 'every 15m: web_fetch {"url": "https://api.open-meteo.com/x"}' in said
        assert "web_fetch IS NOT READ-ONLY" in said
        assert "the outside temperature (the value at current.temperature_2m)" in said
        assert "Who may read it\n  only you" in said
        assert "watches that sense it decide" in said
        assert "ANYONE IN THE HOUSE" in house and "never call anything as you" in house

    def test_a_reading_from_a_template_says_its_call_its_blanks_and_its_signer(
            self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            said = card(agent, "install_indriya_template", {
                "template": "air-quality", "inputs": {"latitude": 35.79}})
            market = card(agent, "install_indriya_template", {
                "template": "air-quality", "from_market": True, "name": "aqi"})
        finally:
            manager.shutdown()
        assert said.startswith("New reading kept up to date: air-quality\n"
                               "  from the template air-quality that comes with Prahari")
        assert 'calls web_fetch {"url": "https://aq.example/[latitude]"} every 1h' in said
        assert "Filled in with\n  latitude = 35.79" in said
        assert market.startswith("New reading kept up to date: aqi")
        assert "made only if a reviewer you trust signed it" in market

    def test_a_change_says_the_reading_now_and_that_its_watches_start_over(
            self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            mend = card(agent, "update_indriya", {"name": "outside-temp",
                                                  "pick": {"json": "current.x"}})
            pace = card(agent, "update_indriya", {"name": "outside-temp", "every": "30m"})
            gone = card(agent, "delete_indriya", {"name": "outside-temp"})
        finally:
            manager.shutdown()
        assert mend.startswith("Change the reading outside-temp\n  now: outside-temp: 56.9")
        assert "start over" in mend
        assert "How often it reads\n  every 30m" in pace and "start over" not in pace
        assert gone.startswith("Delete the reading outside-temp and everything it read")

    def test_a_watch_that_senses_says_its_reading_not_a_missing_tool(self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            said = card(agent, "create_watch", {"name": "Heat the guestroom",
                                                "sense": "outside-temp",
                                                "when": {"below": 60}})
        finally:
            manager.shutdown()
        assert "the reading 'outside-temp' that Prahari already keeps" in said
        assert "NO TOOL" not in said and "every 1h" not in said
        assert "now: It sees 61.2 now" in said      # Prahari's own preview, asked

    def test_the_readings_are_named_again_once_one_is_made_in_the_turn(self, fake,
                                                                       tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            (tmp_path / "state-readings").write_text(json.dumps([
                {"name": "guestroom-temp", "owner": "local", "state": "active",
                 "sentence": "reads the guestroom temperature every 5 minutes"}]))
            agent.registry.get("mcp__prahari__create_indriya").run(OUTSIDE, agent.ctx)
            layer = agent.prompt.get("watches")
        finally:
            manager.shutdown()
        assert "- guestroom-temp: reads the guestroom temperature" in layer
        assert "outside-temp" not in layer


class TestReadingsOnThePanel:
    served = TestThePanel.served

    def test_the_panel_lists_readings_and_marks_which_are_theirs(self, fake, tmp_path):
        manager, client = self.served(fake[0], tmp_path)
        try:
            got = client.get("/api/indriyas").json()["indriyas"]
        finally:
            manager.shutdown()
        assert [(i["name"], i["mine"]) for i in got] == [("outside-temp", True),
                                                         ("steps", False)]

    def test_read_now_pause_and_resume_go_through_the_program(self, fake, tmp_path):
        manager, client = self.served(fake[0], tmp_path)
        try:
            assert client.post("/api/indriyas/outside-temp/look").json()["result"] == {
                "started": "outside-temp"}
            paused = client.post("/api/indriyas/outside-temp/pause").json()
            bad = client.post("/api/indriyas/--help/pause")
            odd = client.post("/api/indriyas/outside-temp/rm")
        finally:
            manager.shutdown()
        assert paused["result"]["state"] == "paused"
        assert ["indriya", "pause", "outside-temp", "--json"] in [c[-4:] for c in calls(fake[1])]
        assert bad.status_code == 400 and "not a name" in bad.json()["detail"]
        assert odd.status_code == 404 and "no indriya action" in odd.json()["detail"]


class TestOneCallByItselfOnTheCard:
    def test_the_layer_says_ask_for_fresh_and_move_only_on_a_yes(self):
        cards = [{"name": "outside-temp", "owner": "local", "sentence": "reads x"}]
        layer = prahari_link.prompt_text({"serving": True},
                                         readings=prahari_link.readings_text(cards))
        assert "give `max_age` (\"5m\") and it is read again first" in layer
        assert "goes by that reading by itself" in layer
        assert "only on the person's yes" in layer and "`from_watch`" in layer

    def test_a_watch_that_will_go_by_a_reading_says_so_in_capitals(self, fake, tmp_path):
        (tmp_path / "state-reader").write_text("")
        _, agent, manager, _ = start(fake[0], tmp_path,
                                     tools=[Named("tradingview_open", read_only=True)])
        try:
            said = card(agent, "create_watch", PRICE)
            own = card(agent, "create_watch", {**PRICE, "look_itself": True})
        finally:
            manager.shutdown()
        assert ("BUT A READING ALREADY MAKES THIS CALL: It goes by the reading "
                "outside-temp") in said and "No second call is made" in said
        assert "look_itself" not in said.split("No second call")[0].split("CALL:")[1]
        assert "ALREADY MAKES" not in own

    def test_moving_a_watch_onto_a_reading_says_what_changes(self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            said = card(agent, "update_watch", {"id": "w-1", "sense": "outside-temp"})
        finally:
            manager.shutdown()
        assert said.startswith("Change watch w-1\n  w-1 [active] ALAB under 50")
        assert "moves onto the reading outside-temp: it stops making its own call" in said
        assert "memory starts over" not in said

    def test_moving_a_watch_back_to_its_own_call_says_so(self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            said = card(agent, "update_watch", {"id": "w-1", "look_itself": True})
        finally:
            manager.shutdown()
        assert "goes back to making its reading's call itself" in said
        assert "the reading may slow back down" in said
        assert "memory starts over" not in said

    def test_a_reading_made_from_a_watch_says_whether_others_move(self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            listed = card(agent, "create_indriya", {"name": "alab", "from_watch": "w-1"})
            moving = card(agent, "create_indriya", {"name": "alab", "from_watch": "w-1",
                                                    "move": True, "share": "house"})
        finally:
            manager.shutdown()
        assert "the very call watch w-1 makes, with its pick, as often as" in listed
        assert "w-1 [active] ALAB under 50" in listed
        assert "keep calling themselves until you say" in listed
        assert "NO TOOL" not in listed and "every 15m" not in listed
        assert "MOVES ONTO IT every watch of yours on that same call" in moving
        assert "ANYONE IN THE HOUSE" in moving


class TestAReadingSentIn:
    def test_the_layer_says_a_sender_can_push_its_own_number(self):
        layer = prahari_link.prompt_text({"serving": True})
        assert "pushed reading" in layer and "`pushed`: true and no check" in layer

    def test_its_card_says_nothing_is_called_and_silence_is_told(self, fake, tmp_path):
        _, agent, manager, _ = start(fake[0], tmp_path)
        try:
            said = card(agent, "create_indriya", {"name": "guestroom-temp", "pushed": True,
                                                  "thing": "the guestroom temperature"})
        finally:
            manager.shutdown()
        assert said.startswith("New reading kept up to date: guestroom-temp")
        assert "SENT IN by something outside" in said and "no call is made" in said
        assert "expected at least every 1h" in said and "after three misses" in said
        assert "NO TOOL" not in said and "every 15m" not in said
        assert "What it keeps\n  the guestroom temperature" in said


class TestWhatHappenedToAReading:
    def test_the_panel_asks_for_one_readings_history_as_the_person(self, fake, tmp_path):
        manager, client = TestThePanel.served(None, fake[0], tmp_path)
        try:
            got = client.get("/api/indriyas/outside-temp/readings")
            bad = client.get("/api/indriyas/--help/readings")
        finally:
            manager.shutdown()
        [row] = got.json()["readings"]
        assert row["decided"][0]["name"] == "Heat the guestroom" and row["others"] == 1
        assert calls(fake[1])[-1][-4:] == ["readings", "outside-temp", "--limit=20", "--json"]
        assert bad.status_code == 400 and "not a name" in bad.json()["detail"]

    def test_someone_else_is_asked_as_themselves(self, fake):
        data, program = prahari_link.load("on", str(fake[0]))
        link = prahari_link.Prahari(mode="on", data=data, program=program, person="asha")
        link.indriya_history("outside-temp", limit=5)
        assert calls(fake[1])[-1][-3:] == ["--limit=5", "--as=asha", "--json"]
