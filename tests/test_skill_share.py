"""Sharing a learned skill, and installing one somebody shared (skills/share.py).

The BIAS is A RECIPE THAT LEAVES WITH SOMETHING OF YOURS IN IT: a token
in a curl line, your fan's entity id, your email, your home folder. Any
one of those stops the share, names the file and line, and writes
nothing -- a scrubber that quietly cut the value out would hand somebody
a recipe with a hole in it.

Also designed against:

* **Your record travelling with it.** The ``learned:`` counters and a
  ``tool:`` promotion are yours, not the recipe's.
* **An install that overwrites.** Installing never replaces a skill you
  already have under that name; the same recipe again is a no-op.
* **Installing unseen code.** The host shows every file and asks; with
  nobody to ask, nothing is installed.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from yantra.cli.share import install_recipe, share_skill
from yantra.skills.loader import SkillError, discover, load_skill
from yantra.skills.share import (install, memory_values, prepare, recipe_hash,
                                 without_keys, write)

SKILL = """\
---
name: home-fan
description: Turn a Home Assistant fan on or off. Use when the person asks to
  switch, start or stop a fan at home.
origin: learned
needs: setu:homeassistant
inputs: entity (from memory), action (on | off)
tool: fan_control scripts/fan.py
learned: 2026-09-28 · worked 6 · failed 0 · last ok 2026-10-02
---

