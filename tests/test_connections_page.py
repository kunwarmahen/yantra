"""The Connections panel: sign in from the page, and the tools follow.

The bias is A PAGE THAT KNOWS TOO MUCH. Signing in is Setu's: the page
starts ``setu connect --json`` and shows the address it prints, and
nothing in Yantra ever holds a code, a key or a client file's contents.
So these tests drive a fake ``setu`` program -- the same command road a
real one uses -- and check what reaches the page, what reaches the
tools, and what never reaches either.

Also designed against:

* **A sign-in started from another device.** Google's reply comes back
  to a port on THIS computer, so a page elsewhere is refused and given
  the command instead -- never a sign-in that hangs forever.
* **Tools changing under a running turn.** A sign-in that finishes
  mid-turn reaches the tools when the turn ends.
* **Tools outliving their account.** Disconnect pulls the server and
  its tools; a connection made in a terminal appears on refresh.
* **An option smuggled in as an account name.** It is one argv word, so
  it may not start with ``-``.
"""

from __future__ import annotations

import io
import json
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest
from rich.console import Console

from conftest import ScriptedProvider
from test_setu_link import CONNECTOR, VERBS

from yantra import setu_link
from yantra.agent import Agent
from yantra.cli.main import _connect_setu
from yantra.mcp import MCPManager
from yantra.tools.base import ToolRegistry

#: A `setu` with state: connections in a JSON file, a client file that may
#: or may not be set, and a sign-in that waits for the "person" (a file)
#: unless told not to.
FAKE_SETU = """\
    import json, os, sys, time
    STATE = {state!r}
    CONNECTOR = {connector!r}
    def load():
        return json.load(open(STATE))
    def save(s):
        json.dump(s, open(STATE, "w"))
    def row(ref, level):
        connector, account = ref.split(":")
        return {{"ref": ref, "connector": connector, "account": account,
                 "email": account + "@example.com", "level": level,
                 "level_label": level.title(), "scopes": [], "last_used": None,
                 "installed": True,
                 "mcp": {{"name": ref.replace(":", "-"), "command": {python!r},
                         "args": [CONNECTOR]}}}}
    def emit(**e):
        print(json.dumps(e), flush=True)
    s = load()
    args = sys.argv[1:]
    s.setdefault("calls", []).append(args)
    save(s)
    if args[:2] == ["status", "--json"]:
        ready = bool(s.get("client_file"))
        print(json.dumps({{
            "format": "setu.status.v1", "version": "0.2.0", "command": sys.argv[0],
            "problems": [],
            "connections": [row(r, lv) for r, lv in s["connections"].items()],
            "connectors": [{{"id": "gmail", "name": "Gmail", "summary": "Read your mail.",
                            "connected": any(r.startswith("gmail:") for r in s["connections"]),
                            "verbs": {verbs!r}, "default_level": "read",
                            "levels": [{{"name": "read", "label": "Read only", "description": "",
                                        "scopes": []}},
                                       {{"name": "send", "label": "Read, draft and send",
                                        "description": "", "scopes": []}}],
                            "ready": ready,
                            "not_ready": "" if ready else "needs a client file"}}],
            "setup": {{"google_client_file": s.get("client_file")}},
            "watch": [STATE, None], "catalog": s.get("catalog")}}))
    elif args[0] == "connect" and "--site" in args:
        site = args[args.index("--site") + 1]
        ref = site.split(".")[0] + ":" + args[args.index("--as") + 1]
        emit(event="started", ref=None, site=site, level="read", scopes=[])
        emit(event="window", browser="chrome", url=None, login_url=None)
        emit(event="ask", question="Setu could not tell. Did you sign in?")
        if sys.stdin.readline().strip() != "yes":
            emit(event="error", message="could not tell whether you signed in")
            sys.exit(2)
        s = load()
        s["connections"][ref] = "read"
        save(s)
        emit(event="connected", ref=ref, email=site, level="read",
             level_label="Read only", asked_level="read", note="",
             manifest_path="/tmp/sites/x.toml")
    elif args[0] == "connect":
        ref = "gmail:" + args[args.index("--as") + 1]
        level = args[args.index("--level") + 1]
        emit(event="started", ref=ref, level=level, level_label=level.title(), scopes=[])
        emit(event="url", url="https://accounts.example/o/oauth2/auth?state=abc")
        while s.get("hold") and not os.path.exists(STATE + ".release"):
            time.sleep(0.05)
        if s.get("refuse"):
            emit(event="error", message="the person said no")
            sys.exit(2)
        s = load()
        s["connections"][ref] = level
        save(s)
        emit(event="connected", ref=ref, email=ref.split(":")[1] + "@example.com",
             level=level, level_label=level.title(), asked_level=level)
    elif args[0] == "install":
        print("installed " + args[1] + " 1.0.0, its wheel checked against the signed catalog")
    elif args[:2] == ["catalog", "recipe"]:
        if s.get("refuse_recipe"):
            print("error: the bundle is not the one the signed catalog names", file=sys.stderr)
            sys.exit(2)
        target = os.path.join(args[args.index("--into") + 1], args[2])
        os.makedirs(os.path.join(target, "scripts"), exist_ok=True)
        open(os.path.join(target, "SKILL.md"), "w").write(
            "---\\nname: " + args[2] + "\\ndescription: Set a Home Assistant fan's "
            "speed. Use when the person asks for a fan faster or slower.\\n"
            "origin: learned\\nneeds: setu:homeassistant\\n---\\n\\n1. Run the script.\\n")
        open(os.path.join(target, "scripts", "fan.py"), "w").write("print('fan')\\n")
        print(args[2] + ": checked against the signed catalog")
    elif args[:2] == ["site", "guide"]:
        if s.get("refuse_guide"):
            print("error: the guide in sites/x.toml is written by hand", file=sys.stderr)
            sys.exit(2)
        print(args[2] + ": guide saved")
    elif args[0] == "disconnect":
        s["connections"].pop(args[1], None)
        save(s)
        print("disconnected " + args[1])
    elif args[:2] == ["config", "client-file"]:
        if not args[2].endswith(".json"):
            print("error: not a Desktop app client file", file=sys.stderr)
            sys.exit(2)
        s["client_file"] = args[2]
        save(s)
        print("client-file: " + args[2])
"""


