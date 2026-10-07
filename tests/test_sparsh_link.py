"""Sparsh at startup: found, classed by its own kinds, and a card in words.

The bias is A YES THAT COUNTS ONLY WHERE IT IS ASKED FOR. The phone's
ordinary steps run without asking (Sparsh holds the risky ones itself),
so the whole of the person's protection is in two places: ``confirm``
must ask EVERY time -- a blanket yes (--yolo) included -- and its card
must say what will happen on the phone, in Sparsh's words, not show a
hold id. These tests drive a fake ``sparsh`` over the same command road
and MCP server a real one uses.

Also designed against:

* **A phone worked with nobody watching.** An unattended run gets no
  Sparsh tools at all.
* **Tools nobody asked for.** In auto mode, no attached phone means no
  tools and no prompt; asked for by name, it connects and says so.
* **A Sparsh that speaks another format.** Refused, never guessed at.
* **A tool Sparsh didn't name.** It keeps its own hint (pessimistic).
"""

from __future__ import annotations

import base64
import io
import json
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from conftest import ScriptedProvider

from yantra import sparsh_link, status
from yantra.agent import Agent
from yantra.cli.main import _connect_sparsh
from yantra.mcp import MCPManager
from yantra.permissions import PermissionRequest, yolo
from yantra.tools.base import ToolRegistry

FAKE_SPARSH = """\
    import json, sys
    LOG = {log!r}
    args = sys.argv[1:]
    with open(LOG, "a") as out:
        out.write(json.dumps(args) + "\\n")
    def send(m): sys.stdout.write(json.dumps(m) + "\\n"); sys.stdout.flush()
    KINDS = {{"look": "read", "describe_hold": "read", "tap": "act", "confirm": "confirm"}}
    def tool(name):
        hint = {{"read": {{"readOnlyHint": True}}, "act": {{"readOnlyHint": False}},
                 "confirm": {{"readOnlyHint": False, "destructiveHint": True}}}}
        return {{"name": name, "description": name, "inputSchema": {{"type": "object"}},
                "annotations": hint.get(KINDS.get(name), {{}})}}
    if args == ["status", "--json"]:
        print(json.dumps({{"format": {fmt!r}, "version": "0.1.0", "state": "/s",
                          "rules": {{"path": "/s/rules.toml", "exists": True,
                                    "never": {never!r}, "ask": ["send"]}},
                          "adb": "ok", "phones": {phones!r},
                          "mcp": {{"command": {python!r}, "args": [sys.argv[0], "mcp"]}},
                          "tools": KINDS}}))
    elif args[0] == "look":
        if "--serial" in args and args[args.index("--serial") + 1] == "gone":
            sys.exit("sparsh: no phone called gone is attached")
        if "--shot" in args:
            with open(args[args.index("--shot") + 1], "wb") as png:
                png.write(b"\\x89PNG fake")
        print(json.dumps({{"app": "com.android.settings", "size": [1080, 2400], "note": "",
                          "elements": [{{"n": 1, "kind": "item", "label": "Airplane mode",
                                        "bounds": [0, 0, 10, 10], "tap": True,
                                        "switch": True, "on": False, "enabled": True}}]}}))
    elif args[0] == "mcp":
        TOOLS = [tool(n) for n in ("look", "describe_hold", "tap", "confirm", "mystery")]
        for line in sys.stdin:
            msg = json.loads(line)
            method, mid = msg.get("method"), msg.get("id")
            if method == "initialize":
                send({{"jsonrpc": "2.0", "id": mid, "result": {{
                    "protocolVersion": msg["params"]["protocolVersion"],
                    "capabilities": {{"tools": {{}}}}, "serverInfo": {{"name": "sparsh"}}}}}})
            elif method == "tools/list":
                send({{"jsonrpc": "2.0", "id": mid, "result": {{"tools": TOOLS}}}})
            elif method == "tools/call":
                name, a = msg["params"]["name"], msg["params"].get("arguments") or {{}}
                if name == "describe_hold" and a.get("hold") != "h1":
                    send({{"jsonrpc": "2.0", "id": mid, "result": {{"isError": True,
                        "content": [{{"type": "text", "text": "no step is waiting"}}]}}}})
                    continue
                text = {{"describe_hold": 'On the phone emulator-5554: tap button "Send SMS" '
                                         'in com.google.android.apps.messaging\\n'
                                         '1 field "running late"'}}.get(name, "done")
                send({{"jsonrpc": "2.0", "id": mid, "result": {{
                    "content": [{{"type": "text", "text": text}}]}}}})
            elif mid is not None:
                send({{"jsonrpc": "2.0", "id": mid, "error": {{"code": -32601, "message": "no"}}}})
    else:
        sys.exit("unexpected: " + " ".join(args))
"""

