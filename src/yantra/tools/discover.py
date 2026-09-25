"""Tools from outside the tree: loading a package's own Tool subclasses.

Until now a package could only NARROW the built-in set -- ``tools.allow``
and ``tools.deny`` pick a subset of sixteen. That makes "build your own
agent with Yantra" mean "choose some of my tools", which is a
configurable CLI, not a framework. So a package may ship code:

    researcher/
    |-- agent.toml       [tools] dirs = ["tools"]   (or just have tools/)
    `-- tools/
        |-- outline.py   -> class Outline(Tool)
        `-- _shared.py   helpers; leading underscore means "not a tool file"

WHAT IS NEW IS ONLY DISCOVERY. A third-party tool is the same ``Tool``
subclass a built-in is, with the same hand-written ``parameters`` schema.
There is deliberately NO ``@tool`` decorator that reads a function
signature and generates the schema for you. Every framework in this space
offers one; tools/base.py says in as many words that writing the schema
by hand -- real ``description`` strings, a ``required`` list,
``additionalProperties: false`` -- IS the lesson, because vague schemas
produce vague tool calls. Generating a schema from type hints produces
exactly the vague schema the base module warns about, so the sugar would
quietly delete the most useful thing this harness teaches a tool author.

BY PATH, NEVER BY sys.path. Each file is loaded with
``importlib.util.spec_from_file_location`` under a private, per-directory
module name. Two packages must both be able to ship ``tools/search.py``,
and neither may become importable as ``search`` for everything else in
the process -- an agent package is not allowed to change what ``import
json`` means for its host.

COLLISIONS ARE LOUD. ``ToolRegistry.register`` already refuses a
duplicate name, and this module turns that into an error naming the file.
A package tool that silently shadowed ``bash`` would be a security hole
dressed up as a convenience.

``read_only`` IS THE AUTHOR'S DECLARATION OF RISK, AND NOTHING CHECKS IT.
It drives permission auto-approval, so an author who writes
``read_only = True`` on a tool that deletes files has lied to the gate
and the gate has no way to know. That is the same trust you extend to
any dependency you install; it is stated here because for package tools
it is the whole of the security model.

THE SECURITY PARAGRAPH, WHICH IS THE IMPORTANT PART OF THIS MODULE.
Loading ``tools/*.py`` is arbitrary code execution as the invoking user,
at import time, before any permission gate exists. That is fine and
normal for a package YOU chose to run -- it is exactly what ``pip
install`` does. It stops being fine the moment a package path can come
from anywhere but a human's hands:

1. A package path comes from the operator's command line, or from a root
   the owner configured. NEVER from a trigger payload, a chat message, a
   webhook body, or a string a model produced.
2. A service resolves packages from an owner-controlled directory only.
   A message that can name a package path is remote code execution with
   the harness's own credentials behind it.
3. Loading a package is not sandboxed and will not pretend to be. The
   sandbox confines the AGENT's tool calls; it has nothing to say about
   a module's import-time side effects.

Which is also why ``load_package`` does not call any of this: parsing a
manifest stays a pure read, and code runs only when something actually
builds an agent from it.

A PACK IS NAMED WITH THE RELEASE IT WAS WRITTEN AGAINST, IF ITS AUTHOR
WANTS (notes/87). ``packs = ["tide-pack==0.2.1"]`` is CHECKED, never
resolved: the environment's lockfile still chooses what is installed,
and a manifest that disagrees with it refuses to start rather than
grading an agent against a release nobody meant. Exact versions only --
a range is a resolver's question, and answering it here would mean a
PEP 440 parser or a dependency the core does not have. The same load
reads what the pack says it needs from Yantra (``Requires-Dist:
yantra>=X``) and refuses a pack built for a newer base class at the
door, with the pack's own requirement in the message, instead of at
registration with a TypeError. And a pack's tools may be RENAMED by a
prefix the manifest chooses, the one way out of a collision between two
packs that both ship ``search`` short of forking one.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import importlib.util
import inspect
import re
import sys
import types
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

from yantra.errors import ConfigError
from yantra.tools.base import Tool, ToolRegistry

#: Every package tool module lives under this name. Private, and never on
#: sys.path: the only way in is by the path the operator named.
NAMESPACE = "yantra._package_tools"


def _parent_module(directory: Path) -> str:
    """A private parent package for one directory, created on demand.

    The name carries a digest of the resolved directory, which is what
    lets two agent packages each ship ``tools/search.py`` without the
    second one landing on the first in ``sys.modules``.

    It is a real package (it has ``__path__``), so a tool file can say
    ``from . import _shared`` and get the helper sitting next to it --
    resolved against THIS directory only, with global imports untouched.
    """
    if NAMESPACE not in sys.modules:
        root = types.ModuleType(NAMESPACE)
        root.__path__ = []  # a package with no importable children of its own
        sys.modules[NAMESPACE] = root

    digest = hashlib.sha256(str(directory).encode("utf-8")).hexdigest()[:12]
    name = f"{NAMESPACE}.{directory.name}_{digest}"
    if name not in sys.modules:
        parent = types.ModuleType(name)
        parent.__path__ = [str(directory)]
        sys.modules[name] = parent
    return name


def _load_module(path: Path, parent: str) -> types.ModuleType:
    """Execute one file as a module, or report which file broke."""
    name = f"{parent}.{path.stem}"
    if name in sys.modules:
        # The same directory reached twice in one process (two agents from
        # one package, a rebuilt spec). Re-executing would produce a second
        # set of classes that are not the first set.
        return sys.modules[name]

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"{path}: cannot be loaded as a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # before exec: dataclasses and `from .` look here
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        del sys.modules[name]
        raise ConfigError(f"{path}: {type(exc).__name__}: {exc}") from exc
    return module


def load_module_file(path: Path) -> types.ModuleType:
    """One file executed as a private module, addressed by path.

    The same import discipline tools get -- a per-directory private
    parent, nothing added to ``sys.path``, the file's own directory
    importable as ``from . import _helpers`` -- offered to anything else
    a package ships that is Python rather than data. An eval suite's
    ``graders.py`` is the first caller (eval_suite.py); the loading rules
    are identical because the trust is identical.
    """
    path = Path(path)
    return _load_module(path, _parent_module(path.parent.resolve()))


def _tool_classes(module: types.ModuleType) -> list[type[Tool]]:
    """Concrete Tool subclasses DEFINED in this module.

    ``__module__`` is the whole of the filter's work: a tool file has to
    import ``Tool`` to subclass it, and without this check every package
    tool module would also try to register the abstract base itself.
    ``isabstract`` catches the other half -- a shared base class in a
    module that also defines a concrete tool.
    """
    return [
        value for value in vars(module).values()
        if inspect.isclass(value) and issubclass(value, Tool)
        and value.__module__ == module.__name__
        and not inspect.isabstract(value)
    ]


def _instantiate(cls: type[Tool], path: Path) -> Tool:
    name = getattr(cls, "name", None)
    if not isinstance(name, str) or not name:
        raise ConfigError(
            f"{path}: {cls.__name__} has no 'name' -- a tool needs the name "
            f"the model will call it by"
        )
    try:
        return cls()
    except Exception as exc:
        raise ConfigError(
            f"{path}: {cls.__name__}() failed: {type(exc).__name__}: {exc} "
            f"(a package tool must be constructible with no arguments -- "
            f"anything it needs, it builds or reads in run())"
        ) from exc


def _tools_in(directory: Path) -> Iterator[tuple[Path, Tool]]:
    """Every tool defined by the ``*.py`` files directly inside a directory.

    Not recursive, and files starting with ``_`` are skipped, so a tool
    file may keep helpers beside it (``_shared.py``, ``__init__.py``)
    without them being mistaken for tools. Sorted, so the order tools
    register in is the order you see in the directory listing.

    A file that is scanned and defines NO tool is an error. It is the
    shape of the mistake that actually happens -- a class that forgot to
    subclass ``Tool``, or a typo in the import -- and the alternative is
    an agent that quietly lacks the tool its author believes they shipped.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise ConfigError(f"tools directory {directory} is not a directory")
    parent = _parent_module(directory.resolve())

    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_") or not path.is_file():
            continue
        module = _load_module(path, parent)
        classes = _tool_classes(module)
        if not classes:
            raise ConfigError(
                f"{path}: defines no Tool subclass (a file in a tools "
                f"directory must define at least one; name helper files "
                f"with a leading underscore to have them skipped)"
            )
        for cls in classes:
            yield path, _instantiate(cls, path)