@pytest.fixture
def setu(tmp_path, monkeypatch):
    connector = tmp_path / "connector.py"
    connector.write_text(textwrap.dedent(CONNECTOR))
    state = tmp_path / "setu-state.json"
    state.write_text(json.dumps({"connections": {"gmail:personal": "read"},
                                 "client_file": "/keys/client.json"}))
    program = tmp_path / "setu"
    program.write_text(f"#!{sys.executable}\n" + textwrap.dedent(FAKE_SETU).format(
        state=str(state), connector=str(connector), python=sys.executable, verbs=VERBS))
    program.chmod(0o755)
    monkeypatch.setitem(sys.modules, "setu.status", None)
    monkeypatch.setattr(setu_link.shutil, "which", lambda _name: None)
    monkeypatch.delenv(setu_link.ENV, raising=False)

    class Fake:
        path = program

        def get(self):
            return json.loads(state.read_text())

        def set(self, **kw):
            data = self.get()
            data.update(kw)
            state.write_text(json.dumps(data))

        def release(self):
            (tmp_path / "setu-state.json.release").write_text("")
    return Fake()


def build(tmp_path, program, flag=None):
    agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
    manager = MCPManager(agent.registry, agent=agent,
                         memory_path=tmp_path / ".yantra" / "mcp.json")
    console = Console(file=io.StringIO(), width=200)
    _connect_setu(SimpleNamespace(setu=flag or str(program)), manager, agent, console)
    return agent, manager


def served(tmp_path, program, *, local=True):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from yantra.web.server import WebSession, make_app

    agent, manager = build(tmp_path, program)
    session = WebSession()
    session.attach(agent, None, mcp=manager)
    where = ("127.0.0.1", 51000) if local else ("192.168.1.40", 51000)
    return session, agent, manager, TestClient(make_app(session), client=where)


