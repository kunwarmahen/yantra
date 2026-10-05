"""Samay at startup: found, carded in plain words, and a panel on the page.

The bias is A YES TO WORDS NOBODY CAN READ. A schedule spends money and
reaches accounts while nobody watches, and the call that makes one is a
prompt, a JSON ``when`` and a list of globs. A card that shows those raw
is a card people approve without reading. So these tests drive a fake
``samay`` -- the same command road and MCP server a real one uses -- and
check that the card says when (in Samay's own sentence), what, when the
person hears, which tools each glob really reaches in THIS agent, and
which accounts the run reaches through Setu; and that a made-up tool
name is said in capitals rather than passed through.

Also designed against:

* **Offers from a run nobody watches.** A scheduled run is itself an
  unattended Yantra; it gets no Samay tools and no prompt to offer.
* **Somebody else's schedules.** The server is started ``--for`` one
  person; the model has no argument to name another.
* **A Samay that speaks another format.** Refused, never guessed at.
* **An option smuggled in as a schedule id.** It becomes one argv word
  of a ``samay`` command, so it may not start with ``-``.
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

from yantra import samay_link
from yantra.agent import Agent
from yantra.cli.main import _connect_samay
from yantra.mcp import MCPManager
from yantra.tools.base import Tool, ToolRegistry

FAKE_SAMAY = """\
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
    CARD = {{"id": "s-abc", "owner": "local", "prompt": "check my mail", "state": "active",
             "sentence": "every 2 hours", "notify": "when_new", "allow_tools": [],
             "upcoming": [], "last_run": None}}
    if args == ["status", "--json"]:
        print(json.dumps({{"format": {fmt!r}, "version": "0.1.0", "state": {state!r},
                          "serving": {serving!r}, "url": None,
                          "schedules": {{"active": 1, "paused": 0, "done": 0}},
                          "dvara": None,
                          "mcp": {{"command": {python!r},
                                  "args": [sys.argv[0], "--state", {state!r}, "mcp"]}}}}))
    elif args == ["list", "--json"]:
        print(json.dumps([CARD]))
    elif args[0] in ("pause", "resume") and args[-1] == "--json":
        print(json.dumps({{**CARD, "state": "paused" if args[0] == "pause" else "active"}}))
    elif args[0] == "rm":
        print(json.dumps({{"removed": args[1]}}))
    elif args[0] == "runs":
        print(json.dumps([{{"id": "r1", "schedule": args[1], "outcome": "ok",
                           "due_at": "2026-10-05T12:00:00+00:00", "summary": "2 new"}}]))
    elif args[0] == "run-now":
        pass
    elif args[0] == "mcp":
        TOOLS = [tool("preview_schedule", True), tool("list_schedules", True),
                 tool("create_schedule", False), tool("delete_schedule", False)]
        for line in sys.stdin:
            msg = json.loads(line)
            method, mid = msg.get("method"), msg.get("id")
            if method == "initialize":
                send({{"jsonrpc": "2.0", "id": mid, "result": {{
                    "protocolVersion": msg["params"]["protocolVersion"],
                    "capabilities": {{"tools": {{}}}}, "serverInfo": {{"name": "samay"}}}}}})
            elif method == "tools/list":
                send({{"jsonrpc": "2.0", "id": mid, "result": {{"tools": TOOLS}}}})
            elif method == "tools/call":
                name, a = msg["params"]["name"], msg["params"].get("arguments") or {{}}
                if name == "preview_schedule" and a.get("when") == {{"every": "1s"}}:
                    send({{"jsonrpc": "2.0", "id": mid, "result": {{"isError": True,
                        "content": [{{"type": "text", "text": "5 minutes at the least"}}]}}}})
                    continue
                text = {{"preview_schedule": "every 2 hours, from now -- next: 14:00, 16:00",
                         "list_schedules": "s-abc [active] every 2 hours\\n  does: check my mail",
                         }}.get(name, "done")
                send({{"jsonrpc": "2.0", "id": mid, "result": {{
                    "content": [{{"type": "text", "text": text}}]}}}})
            elif mid is not None:
                send({{"jsonrpc": "2.0", "id": mid, "error": {{"code": -32601, "message": "no"}}}})
    else:
        sys.exit("unexpected: " + " ".join(args))