def discover_tools(directory: Path) -> list[Tool]:
    """Instantiated tools from one directory -- for hosts composing their
    own registry. Registering them is ``register_tool_dirs``."""
    return [tool for _, tool in _tools_in(directory)]


def register_tool_dirs(registry: ToolRegistry,
                       dirs: Iterable[Path]) -> list[str]:
    """Load every directory into ``registry``; return the names admitted.

    Admission still applies: these go through ``register`` like anything
    else, so a package that ships a tool its own ``tools.allow`` does not
    name gets it turned away and listed in ``refused_names()``. That is
    the same policy governing MCP tools and load_skill, and the one place
    a package's own code could otherwise smuggle itself past its own
    declared tool list.
    """
    admitted = []
    for directory in dirs:
        for path, tool in _tools_in(directory):
            try:
                registry.register(tool)
            except ValueError:
                raise ConfigError(
                    f"{path}: tool {tool.name!r} is already registered -- "
                    f"a package tool may not shadow another tool (rename "
                    f"it, or exclude the original with tools.deny)"
                ) from None
            if tool.name in registry:
                admitted.append(tool.name)
    return admitted


#: The entry-point group a distribution publishes tools under. One group,
#: named after this project, because a tool pack is a thing you install
#: FOR Yantra rather than a thing that happens to contain Tool subclasses.
ENTRY_POINT_GROUP = "yantra.tools"