def until(ws, kind, event=None, limit=80):
    seen = []
    while len(seen) < limit:
        seen.append(ws.receive_json())
        last = seen[-1]
        if last["type"] == kind and (event is None or last.get("event") == event):
            return seen
    raise AssertionError(f"never got {kind}/{event}: {[s['type'] for s in seen]}")


# ---- the live handle --------------------------------------------------------------


class TestSync:
    def test_startup_leaves_a_handle_the_page_can_use(self, setu, tmp_path):
        agent, manager = build(tmp_path, setu.path)
        try:
            assert agent.setu.servers == {"gmail-personal"}
            assert "mcp__gmail-personal__search_threads" in agent.registry
        finally:
            manager.shutdown()

    def test_a_level_changed_in_setu_restarts_its_connector(self, setu, tmp_path):
        """A connector reads its level once, at start: without a restart the
        model keeps the old level's tools after "change access"."""
        agent, manager = build(tmp_path, setu.path)
        try:
            before = manager.sessions["gmail-personal"]
            agent.setu.refresh()
            assert agent.setu.sync(manager, agent).notes == []     # nothing changed
            assert manager.sessions["gmail-personal"] is before
            setu.set(connections={"gmail:personal": "send"})
            agent.setu.refresh()
            done = agent.setu.sync(manager, agent)
            assert "gmail:personal restarted at Send" in done.notes
            assert manager.sessions["gmail-personal"] is not before
            assert "mcp__gmail-personal__search_threads" in agent.registry
        finally:
            manager.shutdown()

    def test_a_connection_made_elsewhere_arrives_on_refresh(self, setu, tmp_path):
        agent, manager = build(tmp_path, setu.path)
        try:
            setu.set(connections={"gmail:personal": "read", "gmail:work": "send"})
            agent.setu.refresh()
            done = agent.setu.sync(manager, agent)
            assert set(done.connected) == {"gmail-personal", "gmail-work"}
            # two Gmail accounts: one set of tools, with `account`
            assert "mcp__gmail__search_threads" in agent.registry
            assert "mcp__gmail-work__search_threads" not in agent.registry
            assert "`mcp__gmail__*`" in agent.system and "`work`" in agent.system
        finally:
            manager.shutdown()

    def test_a_gone_connection_takes_its_tools(self, setu, tmp_path):
        agent, manager = build(tmp_path, setu.path)
        try:
            setu.set(connections={})
            agent.setu.refresh()
            done = agent.setu.sync(manager, agent)
            assert done.dropped == ["gmail-personal"]
            assert not [n for n in agent.registry.names() if n.startswith("mcp__gmail")]
            assert "gmail-personal" not in manager.sessions
        finally:
            manager.shutdown()

    def test_the_description_carries_no_secret_shaped_field(self, setu, tmp_path):
        agent, manager = build(tmp_path, setu.path)
        try:
            text = json.dumps(agent.setu.describe(manager))
            assert "secret" not in text and "token" not in text.lower()
            row = agent.setu.describe(manager)["connections"][0]
            assert row["tools"] == 3 and row["running"]
        finally:
            manager.shutdown()


# ---- signing in -------------------------------------------------------------------


class TestSignIn:
    def test_events_arrive_as_setu_prints_them(self, setu):
        events = []
        signin = setu_link.SignIn(str(setu.path), "gmail", "work", "read", events.append)
        signin.thread.join(10)
        assert [e["event"] for e in events] == ["started", "url", "connected", "done"]
        assert events[1]["url"].startswith("https://accounts.example/")

    def test_cancel_stops_it_and_saves_nothing(self, setu):
        setu.set(hold=True)
        events = []
        signin = setu_link.SignIn(str(setu.path), "gmail", "work", "read", events.append)
        deadline = time.time() + 10
        while len(events) < 2 and time.time() < deadline:
            time.sleep(0.02)
        signin.cancel()
        signin.thread.join(10)
        assert events[-2]["event"] == "cancelled" and events[-1]["event"] == "done"
        assert "gmail:work" not in setu.get()["connections"]

    def test_an_account_name_cannot_be_an_option(self, setu):
        with pytest.raises(setu_link.SetuLinkError, match="account name"):
            setu_link.SignIn(str(setu.path), "gmail", "--client-file=/etc/x", "read",
                             lambda e: None)



