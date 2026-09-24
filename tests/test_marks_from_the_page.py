"""A person's verdict, given from the browser (notes/77).

The bias here is a second, drifting implementation. The page's buttons
must write exactly the line ``--mark`` writes, against a turn the file
already holds, and never while a turn could be appending. The tests pin:

* a recorded web turn tells the page its id before ``turn_done``;
* good, bad-with-a-reason and clear from the page land in the trace the
  same way the terminal command's do;
* a page with no recording, an unknown id or a bad verdict is a 400,
  not a silent no-op.

And the turns the page never saw (notes/81). The mark row under a turn
only exists while the page watched it, so the panel reads the file: the
bias there is a page that knows only its own session. The tests write
turns BEFORE any page exists and expect them listed, flagged by the
terminal's rule, and markable.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from conftest import assistant_text  # noqa: E402
from test_web_server import drain_until, make_session  # noqa: E402
from yantra.trace import ToolStep, Trajectory, TrajectoryLog  # noqa: E402
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


def old_turn(tid, **kw):
    return Trajectory(id=tid + "0" * 24, at="2026-09-23T09:00:00Z",
                      provider="ollama", model="qwen3.8:latest",
                      detail="shape", task=f"task {tid}", **kw)


def page_over(tmp_path, *turns):
    """A page opened on a recording that already has ``turns`` in it."""
    session, _ = make_session([])
    session.trace = TrajectoryLog(tmp_path / "old.jsonl")
    for t in turns:
        session.trace.record(t)
    return session, TestClient(make_app(session))


class TestTurnsFromBeforeThePage:
    def test_they_are_listed_newest_first(self, tmp_path):
        _, client = page_over(tmp_path, old_turn("aaaaaaaa"),
                              old_turn("bbbbbbbb"))
        data = client.get("/api/turns").json()
        assert [t["id"][:8] for t in data["turns"]] == ["bbbbbbbb",
                                                        "aaaaaaaa"]
        assert data["total"] == 2 and data["path"].endswith("old.jsonl")

    def test_they_are_flagged_by_the_terminals_rule(self, tmp_path):
        _, client = page_over(
            tmp_path, old_turn("aaaaaaaa"),
            old_turn("bbbbbbbb", steps=[ToolStep("read_file", ok=False)]))
        data = client.get("/api/turns").json()
        flags = {t["id"][:8]: (t["flagged"], t["flag_why"])
                 for t in data["turns"]}
        assert flags == {"aaaaaaaa": (False, ""),
                         "bbbbbbbb": (True, "read_file (failed)")}
        assert data["flagged"] == 1

    def test_one_can_be_marked_and_the_list_follows(self, tmp_path):
        session, client = page_over(tmp_path, old_turn("aaaaaaaa"))
        client.post("/api/mark", json={"id": "aaaaaaaa", "verdict": "bad",
                                       "why": "wrong file"})
        (row,) = client.get("/api/turns").json()["turns"]
        assert (row["flagged"], row["judged_by"], row["why"]) == \
            (True, "person", "wrong file")
        assert row["flag_why"] == "marked bad by a person: wrong file"

    def test_the_list_is_capped_and_says_how_many_there_are(self, tmp_path):
        from yantra.web import server
        session, _ = page_over(tmp_path, *[old_turn(f"{i:08d}")
                                          for i in range(5)])
        data = session.turns(limit=2)
        assert [t["id"][:8] for t in data["turns"]] == ["00000004",
                                                        "00000003"]
        assert data["total"] == 5
        assert server.TURNS_SHOWN >= 50

    def test_before_the_first_turn_there_is_nothing_yet(self, tmp_path):
        session, _ = make_session([])
        session.trace = TrajectoryLog(tmp_path / "not-yet.jsonl")
        data = TestClient(make_app(session)).get("/api/turns").json()
        assert data["turns"] == [] and data["total"] == 0

    def test_without_a_recording_it_is_a_400(self, tmp_path):
        session, _ = make_session([])
        assert TestClient(make_app(session)).get("/api/turns"
                                                 ).status_code == 400