"""


def make_samay(tmp_path: Path, *, fmt: str = samay_link.FORMAT, serving: bool = False):
    log = tmp_path / "samay-calls.log"
    program = tmp_path / "samay"
    program.write_text(f"#!{sys.executable}\n" + textwrap.dedent(FAKE_SAMAY).format(
        log=str(log), state=str(tmp_path / "state"), fmt=fmt, serving=serving,
        python=sys.executable))
    program.chmod(0o755)
    return program, log


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def fake_samay(tmp_path, monkeypatch):
    monkeypatch.setattr(samay_link.shutil, "which", lambda _name: None)
    monkeypatch.delenv(samay_link.ENV, raising=False)
    monkeypatch.delenv("YANTRA_UNATTENDED", raising=False)
    return make_samay(tmp_path)


class Named(Tool):
    """A tool that only has a name and a class -- what the card reads."""

    description = "x"
    parameters: dict = {"type": "object"}

    def __init__(self, name: str, read_only: bool = False, always_ask: bool = False):
        self.name, self.read_only, self.always_ask = name, read_only, always_ask

    def summary(self, args, ctx):
        return self.name

    def run(self, args, ctx):
        return "ran"


def start(program, tmp_path, flag=None, spec=None, tools=()):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    agent = Agent(ScriptedProvider([]), model="m", tools=registry)
    manager = MCPManager(agent.registry, agent=agent,
                         memory_path=tmp_path / ".yantra" / "mcp.json")
    console = Console(file=io.StringIO(), width=200)
    code = _connect_samay(SimpleNamespace(samay=flag or str(program)), manager, agent,
                          console, spec)
    return code, agent, manager, console.file.getvalue()


def card(agent, name, args):
    return agent.registry.get(f"mcp__samay__{name}").summary(args, agent.ctx)


class TestFoundAndAnnounced:
    def test_the_server_is_started_for_the_person_here(self, fake_samay, tmp_path):
        code, agent, manager, out = start(fake_samay[0], tmp_path)
        try:
            assert code is None
            assert "mcp__samay__create_schedule" in agent.registry
            mcp_call = [c for c in calls(fake_samay[1]) if "mcp" in c][0]
            assert mcp_call[-2:] == ["--for", "local"]
            assert "samay: 4 tool(s), 1 active schedule(s)" in out
        finally:
            manager.shutdown()

    def test_a_package_session_makes_schedules_that_run_that_package(self, fake_samay,
                                                                     tmp_path):
        spec = SimpleNamespace(root=tmp_path / "mail-agent")
        _, _, manager, _ = start(fake_samay[0], tmp_path, spec=spec)
        try:
            mcp_call = [c for c in calls(fake_samay[1]) if "mcp" in c][0]
            assert mcp_call[-4:] == ["--for", "local", "--agent", str(tmp_path / "mail-agent")]
        finally:
            manager.shutdown()

    def test_a_clock_that_is_not_running_is_said_at_start_and_to_the_model(
            self, fake_samay, tmp_path):
        _, agent, manager, out = start(fake_samay[0], tmp_path)
        try:
            assert "clock is NOT running" in out
            layer = agent.prompt.get("schedules")
            assert "preview_schedule" in layer and "say yes" in layer
            assert "samay serve" in layer
        finally:
            manager.shutdown()

    def test_the_model_is_told_reads_need_no_allowing_and_how_to_notify(
            self, fake_samay, tmp_path):
        _, agent, manager, _ = start(fake_samay[0], tmp_path)
        try:
            layer = agent.prompt.get("schedules")
            assert "run unasked anyway: leave them out" in layer
            assert "Never allow a tool that sends" in layer
            assert "`always` for a digest, a summary or a reminder" in layer
        finally:
            manager.shutdown()

    def test_reads_run_unasked_and_writes_are_asked(self, fake_samay, tmp_path):
        _, agent, manager, _ = start(fake_samay[0], tmp_path)
        try:
            assert agent.registry.get("mcp__samay__preview_schedule").read_only is True
            assert agent.registry.get("mcp__samay__create_schedule").read_only is False
        finally:
            manager.shutdown()


class TestAHostServingSeveralPeople:
    def test_each_turn_is_for_its_person_on_the_hosts_road(self, fake_samay, tmp_path):
        """Dvara's seam: one Samay per turn, for that turn's person."""
        data, program = samay_link.load("on", str(fake_samay[0]))
        agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
        manager = MCPManager(agent.registry, agent=agent,
                             memory_path=tmp_path / ".yantra" / "mcp.json")
        try:
            samay_link.Samay(mode="on", data=data, program=program, person="priya",
                             agent="mail", runner="dvara",
                             seen_at="Ask me to list or pause them.",
                             clock_off="The owner has to start the clock.").connect(manager,
                                                                                    agent)
            mcp_call = [c for c in calls(fake_samay[1]) if "mcp" in c][0]
            assert mcp_call[-6:] == ["--for", "priya", "--agent", "mail",
                                     "--runner", "dvara"]
            layer = agent.prompt.get("schedules")
            assert "Ask me to list or pause them." in layer
            assert "Schedules panel" not in layer
            assert "The owner has to start the clock." in layer
            assert "the person starts `samay serve`" not in layer
        finally:
            manager.shutdown()

    def test_a_person_that_looks_like_an_option_is_refused(self, fake_samay):
        data, _ = samay_link.load("on", str(fake_samay[0]))
        with pytest.raises(samay_link.SamayLinkError):
            samay_link.server_config(data, person="--state")


class TestWhenItIsNotThere:
    def test_auto_with_no_samay_is_silence(self, fake_samay, tmp_path):
        code, agent, manager, out = start(None, tmp_path, flag="auto")
        assert code is None and agent.samay is None and out == ""

    def test_asked_for_and_missing_stops_the_start(self, fake_samay, tmp_path):
        code, agent, _, _ = start(None, tmp_path, flag="on")
        assert code == 2 and agent.samay is None

    def test_off_never_looks(self, fake_samay, tmp_path):
        code, agent, _, _ = start(None, tmp_path, flag="off")
        assert code is None and agent.samay is None
        assert calls(fake_samay[1]) == []

    def test_a_run_nobody_watches_gets_no_samay(self, fake_samay, tmp_path, monkeypatch):
        monkeypatch.setenv("YANTRA_UNATTENDED", "1")
        code, agent, _, _ = start(fake_samay[0], tmp_path)
        assert code is None and agent.samay is None
        assert "mcp__samay__create_schedule" not in agent.registry
        assert calls(fake_samay[1]) == []

    def test_another_format_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.delenv("YANTRA_UNATTENDED", raising=False)
        program, _ = make_samay(tmp_path, fmt="samay.status.v9")
        with pytest.raises(samay_link.SamayLinkError, match="samay.status.v9"):
            samay_link.load("on", str(program))

    def test_a_flag_outranks_the_environment_and_a_path_means_on(self):
        assert samay_link.resolve_mode(None, "off") == ("off", None)
        assert samay_link.resolve_mode("on", "off") == ("on", None)
        assert samay_link.resolve_mode("/opt/samay", "") == ("on", "/opt/samay")


class TestTheCardIsWords:
    def test_create_says_when_what_and_when_you_hear(self, fake_samay, tmp_path):
        _, agent, manager, _ = start(fake_samay[0], tmp_path)
        try:
            said = card(agent, "create_schedule", {
                "prompt": "check my mail;\n tell me what needs me",
                "when": {"every": "2h"}, "notify": "when_new"})
            assert "every 2 hours, from now -- next: 14:00, 16:00" in said
            assert "check my mail; tell me what needs me" in said
            assert "only when there is something new" in said
            assert "NOBODY watching" in said
            assert '{"every"' not in said
        finally:
            manager.shutdown()

    def test_a_when_samay_cannot_read_is_said_on_the_card(self, fake_samay, tmp_path):
        _, agent, manager, _ = start(fake_samay[0], tmp_path)
        try:
            said = card(agent, "create_schedule", {"prompt": "x", "when": {"every": "1s"}})
            assert "SAMAY CANNOT READ" in said and "5 minutes at the least" in said
        finally:
            manager.shutdown()

    def test_each_glob_shows_what_it_reaches_and_a_made_up_name_is_loud(
            self, fake_samay, tmp_path):
        tools = [Named("browser_open", read_only=True), Named("browser_click")]
        _, agent, manager, _ = start(fake_samay[0], tmp_path, tools=tools)
        try:
            said = card(agent, "create_schedule", {
                "prompt": "x", "when": {"at": "08:00"},
                "allow_tools": ["browser_*", "browser_navigate"]})
            assert "browser_*: browser_click, browser_open" in said
            assert "browser_navigate: NO TOOL BY THIS NAME" in said
        finally:
            manager.shutdown()

    def test_no_allow_tools_means_only_reads(self):
        said = samay_link.explain_create({"prompt": "x", "when": "every 2h"},
                                         registry=ToolRegistry(), sentence="every 2 hours")
        assert "only tools that read" in said

    def test_a_spending_tool_is_said_to_be_refused_with_nobody_there(self):
        registry = ToolRegistry()
        registry.register(Named("shop_buy", always_ask=True))
        said = samay_link.explain_create({"prompt": "x", "when": "every 2h",
                                          "allow_tools": ["shop_*"]}, registry=registry)
        assert "shop_buy spends money" in said and "refused" in said

    def test_the_accounts_a_run_reaches_through_setu_are_named(self):
        registry = ToolRegistry()
        registry.register(Named("mcp__gmail-personal__search", read_only=True))
        registry.register(Named("mcp__gmail-personal__send_message"))
        row = {"ref": "gmail:personal", "connector": "gmail", "account": "personal",
               "email": "me@example.com", "level_label": "Read, draft and send",
               "mcp": {"name": "gmail-personal"}}
        link = SimpleNamespace(connections=[row],
                               connectors={"gmail": {"name": "Gmail"}})
        setu = SimpleNamespace(link=link, allow=None, sites={}, servers={"gmail-personal"})
        said = samay_link.explain_create(
            {"prompt": "x", "when": "every 2h", "allow_tools": ["mcp__gmail-personal__*"]},
            registry=registry, setu=setu)
        assert "through Setu, it can read without asking: Gmail (me@example.com)" in said
        assert ("THIS CHANGES THINGS IN YOUR Gmail (me@example.com) WITHOUT ASKING: "
                "send_message") in said

    def test_merged_tools_name_every_account_they_act_on(self):
        registry = ToolRegistry()
        registry.register(Named("mcp__gmail__send_message"))
        rows = [{"ref": f"gmail:{a}", "connector": "gmail", "account": a,
                 "email": f"{a}@example.com", "mcp": {"name": f"gmail-{a}"}}
                for a in ("mine", "personal")]
        link = SimpleNamespace(connections=rows, connectors={"gmail": {"name": "Gmail"}})
        setu = SimpleNamespace(link=link, allow=None, sites={},
                               servers={"gmail-mine", "gmail-personal"})
        said = samay_link.explain_create(
            {"prompt": "x", "when": "every 2h", "allow_tools": ["mcp__gmail__*"]},
            registry=registry, setu=setu)
        assert ("YOUR Gmail (mine@example.com; personal@example.com) WITHOUT ASKING: "
                "send_message") in said

    def test_what_it_may_do_unasked_comes_before_a_long_prompt(self):
        registry = ToolRegistry()
        registry.register(Named("browser_click"))
        said = samay_link.explain_create(
            {"prompt": "word " * 300, "when": "every 2h", "allow_tools": ["browser_*"]},
            registry=registry)
        assert said.index("browser_click") < said.index("does:")

    def test_a_package_does_not_hear_of_accounts_it_was_not_allowed(self):
        row = {"ref": "gmail:personal", "connector": "gmail", "mcp": {"name": "gmail-personal"}}
        link = SimpleNamespace(connections=[row], connectors={})
        setu = SimpleNamespace(link=link, allow={"outlook": "read"}, sites={},
                               servers={"gmail-personal"})
        said = samay_link.explain_create({"prompt": "x", "when": "every 2h"},
                                         registry=ToolRegistry(), setu=setu)
        assert "Setu" not in said

    def test_delete_says_for_good_and_which_schedule(self, fake_samay, tmp_path):
        _, agent, manager, _ = start(fake_samay[0], tmp_path)
        try:
            said = card(agent, "delete_schedule", {"id": "s-abc"})
            assert "for good" in said and "s-abc [active] every 2 hours" in said
        finally:
            manager.shutdown()


class TestThePanel:
    def served(self, program, tmp_path):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app

        _, agent, manager, _ = start(program, tmp_path)
        session = WebSession()
        session.attach(agent, None, mcp=manager)
        return agent, manager, TestClient(make_app(session))

    def test_the_page_state_says_samay_is_here(self, fake_samay, tmp_path):
        _, manager, client = self.served(fake_samay[0], tmp_path)
        try:
            samay = client.get("/api/state").json()["samay"]
            assert samay["found"] is True and samay["person"] == "local"
            assert samay["serving"] is False and samay["counts"]["active"] == 1
        finally:
            manager.shutdown()

    def test_the_list_runs_and_switches_go_through_the_program(self, fake_samay, tmp_path):
        _, manager, client = self.served(fake_samay[0], tmp_path)
        try:
            listed = client.get("/api/schedules").json()
            assert listed["schedules"][0]["id"] == "s-abc"
            assert client.get("/api/schedules/s-abc/runs").json()["runs"][0]["summary"] == "2 new"
            assert client.post("/api/schedules/s-abc/pause").json()["result"]["state"] == "paused"
            assert client.post("/api/schedules/s-abc/delete").json()["result"] == {
                "removed": "s-abc"}
            assert client.post("/api/schedules/s-abc/run").json() == {"started": "s-abc"}
            state = str(tmp_path / "state")
            made = calls(fake_samay[1])
            assert ["--state", state, "pause", "s-abc", "--json"] in made
            assert ["--state", state, "rm", "s-abc", "--json"] in made
        finally:
            manager.shutdown()

    def test_an_option_is_not_a_schedule_id(self, fake_samay, tmp_path):
        _, manager, client = self.served(fake_samay[0], tmp_path)
        try:
            assert client.post("/api/schedules/--help/pause").status_code == 400
            assert client.post("/api/schedules/--help/run").status_code == 400
            assert not any("--help" in c for c in calls(fake_samay[1]))
        finally:
            manager.shutdown()

    def test_with_no_samay_the_panel_says_how_to_get_one(self, tmp_path, monkeypatch):
        monkeypatch.setattr(samay_link.shutil, "which", lambda _name: None)
        monkeypatch.delenv(samay_link.ENV, raising=False)
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app

        _, agent, manager, _ = start(None, tmp_path, flag="auto")
        session = WebSession()
        session.attach(agent, None, mcp=manager)
        client = TestClient(make_app(session))
        assert client.get("/api/state").json()["samay"] is None
        answer = client.get("/api/schedules")
        assert answer.status_code == 400 and "samay" in answer.json()["detail"]