def wait_for(events, kind, timeout=10.0):
    deadline = time.time() + timeout
    while not any(e.get("event") == kind for e in events) and time.time() < deadline:
        time.sleep(0.02)
    assert any(e.get("event") == kind for e in events), events


class TestAddingASite:
    def test_setu_asks_and_the_answer_goes_back(self, setu):
        events = []
        signin = setu_link.SignIn(str(setu.path), "", "work", "", events.append,
                                  site="example.com")
        assert signin.ref == "example.com:work"
        wait_for(events, "ask")
        signin.answer(True)
        signin.thread.join(10)
        assert [e["event"] for e in events] == ["started", "window", "ask", "connected",
                                                "done"]
        assert signin.ref == "example:work" and events[-1]["ref"] == "example:work"
        assert ["connect", "--site", "example.com", "--as", "work", "--json"] \
            in setu.get()["calls"]

    def test_no_saves_nothing(self, setu):
        events = []
        signin = setu_link.SignIn(str(setu.path), "", "work", "", events.append,
                                  site="example.com")
        wait_for(events, "ask")
        signin.answer(False)
        signin.thread.join(10)
        assert events[-2]["event"] == "error" and "example:work" not in setu.get()["connections"]

    def test_an_answer_nobody_asked_for_is_refused(self, setu):
        signin = setu_link.SignIn(str(setu.path), "gmail", "work", "read", lambda e: None)
        signin.thread.join(10)
        with pytest.raises(setu_link.SetuLinkError, match="not waiting"):
            signin.answer(True)

    def test_an_address_cannot_be_an_option(self, setu):
        for bad in ("--browser=/bin/sh", "example com", "localhost"):
            with pytest.raises(setu_link.SetuLinkError, match="not a site"):
                setu_link.SignIn(str(setu.path), "", "work", "", lambda e: None, site=bad)

    def test_from_the_page_the_question_and_the_answer(self, setu, tmp_path):
        session, agent, manager, client = served(tmp_path, setu.path)
        try:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                res = client.post("/api/connections/add-site", json={
                    "site": "example.com", "account": "work"})
                assert res.status_code == 200, res.text
                seen = until(ws, "setu_signin", "ask")
                assert seen[-1]["question"].endswith("Did you sign in?")
                assert client.post("/api/connections/signin-answer",
                                   json={"yes": True}).status_code == 200
                seen = until(ws, "connections")
            events = [e.get("event") for e in seen if e["type"] == "setu_signin"]
            assert "connected" in events
            assert "example:work" in setu.get()["connections"]
        finally:
            manager.shutdown()

    def test_a_page_on_another_device_is_given_the_command(self, setu, tmp_path):
        _, _, manager, client = served(tmp_path, setu.path, local=False)
        try:
            res = client.post("/api/connections/add-site", json={"site": "example.com"})
            assert res.status_code == 403
            assert "setu connect --site example.com --as personal" in res.json()["detail"]
        finally:
            manager.shutdown()


