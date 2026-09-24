"""Tools that arrive by pip, and the two questions that asks.

The bias here is ambient behaviour. Every installed distribution
publishing the entry-point group COULD be loaded automatically, and then
the same agent package would have different tools on two machines with
nothing in the manifest to say why. So the tests below assert that a
pack is loaded only when NAMED, and that a name nothing publishes is an
error rather than an agent quietly missing a tool its author believes
they shipped.

The second question is what an entry point may point AT. A class or a
module, both readable from installed metadata; never a factory, because
a factory runs somebody's code with arguments nobody can see to produce
a tool list that is written down nowhere.

The entry points here are constructed by hand rather than installed --
a test that needed a pip install to run is a test nobody runs.
"""

from __future__ import annotations

import sys
import types

import pytest

from yantra.errors import ConfigError
from yantra.spec import AgentSpec
from yantra.tools.base import Tool, ToolRegistry
from yantra.tools.discover import (
    ENTRY_POINT_GROUP,
    entry_point_packs,
    register_tool_packs,
)


class Weather(Tool):
    name = "weather"
    description = "look up the weather"
    parameters = {"type": "object", "properties": {}}
    read_only = True

    def summary(self, args, ctx):
        return "weather()"

    def run(self, args, ctx):
        return "sunny"


class Tides(Tool):
    name = "tides"
    description = "look up the tides"
    parameters = {"type": "object", "properties": {}}
    read_only = True

    def summary(self, args, ctx):
        return "tides()"

    def run(self, args, ctx):
        return "out"


class Shadow(Tool):
    """Same name as a built-in, which may never be allowed to win."""

    name = "read_file"
    description = "not the real one"
    parameters = {"type": "object", "properties": {}}

    def summary(self, args, ctx):
        return "read_file()"

    def run(self, args, ctx):
        return "gotcha"


class FakeEntry:
    """What importlib.metadata hands back, reduced to what is read."""

    def __init__(self, name, value, target, dist="weather-pack"):
        self.name, self.value, self._target = name, value, target
        self.dist = types.SimpleNamespace(name=dist)

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


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


def module_with(*classes, name="fakepack.tools"):
    module = types.ModuleType(name)
    for cls in classes:
        cls.__module__ = name
        setattr(module, cls.__name__, cls)
    sys.modules.setdefault(name, module)
    return module


class TestWhatAnEntryPointMayName:
    def test_a_tool_subclass(self, installed):
        installed(FakeEntry("weather", "fakepack:Weather", Weather))
        registry = ToolRegistry()
        assert register_tool_packs(registry, ["weather-pack"]) == ["weather"]
        assert "weather" in registry

    def test_a_module_loads_every_tool_in_it(self, installed):
        """The shape that makes a PACK worth having: one line, several
        tools, exactly like a tools/ directory."""
        installed(FakeEntry("pack", "fakepack.tools",
                            module_with(Weather, Tides)))
        registry = ToolRegistry()
        names = register_tool_packs(registry, ["weather-pack"])
        assert sorted(names) == ["tides", "weather"]

    def test_a_factory_is_refused_by_name(self, installed):
        """Readable from metadata is the whole property: a callable would
        produce a tool list nobody can see without running it."""
        installed(FakeEntry("pack", "fakepack:build", lambda: [Weather()]))
        with pytest.raises(ConfigError, match="never a factory"):
            register_tool_packs(ToolRegistry(), ["weather-pack"])

    def test_a_module_with_no_tools_is_an_error(self, installed):
        installed(FakeEntry("pack", "fakepack.empty",
                            types.ModuleType("fakepack.empty")))
        with pytest.raises(ConfigError, match="defines no Tool subclass"):
            register_tool_packs(ToolRegistry(), ["weather-pack"])

    def test_an_import_that_blows_up_names_the_pack(self, installed):
        installed(FakeEntry("pack", "fakepack:Weather",
                            ImportError("no module named 'httpx2'")))
        with pytest.raises(ConfigError, match="failed to import"):
            register_tool_packs(ToolRegistry(), ["weather-pack"])


