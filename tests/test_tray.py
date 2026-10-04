"""The to-keep tray survives a restart (web/tray.py).

Designed against:

* **A restart losing every waiting offer** -- a tested recipe, facts, a
  site's guide -- while the recipe's staged files stayed on disk.
* **Facts about the person written into the project folder.** The file
  lives in the state directory, beside memory.
* **An offer that can no longer be saved coming back.** Its staged
  folder, or the skill it updates, is gone: it is dropped, and said.
* **A broken file breaking the session.** It is set aside as .bad.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from yantra.memory.reflect import Candidate
from yantra.site_guide import GuideOffer
from yantra.skills.learn import Draft, Offer
from yantra.types import Usage
from yantra.web import tray


def offer(tmp_path, **kw):
    staging = tmp_path / ".yantra" / "learning" / "csv-to-json"
    staging.mkdir(parents=True, exist_ok=True)
    draft = Draft(name="csv-to-json", description="CSV to JSON", scope="user", inputs="",
                  needs="", body="Run the script.", script_name="scripts/csv2json.py",
                  script="print(1)", test="python3 x", facts="")
    return Offer(draft=draft, staging=staging, tested=True, test_output="records: 40",
                 test_runs=1, spent=Usage(input_tokens=2025, output_tokens=1819), **kw)


LEARNER = SimpleNamespace(_setu_link=lambda: None)
AGENT = SimpleNamespace(skills=None)


class TestTheFile:
    def test_everything_waiting_comes_back(self, tmp_path):
        guide = GuideOffer(site="teashop", name="Teashop", old="", new="Orders: /o", calls=3)
        tray.save(tmp_path, {"a1": (LEARNER, offer(tmp_path))},
                  [Candidate("lives in Pune", "fact")], {"g1": guide})
        recipes, memories, guides, notes = tray.load(tmp_path, AGENT, LEARNER)
        restored = recipes["a1"][1]
        assert restored.draft.name == "csv-to-json" and restored.tested is True
        assert restored.test_output == "records: 40" and restored.spent.input_tokens == 2025
        assert memories == [Candidate("lives in Pune", "fact")]
        assert guides["g1"] == guide and notes == []

    def test_it_lives_beside_memory_not_in_the_project(self, tmp_path):
        tray.save(tmp_path, {}, [Candidate("lives in Pune")], {})
        where = tray.path_for(tmp_path)
        assert os.environ["XDG_STATE_HOME"] in str(where)
        assert not str(where).startswith(str(tmp_path))
        assert oct(where.stat().st_mode & 0o777) == "0o600"

    def test_an_empty_tray_leaves_no_file(self, tmp_path):
        tray.save(tmp_path, {}, [Candidate("x")], {})
        tray.save(tmp_path, {}, [], {})
        assert not tray.path_for(tmp_path).exists()

    def test_each_workspace_has_its_own(self, tmp_path):
        tray.save(tmp_path / "a", {}, [Candidate("in a")], {})
        assert tray.load(tmp_path / "b", AGENT, LEARNER)[1] == []

    def test_an_offer_whose_files_are_gone_is_dropped_and_said(self, tmp_path):
        o = offer(tmp_path)
        tray.save(tmp_path, {"a1": (LEARNER, o)}, [], {})
        for f in o.staging.iterdir():
            f.unlink()
        o.staging.rmdir()
        recipes, _, _, notes = tray.load(tmp_path, AGENT, LEARNER)
        assert recipes == {} and "csv-to-json" in notes[0]

    def test_an_update_to_a_skill_that_is_gone_is_dropped(self, tmp_path):
        skill = SimpleNamespace(directory=tmp_path / "skills" / "csv-to-json")
        tray.save(tmp_path, {"a1": (LEARNER, offer(tmp_path, repairs=skill))}, [], {})
        assert tray.load(tmp_path, SimpleNamespace(skills=[]), LEARNER)[0] == {}
        found = tray.load(tmp_path, SimpleNamespace(skills=[skill]), LEARNER)[0]
        assert found["a1"][1].repairs is skill

    def test_a_broken_file_is_set_aside(self, tmp_path):
        where = tray.path_for(tmp_path)
        where.parent.mkdir(parents=True)
        where.write_text("{not json")
        recipes, memories, guides, notes = tray.load(tmp_path, AGENT, LEARNER)
        assert (recipes, memories, guides) == ({}, [], {}) and "set aside" in notes[0]
        assert where.with_suffix(".json.bad").exists() and not where.exists()


class TestARestartedServer:
    def test_a_new_session_finds_the_tray(self, tmp_path):
        pytest.importorskip("fastapi")
        from conftest import ScriptedProvider
        from yantra.agent import Agent
        from yantra.tools.base import ToolRegistry
        from yantra.web.server import WebSession

        def agent():
            a = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
            a.ctx.cwd = tmp_path
            return a

        first = WebSession()
        first.attach(agent(), None)
        first._keep_guide(GuideOffer(site="teashop", name="Teashop", old="",
                                     new="Orders: /account/orders", calls=3))
        first.keep_memories([Candidate("prefers tea")])
        second = WebSession()
        second.attach(agent(), None)
        view = second.kept_view()
        assert [g["new"] for g in view["guides"]] == ["Orders: /account/orders"]
        assert [m["statement"] for m in view["memories"]] == ["prefers tea"]
        assert second.state()["kept"]["count"] == 2
