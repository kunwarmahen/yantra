"""Every turn's model use, one line, in the house's usage file.

The bias: a meter that lies is worse than none. A local model's turn
counted as $0.00, so a cloud turn of unknown price looks free beside it.
A message or an answer copied into a file meant to be handed to anybody.
A turn lost because its receipt couldn't be written. A second program's
line spliced into the first's.
"""

from __future__ import annotations

import json

from conftest import ScriptedProvider, assistant_text

from yantra import usage_log
from yantra.agent import Agent


def turn(monkeypatch, tmp_path, *, model="qwen3.8:latest", name="ollama", **env):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("YANTRA_USAGE_FILE", str(path))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    provider = ScriptedProvider([assistant_text("the secret answer", model=model)])
    provider.name = name
    agent = Agent(provider, model=model)
    agent.usage_who = "asha via telegram"
    list(agent.run_streaming("my private question"))
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_one_line_per_turn_with_who_and_what_never_the_words(monkeypatch, tmp_path):
    rows = turn(monkeypatch, tmp_path, YANTRA_USAGE_BY="dvara")
    assert len(rows) == 1
    row = rows[0]
    assert row["format"] == "yantra.usage.v1" and row["by"] == "dvara"
    assert row["who"] == "asha via telegram" and row["local"] is True
    assert row["model"] == "qwen3.8:latest" and row["reason"] == "end_turn"
    text = json.dumps(row)
    assert "private question" not in text and "secret answer" not in text


def test_a_local_turn_has_no_dollars_never_zero(monkeypatch, tmp_path):
    assert turn(monkeypatch, tmp_path)[0]["usd"] is None


def test_a_priced_cloud_turn_has_its_dollars(monkeypatch, tmp_path):
    row = turn(monkeypatch, tmp_path, model="claude-sonnet-5-5", name="anthropic")[0]
    assert row["local"] is False and row["usd"] is not None


def test_a_program_that_said_who_asked_is_the_who(monkeypatch, tmp_path):
    path = tmp_path / "u.jsonl"
    monkeypatch.setenv("YANTRA_USAGE_FILE", str(path))
    monkeypatch.setenv("YANTRA_ASKED_BY", "Samay: Morning brief (bfaea182) · mahen")
    agent = Agent(ScriptedProvider([assistant_text("ok")]), model="m")
    list(agent.run_streaming("go"))
    assert json.loads(path.read_text())["who"].startswith("Samay: Morning brief")


def test_off_writes_nothing_and_an_unwritable_file_costs_the_turn_nothing(
        monkeypatch, tmp_path):
    monkeypatch.setenv("YANTRA_USAGE", "off")
    assert usage_log.path() is None
    monkeypatch.delenv("YANTRA_USAGE")
    monkeypatch.setenv("YANTRA_USAGE_FILE", str(tmp_path / "a-file" / "x" / "u.jsonl"))
    (tmp_path / "a-file").write_text("not a folder")
    agent = Agent(ScriptedProvider([assistant_text("still answered")]), model="m")
    events = list(agent.run_streaming("go"))
    assert events[-1].response.message.text() == "still answered"


def test_read_back_skips_what_it_cant_read(tmp_path):
    path = tmp_path / "u.jsonl"
    path.write_text('{"format": "yantra.usage.v1", "at": "2026-11-13T06:00:00-0500"}\n'
                    'half a line\n{"format": "other"}\n')
    assert len(usage_log.read(path)) == 1
