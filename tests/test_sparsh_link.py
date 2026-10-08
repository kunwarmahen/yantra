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
* **A phone's screen sent to a cloud model unasked.** Screenshots of
  screens the list can't read go to a local model that can see; to a
  cloud one only with YANTRA_PHONE_SHOTS=on; and a switch to a cloud
  model mid-session stops them.
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
from yantra.permissions import PermissionRequest, card_picture, yolo
from yantra.setu_link import Link, announce, phone_rules, prompt_text
from yantra.tools.base import ToolRegistry
from yantra.types import ToolCall

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
                          **({{"shots": "--shots"}} if {shots!r} else {{}}),
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
    elif args[0] == "log":
        print(json.dumps([{{"at": "2026-10-07T10:15:07-04:00", "by": "agent",
                           "action": "press_key", "args": {{"keys": ["enter"]}},
                           "on": None, "outcome": "held",
                           "said": "h1: enter could do what item Send SMS does"}}]))
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
                if name == "describe_hold" and a.get("hold") not in ("h1", "h2"):
                    send({{"jsonrpc": "2.0", "id": mid, "result": {{"isError": True,
                        "content": [{{"type": "text", "text": "no step is waiting"}}]}}}})
                    continue
                text = {{"describe_hold": 'On the phone emulator-5554: tap button "Send SMS" '
                                         'in com.google.android.apps.messaging\\n'
                                         '1 field "running late"'}}.get(name, "done")
                content = [{{"type": "text", "text": text}}]
                if name == "describe_hold" and a.get("hold") == "h2":  # a tap by position
                    content.append({{"type": "image", "mimeType": "image/png",
                                    "data": "iVBORw0KGgo="}})
                if name == "look" and "--shots" in args:
                    content.append({{"type": "image", "mimeType": "image/png",
                                    "data": "iVBORw0KGgo="}})
                send({{"jsonrpc": "2.0", "id": mid, "result": {{"content": content}}}})
            elif mid is not None:
                send({{"jsonrpc": "2.0", "id": mid, "error": {{"code": -32601, "message": "no"}}}})
    else:
        sys.exit("unexpected: " + " ".join(args))
