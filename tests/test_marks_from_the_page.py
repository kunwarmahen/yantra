"""A person's verdict, given from the browser (notes/77).

The bias here is a second, drifting implementation. The page's buttons
must write exactly the line ``--mark`` writes, against a turn the file
already holds, and never while a turn could be appending. The tests pin:

* a recorded web turn tells the page its id before ``turn_done``;
* good, bad-with-a-reason and clear from the page land in the trace the
  same way the terminal command's do;
* a page with no recording, an unknown id or a bad verdict is a 400,
  not a silent no-op.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from conftest import assistant_text  # noqa: E402
from test_web_server import drain_until, make_session  # noqa: E402
from yantra.trace import TrajectoryLog  # noqa: E402
from yantra.web.server import make_app  # noqa: E402


def recorded_turn(tmp_path, *, trace=True):
    session, _ = make_session([assistant_text("done")])
    if trace:
        session.trace = TrajectoryLog(tmp_path / "web.jsonl")
    client = TestClient(make_app(session))
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "state"
        assert client.post("/api/message", json={"text": "say hi"}
                           ).status_code == 200
        envelopes = drain_until(ws, {"turn_done"})
    return session, client, envelopes


def test_the_page_learns_the_recorded_turns_id_before_turn_done(tmp_path):
    session, _, envelopes = recorded_turn(tmp_path)
    kinds = [e["type"] for e in envelopes]
    (recorded,) = [e for e in envelopes if e["type"] == "recorded"]
    assert kinds.index("recorded") < kinds.index("turn_done")
    assert recorded["id"] == session.trace.read()[0].id


def test_nothing_is_offered_when_nothing_is_recorded(tmp_path):
    _, client, envelopes = recorded_turn(tmp_path, trace=False)
    assert "recorded" not in [e["type"] for e in envelopes]
    assert client.post("/api/mark", json={"id": "x", "verdict": "good"}
                       ).status_code == 400


def test_a_bad_mark_from_the_page_is_the_terminals_line(tmp_path):
    session, client, _ = recorded_turn(tmp_path)
    tid = session.trace.read()[0].id
    res = client.post("/api/mark", json={"id": tid[:8], "verdict": "bad",
                                         "why": "  cited nothing  "})
    assert res.status_code == 200
    assert res.json() == {"id": tid, "passed": False, "judged_by": "person",
                          "why": "cited nothing"}
    (turn,) = session.trace.read()
    assert (turn.passed, turn.judged_by, turn.why) == \
        (False, "person", "cited nothing")


def test_taking_it_back_from_the_page_clears_the_line(tmp_path):
    session, client, _ = recorded_turn(tmp_path)
    tid = session.trace.read()[0].id
    client.post("/api/mark", json={"id": tid, "verdict": "good"})
    res = client.post("/api/mark", json={"id": tid, "verdict": "clear"})
    assert res.json()["judged_by"] is None
    (turn,) = session.trace.read()
    assert turn.passed is None and turn.judged_by is None


def test_an_empty_reason_is_no_reason(tmp_path):
    session, client, _ = recorded_turn(tmp_path)
    tid = session.trace.read()[0].id
    client.post("/api/mark", json={"id": tid, "verdict": "bad", "why": ""})
    assert session.trace.read()[0].why is None


@pytest.mark.parametrize("body", [
    {"id": "zzzz", "verdict": "good"},        # no such turn
    {"id": "", "verdict": "good"},
    {"verdict": "bad"},
])
def test_a_mark_that_cannot_land_is_a_400(tmp_path, body):
    _, client, _ = recorded_turn(tmp_path)
    assert client.post("/api/mark", json=body).status_code == 400


def test_the_verdict_is_one_of_three_words(tmp_path):
    session, client, _ = recorded_turn(tmp_path)
    tid = session.trace.read()[0].id
    assert client.post("/api/mark", json={"id": tid, "verdict": "meh"}
                       ).status_code == 400
