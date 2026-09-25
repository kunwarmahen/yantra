"""A held turn in the browser: shown, answered, or set aside (notes/88).

The bias here is a page that loses the question. The approval modal is
withdrawn when the clock runs out; with ``--on-timeout hold`` the turn
stops instead, and the questions must survive everything a person does
next -- a reload, a wrong answer, a new message -- or be dropped on
purpose:

* the turn ends ``held`` and the page is told what waits, with its age;
* ``/api/state`` carries it too, so a page opened later still shows it;
* ``/api/resume`` runs what was approved and the turn carries on, and a
  partial answer is a 400 that changes nothing;
* a new message sets the hold aside, and clear drops it;
* the resumed turn's recording names the held one.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from conftest import assistant_text  # noqa: E402
from test_web_server import drain_until, make_session  # noqa: E402
from yantra.cli.main import main  # noqa: E402
from yantra.trace import TrajectoryLog  # noqa: E402
from yantra.types import Message, ModelResponse, ToolCall, Usage  # noqa: E402
from yantra.web.server import make_app  # noqa: E402


def two_writes_at_once():
    return [ModelResponse(
        message=Message("assistant", [
            ToolCall(id="w1", name="write_thing", arguments={"text": "a"}),
            ToolCall(id="w2", name="write_thing", arguments={"text": "b"})]),
        stop_reason="tool_use", usage=Usage()),
        assistant_text("wrote one, not the other")]


def held_session(tmp_path=None):
    session, agent = make_session(two_writes_at_once())
    session.set_wait_budget(0.2, on_timeout="hold")
    if tmp_path is not None:
        session.trace = TrajectoryLog(tmp_path / "t.jsonl")
    client = TestClient(make_app(session))
    return session, agent, client


def post_and_finish(client, ws, path, payload):
    response = client.post(path, json=payload)
    assert response.status_code == 200, response.text
    return drain_until(ws, {"turn_done"})


def answers(**decisions):
    return {"answers": {k: v for k, v in decisions.items()}}


class TestTheTurnWaits:
    def test_it_ends_held_and_says_what_waits(self):
        session, agent, client = held_session()
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            envelopes = post_and_finish(client, ws, "/api/message",
                                        {"text": "go"})
        (end,) = [e for e in envelopes if e["type"] == "turn_end"]
        assert end["reason"] == "held"
        assert [c["id"] for c in end["held"]["calls"]] == ["w1", "w2"]
        assert end["held"]["age"] >= 0
        # Asked once, then withdrawn; the second was never put up.
        assert len([e for e in envelopes
                    if e["type"] == "permission_request"]) == 1

    def test_a_page_opened_later_still_sees_it(self):
        session, agent, client = held_session()
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            post_and_finish(client, ws, "/api/message", {"text": "go"})
        held = client.get("/api/state").json()["held"]
        assert [c["tool_name"] for c in held["calls"]] == ["write_thing"] * 2


class TestAnsweringIt:
    def test_resume_runs_what_was_approved_and_carries_on(self):
        session, agent, client = held_session()
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            post_and_finish(client, ws, "/api/message", {"text": "go"})
            envelopes = post_and_finish(client, ws, "/api/resume", answers(
                w1={"decision": "approve"},
                w2={"decision": "deny", "reason": "not b"}))
        results = [(e["name"], e["refusal"], e["output"]) for e in envelopes
                   if e["type"] == "tool_result"]
        assert results[0] == ("write_thing", None, "wrote:a")
        assert results[1][1] == "user" and "not b" in results[1][2]
        assert [e["reason"] for e in envelopes
                if e["type"] == "turn_end"] == ["end_turn"]
        assert client.get("/api/state").json()["held"] is None

    def test_an_edit_is_what_runs(self):
        session, agent, client = held_session()
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            post_and_finish(client, ws, "/api/message", {"text": "go"})
            envelopes = post_and_finish(client, ws, "/api/resume", answers(
                w1={"decision": "edit", "edited_args": {"text": "A"}},
                w2={"decision": "approve"}))
        assert [e["output"] for e in envelopes
                if e["type"] == "tool_result"] == ["wrote:A", "wrote:b"]

    @pytest.mark.parametrize("payload", [
        answers(w1={"decision": "approve"}),                   # w2 missing
        answers(w1={"decision": "maybe"}, w2={"decision": "approve"}),
        {"answers": "yes"},
    ])
    def test_a_wrong_answer_is_a_400_and_changes_nothing(self, payload):
        session, agent, client = held_session()
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            post_and_finish(client, ws, "/api/message", {"text": "go"})
        assert client.post("/api/resume", json=payload).status_code == 400
        assert agent.held is not None and not session.turn_active

    def test_nothing_held_is_a_400(self):
        session, agent, client = held_session()
        assert client.post("/api/resume", json=answers()).status_code == 400


class TestSettingItAside:
    def test_a_new_message_sets_it_aside(self):
        session, agent, client = held_session()
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            post_and_finish(client, ws, "/api/message", {"text": "go"})
            post_and_finish(client, ws, "/api/message", {"text": "skip it"})
        assert agent.held is None
        assert "set aside" in agent.history[2].content[0].content

    def test_clear_drops_it(self):
        session, agent, client = held_session()
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            post_and_finish(client, ws, "/api/message", {"text": "go"})
        client.post("/api/clear")
        assert agent.held is None


def test_the_resumed_turn_names_the_held_one(tmp_path):
    session, agent, client = held_session(tmp_path)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        post_and_finish(client, ws, "/api/message", {"text": "go"})
        post_and_finish(client, ws, "/api/resume", answers(
            w1={"decision": "approve"}, w2={"decision": "approve"}))
    held, resumed = session.trace.read()
    assert held.outcome == "held" and resumed.resumes == held.id
    assert resumed.task == "go" and resumed.outcome == "end_turn"


def test_the_flag_takes_hold():
    assert main(["--on-timeout", "hold", "--wait-budget", "5",
                 "--prompt", "hi"]) == 2    # still needs --web


def test_a_reconnect_does_not_draw_waiting_calls_as_run():
    session, agent, client = held_session()
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        post_and_finish(client, ws, "/api/message", {"text": "go"})
    replay = session.history_envelopes()
    assert not [e for e in replay if e["type"] == "tool_result"]


def test_the_link_to_the_held_turn_survives_a_restart(tmp_path):
    from yantra.session import SessionStore
    session, agent, client = held_session(tmp_path)
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        post_and_finish(client, ws, "/api/message", {"text": "go"})
    store = SessionStore(tmp_path / "s.db")
    store.save(agent, provider_name="anthropic")
    # A new process: a new session and agent, the same trace file.
    fresh, fresh_agent = make_session([assistant_text("done")])
    fresh.trace = TrajectoryLog(tmp_path / "t.jsonl")
    from yantra.session import apply_payload
    apply_payload(fresh_agent, store.load_latest(), history_only=True)
    client = TestClient(make_app(fresh))
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        post_and_finish(client, ws, "/api/resume", answers(
            w1={"decision": "approve"}, w2={"decision": "deny"}))
    held, resumed = fresh.trace.read()
    assert resumed.resumes == held.id and resumed.task == "go"