EMULATOR = [{"serial": "emulator-5554", "state": "device", "model": "sdk_gphone64"}]


def make_sparsh(tmp_path: Path, *, fmt: str = sparsh_link.FORMAT, phones=None,
                never=()) -> Path:
    program = tmp_path / "sparsh"
    program.write_text(f"#!{sys.executable}\n" + textwrap.dedent(FAKE_SPARSH).format(
        log=str(tmp_path / "sparsh-calls.log"), fmt=fmt,
        phones=EMULATOR if phones is None else phones, never=list(never),
        python=sys.executable))
    program.chmod(0o755)
    return program


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.setattr(sparsh_link.shutil, "which", lambda _name: None)
    monkeypatch.delenv(sparsh_link.ENV, raising=False)
    monkeypatch.delenv("YANTRA_UNATTENDED", raising=False)


def start(tmp_path, flag):
    agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
    manager = MCPManager(agent.registry, agent=agent,
                         memory_path=tmp_path / ".yantra" / "mcp.json")
    console = Console(file=io.StringIO(), width=200)
    code = _connect_sparsh(SimpleNamespace(sparsh=flag), manager, agent, console)
    return code, agent, manager, console.file.getvalue()


def tool(agent, name):
    return agent.registry.get(f"mcp__sparsh__{name}")


class TestClassed:
    def test_reads_and_acts_run_unasked_and_confirm_always_asks(self, clean, tmp_path):
        code, agent, manager, out = start(tmp_path, str(make_sparsh(tmp_path)))
        try:
            assert code is None
            assert "sparsh: 5 tool(s); phone emulator-5554 (sdk_gphone64)" in out
            assert tool(agent, "look").read_only and tool(agent, "tap").read_only
            assert not tool(agent, "tap").always_ask
            confirm = tool(agent, "confirm")
            assert not confirm.read_only and confirm.always_ask
        finally:
            manager.shutdown()

    def test_yolo_is_not_a_yes_to_confirm(self, clean, tmp_path):
        _, agent, manager, _ = start(tmp_path, str(make_sparsh(tmp_path)))
        try:
            confirm = tool(agent, "confirm")
            request = PermissionRequest(tool_name=confirm.name, arguments={"hold": "h1"},
                                        summary="", read_only=confirm.read_only,
                                        always_ask=confirm.always_ask)
            assert yolo(request) is False
        finally:
            manager.shutdown()

    def test_a_tool_sparsh_did_not_name_keeps_its_own_hint(self, clean, tmp_path):
        _, agent, manager, _ = start(tmp_path, str(make_sparsh(tmp_path)))
        try:
            assert tool(agent, "mystery").read_only is False
        finally:
            manager.shutdown()


class TestTheCard:
    def test_it_says_what_will_happen_on_the_phone(self, clean, tmp_path):
        _, agent, manager, _ = start(tmp_path, str(make_sparsh(tmp_path)))
        try:
            card = tool(agent, "confirm").summary({"hold": "h1"}, agent.ctx)
            assert card.startswith("Do this on the phone?")
            assert 'tap button "Send SMS"' in card and '"running late"' in card
        finally:
            manager.shutdown()

    def test_a_hold_it_cannot_describe_is_said_in_capitals(self, clean, tmp_path):
        _, agent, manager, _ = start(tmp_path, str(make_sparsh(tmp_path)))
        try:
            card = tool(agent, "confirm").summary({"hold": "h9"}, agent.ctx)
            assert "SPARSH CANNOT DESCRIBE IT: no step is waiting" in card
        finally:
            manager.shutdown()


class TestTheModelIsTold:
    def test_by_number_and_what_a_hold_means(self, clean, tmp_path):
        _, agent, manager, _ = start(tmp_path, str(make_sparsh(tmp_path)))
        try:
            layer = agent.prompt.get("phone")
            assert "emulator-5554" in layer and "BY NUMBER" in layer
            assert "mcp__sparsh__confirm" in layer and "Never try to get round" in layer
        finally:
            manager.shutdown()

    def test_apps_kept_out_are_said_at_start(self, clean, tmp_path):
        _, _, manager, out = start(tmp_path, str(make_sparsh(tmp_path, never=["*bank*"])))
        try:
            assert "kept out of *bank*" in out
        finally:
            manager.shutdown()


