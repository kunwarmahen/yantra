"""Tools from outside the tree: discovery, isolation, and collisions.

The bias here is that a package tool is SOMEBODY ELSE'S CODE running as
you. So these tests care less about the happy path than about the four
ways discovery could quietly do the wrong thing: shadowing a built-in,
one package's module landing on another's, a broken file failing
silently, and a tool slipping past the admission policy its own manifest
declared. Each one is a security property wearing an ergonomics costume.
"""

from __future__ import annotations

import sys

import pytest

from conftest import ScriptedProvider, assistant_text
from yantra.agent import ToolExecuted
from yantra.errors import ConfigError
from yantra.permissions import allow_read_only
from yantra.package import MANIFEST, load_package
from yantra.spec import AgentSpec
from yantra.tools import default_registry
from yantra.tools.base import ToolContext, ToolRegistry
from yantra.tools.discover import (
    NAMESPACE,
    discover_tools,
    package_tool_names,
    register_tool_dirs,
)
from yantra.types import Message, ModelResponse, ToolCall

#: A whole tool file, parameterized so a test can vary the one thing it
#: is about. This is what an author actually writes.
TOOL = '''
from yantra.tools.base import Tool

class {cls}(Tool):
    name = "{name}"
    description = "a package tool"
    parameters = {{"type": "object", "properties": {{}}}}
    read_only = {read_only}

    def summary(self, args, ctx):
        return "{name}()"

    def run(self, args, ctx):
        return "{says}"
'''


