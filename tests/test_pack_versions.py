"""Which release of a pack, what Yantra it needs, and what it is called
(notes/87).

The bias here is the check that looks like it holds and does not. Each
of these was tempting and is pinned against:

* **A range that is accepted and means nothing.** ``tide-pack>=0.2``
  read as a name, or parsed and never compared, is a line an author
  trusts and nothing enforces. Anything but ``==`` is refused, at
  manifest load, against the file.
* **A check that runs after the harm.** A pack that is the wrong
  release, or built for a newer Yantra, must be refused BEFORE it is
  imported -- its import-time code is the thing not to run.
* **A guess dressed as a check.** A requirement this cannot compare
  (``~=``, a wildcard, a pre-release, a conditional line) is left alone
  rather than approximated.
* **A rename that only half happens.** A prefixed tool is registered,
  admitted, and collides under its NEW name; the old name is gone.
* **A report that says "something moved" when it could say what.**
"""

from __future__ import annotations

import types

import pytest

from yantra.errors import ConfigError
from yantra.eval_report import compare, read_report, write_report
from yantra.fingerprint import fingerprint
from yantra.spec import AgentSpec
from yantra.tools.base import ToolRegistry
from yantra.tools.discover import (ENTRY_POINT_GROUP,
                                   installed_pack_versions, parse_pack,
                                   register_tool_packs)

from test_fingerprint import run
from test_tool_packs import Tides, Weather, module_with


@pytest.fixture
def installed(monkeypatch):
    """Install packs for the duration of one test."""
    def install(*entries):
        def entry_points(*, group):
            assert group == ENTRY_POINT_GROUP
            return list(entries)
        monkeypatch.setattr("yantra.tools.discover.metadata.entry_points",
                            entry_points)
    return install


class Entry:
    """An entry point whose distribution has a version and requirements."""

    def __init__(self, target, *, dist="tide-pack", version="0.2.1",
                 requires=None, value="tidepack:Tides"):
        self.name, self.value, self._target = "tools", value, target
        self.dist = types.SimpleNamespace(name=dist, version=version,
                                          requires=requires)

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


class TestTheLine:
    def test_a_name_alone(self):
        assert parse_pack("tide-pack") == ("tide-pack", None)

    def test_a_name_and_a_release(self):
        assert parse_pack(" tide-pack == 0.2.1 ") == ("tide-pack", "0.2.1")

    @pytest.mark.parametrize("line", ["tide-pack>=0.2", "tide-pack~=0.2",
                                      "tide-pack==0.2,<1", "tide-pack==0.*",
                                      "tide-pack==", "==0.2"])
    def test_anything_but_one_exact_release_is_refused(self, line):
        with pytest.raises(ConfigError):
            parse_pack(line)


class TestTheReleaseIsChecked:
    def test_the_named_release_loads(self, installed):
        installed(Entry(Tides))
        assert register_tool_packs(ToolRegistry(),
                                   ["tide-pack==0.2.1"]) == ["tides"]

    def test_another_release_is_refused_with_both_numbers(self, installed):
        installed(Entry(Tides, version="0.3.0"))
        with pytest.raises(ConfigError, match=r"0\.2\.1.*0\.3\.0"):
            register_tool_packs(ToolRegistry(), ["tide-pack==0.2.1"])

    def test_the_check_comes_before_the_import(self, installed):
        """A pack that would blow up on import is refused for its release,
        which means its import-time code never ran."""
        installed(Entry(RuntimeError("import-time side effect"),
                        version="0.3.0"))
        with pytest.raises(ConfigError, match="names release"):
            register_tool_packs(ToolRegistry(), ["tide-pack==0.2.1"])

    def test_no_pin_loads_whatever_is_installed(self, installed):
        installed(Entry(Tides, version="9.9"))
        assert register_tool_packs(ToolRegistry(), ["tide-pack"]) == ["tides"]


class TestWhatThePackNeedsFromYantra:
    """This build is yantra 0.1.0."""

    @pytest.mark.parametrize("requires", [
        None, [], ["yantra>=0.1"], ["yantra (>=0.1.0)"], ["yantra==0.1"],
        ["httpx>=0.28"], ["Yantra>=0.1,<1"],
    ])
    def test_a_requirement_this_build_meets(self, installed, requires):
        installed(Entry(Tides, requires=requires))
        assert register_tool_packs(ToolRegistry(), ["tide-pack"]) == ["tides"]

    @pytest.mark.parametrize("requires", [["yantra>=0.4"],
                                          ["yantra>=0.1,<0.1"],
                                          ["yantra[web]>=2"]])
    def test_a_newer_base_is_refused_at_the_door(self, installed, requires):
        installed(Entry(RuntimeError("never imported"), requires=requires))
        with pytest.raises(ConfigError, match="requires yantra"):
            register_tool_packs(ToolRegistry(), ["tide-pack"])

    @pytest.mark.parametrize("requires", [
        ['yantra>=9; extra == "web"'], ["yantra~=9.0"], ["yantra>=9.0rc1"],
        ["yantra==9.*"],
    ])
    def test_what_cannot_be_compared_is_left_alone(self, installed, requires):
        installed(Entry(Tides, requires=requires))
        assert register_tool_packs(ToolRegistry(), ["tide-pack"]) == ["tides"]


