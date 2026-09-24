"""Scrubbing a recording before it is written (notes/79).

The bias here is a scrubber that either misses content or eats shape.
Missing content is the privacy failure the flag exists for; eating shape
(an id, a tool name, a timestamp) makes the line useless for --turns and
--fossil, which is the failure that makes people turn the flag off. The
tests pin:

* the task, arguments (nested), results, answers and a child's task and
  answer are scrubbed; names, ids and counts are not;
* the turn's own objects are not mutated -- the agent still holds the
  real arguments it ran with;
* every line written with patterns says how many it replaced, zero
  included, and a line without patterns says nothing;
* ``email`` and ``token`` are built in, and a bad or empty-matching
  pattern is refused at startup rather than discovered in the file.
"""

from __future__ import annotations

import json

import pytest

from yantra.cli.main import main
from yantra.errors import ConfigError
from yantra.trace import ChildRun, ToolStep, Trajectory, TrajectoryLog


def turn(**over):
    base = dict(
        id="a" * 32, at="2026-09-24T12:00:00Z", provider="ollama",
        model="qwen3.8:latest", detail="full",
        task="email ana@example.com the key sk-ant-abcdefghijklmnop1234",
        steps=[ToolStep("send_mail", ok=True,
                        arguments={"to": "ana@example.com",
                                   "cc": ["bo@example.org"], "n": 2},
                        result="sent to ana@example.com")],
        answer="Done -- mailed ana@example.com.",
        children=[ChildRun(number=1, agent="helper", model="m",
                           steps=[ToolStep("read_file", ok=True)],
                           task="find bo@example.org", answer="none")],
    )
    base.update(over)
    return Trajectory(**base)


def written(tmp_path, trajectory, redact):
    path = tmp_path / "t.jsonl"
    TrajectoryLog(path, detail="full", redact=redact).record(trajectory)
    return json.loads(path.read_text())


def test_every_content_field_is_scrubbed_and_counted(tmp_path):
    raw = written(tmp_path, turn(), ["email", "token"])
    text = json.dumps(raw)
    assert "@example" not in text and "sk-ant" not in text
    assert raw["task"] == "email [redacted] the key [redacted]"
    assert raw["steps"][0]["arguments"] == {
        "to": "[redacted]", "cc": ["[redacted]"], "n": 2}
    assert raw["children"][0]["task"] == "find [redacted]"
    assert raw["redacted"] == 7


def test_shape_is_left_alone(tmp_path):
    raw = written(tmp_path, turn(model="ana@example.com"), ["email"])
    assert raw["model"] == "ana@example.com"   # not content: not scrubbed
    assert raw["id"] == "a" * 32
    assert raw["steps"][0]["name"] == "send_mail"
    assert raw["children"][0]["agent"] == "helper"


def test_the_turn_itself_keeps_what_it_ran_with(tmp_path):
    t = turn()
    written(tmp_path, t, ["email"])
    assert t.steps[0].arguments["to"] == "ana@example.com"


def test_a_scrubbed_line_with_nothing_to_scrub_still_says_so(tmp_path):
    raw = written(tmp_path, turn(task="hello", steps=[], answer=None,
                                 children=[]), ["email"])
    assert raw["redacted"] == 0


def test_a_line_without_patterns_carries_no_count(tmp_path):
    assert "redacted" not in written(tmp_path, turn(), [])


def test_the_count_reads_back(tmp_path):
    path = tmp_path / "t.jsonl"
    TrajectoryLog(path, redact=["email"]).record(turn())
    assert TrajectoryLog(path).read()[0].redacted == 6


def test_any_regular_expression_works(tmp_path):
    raw = written(tmp_path, turn(task="ticket ACME-1234 is open"),
                  [r"ACME-\d+"])
    assert raw["task"] == "ticket [redacted] is open"


@pytest.mark.parametrize("secret", [
    "ghp_" + "a1" * 12, "xoxb-1234567890-abcdef", "AKIAABCDEFGHIJKLMNOP",
    "Bearer abcdefghijklmnopqrstuvwxyz",
    "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4",
])
def test_the_token_preset_knows_the_usual_suspects(tmp_path, secret):
    raw = written(tmp_path, turn(task=f"use {secret} please", steps=[],
                                 answer=None, children=[]), ["token"])
    assert raw["task"] == "use [redacted] please", secret


def test_the_token_preset_leaves_ordinary_words(tmp_path):
    task = "summarize notes/57-a-turn-written-down.md for the bearer"
    raw = written(tmp_path, turn(task=task, steps=[], answer=None,
                                 children=[]), ["token"])
    assert raw["task"] == task


@pytest.mark.parametrize("pattern", ["(unclosed", "a*"])
def test_a_pattern_that_would_do_nothing_useful_is_refused(tmp_path,
                                                           pattern):
    with pytest.raises(ConfigError):
        TrajectoryLog(tmp_path / "t.jsonl", redact=[pattern])


def test_the_banner_says_it_is_scrubbing(tmp_path):
    log = TrajectoryLog(tmp_path / "t.jsonl", redact=["email", "token"])
    assert log.label == "shape, redacting 2 pattern(s)"
    assert TrajectoryLog(tmp_path / "t.jsonl").label == "shape"


class TestTheFlag:
    def test_it_needs_a_trace(self, capsys):
        assert main(["--trace-redact", "email"]) == 2

    def test_a_bad_pattern_fails_before_anything_runs(self, tmp_path,
                                                      capsys):
        assert main(["--trace", str(tmp_path / "t.jsonl"),
                     "--trace-redact", "(oops", "--prompt", "hi"]) == 2
        assert "not a regular expression" in capsys.readouterr().err

    def test_it_does_not_rescrub_a_written_file(self, tmp_path, capsys):
        assert main(["--turns", "--trace", str(tmp_path / "t.jsonl"),
                     "--trace-redact", "email"]) == 2

    def test_fossil_says_a_scrubbed_task_needs_its_words_back(
            self, tmp_path, capsys):
        path = tmp_path / "t.jsonl"
        TrajectoryLog(path, redact=["email"]).record(turn())
        assert main(["--fossil", "aaaa", "--trace", str(path)]) == 0
        assert "put the real words back" in capsys.readouterr().err
