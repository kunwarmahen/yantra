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
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
import types
from collections.abc import Iterable, Iterator
from pathlib import Path

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