"""

EMULATOR = [{"serial": "emulator-5554", "state": "device", "model": "sdk_gphone64"}]


def make_sparsh(tmp_path: Path, *, fmt: str = sparsh_link.FORMAT, phones=None,
                never=(), shots=True) -> Path:
    program = tmp_path / "sparsh"
    program.write_text(f"#!{sys.executable}\n" + textwrap.dedent(FAKE_SPARSH).format(
        log=str(tmp_path / "sparsh-calls.log"), fmt=fmt,
        phones=EMULATOR if phones is None else phones, never=list(never),
        python=sys.executable, shots=shots))
    program.chmod(0o755)
    return program


@pytest.fixture
def clean(monkeypatch):
    monkeypatch.setattr(sparsh_link.shutil, "which", lambda _name: None)
    monkeypatch.delenv(sparsh_link.ENV, raising=False)
    monkeypatch.delenv("YANTRA_UNATTENDED", raising=False)


def start(tmp_path, flag, road=("", "", "")):
    agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
    manager = MCPManager(agent.registry, agent=agent,
                         memory_path=tmp_path / ".yantra" / "mcp.json")
    console = Console(file=io.StringIO(), width=200)
    code = _connect_sparsh(SimpleNamespace(sparsh=flag), manager, agent, console, road)
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

    def test_a_tap_by_position_brings_its_picture_for_the_person(self, clean, tmp_path):
        _, agent, manager, _ = start(tmp_path, str(make_sparsh(tmp_path)))
        try:
            confirm = tool(agent, "confirm")
            card = confirm.summary({"hold": "h2"}, agent.ctx)
            assert "a tap is where it is ringed" in card
            picture = card_picture(confirm, {"hold": "h2"})
            assert picture.media_type == "image/png" and picture.data == "iVBORw0KGgo="
            # a step that is words alone has none
            confirm.summary({"hold": "h1"}, agent.ctx)
            assert card_picture(confirm, {"hold": "h1"}) is None
        finally:
            manager.shutdown()

    def test_the_picture_reaches_the_gate_and_not_the_model(self, clean, tmp_path):
        _, agent, manager, _ = start(tmp_path, str(make_sparsh(tmp_path)))
        asked = []
        agent.permissions = lambda request: asked.append(request) or False
        try:
            call = ToolCall(id="c1", name="mcp__sparsh__confirm", arguments={"hold": "h2"})
            result = agent._execute(call)
            assert asked[0].picture.data == "iVBORw0KGgo="
            assert "iVBORw0KGgo" not in str(result)
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

    def test_an_iphone_is_called_an_iphone_and_told_how_back_works(self):
        iphone = {"serial": "http://127.0.0.1:8100", "state": "device", "model": "iPhone"}
        layer = sparsh_link.prompt_text({"phones": [iphone]})
        assert "person's iPhone (http://127.0.0.1:8100 (iPhone))" in layer
        assert "Android" not in layer and "no Back key" in layer

    def test_an_android_phone_gets_no_iphone_advice(self):
        android = {"serial": "emulator-5554", "state": "device", "model": "sdk"}
        layer = sparsh_link.prompt_text({"phones": [android]})
        assert "person's Android phone" in layer and "no Back key" not in layer

    def test_no_phone_says_how_either_kind_is_connected(self):
        layer = sparsh_link.prompt_text({"phones": []})
        assert "USB debugging" in layer and "WebDriverAgent" in layer

    def test_an_iphone_signature_running_out_is_said_at_start(self):
        note = "the iPhone's WebDriverAgent signature runs out Wed 08 Oct 16:02: rebuild it"
        line = sparsh_link.announce({"phones": [], "wda": {"note": note}}, 9, "sparsh")
        assert f"; {note} -- via sparsh" in line
        assert "signature" not in sparsh_link.announce({"phones": [], "wda": None}, 9, "sparsh")

    def test_apps_kept_out_are_said_at_start(self, clean, tmp_path):
        _, _, manager, out = start(tmp_path, str(make_sparsh(tmp_path, never=["*bank*"])))
        try:
            assert "kept out of *bank*" in out
        finally:
            manager.shutdown()


class TestWhenNot:
    def test_no_phone_in_auto_mode_is_silence_and_waits(self, clean, tmp_path, monkeypatch):
        program = make_sparsh(tmp_path, phones=[])
        monkeypatch.setattr(sparsh_link.shutil, "which", lambda _name: str(program))
        code, agent, manager, out = start(tmp_path, None)
        try:
            assert code is None and out == ""
            assert not any(n.startswith("mcp__sparsh__") for n in agent.registry.names())
            prompt = getattr(agent, "prompt", None)
            assert prompt is None or prompt.get("phone") is None
            # dormant, for a phone attached later
            assert agent.sparsh is not None and agent.sparsh.connected is False
        finally:
            manager.shutdown()

    def test_no_phone_when_asked_for_connects_and_says_so(self, clean, tmp_path):
        _, agent, manager, out = start(tmp_path, str(make_sparsh(tmp_path, phones=[])))
        try:
            assert "phone no phone attached" in out
            assert "none is ready right now" in agent.prompt.get("phone")
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


class TestAPhoneAttachedLater:
    """Plugged in after the start: taken up when the person says so,
    without a restart -- and nothing happens by itself."""

    def dormant(self, tmp_path, monkeypatch):
        program = make_sparsh(tmp_path, phones=[])
        monkeypatch.setattr(sparsh_link.shutil, "which", lambda _name: str(program))
        _, agent, manager, _ = start(tmp_path, None)
        return agent, manager

    def test_auto_with_a_named_program_still_waits_for_a_phone(self, clean, tmp_path):
        # What Sarathi passes: the sparsh it found, without turning it on.
        program = make_sparsh(tmp_path, phones=[])
        assert sparsh_link.resolve_mode(f"auto:{program}") == ("auto", str(program))
        _, agent, manager, out = start(tmp_path, f"auto:{program}")
        try:
            assert out == "" and not agent.sparsh.connected
            assert not [n for n in agent.registry.names() if n.startswith("mcp__sparsh__")]
            make_sparsh(tmp_path)                   # a phone, and asked again
            assert agent.sparsh.use_now(manager, agent).startswith("sparsh: 5 tool(s)")
        finally:
            manager.shutdown()

    def test_use_now_with_no_phone_says_how(self, clean, tmp_path, monkeypatch):
        agent, manager = self.dormant(tmp_path, monkeypatch)
        try:
            with pytest.raises(sparsh_link.SparshLinkError, match="no phone is ready"):
                agent.sparsh.use_now(manager, agent)
            assert not agent.sparsh.connected
        finally:
            manager.shutdown()

    def test_use_now_starts_the_tools_and_the_prompt(self, clean, tmp_path, monkeypatch):
        agent, manager = self.dormant(tmp_path, monkeypatch)
        try:
            make_sparsh(tmp_path)                   # the phone is plugged in
            said = agent.sparsh.use_now(manager, agent)
            assert said.startswith("sparsh: 5 tool(s); phone emulator-5554")
            assert tool(agent, "confirm").always_ask
            assert "BY NUMBER" in agent.prompt.get("phone")
            assert "already in use" in agent.sparsh.use_now(manager, agent)
        finally:
            manager.shutdown()

    def test_the_terminal_says_and_uses(self, clean, tmp_path, monkeypatch):
        from yantra.cli.repl import Repl

        agent, manager = self.dormant(tmp_path, monkeypatch)
        out = io.StringIO()
        repl = Repl(agent, Console(file=out, width=200), mcp=manager)
        try:
            repl._command("/phone")
            assert "phone: none attached" in out.getvalue()
            make_sparsh(tmp_path)
            repl._command("/phone")
            assert "in use: no -- /phone use to start" in out.getvalue()
            repl._command("/phone use")
            assert "sparsh: 5 tool(s)" in out.getvalue()
            repl._command("/phone log")
            assert 'press_key {"keys": ["enter"]} -> held' in out.getvalue()
        finally:
            manager.shutdown()

    def test_the_page_uses_it_and_reads_the_steps(self, clean, tmp_path, monkeypatch):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app

        agent, manager = self.dormant(tmp_path, monkeypatch)
        session = WebSession()
        session.attach(agent, None, mcp=manager)
        client = TestClient(make_app(session))
        try:
            state = client.get("/api/state").json()["sparsh"]
            assert state["found"] and not state["connected"] and state["ready"] == []
            assert client.post("/api/phone/use").status_code == 409
            make_sparsh(tmp_path)
            used = client.post("/api/phone/use").json()
            assert used["connected"] and used["said"].startswith("sparsh: 5 tool(s)")
            assert "mcp__sparsh__tap" in agent.registry
            steps = client.get("/api/phone/log", params={"serial": "emulator-5554"}).json()
            assert steps["steps"][0]["outcome"] == "held"
            looks = [c for c in calls(tmp_path) if c[0] == "log"]
            assert looks[0][:6] == ["log", "--json", "-n", "20", "--serial", "emulator-5554"]
        finally:
            manager.shutdown()


def test_an_iphone_is_named_by_its_wda_address():
    assert sparsh_link.SERIAL_RE.match("http://127.0.0.1:8100")
    assert sparsh_link.SERIAL_RE.match("emulator-5554")
    assert not sparsh_link.SERIAL_RE.match("--help")
    assert not sparsh_link.SERIAL_RE.match("http://x/../../etc")


# -- screenshots of screens the list can't read -----------------------------

LOCAL = ("ollama", "http://localhost:11434/v1", "gemma4:26b")
CLOUD = ("anthropic", "https://api.anthropic.com", "a-cloud-model")


@pytest.fixture
def sees(monkeypatch):
    monkeypatch.delenv(sparsh_link.SHOTS_ENV, raising=False)
    seen = {"gemma4:26b": True}
    monkeypatch.setattr(sparsh_link, "_can_see", lambda _url, model: seen.get(model, False))
    return seen


def looked(agent):
    return tool(agent, "look").run({}, agent.ctx)


def on_road(agent, road):
    """The agent's model as it is mid-session: asked again on every call."""
    agent.provider = SimpleNamespace(name=road[0], settings=SimpleNamespace(base_url=road[1]))
    agent.model = road[2]


