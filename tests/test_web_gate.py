"""The page's token: once it listens beyond this computer, nobody uses it without.

The bias: A PAGE ON THE HOME NETWORK IS A SHELL FOR EVERYONE ON THE
WI-FI unless every request but its own code proves it came from someone
given the link. These tests go at it the way a stranger would -- the
API, the websocket, a wrong token -- and the way the owner does: the
link once, then a cookie that keeps working with no token in sight.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from conftest import assistant_text  # noqa: E402
from test_web_server import make_session  # noqa: E402
from yantra.web import gate  # noqa: E402
from yantra.web.server import make_app  # noqa: E402

TOKEN = "s3cret-token"


def guarded() -> TestClient:
    session, _agent = make_session([assistant_text("hi")])
    return TestClient(make_app(session, token=TOKEN))


def test_without_the_token_the_api_and_the_socket_are_closed_to_a_stranger():
    client = guarded()
    assert client.get("/api/history").status_code == 401
    assert client.post("/api/message", json={"text": "rm -rf ~"}).status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
    page = client.get("/")
    assert page.status_code == 401 and "?token=" in page.text


def test_a_wrong_token_in_the_link_sets_nothing():
    client = guarded()
    answer = client.get("/?token=guess", follow_redirects=False)
    assert answer.status_code == 401 and gate.COOKIE not in client.cookies
    assert client.get("/api/history").status_code == 401


def test_the_link_once_then_the_cookie_with_no_token_in_the_address_bar():
    client = guarded()
    answer = client.get(f"/?token={TOKEN}", follow_redirects=False)
    assert answer.status_code == 303 and answer.headers["location"] == "/"
    cookie = answer.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/").status_code == 200
    assert client.get("/api/history").status_code == 200
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "state"


def test_a_program_may_send_the_token_as_a_bearer_header():
    client = guarded()
    assert client.get("/api/history",
                      headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_the_pages_own_code_needs_no_token():
    assert guarded().get("/static/app.js").status_code == 200


def test_on_this_computer_alone_there_is_no_token_unless_one_is_asked_for(monkeypatch):
    monkeypatch.delenv(gate.ENV_TOKEN, raising=False)
    assert not gate.wanted("127.0.0.1")
    assert gate.wanted("0.0.0.0") and gate.wanted("192.168.1.44")
    monkeypatch.setenv(gate.ENV_TOKEN, "given")
    assert gate.wanted("127.0.0.1")


def test_the_kept_token_is_made_once_and_readable_only_by_you(tmp_path, monkeypatch):
    monkeypatch.delenv(gate.ENV_TOKEN, raising=False)
    first = gate.page_token(tmp_path)
    assert gate.page_token(tmp_path) == first
    assert oct(os.stat(tmp_path / "web.token").st_mode & 0o777) == "0o600"
    monkeypatch.setenv(gate.ENV_TOKEN, "from-sarathi")
    assert gate.page_token(tmp_path) == "from-sarathi"