1. Find which fan entity the person means in memory.
2. Run `python3 "$SKILL_DIR/scripts/fan.py" ENTITY on|off`.
"""

SCRIPT = """\
import sys, urllib.request
entity, action = sys.argv[1], sys.argv[2]
url = "http://homeassistant.local:8123/api/services/fan/turn_" + action
print("posting", entity, "to", url)
"""


def learned_skill(root: Path, script: str = SCRIPT, skill: str = SKILL) -> Path:
    folder = root / ".yantra" / "skills" / "learned" / "home-fan"
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text(skill)
    (folder / "scripts" / "fan.py").write_text(script)
    return folder / "SKILL.md"


def load(path: Path):
    return load_skill(path, source="learned-local")


def check(path: Path, **kw):
    kw.setdefault("home", "/home/nobody-here")
    kw.setdefault("user", "zz")
    kw.setdefault("memories", [])
    return prepare(load(path), **kw)


class TestTheChecks:
    def test_a_clean_recipe_leaves_without_your_record(self, tmp_path):
        prepared = check(learned_skill(tmp_path))
        assert prepared.ok, prepared.problems
        assert sorted(prepared.files) == ["SKILL.md", "scripts/fan.py"]
        md = prepared.files["SKILL.md"]
        assert "learned:" not in md and "tool:" not in md
        assert "inputs: entity (from memory)" in md and "needs: setu:homeassistant" in md
        assert any("left out tool: fan_control" in n for n in prepared.notes)
        target = write(prepared, tmp_path / "out")
        assert target == tmp_path / "out" / "recipes" / "home-fan"
        assert (target / "scripts" / "fan.py").read_text() == SCRIPT

    def test_a_token_stops_it_and_nothing_is_written(self, tmp_path):
        script = SCRIPT + 'headers = {"Authorization": "Bearer abcdefghijklmnop0123456789"}\n'
        prepared = check(learned_skill(tmp_path, script))
        assert not prepared.ok
        assert [str(f) for f in prepared.problems] == [
            "scripts/fan.py:5: a secret-looking value (a token, a key, an "
            "Authorization header)"]
        with pytest.raises(SkillError, match="nothing written"):
            write(prepared, tmp_path / "out")
        assert not (tmp_path / "out").exists()

    def test_a_value_you_told_memory_is_named_and_words_are_not(self, tmp_path):
        script = SCRIPT.replace("sys.argv[1]", '"fan.office_ceiling_2"')
        memories = ["Their office fan's Home Assistant entity id is fan.office_ceiling_2."]
        prepared = check(learned_skill(tmp_path, script), memories=memories)
        whys = [f.why for f in prepared.problems]
        assert len(whys) == 1 and "'fan.office_ceiling_2'" in whys[0]
        assert "make it an input" in whys[0]
        # "Home Assistant" is in the recipe AND the memory: not a leak
        assert "Home" not in memory_values(memories)

    def test_an_email_your_home_and_your_login_are_found(self, tmp_path):
        script = SCRIPT + ('# by asha@example.com\n'
                           'LOG = "/home/asha/fan.log"  # asha ran this\n')
        prepared = check(learned_skill(tmp_path, script), home="/home/asha", user="asha")
        whys = " | ".join(f.why for f in prepared.problems)
        assert "an email address (asha@example.com)" in whys
        assert "your home folder (/home/asha)" in whys
        assert "your login name (asha)" in whys

    def test_the_trace_redaction_is_honoured(self, tmp_path):
        prepared = check(learned_skill(tmp_path), redact=re.compile(r"homeassistant\.local"))
        assert any("matches your trace redaction" in f.why for f in prepared.problems)

    def test_needs_are_checked_against_setu_when_it_is_there(self, tmp_path):
        known = SimpleNamespace(connectors={"gmail": {"name": "Gmail"}}, connections=[])
        prepared = check(learned_skill(tmp_path), link=known)
        assert any("setu:homeassistant, which Setu has no connector for" in f.why
                   for f in prepared.problems)
        prepared = check(learned_skill(tmp_path / "b"), link=None)
        assert prepared.ok
        assert any("setu:homeassistant was not checked" in n for n in prepared.notes)

    def test_memory_out_of_reach_is_said(self, tmp_path):
        prepared = check(learned_skill(tmp_path), memories=None)
        assert any("memory was not reachable" in n for n in prepared.notes)

    def test_a_hand_written_skill_is_not_a_recipe(self, tmp_path):
        folder = tmp_path / "skills" / "notes"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            "---\nname: notes\ndescription: Write tidy meeting notes from a "
            "transcript the person pastes in.\n---\n\nDo it.\n")
        prepared = prepare(load_skill(folder / "SKILL.md", source="project"))
        assert not prepared.ok and "not a learned skill" in prepared.problems[0].why


def test_dropping_keys_keeps_folded_values_whole():
    text = "---\nname: x\ntool: a\n  b\ninputs: one\n  two\n---\nbody\ntool: stays\n"
    assert without_keys(text) == "---\nname: x\ninputs: one\n  two\n---\nbody\ntool: stays\n"


class TestInstall:
    def shared(self, tmp_path) -> Path:
        prepared = check(learned_skill(tmp_path / "author"))
        return write(prepared, tmp_path / "author")

    def test_installed_with_fresh_counters_and_its_hash(self, tmp_path):
        source = self.shared(tmp_path)
        home = tmp_path / "home"
        skill = install(source, home=home, today="2026-10-01")
        assert skill.path == home / ".yantra" / "skills" / "learned" / "home-fan" / "SKILL.md"
        assert skill.is_learned and skill.learned.since == "2026-10-01"
        assert skill.learned.worked == 0 and not skill.tool
        files = {p.relative_to(source).as_posix(): p.read_text()
                 for p in source.rglob("*") if p.is_file()}
        assert skill.shared == recipe_hash(files)

    def test_the_same_recipe_again_is_a_no_op_and_another_is_refused(self, tmp_path):
        source = self.shared(tmp_path)
        home = tmp_path / "home"
        first = install(source, home=home)
        assert install(source, home=home).path == first.path
        (source / "scripts" / "fan.py").write_text(SCRIPT + "# changed\n")
        with pytest.raises(SkillError, match="already have a skill named home-fan"):
            install(source, home=home)

    def test_the_host_shows_every_file_and_asks(self, tmp_path, monkeypatch):
        source = self.shared(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        out = io.StringIO()
        console = Console(file=out, width=200)
        assert install_recipe(source, console, None) == 1
        assert "not installed: run it in a terminal" in out.getvalue()
        assert "── scripts/fan.py" in out.getvalue() and "urllib.request" in out.getvalue()
        assert install_recipe(source, console, lambda q: False) == 1
        assert not (tmp_path / "home" / ".yantra").exists()
        asked = []
        assert install_recipe(source, console, lambda q: asked.append(q) or True) == 0
        assert asked and asked[0].startswith("install home-fan?")
        assert (tmp_path / "home" / ".yantra" / "skills" / "learned" / "home-fan").is_dir()


def test_share_from_the_terminal_writes_under_recipes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    learned_skill(tmp_path)
    out = io.StringIO()
    code = share_skill("home-fan", discover(tmp_path, home=tmp_path / "home"), None,
                       Console(file=out, width=200), tmp_path, memories=[])
    assert code == 0, out.getvalue()
    assert (tmp_path / "recipes" / "home-fan" / "SKILL.md").is_file()
    assert "sha256:" in out.getvalue() and "--skill-install" in out.getvalue()