class TestATerminalCatchesUp:
    def repl(self, setu, tmp_path):
        from yantra.cli.repl import Repl
        agent, manager = build(tmp_path, setu.path)
        out = io.StringIO()
        repl = Repl(agent, Console(file=out, width=200), mcp=manager,
                    input_fn=lambda prompt: "")
        return agent, manager, repl, out

    def test_an_account_connected_elsewhere_arrives_before_the_next_turn(self, setu,
                                                                         tmp_path):
        agent, manager, repl, out = self.repl(setu, tmp_path)
        try:
            repl._setu_catch_up()                       # nothing moved
            assert out.getvalue() == ""
            time.sleep(0.01)
            state = setu.get()
            state["connections"]["gmail:work"] = "read"
            setu.set(connections=state["connections"])  # `setu connect` in another terminal
            repl._setu_catch_up()
            assert "setu: gmail-work (" in out.getvalue()
            assert "gmail-personal" not in out.getvalue()   # only what is new
            assert any(n.startswith("mcp__gmail-work__") or "gmail" in n
                       for n in agent.registry.names())
            before = out.getvalue()
            repl._setu_catch_up()                       # and only once
            assert out.getvalue() == before
        finally:
            manager.shutdown()

    def test_a_disconnect_elsewhere_takes_the_tools(self, setu, tmp_path):
        agent, manager, repl, out = self.repl(setu, tmp_path)
        try:
            repl._setu_catch_up()
            time.sleep(0.01)
            setu.set(connections={})
            repl._setu_catch_up()
            assert "gmail-personal -- gone from this session" in out.getvalue()
        finally:
            manager.shutdown()


CATALOG = {"source": "https://catalog.test", "key": "k", "issued": "2026-10-04",
           "connectors": [{"id": "notion", "name": "Notion", "label": "partner",
                           "author": "you", "installs": 3, "version": "1.0.0"}],
           "recipes": [{"name": "ha-fan-speed", "needs": ["homeassistant"],
                        "author": "priya", "label": "partner",
                        "bundle": {"url": "https://catalog.test/files/x.json",
                                   "sha256": "0" * 64}}]}


class TestInstallingFromTheCatalog:
    def test_a_listed_connector_is_installed_through_setu(self, setu, tmp_path):
        setu.set(catalog=CATALOG)
        _, _, manager, client = served(tmp_path, setu.path)
        try:
            res = client.post("/api/catalog/install", json={"id": "notion"})
            assert res.status_code == 200, res.text
            assert ["install", "notion"] in setu.get()["calls"]
            assert client.post("/api/catalog/install",
                               json={"id": "evil"}).status_code == 404
        finally:
            manager.shutdown()

    def test_a_page_elsewhere_gets_the_command(self, setu, tmp_path):
        setu.set(catalog=CATALOG)
        _, _, manager, client = served(tmp_path, setu.path, local=False)
        try:
            res = client.post("/api/catalog/install", json={"id": "notion"})
            assert res.status_code == 403 and "setu install notion" in res.json()["detail"]
            res = client.post("/api/catalog/recipe/preview", json={"name": "ha-fan-speed"})
            assert res.status_code == 403
        finally:
            manager.shutdown()

    def test_a_recipe_is_shown_whole_then_installed(self, setu, tmp_path, monkeypatch):
        setu.set(catalog=CATALOG)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        _, _, manager, client = served(tmp_path, setu.path)
        try:
            seen = client.post("/api/catalog/recipe/preview", json={"name": "ha-fan-speed"})
            assert seen.status_code == 200, seen.text
            body = seen.json()
            assert set(body["files"]) == {"SKILL.md", "scripts/fan.py"}
            assert body["digest"].startswith("sha256:")
            assert not (tmp_path / "home" / ".yantra").exists()       # shown, not installed
            done = client.post("/api/catalog/recipe/install", json={"key": body["key"]})
            assert done.status_code == 200, done.text
            assert (tmp_path / "home" / ".yantra" / "skills" / "learned" /
                    "ha-fan-speed" / "scripts" / "fan.py").is_file()
            again = client.post("/api/catalog/recipe/install", json={"key": body["key"]})
            assert again.status_code == 404                           # one preview, one use
        finally:
            manager.shutdown()

    def test_a_refused_bundle_shows_nothing(self, setu, tmp_path):
        setu.set(catalog=CATALOG, refuse_recipe=True)
        _, _, manager, client = served(tmp_path, setu.path)
        try:
            res = client.post("/api/catalog/recipe/preview", json={"name": "ha-fan-speed"})
            assert res.status_code == 502 and "signed catalog" in res.json()["detail"]
        finally:
            manager.shutdown()