def _tools_from_entry_point(entry) -> list[Tool]:
    """One entry point -> the tools it names.

    Two shapes, both declarative, and the second is why a pack is worth
    having at all:

    * ``name = "yourpack.tools:Weather"`` -- one ``Tool`` subclass.
    * ``name = "yourpack.tools"`` -- a MODULE, and every concrete ``Tool``
      subclass defined in it, exactly as a ``tools/`` directory works.

    A CALLABLE IS NOT A SHAPE HERE. An entry point that resolved to a
    factory would run somebody's code with arguments nobody can read, at
    a moment nobody chose, to produce a tool list that is not written
    down anywhere. The two forms above are both readable from the
    installed metadata without executing anything but an import.
    """
    try:
        target = entry.load()
    except Exception as exc:
        raise ConfigError(
            f"tool pack {entry.value!r} ({entry.name}) failed to import: "
            f"{type(exc).__name__}: {exc}") from None
    where = Path(f"<{entry.value}>")
    if inspect.isclass(target) and issubclass(target, Tool):
        if inspect.isabstract(target):
            raise ConfigError(
                f"tool pack {entry.value!r} names an abstract Tool subclass")
        return [_instantiate(target, where)]
    if isinstance(target, types.ModuleType):
        classes = _tool_classes(target)
        if not classes:
            raise ConfigError(
                f"tool pack {entry.value!r} defines no Tool subclass "
                f"(a module entry point loads every concrete Tool in it)")
        return [_instantiate(cls, where) for cls in classes]
    raise ConfigError(
        f"tool pack {entry.value!r} is neither a Tool subclass nor a "
        f"module: an entry point names a class or a module, never a "
        f"factory, so what a pack ships can be read from its metadata")


def entry_point_packs() -> dict[str, list[Any]]:
    """Installed distributions publishing ``yantra.tools``, by NAME.

    A listing, not a load: nothing here imports anything, so a host can
    show an operator what is installed and available without running it.
    """
    packs: dict[str, list[Any]] = {}
    for entry in metadata.entry_points(group=ENTRY_POINT_GROUP):
        dist = getattr(entry, "dist", None)
        name = getattr(dist, "name", None) or entry.name
        packs.setdefault(name, []).append(entry)
    return packs