class TestAPrefix:
    def test_it_renames_every_tool_in_the_pack(self, installed):
        installed(Entry(module_with(Weather, Tides, name="tidepack.tools"),
                        value="tidepack.tools"))
        registry = ToolRegistry()
        names = register_tool_packs(registry, ["tide-pack"],
                                    prefixes={"tide-pack": "tide"})
        assert sorted(names) == ["tide_tides", "tide_weather"]
        assert "weather" not in registry
        assert registry.get("tide_weather").spec().name == "tide_weather"

    def test_it_settles_a_collision(self, installed):
        registry = ToolRegistry()
        registry.register(Weather())
        installed(Entry(Weather, value="tidepack:Weather"))
        with pytest.raises(ConfigError, match=r"\[tools\.prefix\]"):
            register_tool_packs(registry, ["tide-pack"])
        register_tool_packs(registry, ["tide-pack"],
                            prefixes={"tide-pack": "tide"})
        assert "tide_weather" in registry and "weather" in registry

    def test_the_class_keeps_its_own_name(self, installed):
        installed(Entry(Weather, value="tidepack:Weather"))
        register_tool_packs(ToolRegistry(), ["tide-pack"],
                            prefixes={"tide-pack": "tide"})
        assert Weather.name == "weather"

    def test_admission_names_the_prefixed_tool(self, installed):
        installed(Entry(Weather, value="tidepack:Weather"))
        registry = ToolRegistry()
        registry.admit_only(("tide_weather",), ())
        assert register_tool_packs(registry, ["tide-pack"],
                                   prefixes={"tide-pack": "tide"}) == [
            "tide_weather"]

    @pytest.mark.parametrize("prefix", ["Tide", "tide_", "1tide", ""])
    def test_a_bad_prefix_is_refused(self, installed, prefix):
        installed(Entry(Weather, value="tidepack:Weather"))
        with pytest.raises(ConfigError, match="prefix"):
            register_tool_packs(ToolRegistry(), ["tide-pack"],
                                prefixes={"tide-pack": prefix})

    def test_through_the_spec(self, installed):
        from conftest import ScriptedProvider
        installed(Entry(Weather, value="tidepack:Weather"))
        spec = AgentSpec(tool_packs=("tide-pack==0.2.1",),
                         tool_prefixes=(("tide-pack", "tide"),), model="m")
        agent = spec.build(provider=ScriptedProvider([]),
                           provider_name="anthropic")
        assert "tide_weather" in agent.registry


class TestTheManifest:
    def load(self, tmp_path, tools):
        from yantra.package import MANIFEST, load_package
        (tmp_path / MANIFEST).write_text(
            f'[agent]\nname = "p"\n[tools]\n{tools}\n')
        return load_package(tmp_path)

    def test_a_pin_and_a_prefix(self, tmp_path):
        spec = self.load(tmp_path, 'packs = ["tide-pack==0.2.1"]\n'
                                   '[tools.prefix]\n"tide-pack" = "tide"')
        assert spec.tool_packs == ("tide-pack==0.2.1",)
        assert spec.tool_prefixes == (("tide-pack", "tide"),)

    def test_a_range_is_refused_against_the_file(self, tmp_path):
        with pytest.raises(ConfigError, match="agent.toml.*lockfile"):
            self.load(tmp_path, 'packs = ["tide-pack>=0.2"]')

    def test_a_prefix_for_a_pack_not_loaded_is_a_typo(self, tmp_path):
        with pytest.raises(ConfigError, match="does not load"):
            self.load(tmp_path, 'packs = ["tide-pack"]\n'
                                '[tools.prefix]\n"tides-pack" = "tide"')

    def test_a_bad_prefix_is_refused_against_the_file(self, tmp_path):
        with pytest.raises(ConfigError, match="lower-case"):
            self.load(tmp_path, 'packs = ["tide-pack"]\n'
                                '[tools.prefix]\n"tide-pack" = "Tide"')


class TestTheReportSaysWhichPack:
    def test_a_pinned_line_is_fingerprinted_by_name(self, tmp_path):
        spec = AgentSpec(tool_packs=("httpx==0.0.1",), root=tmp_path)
        assert fingerprint(spec) == fingerprint(
            AgentSpec(tool_packs=("httpx",), root=tmp_path))

    def test_installed_versions_by_name(self):
        versions = installed_pack_versions(["httpx==0.0.1", "no-such-pack"])
        assert versions["httpx"] and versions["no-such-pack"] is None

    def test_the_packs_round_trip(self, tmp_path):
        r = run("aaa", "2026-09-01T00:00:00Z")
        r.packs = {"tide-pack": "0.2.1"}
        write_report(tmp_path / "r.json", r)
        assert read_report(tmp_path / "r.json").packs == {"tide-pack": "0.2.1"}

    def test_a_moved_pack_is_named(self):
        before, after = (run("aaa", "2026-09-01T00:00:00Z"),
                         run("bbb", "2026-09-02T00:00:00Z"))
        before.packs = {"tide-pack": "0.2.1", "old-pack": "1.0"}
        after.packs = {"tide-pack": "0.3.0"}
        assert compare(before, after).packs_moved == [
            ("old-pack", "1.0", None), ("tide-pack", "0.2.1", "0.3.0")]

    def test_an_older_report_moves_nothing(self):
        after = run("bbb", "2026-09-02T00:00:00Z")
        after.packs = {"tide-pack": "0.3.0"}
        assert compare(run("aaa", "2026-09-01T00:00:00Z"),
                       after).packs_moved == []