class TestKeepingAGuide:
    def offer(self, session):
        from yantra.site_guide import GuideOffer
        session._keep_guide(GuideOffer(site="example", name="Example", old="",
                                       new="Orders: /orders", calls=3))
        return next(iter(session.kept_guides))

    def test_kept_as_edited_through_setu(self, setu, tmp_path):
        session, _, manager, client = served(tmp_path, setu.path)
        try:
            key = self.offer(session)
            tray = client.get("/api/kept").json()
            assert tray["guides"][0]["new"] == "Orders: /orders"
            assert session.state()["kept"]["count"] == 1
            res = client.post(f"/api/kept/guide/{key}/save",
                              json={"guide": "Orders: /account/orders"})
            assert res.status_code == 200, res.text
            assert ["site", "guide", "example", "--set", "Orders: /account/orders"] \
                in setu.get()["calls"]
            assert res.json()["guides"] == [] and session.state()["kept"]["count"] == 0
        finally:
            manager.shutdown()

    def test_a_refusal_keeps_the_offer_and_says_why(self, setu, tmp_path):
        setu.set(refuse_guide=True)
        session, _, manager, client = served(tmp_path, setu.path)
        try:
            key = self.offer(session)
            res = client.post(f"/api/kept/guide/{key}/save", json={})
            assert res.status_code == 502 and "written by hand" in res.json()["detail"]
            assert key in session.kept_guides
        finally:
            manager.shutdown()

    def test_dropped_writes_nothing(self, setu, tmp_path):
        session, _, manager, client = served(tmp_path, setu.path)
        try:
            key = self.offer(session)
            assert client.post(f"/api/kept/guide/{key}/drop").json()["guides"] == []
            assert not any(c[:1] == ["site"] for c in setu.get()["calls"])
        finally:
            manager.shutdown()


# ---- the page -------------------------------------------------------------------------


