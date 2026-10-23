"""`yantra keeps`: where Yantra keeps what is yours, for a backup.

The bias here is a backup that quietly misses something. Sarathi packs
whatever these places say and nothing else, so the failures these tests
are designed against are a place resolved from the wrong variable (a
moved state home backed up from where it used to be), a moved History
file left out, and the bare word turning into a paid turn the way
``status`` once did.
"""

from __future__ import annotations

import json

from yantra import keeps
from yantra.cli.main import main


def _places(data):
    return {p["id"]: p for p in data["keeps"]}


class TestWhereThingsAre:
    def test_the_state_home_follows_xdg_state_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.delenv("YANTRA_HISTORY", raising=False)
        places = _places(keeps.report(home=tmp_path))
        assert places["state"]["path"] == str(tmp_path / "state" / "yantra")
        assert places["skills"]["path"] == str(tmp_path / ".yantra" / "skills")
        assert "history" not in places      # it is inside the state home

    def test_a_history_file_elsewhere_is_its_own_place(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("YANTRA_HISTORY", str(tmp_path / "talks.sqlite"))
        assert _places(keeps.report(home=tmp_path))["history"]["path"] == \
            str(tmp_path / "talks.sqlite")

    def test_history_turned_off_adds_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("YANTRA_HISTORY", "off")
        assert "history" not in _places(keeps.report(home=tmp_path))

    def test_the_browser_caches_are_left_out_and_nothing_else_of_state(self, tmp_path,
                                                                       monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        leave_out = _places(keeps.report(home=tmp_path))["state"]["leave_out"]
        assert "Cache" in leave_out and "Singleton*" in leave_out
        assert not {"*.sqlite", "tray", "browser-profile"} & set(leave_out)

    def test_whether_each_place_exists_is_said(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        (tmp_path / "yantra").mkdir()
        places = _places(keeps.report(home=tmp_path))
        assert places["state"]["exists"] and not places["skills"]["exists"]


class TestTheWordIsNotAPrompt:
    def test_keeps_json_prints_the_format_and_runs_no_turn(self, tmp_path, monkeypatch,
                                                           capsys):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert main(["keeps", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["format"] == "yantra.keeps.v1"

    def test_anything_else_after_keeps_is_refused(self, capsys):
        assert main(["keeps", "now"]) == 2
        assert "--prompt" in capsys.readouterr().err
