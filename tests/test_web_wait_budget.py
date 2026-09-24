"""A turn's allowance for waiting on approvals, in the browser (notes/78).

The bias here is a budget that behaves differently from the library's.
``with_wait_budget`` cannot wrap the browser's gate (it blocks a thread,
it does not suspend), so the rule is kept a second time in the web
session, and these tests hold it to the same promises:

* a prompt nobody answers is withdrawn when the allowance runs out, and
  refused as ``timeout`` -- or approved, when silence was told to mean yes;
* once spent, nothing more is ASKED that turn (no prompt reaches the
  page) and the refusal says ``out_of_time``;
* reads are never asked, so they are never refused;
* the allowance is per turn: the next turn starts full;
* the CLI refuses the flag without --web, and without a verdict on silence.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from conftest import assistant_text, assistant_tool_call  # noqa: E402
from test_web_server import drain_until, make_session  # noqa: E402
from yantra.cli.main import main  # noqa: E402
from yantra.web.server import make_app  # noqa: E402


def two_writes_and_a_read():
    return [
        assistant_tool_call("w1", "write_thing", {"text": "a"}),
        assistant_tool_call("w2", "write_thing", {"text": "b"}),
        assistant_tool_call("r1", "echo", {"text": "c"}),
        assistant_text("done"),
    ]


def run(session, text="go"):
    client = TestClient(make_app(session))
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "state"
        assert client.post("/api/message", json={"text": text}
                           ).status_code == 200
        return drain_until(ws, {"turn_done"})


def results(envelopes):
    return [(e["name"], e["refusal"]) for e in envelopes
            if e["type"] == "tool_result"]


def test_an_unanswered_prompt_is_withdrawn_and_then_nothing_is_asked():
    session, _ = make_session(two_writes_and_a_read())
    session.set_wait_budget(0.3, on_timeout="deny")
    envelopes = run(session)
    asked = [e for e in envelopes if e["type"] == "permission_request"]
    assert len(asked) == 1                    # the second write never asked
    assert asked[0]["wait_left"] == 0.3
    assert {"type": "resolved", "id": asked[0]["id"]} in envelopes
    assert results(envelopes) == [("write_thing", "timeout"),
                                  ("write_thing", "out_of_time"),
                                  ("echo", None)]


def test_the_refusal_tells_the_model_nobody_said_no():
    session, agent = make_session(two_writes_and_a_read())
    session.set_wait_budget(0.3, on_timeout="deny")
    run(session)
    said = [b.content for m in agent.history for b in m.content
            if type(b).__name__ == "ToolResult" and b.is_error]
    assert "went unanswered" in said[0] and "Nobody refused it" in said[0]
    assert "nobody was asked" in said[1]


def test_silence_can_be_told_to_mean_yes():
    session, _ = make_session(two_writes_and_a_read())
    session.set_wait_budget(0.3, on_timeout="allow")
    envelopes = run(session)
    assert results(envelopes) == [("write_thing", None),
                                  ("write_thing", None), ("echo", None)]


def test_each_turn_starts_with_the_whole_allowance():
    session, _ = make_session(two_writes_and_a_read()
                              + two_writes_and_a_read())
    session.set_wait_budget(0.3, on_timeout="deny")
    client = TestClient(make_app(session))
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "state"
        for text in ("one", "two"):
            client.post("/api/message", json={"text": text})
            second = drain_until(ws, {"turn_done"})
    assert [e for e in second if e["type"] == "permission_request"]
    assert results(second)[0] == ("write_thing", "timeout")


def test_without_a_budget_the_prompt_carries_no_clock():
    session, _ = make_session([assistant_text("hi")])
    assert session.state()["wait_budget"] is None
    session.set_wait_budget(5, on_timeout="deny")
    assert session.state()["wait_budget"] == {"seconds": 5,
                                              "on_timeout": "deny"}


@pytest.mark.parametrize("seconds,on_timeout", [(0, "deny"), (5, "maybe")])
def test_a_budget_that_means_nothing_is_refused(seconds, on_timeout):
    session, _ = make_session([])
    with pytest.raises(ValueError):
        session.set_wait_budget(seconds, on_timeout=on_timeout)


@pytest.mark.parametrize("argv", [
    ["--wait-budget", "30", "--on-timeout", "deny"],     # no --web
    ["--web", "--wait-budget", "30"],                    # no verdict
    ["--web", "--on-timeout", "deny"],                   # no budget
    ["--web", "--wait-budget", "0", "--on-timeout", "deny"],
])
def test_the_flags_refuse_what_would_mean_nothing(argv, capsys):
    assert main(argv) == 2


def test_an_answer_in_time_is_an_answer_and_costs_what_it_waited():
    session, _ = make_session([
        assistant_tool_call("w1", "write_thing", {"text": "a"}),
        assistant_text("done")])
    session.set_wait_budget(30, on_timeout="deny")
    client = TestClient(make_app(session))
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "state"
        client.post("/api/message", json={"text": "go"})
        (asked,) = [e for e in drain_until(ws, {"permission_request"})
                    if e["type"] == "permission_request"]
        ws.send_json({"type": "answer", "id": asked["id"],
                      "decision": "approve"})
        envelopes = drain_until(ws, {"turn_done"})
    assert results(envelopes) == [("write_thing", None)]
    assert 0 < session._wait_left < 30