class TestPage:
    def test_the_panel_data(self, setu, tmp_path):
        _, _, manager, client = served(tmp_path, setu.path)
        try:
            data = client.get("/api/connections").json()
            assert data["found"] and data["local"] is True
            assert data["connections"][0]["ref"] == "gmail:personal"
            assert data["connectors"][0]["ready"] is True
        finally:
            manager.shutdown()

    def test_sign_in_from_the_page_and_the_tools_follow(self, setu, tmp_path):
        session, agent, manager, client = served(tmp_path, setu.path)
        try:
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
                res = client.post("/api/connections/connect", json={
                    "connector": "gmail", "account": "work", "level": "send"})
                assert res.status_code == 200, res.text
                seen = until(ws, "connections")
            events = [e.get("event") for e in seen if e["type"] == "setu_signin"]
            assert events[:3] == ["started", "url", "connected"]
            assert "mcp__gmail__send_message" in agent.registry
            assert seen[-1]["sync"]["connected"]["gmail-work"] == 3
            assert ["connect", "gmail", "--as", "work", "--level", "send", "--json"] \
                in setu.get()["calls"]
        finally:
            manager.shutdown()

    def test_a_page_on_another_device_is_given_the_command(self, setu, tmp_path):
        _, _, manager, client = served(tmp_path, setu.path, local=False)
        try:
            res = client.post("/api/connections/connect", json={
                "connector": "gmail", "account": "work", "level": "read"})
            assert res.status_code == 403
            assert "setu connect gmail --as work --level read" in res.json()["detail"]
            assert not any(c[0] == "connect" for c in setu.get()["calls"])
        finally:
            manager.shutdown()

    def test_a_level_the_connector_does_not_have_is_refused(self, setu, tmp_path):
        _, _, manager, client = served(tmp_path, setu.path)
        try:
            res = client.post("/api/connections/connect", json={
                "connector": "gmail", "account": "work", "level": "everything"})
            assert res.status_code == 400
        finally:
            manager.shutdown()

    def test_no_client_file_means_no_sign_in_yet(self, setu, tmp_path):
        setu.set(client_file=None)
        _, _, manager, client = served(tmp_path, setu.path)
        try:
            res = client.post("/api/connections/connect", json={
                "connector": "gmail", "account": "work", "level": "read"})
            assert res.status_code == 409 and "client file" in res.json()["detail"]
            res = client.post("/api/connections/client-file", json={"path": "/k/c.txt"})
            assert res.status_code == 400
            res = client.post("/api/connections/client-file", json={"path": "/k/c.json"})
            assert res.status_code == 200 and res.json()["connectors"][0]["ready"]
        finally:
            manager.shutdown()

    def test_the_setup_a_connector_names_is_passed_to_setu_and_nothing_else(self, setu,
                                                                            tmp_path):
        setu.set(client_file=None)
        _, _, manager, client = served(tmp_path, setu.path)
        try:
            res = client.post("/api/connections/setup", json={"key": "vault_path",
                                                              "value": "/x"})
            assert res.status_code == 400 and "unknown setup" in res.json()["detail"]
            res = client.post("/api/connections/setup", json={"key": "google_client_file",
                                                              "value": "--evil"})
            assert res.status_code == 400
            res = client.post("/api/connections/setup", json={"key": "google_client_file",
                                                              "value": "/k/c.json"})
            assert res.status_code == 200 and res.json()["connectors"][0]["ready"]
            assert ["config", "client-file", "/k/c.json"] in setu.get()["calls"]
        finally:
            manager.shutdown()

    def test_a_sign_in_that_ends_mid_turn_waits_for_the_turn(self, setu, tmp_path):
        session, agent, manager, client = served(tmp_path, setu.path)
        try:
            session.turn_active = True
            session.start_signin("gmail", "work", "read")
            session.signin.thread.join(10)
            assert "mcp__gmail__search_threads" not in agent.registry
            session.turn_active = False
            session._setu_settle()
            assert "mcp__gmail__search_threads" in agent.registry
        finally:
            manager.shutdown()

    def test_disconnect_revokes_through_setu_and_pulls_the_tools(self, setu, tmp_path):
        _, agent, manager, client = served(tmp_path, setu.path)
        try:
            res = client.post("/api/connections/disconnect", json={"ref": "gmail:personal"})
            assert res.status_code == 200, res.text
            assert ["disconnect", "gmail:personal"] in setu.get()["calls"]
            assert res.json()["connections"] == []
            assert not [n for n in agent.registry.names() if n.startswith("mcp__gmail")]
        finally:
            manager.shutdown()

    def test_off_means_the_panel_says_so(self, setu, tmp_path):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app

        agent, manager = build(tmp_path, setu.path, flag="off")
        session = WebSession()
        session.attach(agent, None, mcp=manager)
        client = TestClient(make_app(session))
        assert client.get("/api/connections").json()["mode"] == "off"
        assert client.post("/api/connections/refresh").status_code == 400


# ---- a package's question, on the page (notes/110) --------------------------------