class TestScreenshots:
    def test_a_local_model_that_can_see_gets_them(self, clean, sees, tmp_path):
        code, agent, manager, out = start(tmp_path, str(make_sparsh(tmp_path)), LOCAL)
        try:
            assert code is None and "screenshots on (local model)" in out
            assert ["mcp", "--shots"] in calls(tmp_path)
            on_road(agent, LOCAL)
            result = looked(agent)
            assert result.images[0].media_type == "image/png"
            assert "comes with a screenshot" in agent.prompt.get("phone")
        finally:
            manager.shutdown()

    def test_a_cloud_model_does_not_unless_the_person_says_so(self, clean, sees, tmp_path,
                                                               monkeypatch):
        _, agent, manager, out = start(tmp_path, str(make_sparsh(tmp_path)), CLOUD)
        try:
            assert "no screenshots to a cloud model (YANTRA_PHONE_SHOTS=on to allow)" in out
            assert ["mcp"] in calls(tmp_path)
            assert looked(agent) == "done"
            assert "screenshot" not in agent.prompt.get("phone")
        finally:
            manager.shutdown()
        monkeypatch.setenv(sparsh_link.SHOTS_ENV, "on")
        _, agent, manager, out = start(tmp_path, str(make_sparsh(tmp_path)), CLOUD)
        try:
            assert "screenshots on (YANTRA_PHONE_SHOTS=on)" in out
            on_road(agent, CLOUD)
            assert looked(agent).images
        finally:
            manager.shutdown()

    def test_a_switch_to_a_cloud_model_stops_them(self, clean, sees, tmp_path):
        _, agent, manager, _ = start(tmp_path, str(make_sparsh(tmp_path)), LOCAL)
        try:
            on_road(agent, LOCAL)
            assert looked(agent).images
            on_road(agent, CLOUD)
            result = looked(agent)
            assert isinstance(result, str) and "1 non-text content block(s) omitted" in result
        finally:
            manager.shutdown()

    def test_a_model_that_cannot_see_and_an_old_sparsh_get_none(self, clean, sees, tmp_path):
        blind = ("ollama", LOCAL[1], "qwen-text-only")
        _, agent, manager, out = start(tmp_path, str(make_sparsh(tmp_path)), blind)
        try:
            assert "no screenshots: qwen-text-only can't see pictures" in out
        finally:
            manager.shutdown()
        old = make_sparsh(tmp_path, shots=False)
        _, agent, manager, out = start(tmp_path, str(old), LOCAL)
        try:
            assert "this Sparsh can't send them (update it)" in out
            assert looked(agent) == "done"
        finally:
            manager.shutdown()

    @pytest.mark.parametrize("setting,said", [
        ("off", "screenshots off (YANTRA_PHONE_SHOTS=off)"),
        ("maybe", "is not auto, on or off"),
    ])
    def test_off_and_a_word_it_does_not_know_are_no(self, setting, said):
        allowed, why = sparsh_link.shots(*LOCAL, setting=setting)
        assert not allowed and said in why