#: What a manifest's prefix may look like (notes/87): the tool-name rule
#: without its underscore tail, since the prefix is joined with one.
PACK_PREFIX = re.compile(r"[a-z][a-z0-9]*\Z")

#: One clause of a requirement, as a pack author realistically writes it.
_CLAUSE = re.compile(r"\s*(>=|<=|==|!=|~=|>|<)\s*([^\s,]+)\s*\Z")
_RELEASE = re.compile(r"\d+(?:\.\d+)*\Z")
_REQUIREMENT = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?"
                          r"\s*\(?([^()]*)\)?\s*\Z")


def parse_pack(text: str) -> tuple[str, str | None]:
    """``"tide-pack"`` or ``"tide-pack==0.2.1"`` -> (name, pinned version).

    A pure read: a manifest is checked with this before anything is
    imported. ANY OTHER OPERATOR IS AN ERROR that says why -- a range
    would be quietly accepted by a reader who expected it to mean
    something and checked by nothing.
    """
    name, sep, version = text.strip().partition("==")
    name, version = name.strip(), version.strip()
    if not name or any(ch in name for ch in "<>=!~,; "):
        raise ConfigError(
            f"tool pack {text!r}: a pack is a name, or a name and the "
            f"exact release it was written against (tide-pack==0.2.1); "
            f"ranges belong in the environment's lockfile, which is what "
            f"chooses the version installed")
    if sep and (not version or any(ch in version for ch in "<>=!~,; *")):
        raise ConfigError(
            f"tool pack {text!r}: == takes one exact release, like 0.2.1")
    return name, (version if sep else None)


