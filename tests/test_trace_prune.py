"""Pruning a recording by age.

The bias here is a tidy-up that destroys more than it was asked to. A
trace file is the only record of turns somebody may still want to turn
into a case, so the tests pin what a prune must NOT do:

* remove a turn younger than the cutoff;
* remove a line it cannot read -- it has no date, so age cannot judge it;
* lose a turn that another session appended while the prune ran;
* rewrite the file at all when nothing was old enough to go;
* run as a side effect of anything but its own flag.
"""

from __future__ import annotations

import calendar
import json
import time

import pytest

from yantra.cli.main import main
from yantra.errors import ConfigError
from yantra.trace import TrajectoryLog, Trajectory

NOW = calendar.timegm((2026, 9, 24, 12, 0, 0))
DAY = 86400


def stamp(seconds_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - seconds_ago))


def recorded(path, *ages_in_days, task="t"):
    log = TrajectoryLog(path)
    ids = []
    for age in ages_in_days:
        ids.append(log.record(Trajectory(
            id=f"id-{age}", at=stamp(age * DAY), provider="ollama",
            model="qwen3.8:latest", detail="shape", task=task)))
    return log, ids


class TestWhatAPruneRemoves:
    def test_only_turns_older_than_the_cutoff_go(self, tmp_path):
        log, _ = recorded(tmp_path / "t.jsonl", 40, 31, 29, 1)
        pruned = log.prune(30, now=NOW)
        assert (pruned.removed, pruned.kept) == (2, 2)
        assert [t.id for t in log.read()] == ["id-29", "id-1"]

    def test_a_line_it_cannot_read_is_kept_as_it_was(self, tmp_path):
        path = tmp_path / "t.jsonl"
        log, _ = recorded(path, 40)
        with path.open("a") as handle:
            handle.write('{"format": "yantra.trace.v1", "id": "half\n')
        pruned = log.prune(30, now=NOW)
        assert pruned.removed == 1 and pruned.unreadable == 1
        assert path.read_text() == '{"format": "yantra.trace.v1", "id": "half\n'

    def test_a_newer_writers_line_still_has_an_age(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text(json.dumps({"format": "yantra.trace.v9",
                                    "at": stamp(40 * DAY)}) + "\n")
        assert TrajectoryLog(path).prune(30, now=NOW).removed == 1

    def test_nothing_old_means_the_file_is_not_rewritten(self, tmp_path):
        path = tmp_path / "t.jsonl"
        log, _ = recorded(path, 1)
        before = path.stat().st_ino
        assert log.prune(30, now=NOW).removed == 0
        assert path.stat().st_ino == before

    def test_a_turn_appended_during_the_prune_survives(
            self, tmp_path, monkeypatch):
        path = tmp_path / "t.jsonl"
        log, _ = recorded(path, 40, 1)
        real_read = type(path).read_bytes

        def read_then_someone_appends(self):
            data = real_read(self)
            recorded(path, 0)             # another session, mid-prune
            return data

        monkeypatch.setattr(type(path), "read_bytes", read_then_someone_appends)
        log.prune(30, now=NOW)
        assert [t.id for t in log.read()] == ["id-1", "id-0"]

    def test_zero_days_is_refused_by_name(self, tmp_path):
        log, _ = recorded(tmp_path / "t.jsonl", 1)
        with pytest.raises(ConfigError, match="delete"):
            log.prune(0)


class TestTheFlag:
    def test_it_prunes_and_says_what_went(self, tmp_path, capsys):
        path = tmp_path / "t.jsonl"
        recorded(path, 4000, 0)          # one ancient, one from today
        assert main(["--trace", str(path), "--trace-prune", "30"]) == 0
        out = " ".join(capsys.readouterr().out.split())
        assert "removed 1 turn(s) recorded more than 30 day(s) ago, kept 1" in out

    def test_it_needs_the_file(self, capsys):
        assert main(["--trace-prune", "30"]) == 2
        assert "--trace FILE" in capsys.readouterr().err

    def test_it_is_its_own_mode(self, tmp_path, capsys):
        assert main(["--trace", str(tmp_path / "t.jsonl"),
                     "--trace-prune", "30", "--eval"]) == 2
        assert "tidies a recording" in capsys.readouterr().err

    def test_a_missing_file_is_an_error_not_a_crash(self, tmp_path, capsys):
        assert main(["--trace", str(tmp_path / "none.jsonl"),
                     "--trace-prune", "30"]) == 2
        assert "no trace file" in capsys.readouterr().err

    def test_recording_never_prunes(self, tmp_path):
        """Old turns stay put however many new ones are written."""
        log, _ = recorded(tmp_path / "t.jsonl", 4000)
        recorded(tmp_path / "t.jsonl", 0)
        assert len(log.read()) == 2