class TestWhenNot:
    def test_no_phone_in_auto_mode_is_silence(self, clean, tmp_path, monkeypatch):
        program = make_sparsh(tmp_path, phones=[])
        monkeypatch.setattr(sparsh_link.shutil, "which", lambda _name: str(program))
        code, agent, manager, out = start(tmp_path, None)
        try:
            assert code is None and out == "" and agent.sparsh is None
            assert not any(n.startswith("mcp__sparsh__") for n in agent.registry.names())
        finally:
            manager.shutdown()

    def test_no_phone_when_asked_for_connects_and_says_so(self, clean, tmp_path):
        _, agent, manager, out = start(tmp_path, str(make_sparsh(tmp_path, phones=[])))
        try:
            assert "phone no phone attached" in out
            assert "none is attached right now" in agent.prompt.get("phone")
        finally:
            manager.shutdown()

    def test_a_phone_waiting_for_its_usb_yes_is_not_ready(self):
        data = {"phones": [{"serial": "R58", "state": "unauthorized"}]}
        assert sparsh_link.ready_phones(data) == []

    def test_nobody_watching_gets_no_phone(self, clean, tmp_path, monkeypatch):
        monkeypatch.setenv("YANTRA_UNATTENDED", "1")
        program = make_sparsh(tmp_path)
        code, agent, manager, _ = start(tmp_path, str(program))
        try:
            assert code is None and agent.sparsh is None
            assert not (tmp_path / "sparsh-calls.log").exists()
        finally:
            manager.shutdown()

    def test_another_format_is_refused(self, clean, tmp_path, capsys):
        program = make_sparsh(tmp_path, fmt="sparsh.status.v9")
        code, _, manager, _ = start(tmp_path, str(program))
        manager.shutdown()
        assert code == 2 and "speaks 'sparsh.status.v9'" in capsys.readouterr().err

    def test_off_never_looks(self, clean, tmp_path):
        code, agent, manager, out = start(tmp_path, "off")
        manager.shutdown()
        assert code is None and out == "" and agent.sparsh is None


def test_status_reports_sparsh_found_even_with_no_phone(clean, tmp_path, monkeypatch):
    program = make_sparsh(tmp_path, phones=[])
    monkeypatch.setenv(sparsh_link.ENV, str(program))
    data = status.report()
    assert data["sparsh"] == {"found": True, "program": str(program), "phones": []}
    assert any(line == f"sparsh: no phone attached ({program})" for line in status.lines(data))
    calls = [json.loads(x) for x in (tmp_path / "sparsh-calls.log").read_text().splitlines()]
    assert calls == [["status", "--json"]]


class TestThePanel:
    """The page watches; it never becomes the agent's last look."""

    def served(self, tmp_path, **kw):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app

        _, agent, manager, _ = start(tmp_path, str(make_sparsh(tmp_path, **kw)))
        session = WebSession()
        session.attach(agent, None, mcp=manager)
        return manager, TestClient(make_app(session))

    def test_the_page_state_says_which_phone(self, clean, tmp_path):
        manager, client = self.served(tmp_path, never=["*bank*"])
        try:
            sparsh = client.get("/api/state").json()["sparsh"]
            assert sparsh["found"] and sparsh["ready"] == ["emulator-5554"]
            assert sparsh["rules"]["never"] == ["*bank*"] and sparsh["tools"] == 5
            assert client.get("/api/phone").json()["phones"][0]["model"] == "sdk_gphone64"
        finally:
            manager.shutdown()

    def test_the_screen_is_a_peek_with_a_picture(self, clean, tmp_path):
        manager, client = self.served(tmp_path)
        try:
            got = client.get("/api/phone/screen", params={"serial": "emulator-5554"}).json()
            assert got["screen"]["elements"][0]["label"] == "Airplane mode"
            assert base64.b64decode(got["shot"]) == b"\x89PNG fake"
            looks = [c for c in calls(tmp_path) if c[0] == "look"]
            assert looks and all("--peek" in c for c in looks)
            assert looks[0][:5] == ["look", "--peek", "--json", "--serial", "emulator-5554"]
        finally:
            manager.shutdown()

    def test_an_option_is_not_a_phone(self, clean, tmp_path):
        manager, client = self.served(tmp_path)
        try:
            said = client.get("/api/phone/screen", params={"serial": "--help"})
            assert said.status_code == 400 and "not a phone's serial" in said.json()["detail"]
            assert not any(c[0] == "look" for c in calls(tmp_path))
        finally:
            manager.shutdown()

    def test_a_phone_gone_is_said_not_crashed(self, clean, tmp_path):
        manager, client = self.served(tmp_path)
        try:
            said = client.get("/api/phone/screen", params={"serial": "gone"})
            assert said.status_code == 503
            assert said.json()["detail"] == "sparsh: no phone called gone is attached"
        finally:
            manager.shutdown()

    def test_with_no_sparsh_the_panel_says_how_to_get_one(self, clean, tmp_path):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app

        _, agent, manager, _ = start(tmp_path, None)
        session = WebSession()
        session.attach(agent, None, mcp=manager)
        client = TestClient(make_app(session))
        assert client.get("/api/state").json()["sparsh"] is None
        said = client.get("/api/phone")
        assert said.status_code == 400 and "attach a phone" in said.json()["detail"]
        manager.shutdown()


def calls(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "sparsh-calls.log"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