def _installed_version(name: str, entries: list[Any]) -> str | None:
    dist = getattr(entries[0], "dist", None)
    version = getattr(dist, "version", None)
    if version:
        return str(version)
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def installed_pack_versions(packs: Iterable[str]) -> dict[str, str | None]:
    """What each named pack IS in this environment, for a report
    (notes/87): the fingerprint says that something moved, and this says
    which pack and to what. None when it is not installed."""
    versions: dict[str, str | None] = {}
    for text in packs:
        name, _ = parse_pack(text)
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _release(version: str) -> tuple[int, ...] | None:
    """``"0.4.1"`` -> (0, 4, 1); None for anything with a suffix, which is
    not compared rather than compared wrongly."""
    if not _RELEASE.match(version):
        return None
    parts = [int(p) for p in version.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _satisfies(have: tuple[int, ...], op: str, want: tuple[int, ...]
               ) -> bool | None:
    return {">=": have >= want, ">": have > want, "<=": have <= want,
            "<": have < want, "==": have == want,
            "!=": have != want}.get(op)


def _check_base(name: str, entries: list[Any]) -> None:
    """Refuse a pack built for a Yantra this is not (notes/87).

    Reads the pack's own ``Requires-Dist`` for ``yantra``. A line with an
    environment marker is conditional and is left alone; ``~=``, a
    wildcard or a pre-release is not compared, because a check that
    guessed would be worse than the TypeError it replaces. A pack that
    declares nothing loads as it always did.
    """
    dist = getattr(entries[0], "dist", None)
    requires = getattr(dist, "requires", None) or []
    try:
        have = _release(metadata.version("yantra"))
    except metadata.PackageNotFoundError:
        return
    if have is None:
        return
    for line in requires:
        requirement, _, marker = str(line).partition(";")
        match = _REQUIREMENT.match(requirement)
        if marker.strip() or match is None:
            continue
        if re.sub(r"[-_.]+", "-", match.group(1)).lower() != "yantra":
            continue
        for clause in filter(str.strip, match.group(2).split(",")):
            parsed = _CLAUSE.match(clause)
            want = _release(parsed.group(2)) if parsed else None
            if want is None:
                continue
            if _satisfies(have, parsed.group(1), want) is False:
                raise ConfigError(
                    f"tool pack {name} requires "
                    f"yantra{match.group(2).strip()}, and this is yantra "
                    f"{metadata.version('yantra')}; upgrade yantra, or "
                    f"install a release of {name} built for this one")


def register_tool_packs(registry: ToolRegistry, names: Iterable[str], *,
                        prefixes: Mapping[str, str] | None = None
                        ) -> list[str]:
    """Load the named installed packs into ``registry``; return what was
    admitted.

    NAMED, NEVER AMBIENT. Every installed distribution publishing the
    group could be loaded automatically, and that is exactly the thing
    this refuses: an agent's tool list would then depend on what happens
    to be in the virtualenv, so the same package would have different
    tools on two machines and the manifest would not say why. A pack is
    named in ``agent.toml`` (or on the command line) in one line, and
    that line is the record.

    A NAME THAT IS NOT INSTALLED IS AN ERROR, for tools/ directories'
    reason: an author who wrote the line believes they shipped the tool,
    and an agent quietly missing it is the failure this whole area
    exists to prevent. So is a pinned release that is not the one
    installed, and a pack whose stated Yantra requirement this build
    does not meet -- both checked BEFORE the pack is imported, so a pack
    that would not work never gets to run its import-time code.

    ``prefixes`` renames a pack's tools (``{"tide-pack": "tide"}`` makes
    ``search`` into ``tide_search``) on the instance, the way the eval
    recorder wraps a tool without touching its class. The DESCRIPTION IS
    NOT REWRITTEN: prose that says "use search" now names a tool the
    model cannot see, and fixing somebody else's prose by pattern would be
    worse than the stale word.

    Admission still applies afterwards -- these go through ``register``
    like everything else, so a pack a package's own ``tools.allow`` does
    not name is turned away and listed in ``refused_names()``. Allow,
    deny and every eval assertion name the PREFIXED tool: the name the
    model sees is the only name there is.
    """
    prefixes = dict(prefixes or {})
    available = entry_point_packs()
    admitted: list[str] = []
    for text in names:
        name, pinned = parse_pack(text)
        entries = available.get(name)
        if entries is None:
            installed = ", ".join(sorted(available)) or "none"
            raise ConfigError(
                f"tool pack {name!r} is not installed: nothing publishing "
                f"{ENTRY_POINT_GROUP!r} is called that (installed: "
                f"{installed}). Install it into the same environment as "
                f"the agent, or drop the line that asks for it")
        if pinned is not None:
            have = _installed_version(name, entries)
            if have != pinned:
                raise ConfigError(
                    f"tool pack {name}: this agent names release {pinned} "
                    f"and {'release ' + have if have else 'an unknown release'}"
                    f" is installed. Install the one it was written against "
                    f"(pip install {name}=={pinned}), or change the line if "
                    f"the agent has been checked against {have or 'it'}")
        _check_base(name, entries)
        prefix = prefixes.get(name)
        if prefix is not None and not PACK_PREFIX.match(prefix):
            raise ConfigError(
                f"tool pack {name}: prefix {prefix!r} must be lower-case "
                f"letters and digits, starting with a letter (joined to "
                f"each tool's name with '_')")
        for entry in entries:
            for tool in _tools_from_entry_point(entry):
                if prefix is not None:
                    tool.name = f"{prefix}_{tool.name}"
                try:
                    registry.register(tool)
                except ValueError:
                    raise ConfigError(
                        f"tool pack {name}: tool {tool.name!r} is already "
                        f"registered -- a pack may not shadow another tool "
                        f"(exclude the original with tools.deny, or give "
                        f"one pack a prefix in [tools.prefix])") from None
                if tool.name in registry:
                    admitted.append(tool.name)
    return admitted


def package_tool_names(registry: ToolRegistry) -> list[str]:
    """Which of a registry's tools came from a package directory.

    A host prints this: loading them ran somebody's Python, and the
    operator should be able to see that it happened.
    """
    # ``_inner`` unwraps a recording proxy (evals.py): a package tool is
    # still a package tool when something is watching it execute.
    return sorted(tool.name for tool in registry
                  if type(getattr(tool, "_inner", tool)).__module__
                  .startswith(f"{NAMESPACE}."))