def write_tool(directory, stem, *, cls="Mine", name=None, says="hello",
               read_only=True):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.py"
    path.write_text(TOOL.format(cls=cls, name=name or stem, says=says,
                                read_only=read_only), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _clean_namespace():
    """Package modules are cached by directory, and tmp_path changes every
    test -- but a test that reuses a path must not inherit the last one."""
    yield
    for name in [n for n in sys.modules if n.startswith(NAMESPACE)]:
        del sys.modules[name]


class TestDiscovery:
    def test_a_package_tool_registers_and_runs(self, tmp_path):
        write_tool(tmp_path / "tools", "outline", says="# heading")
        registry = ToolRegistry()

        assert register_tool_dirs(registry, [tmp_path / "tools"]) == ["outline"]
        tool = registry.get("outline")
        assert tool.run({}, ToolContext(cwd=tmp_path)) == "# heading"
        assert tool.spec().name == "outline"

    def test_the_imported_Tool_base_is_not_registered(self, tmp_path):
        """Every tool file imports Tool to subclass it. Registering what a
        module imported rather than what it DEFINED would register Tool."""
        write_tool(tmp_path / "tools", "one")
        assert [t.name for t in discover_tools(tmp_path / "tools")] == ["one"]

    def test_an_abstract_base_in_the_file_is_skipped(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "pair.py").write_text('''
from abc import abstractmethod
from yantra.tools.base import Tool

class Shared(Tool):
    """A base the concrete tool below shares -- not a tool itself."""
    description = "shared"
    parameters = {"type": "object", "properties": {}}

    @abstractmethod
    def run(self, args, ctx): ...

    def summary(self, args, ctx):
        return "shared"

class Real(Shared):
    name = "real"

    def run(self, args, ctx):
        return "ran"
''', encoding="utf-8")
        assert [t.name for t in discover_tools(tmp_path / "tools")] == ["real"]

    def test_helpers_are_skipped_and_importable_from_a_sibling(self, tmp_path):
        """A leading underscore says "not a tool file", and `from . import`
        reaches it -- without the directory going on sys.path."""
        tools = tmp_path / "tools"
        tools.mkdir()
        (tools / "_shared.py").write_text("GREETING = 'from the helper'\n",
                                          encoding="utf-8")
        (tools / "greet.py").write_text('''
from yantra.tools.base import Tool
from . import _shared

class Greet(Tool):
    name = "greet"
    description = "greets"
    parameters = {"type": "object", "properties": {}}
    read_only = True

    def summary(self, args, ctx):
        return "greet()"

    def run(self, args, ctx):
        return _shared.GREETING
''', encoding="utf-8")

        [tool] = discover_tools(tools)
        assert tool.run({}, ToolContext(cwd=tmp_path)) == "from the helper"
        assert str(tools) not in sys.path
        assert "_shared" not in sys.modules

    def test_registration_order_follows_the_directory_listing(self, tmp_path):
        for stem in ("charlie", "alpha", "bravo"):
            write_tool(tmp_path / "tools", stem, cls=stem.title())
        assert [t.name for t in discover_tools(tmp_path / "tools")] == [
            "alpha", "bravo", "charlie"]


class TestIsolation:
    def test_two_packages_may_both_ship_the_same_filename(self, tmp_path):
        """The reason modules load by path under a per-directory name: one
        package's search.py must not become the other's."""
        write_tool(tmp_path / "a" / "tools", "search", cls="SearchA",
                   name="search_a", says="from a")
        write_tool(tmp_path / "b" / "tools", "search", cls="SearchB",
                   name="search_b", says="from b")

        registry = ToolRegistry()
        register_tool_dirs(registry, [tmp_path / "a" / "tools",
                                      tmp_path / "b" / "tools"])
        ctx = ToolContext(cwd=tmp_path)
        assert registry.get("search_a").run({}, ctx) == "from a"
        assert registry.get("search_b").run({}, ctx) == "from b"

    def test_a_package_tool_does_not_become_globally_importable(self, tmp_path):
        write_tool(tmp_path / "tools", "search")
        discover_tools(tmp_path / "tools")
        assert "search" not in sys.modules
        assert str(tmp_path / "tools") not in sys.path

    def test_the_same_directory_twice_yields_the_same_classes(self, tmp_path):
        """Two agents from one package. Re-executing the module would make
        a second set of classes that are not the first set."""
        write_tool(tmp_path / "tools", "once")
        first = discover_tools(tmp_path / "tools")[0]
        second = discover_tools(tmp_path / "tools")[0]
        assert type(first) is type(second)


class TestCollisions:
    def test_shadowing_a_builtin_is_refused_by_name(self, tmp_path):
        """The security-hole-as-convenience case: a package tool called
        'bash' that the operator believes is the built-in one."""
        write_tool(tmp_path / "tools", "shell", cls="Shell", name="bash")
        registry = default_registry()
        with pytest.raises(ConfigError, match="already registered"):
            register_tool_dirs(registry, [tmp_path / "tools"])
        # and the real bash is the one still there
        assert type(registry.get("bash")).__module__ == "yantra.tools.shell"

    def test_two_package_tools_with_one_name_collide(self, tmp_path):
        write_tool(tmp_path / "tools", "first", cls="First", name="same")
        write_tool(tmp_path / "tools", "second", cls="Second", name="same")
        with pytest.raises(ConfigError, match="second.py"):
            register_tool_dirs(ToolRegistry(), [tmp_path / "tools"])


class TestBrokenPackages:
    def test_an_import_error_names_the_file(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "broken.py").write_text(
            "import nonexistent_module_xyz\n", encoding="utf-8")
        with pytest.raises(ConfigError, match=r"broken\.py: ModuleNotFoundError"):
            discover_tools(tmp_path / "tools")

    def test_a_file_that_defines_no_tool_is_an_error(self, tmp_path):
        """The mistake that actually happens: a class that forgot to
        subclass Tool. Silence here ships an agent missing its own tool."""
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "almost.py").write_text(
            "class Almost:\n    name = 'almost'\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="defines no Tool subclass"):
            discover_tools(tmp_path / "tools")

    def test_a_tool_needing_constructor_arguments_says_so(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "needy.py").write_text('''
from yantra.tools.base import Tool

class Needy(Tool):
    name = "needy"
    description = "needs wiring"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, session):
        self.session = session

    def summary(self, args, ctx):
        return "needy()"

    def run(self, args, ctx):
        return "ran"
''', encoding="utf-8")
        with pytest.raises(ConfigError, match="constructible with no arguments"):
            discover_tools(tmp_path / "tools")

    def test_a_tool_without_a_name_says_so(self, tmp_path):
        (tmp_path / "tools").mkdir()
        (tmp_path / "tools" / "anon.py").write_text('''
from yantra.tools.base import Tool

class Anon(Tool):
    description = "no name"
    parameters = {"type": "object", "properties": {}}

    def summary(self, args, ctx):
        return "anon()"

    def run(self, args, ctx):
        return "ran"
''', encoding="utf-8")
        with pytest.raises(ConfigError, match="has no 'name'"):
            discover_tools(tmp_path / "tools")

    def test_a_missing_directory_is_an_error(self, tmp_path):
        with pytest.raises(ConfigError, match="is not a directory"):
            discover_tools(tmp_path / "nope")


class TestManifest:
    def _pkg(self, root, manifest: str):
        root.mkdir(parents=True, exist_ok=True)
        (root / MANIFEST).write_text(manifest, encoding="utf-8")
        return root

    def test_a_tools_directory_is_found_by_convention(self, tmp_path):
        folder = self._pkg(tmp_path / "pkg", '[agent]\nname = "p"\n')
        write_tool(folder / "tools", "outline")
        assert load_package(folder).tool_dirs == (folder / "tools",)

    def test_no_tools_directory_means_none(self, tmp_path):
        folder = self._pkg(tmp_path / "pkg", '[agent]\nname = "p"\n')
        assert load_package(folder).tool_dirs == ()

    def test_dirs_may_be_declared_elsewhere(self, tmp_path):
        folder = self._pkg(tmp_path / "pkg",
                           '[tools]\ndirs = ["extra"]\n')
        write_tool(folder / "extra", "outline")
        assert load_package(folder).tool_dirs == ((folder / "extra").resolve(),)

    def test_a_declared_directory_must_exist(self, tmp_path):
        folder = self._pkg(tmp_path / "pkg", '[tools]\ndirs = ["gone"]\n')
        with pytest.raises(ConfigError, match="is not a directory"):
            load_package(folder)

    def test_reading_a_manifest_never_runs_the_tools(self, tmp_path):
        """The property dvara depends on: inspecting a package is a pure
        read. Code runs only when somebody builds an agent from it."""
        folder = self._pkg(tmp_path / "pkg", '[agent]\nname = "p"\n')
        (folder / "tools").mkdir()
        (folder / "tools" / "bomb.py").write_text(
            "raise RuntimeError('import side effect')\n", encoding="utf-8")

        spec = load_package(folder)          # no explosion
        assert spec.tool_dirs == (folder / "tools",)
        with pytest.raises(ConfigError, match="bomb.py"):
            spec.build(provider=object(), provider_name="anthropic", model="m")


class TestBuild:
    def _spec(self, tmp_path, **kwargs) -> AgentSpec:
        return AgentSpec(tool_dirs=(tmp_path / "tools",), cwd=tmp_path,
                         **kwargs)

    def test_build_registers_package_tools(self, tmp_path):
        write_tool(tmp_path / "tools", "outline")
        agent = self._spec(tmp_path).build(
            provider=object(), provider_name="anthropic", model="m")
        assert "outline" in agent.registry
        assert package_tool_names(agent.registry) == ["outline"]

    def test_the_admission_policy_still_governs_package_tools(self, tmp_path):
        """A package cannot smuggle its own code past its own tools.allow --
        the policy is installed before anything registers, package code
        included."""
        write_tool(tmp_path / "tools", "outline")
        agent = self._spec(tmp_path, tool_allow=("read_file",)).build(
            provider=object(), provider_name="anthropic", model="m")
        assert "outline" not in agent.registry
        assert "outline" in agent.registry.refused_names()

    def test_read_only_reaches_the_permission_gate(self, tmp_path):
        """The author's risk declaration is the whole permission story for
        a package tool -- nothing else can judge it, so it had better
        actually arrive at the gate. Here the gate is allow_read_only: the
        tool declaring True runs, the one declaring False is denied, and
        neither of them is a built-in the gate could have known about."""
        write_tool(tmp_path / "tools", "safe", cls="Safe", read_only=True,
                   says="read ok")
        write_tool(tmp_path / "tools", "risky", cls="Risky", read_only=False)
        script = [
            ModelResponse(
                message=Message("assistant", [
                    ToolCall(id="s1", name="safe", arguments={}),
                    ToolCall(id="r1", name="risky", arguments={}),
                ]),
                stop_reason="tool_use",
            ),
            assistant_text("done"),
        ]
        agent = self._spec(tmp_path).build(
            provider=ScriptedProvider(script), provider_name="anthropic",
            model="m", permissions=allow_read_only)

        results = [e.result for e in agent.run_streaming("go")
                   if isinstance(e, ToolExecuted)]
        assert [(r.tool_call_id, r.is_error) for r in results] == [
            ("s1", False), ("r1", True)]
        assert results[0].content == "read ok"
        assert "denied" in results[1].content.lower()

    def test_package_tool_names_ignores_the_builtins(self, tmp_path):
        write_tool(tmp_path / "tools", "outline")
        agent = self._spec(tmp_path).build(
            provider=object(), provider_name="anthropic", model="m")
        assert "read_file" in agent.registry
        assert package_tool_names(agent.registry) == ["outline"]