def served_package(tmp_path, program, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from yantra.web.server import WebSession, make_app

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
    manager = MCPManager(agent.registry, agent=agent)
    spec = SimpleNamespace(name="helper", root=tmp_path / "helper",
                           connections=("gmail:read",))
    _connect_setu(SimpleNamespace(setu=str(program)), manager, agent,
                  Console(file=io.StringIO(), width=200), spec, None)
    session = WebSession()
    session.attach(agent, None, mcp=manager)
    return agent, manager, TestClient(make_app(session), client=("127.0.0.1", 51000))


def gmail_tools(agent):
    return sorted(n for n in agent.registry.names() if n.startswith("mcp__gmail"))


class TestPackageOnThePage:
    def test_the_question_waits_on_the_page_and_a_yes_brings_the_tools(
            self, setu, tmp_path, monkeypatch):
        agent, manager, client = served_package(tmp_path, setu.path, monkeypatch)
        try:
            state = client.get("/api/connections").json()
            assert state["package"] == "helper" and state["granted"] == []
            assert [a["need"] for a in state["asks"]] == ["gmail:read"]
            assert gmail_tools(agent) == []
            out = client.post("/api/connections/answer",
                              json={"need": "gmail:read", "allow": True}).json()
            assert out["asks"] == [] and out["granted"] == ["gmail:read"]
            assert gmail_tools(agent) == ["mcp__gmail-personal__search_threads"]
            assert setu_link.load_approved(agent.setu.package_key) == {"gmail:read"}
        finally:
            manager.shutdown()

    def test_not_now_clears_the_question_and_keeps_nothing(self, setu, tmp_path,
                                                          monkeypatch):
        agent, manager, client = served_package(tmp_path, setu.path, monkeypatch)
        try:
            out = client.post("/api/connections/answer",
                              json={"need": "gmail:read", "allow": False}).json()
            assert out["asks"] == [] and gmail_tools(agent) == []
            assert not setu_link.approvals_path().exists()
            res = client.post("/api/connections/answer",
                              json={"need": "gmail:read", "allow": True})
            assert res.status_code == 404
        finally:
            manager.shutdown()

    def test_forget_takes_the_tools_now_and_the_yes_for_good(self, setu, tmp_path,
                                                            monkeypatch):
        agent, manager, client = served_package(tmp_path, setu.path, monkeypatch)
        try:
            client.post("/api/connections/answer", json={"need": "gmail:read", "allow": True})
            out = client.post("/api/connections/forget", json={"need": "gmail:read"}).json()
            assert out["forgot"] == ["gmail:read"] and out["granted"] == []
            assert gmail_tools(agent) == []
            assert setu_link.load_approved(agent.setu.package_key) == set()
        finally:
            manager.shutdown()

    def test_an_account_connected_mid_session_is_asked_then(self, setu, tmp_path,
                                                             monkeypatch):
        setu.set(connections={})
        agent, manager, client = served_package(tmp_path, setu.path, monkeypatch)
        try:
            assert client.get("/api/connections").json()["asks"] == []
            setu.set(connections={"gmail:personal": "read"})
            out = client.post("/api/connections/refresh").json()["sync"]
            assert out["asked"] == ["helper wants to read your Gmail "
                                    "(personal@example.com). Allow?"]
            state = client.get("/api/connections").json()
            assert [a["need"] for a in state["asks"]] == ["gmail:read"]
            assert gmail_tools(agent) == []          # asked, not yet allowed
            # another look does not ask twice
            assert client.post("/api/connections/refresh").json()["sync"]["asked"] == []
            client.post("/api/connections/answer", json={"need": "gmail:read", "allow": True})
            assert gmail_tools(agent) == ["mcp__gmail-personal__search_threads"]
        finally:
            manager.shutdown()

    def test_not_now_and_forget_are_not_asked_again_this_sitting(self, setu, tmp_path,
                                                                 monkeypatch):
        agent, manager, client = served_package(tmp_path, setu.path, monkeypatch)
        try:
            client.post("/api/connections/answer", json={"need": "gmail:read", "allow": False})
            assert client.post("/api/connections/refresh").json()["sync"]["asked"] == []
            agent.setu.declined.clear()
            client.post("/api/connections/refresh")
            client.post("/api/connections/answer", json={"need": "gmail:read", "allow": True})
            client.post("/api/connections/forget", json={"need": "gmail:read"})
            assert client.post("/api/connections/refresh").json()["sync"]["asked"] == []
            assert client.get("/api/connections").json()["asks"] == []
        finally:
            manager.shutdown()

    def test_your_own_session_has_nothing_to_forget(self, setu, tmp_path):
        session, agent, manager, client = served(tmp_path, setu.path)
        try:
            assert client.post("/api/connections/forget", json={}).status_code == 409
        finally:
            manager.shutdown()


def test_forget_connections_from_the_terminal(tmp_path, monkeypatch, capsys):
    from yantra.cli.main import main

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    pkg = tmp_path / "helper"
    pkg.mkdir()
    (pkg / "agent.toml").write_text('[agent]\nname = "helper"\n'
                                    '[connections]\nneeds = ["gmail"]\n')
    key = setu_link.package_key("helper", pkg)
    setu_link.save_approved(key, "gmail:read")
    assert main(["--agent", str(pkg), "--forget-connections"]) == 0
    assert "forgot gmail:read for helper" in capsys.readouterr().out
    assert setu_link.load_approved(key) == set()