# -- a site's app on the phone: Setu's phone road as Sparsh's rules ------------


X_CARD = {"id": "x", "name": "X", "verbs": {},
          "phone": {"android": "com.twitter.android", "ios": "com.atebits.Tweetie2",
                    "act_words": ["post", "like"], "spend_words": ["buy"], "pace": 3.0,
                    "guide": "Home is the timeline."}}


def x_on_phone(level="read", ref="x:personal", **row):
    return {"ref": ref, "connector": "x", "account": ref.split(":")[1], "level": level,
            "level_label": {"read": "Read only", "write": "Read and post"}[level],
            "mcp": None, "browser": None,
            "phone": {"android": "com.twitter.android", "ios": "com.atebits.Tweetie2"}, **row}


def setu_said(*rows, card=X_CARD):
    return Link(data={"connections": list(rows), "connectors": [card]}, road="test")


class TestTheAppsRules:
    def test_read_only_refuses_acting_and_spending_on_both_phones(self):
        rules = phone_rules(setu_said(x_on_phone("read")))
        assert set(rules) == {"com.twitter.android", "com.atebits.Tweetie2"}
        rule = rules["com.twitter.android"]
        assert rule["refuse"] == ["buy", "post", "like"] and rule["ask"] == []
        assert rule["pace"] == 3.0 and "x:personal" in rule["why"] and "Read only" in rule["why"]

    def test_read_and_post_asks_about_acting_and_still_refuses_spending(self):
        rule = phone_rules(setu_said(x_on_phone("write")))["com.twitter.android"]
        assert rule["refuse"] == ["buy"] and rule["ask"] == ["post", "like"]

    def test_a_packages_ceiling_lowers_the_level(self):
        rule = phone_rules(setu_said(x_on_phone("write")), allow={"x": "read"})
        assert "post" in rule["com.twitter.android"]["refuse"]
        assert phone_rules(setu_said(x_on_phone("write")), allow={"gmail": "read"}) == {}

    def test_two_connections_on_one_app_keep_the_stricter(self):
        rules = phone_rules(setu_said(x_on_phone("write", ref="x:work"),
                                      x_on_phone("read", ref="x:personal")))
        rule = rules["com.twitter.android"]
        assert "post" in rule["refuse"] and rule["ask"] == []

    def test_a_withdrawn_connector_or_none_at_all_gives_no_rules(self):
        assert phone_rules(setu_said(x_on_phone(), card={**X_CARD, "yanked": "bad"})) == {}
        assert phone_rules(None) == {}

    def test_the_model_is_told_to_use_the_app(self):
        said = prompt_text(setu_said(x_on_phone("read")))
        assert "X through its own app on the person's phone (`com.twitter.android`)" in said
        assert "Read only" in said and "Home is the timeline." in said

    def test_the_rules_go_to_sparsh_when_it_starts(self):
        agent = SimpleNamespace(setu=SimpleNamespace(link=setu_said(x_on_phone()), allow=None))
        rules = sparsh_link.app_rules_of(agent)
        config = sparsh_link.server_config({"mcp": {"command": "sparsh", "args": ["mcp"]}},
                                           app_rules=rules)
        sent = json.loads(config.env[sparsh_link.APP_RULES])
        assert "post" in sent["com.twitter.android"]["refuse"]
        assert sparsh_link.server_config({"mcp": {"command": "s"}}).env is None
        assert sparsh_link.app_rules_of(SimpleNamespace(setu=None)) == {}

    def test_the_startup_line_names_a_connection_on_the_phone(self):
        assert announce(setu_said(x_on_phone()), {}) == \
            "setu: x:personal (on the phone) -- via test"