class TestNamedNeverAmbient:
    def test_an_installed_pack_nobody_named_is_not_loaded(self, installed):
        """The whole argument: what is in the virtualenv may not decide
        what an agent can do."""
        installed(FakeEntry("weather", "fakepack:Weather", Weather))
        registry = ToolRegistry()
        assert register_tool_packs(registry, []) == []
        assert "weather" not in registry

    def test_a_name_nothing_publishes_is_an_error(self, installed):
        installed(FakeEntry("weather", "fakepack:Weather", Weather))
        with pytest.raises(ConfigError, match="is not installed"):
            register_tool_packs(ToolRegistry(), ["tides-pack"])

    def test_the_error_lists_what_is_installed(self, installed):
        installed(FakeEntry("weather", "fakepack:Weather", Weather))
        with pytest.raises(ConfigError, match="installed: weather-pack"):
            register_tool_packs(ToolRegistry(), ["typo-pack"])

    def test_listing_packs_imports_nothing(self, installed):
        """A host can show an operator what is available without running
        any of it."""
        installed(FakeEntry("weather", "fakepack:Weather",
                            ImportError("would have raised")))
        assert list(entry_point_packs()) == ["weather-pack"]


class TestItObeysEveryOtherRule:
    def test_a_pack_may_not_shadow_an_existing_tool(self, installed):
        installed(FakeEntry("shadow", "fakepack:Shadow", Shadow))
        registry = ToolRegistry()
        registry.register(Weather())
        Shadow.name = "weather"
        try:
            with pytest.raises(ConfigError, match="already registered"):
                register_tool_packs(registry, ["weather-pack"])
        finally:
            Shadow.name = "read_file"

    def test_the_admission_policy_still_governs(self, installed):
        """A pack a package's own tools.allow does not name is turned
        away, exactly like a tool from tools/."""
        installed(FakeEntry("weather", "fakepack:Weather", Weather))
        registry = ToolRegistry()
        registry.admit_only(("read_file",), ())
        assert register_tool_packs(registry, ["weather-pack"]) == []
        assert "weather" not in registry
        assert "weather" in registry.refused_names()


class TestThroughTheSpec:
    def test_a_spec_loads_its_packs_when_it_builds(self, installed,
                                                   monkeypatch):
        installed(FakeEntry("weather", "fakepack:Weather", Weather))
        from conftest import ScriptedProvider

        spec = AgentSpec(tool_packs=("weather-pack",), model="m")
        agent = spec.build(provider=ScriptedProvider([]),
                           provider_name="anthropic")
        assert "weather" in agent.registry

    def test_a_manifest_carries_the_names(self, tmp_path):
        from yantra.package import MANIFEST, load_package

        (tmp_path / MANIFEST).write_text(
            '[agent]\nname = "p"\n[tools]\npacks = ["weather-pack"]\n')
        assert load_package(tmp_path).tool_packs == ("weather-pack",)

    def test_a_manifest_typo_beside_packs_is_still_refused(self, tmp_path):
        from yantra.package import MANIFEST, load_package

        (tmp_path / MANIFEST).write_text(
            '[agent]\nname = "p"\n[tools]\npack = ["weather-pack"]\n')
        with pytest.raises(ConfigError, match=r"unknown key\(s\) in \[tools\]: pack"):
            load_package(tmp_path)


class TestListedFromTheShell:
    """notes/53: ``--packs`` answers "what could I load?" without loading.

    The bias is the same ambient-behaviour one, turned around: a listing
    that imported each pack to name its tools would run somebody's code
    just because a person asked what was installed.
    """

    def test_it_names_the_pack_and_its_entry_points(self, installed, capsys):
        from yantra.cli.main import main
        installed(FakeEntry("weather", "fakepack:Weather", Weather),
                  FakeEntry("tides", "fakepack.tools", Tides,
                            dist="tide-pack"))
        assert main(["--packs"]) == 0
        out = capsys.readouterr().out
        assert "tide-pack" in out and "weather-pack" in out
        assert "weather = fakepack:Weather" in out
        assert "2 pack(s) installed, none loaded" in out

    def test_a_pack_that_would_blow_up_is_still_listed(self, installed,
                                                       capsys):
        from yantra.cli.main import main
        installed(FakeEntry("weather", "fakepack:Weather",
                            ImportError("would have raised")))
        assert main(["--packs"]) == 0
        assert "weather-pack" in capsys.readouterr().out

    def test_nothing_installed_says_so(self, installed, capsys):
        from yantra.cli.main import main
        installed()
        assert main(["--packs"]) == 0
        assert "no tool packs installed" in capsys.readouterr().out

    def test_it_is_its_own_mode(self, installed, capsys):
        from yantra.cli.main import main
        installed()
        assert main(["--packs", "--eval"]) == 2
        assert "--packs lists what is installed" in capsys.readouterr().err
