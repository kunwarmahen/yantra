"""Agent packages: an agent you can name, share, and review.

A package is a DIRECTORY, and that is the whole idea:

    researcher/
    |-- agent.toml      identity, model, tools, skills, servers, policy
    |-- prompt.md       the system prompt
    |-- tools/          Tool subclasses this agent brings with it (notes/32)
    |-- skills/         procedures this agent knows (notes/30)
    `-- evals/          the acceptance gate: cases.toml (eval_suite.py)

The confusion to clear up first, because everybody has it once: a SKILL
is a procedure an agent may load; a PACKAGE is the whole agent. Packages
contain skills. Never the reverse.

TOML, because ``tomllib`` has been in the standard library since 3.11.
A package format that dragged in a YAML parser would be the first crack
in a project whose runtime dependencies are httpx and rich.

EVERY FIELD IS OPTIONAL. A package whose ``agent.toml`` says only
``[agent] name = "x"`` runs. That is not minimalism for its own sake --
it is what keeps a package portable. A package that hardcoded a cloud
model would be unrunnable for anyone pointing at their own hardware, so
the resolution order stays

    command line  >  agent.toml  >  environment  >  built-in default

and someone can run your Anthropic-authored package with
``--provider ollama`` and get their own model, without editing your file.

UNKNOWN KEYS ARE ERRORS. A typo'd key that is quietly ignored is how a
package comes to "configure" something that never happens -- and the
worst version of that is a misspelled ``deny`` leaving ``bash`` armed in
an agent whose author believes they switched it off. Every table and key
is checked against the known set, and the error names the file.

CONVENTION WHERE IT IS OBVIOUS. ``prompt.md``, ``skills/`` and
``tools/`` are picked up when they exist without being declared, so the
common package needs three lines of TOML and the keys exist for when you
want somewhere else.

READING A MANIFEST NEVER RUNS CODE. ``tools/`` is named here and loaded
in ``yantra/tools/discover.py``, at build time. Anything that wants to
inspect a package -- a listing, a registry, a UI -- can parse it without
executing the author's Python.
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path
from typing import Any

from yantra.errors import ConfigError
from yantra.mcp import MCPServerConfig
from yantra.spec import AgentSpec

#: The file that makes a directory an agent package.
MANIFEST = "agent.toml"

#: Conventional locations, used when the manifest does not say otherwise.
DEFAULT_PROMPT = "prompt.md"
DEFAULT_SKILLS = "skills"
DEFAULT_TOOLS = "tools"

#: Every table and the keys it may hold. The check is exhaustive on
#: purpose: see the module docstring on why silence is the enemy here.
SCHEMA: dict[str, frozenset[str]] = {
    "agent": frozenset({"name", "description", "version", "prompt"}),
    "model": frozenset({"provider", "model", "max_tokens", "max_iterations",
                        "context_window", "cache"}),
    "tools": frozenset({"allow", "deny", "per_turn", "dirs"}),
    "skills": frozenset({"dirs", "disabled", "enabled"}),
    "mcp": frozenset({"name", "command", "args", "env", "url", "headers"}),
    "budget": frozenset({"max_usd_per_turn"}),
    "permissions": frozenset({"mode"}),
    "env": frozenset({"context"}),
}


def _fail(path: Path, message: str) -> None:
    raise ConfigError(f"{path}: {message}")


def _table(data: dict[str, Any], name: str, path: Path) -> dict[str, Any]:
    """One table, checked for unknown keys and for actually being a table."""
    value = data.get(name, {})
    if not isinstance(value, dict):
        _fail(path, f"[{name}] must be a table")
    unknown = sorted(set(value) - SCHEMA[name])
    if unknown:
        known = ", ".join(sorted(SCHEMA[name]))
        _fail(path, f"unknown key(s) in [{name}]: {', '.join(unknown)} "
                    f"(known: {known})")
    return value


def _str(table: dict[str, Any], key: str, path: Path,
         where: str) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(path, f"{where}.{key} must be a string")
    return value


def _int(table: dict[str, Any], key: str, path: Path,
         where: str) -> int | None:
    value = table.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, f"{where}.{key} must be an integer")
    return value


def _bool(table: dict[str, Any], key: str, path: Path,
          where: str) -> bool | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        _fail(path, f"{where}.{key} must be true or false")
    return value


def _float(table: dict[str, Any], key: str, path: Path,
           where: str) -> float | None:
    """A money field. Ints are accepted and widened -- TOML reads ``1`` as
    an int, and somebody writing a one-dollar ceiling means one dollar."""
    value = table.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, f"{where}.{key} must be a number (US dollars)")
    return float(value)


def _str_list(table: dict[str, Any], key: str, path: Path,
              where: str) -> tuple[str, ...] | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        _fail(path, f"{where}.{key} must be a list of strings")
    return tuple(value)


def _mcp_servers(data: dict[str, Any], path: Path) -> tuple[MCPServerConfig, ...]:
    """``[[mcp]]`` entries -> configs, with the transport rule enforced here.

    Checking it in the loader rather than at connect time means a bad
    manifest fails before a model is ever reached, and the message can
    name the file the author has open.
    """
    raw = data.get("mcp", [])
    if isinstance(raw, dict):  # a single [mcp] table instead of [[mcp]]
        raw = [raw]
    if not isinstance(raw, list):
        _fail(path, "[[mcp]] must be a list of tables")

    servers = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            _fail(path, f"[[mcp]] entry {index} must be a table")
        unknown = sorted(set(entry) - SCHEMA["mcp"])
        if unknown:
            _fail(path, f"unknown key(s) in [[mcp]] entry {index}: "
                        f"{', '.join(unknown)}")
        name = _str(entry, "name", path, f"mcp[{index}]")
        if not name:
            _fail(path, f"[[mcp]] entry {index} needs a name")
        command = _str(entry, "command", path, f"mcp[{name}]")
        url = _str(entry, "url", path, f"mcp[{name}]")
        if bool(command) == bool(url):
            _fail(path, f"mcp '{name}' needs exactly one of command (stdio) "
                        f"or url (http)")
        args = _str_list(entry, "args", path, f"mcp[{name}]") or ()
        env = entry.get("env")
        headers = entry.get("headers")
        for label, mapping in (("env", env), ("headers", headers)):
            if mapping is not None and (
                not isinstance(mapping, dict)
                or any(not isinstance(v, str) for v in mapping.values())
            ):
                _fail(path, f"mcp '{name}': {label} must be a table of strings")
        servers.append(MCPServerConfig(
            name=name, command=command, args=list(args),
            env=dict(env) if env else None,
            url=url, headers=dict(headers) if headers else None,
        ))
    return tuple(servers)


def find_manifest(where: Path) -> Path | None:
    """The manifest at or inside ``where``, or None if there is none.

    Accepts the directory or the file itself, so both
    ``--agent ./researcher`` and ``--agent ./researcher/agent.toml`` work.
    """
    where = Path(where).expanduser()
    if where.is_file():
        return where
    candidate = where / MANIFEST
    return candidate if candidate.is_file() else None


def load_package(where: Path) -> AgentSpec:
    """Read an agent package and return the spec it declares.

    Does no model work and touches no network: a manifest that cannot
    produce a working agent should say so before a key is ever needed.
    """
    manifest = find_manifest(where)
    if manifest is None:
        target = Path(where).expanduser()
        raise ConfigError(
            f"no {MANIFEST} in {target}" if target.is_dir()
            else f"not an agent package: {target}"
        )
    root = manifest.parent.resolve()

    try:
        with manifest.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{manifest}: invalid TOML: {exc}") from None
    except OSError as exc:
        raise ConfigError(f"{manifest}: cannot read: {exc}") from None

    unknown_tables = sorted(set(data) - set(SCHEMA))
    if unknown_tables:
        _fail(manifest, f"unknown table(s): {', '.join(unknown_tables)} "
                        f"(known: {', '.join(sorted(SCHEMA))})")

    agent = _table(data, "agent", manifest)
    model = _table(data, "model", manifest)
    tools = _table(data, "tools", manifest)
    skills = _table(data, "skills", manifest)
    budget = _table(data, "budget", manifest)
    permissions = _table(data, "permissions", manifest)
    env = _table(data, "env", manifest)

    # The prompt: a declared path is required to exist (you asked for that
    # file); the conventional prompt.md is used only if it happens to be
    # there. Same rule as skills/ below.
    declared_prompt = _str(agent, "prompt", manifest, "agent")
    prompt_path = root / (declared_prompt or DEFAULT_PROMPT)
    prompt_text: str | None = None
    if prompt_path.is_file():
        try:
            prompt_text = prompt_path.read_text(encoding="utf-8").strip() or None
        except OSError as exc:
            _fail(manifest, f"cannot read prompt {prompt_path}: {exc}")
    elif declared_prompt:
        _fail(manifest, f"agent.prompt points at {prompt_path}, which is "
                        f"not a file")

    # tools/ is picked up by convention like skills/, and for the same
    # reason: the common package should not have to declare where its own
    # things live. NOTHING IS IMPORTED HERE. Reading a manifest is a pure
    # read -- the code in tools/ runs when an agent is actually built from
    # this spec, which is the moment a human asked for it (tools/discover.py).
    declared_tools = _str_list(tools, "dirs", manifest, "tools")
    if declared_tools is None:
        conventional = root / DEFAULT_TOOLS
        tool_dirs = (conventional,) if conventional.is_dir() else ()
    else:
        tool_dirs = tuple((root / d).resolve() for d in declared_tools)
        for directory in tool_dirs:
            if not directory.is_dir():
                _fail(manifest, f"tools.dirs entry {directory} is not a "
                                f"directory")

    declared_dirs = _str_list(skills, "dirs", manifest, "skills")
    if declared_dirs is None:
        conventional = root / DEFAULT_SKILLS
        skill_dirs = (conventional,) if conventional.is_dir() else ()
    else:
        skill_dirs = tuple((root / d).resolve() for d in declared_dirs)
        for directory in skill_dirs:
            if not directory.is_dir():
                _fail(manifest, f"skills.dirs entry {directory} is not a "
                                f"directory")
    skills_on = _bool(skills, "enabled", manifest, "skills")
    if skills_on is None and (skill_dirs or skills):
        skills_on = True

    spec = AgentSpec(
        name=_str(agent, "name", manifest, "agent") or root.name,
        description=_str(agent, "description", manifest, "agent"),
        version=_str(agent, "version", manifest, "agent"),
        provider=_str(model, "provider", manifest, "model"),
        model=_str(model, "model", manifest, "model"),
        max_tokens=_int(model, "max_tokens", manifest, "model"),
        max_iterations=_int(model, "max_iterations", manifest, "model"),
        context_window=_int(model, "context_window", manifest, "model"),
        cache=_bool(model, "cache", manifest, "model"),
        prompt=prompt_text,
        tool_allow=_str_list(tools, "allow", manifest, "tools"),
        tool_deny=_str_list(tools, "deny", manifest, "tools") or (),
        tools_per_turn=_int(tools, "per_turn", manifest, "tools"),
        tool_dirs=tool_dirs,
        skills=skills_on,
        skill_dirs=skill_dirs,
        skills_disabled=_str_list(skills, "disabled", manifest, "skills") or (),
        mcp=_mcp_servers(data, manifest),
        max_usd_per_turn=_float(budget, "max_usd_per_turn", manifest, "budget"),
        permissions_mode=_str(permissions, "mode", manifest, "permissions"),
        env_context=_str(env, "context", manifest, "env"),
        root=root,
    )
    # A package that ships skills and then excludes the tool that loads
    # them is internally incoherent, and the manifest is the only place
    # that can say so usefully -- by the time an agent exists, all anyone
    # sees is a roster of skills the model cannot reach.
    if skills_on and spec.tool_allow is not None:
        if not any(fnmatch.fnmatch("load_skill", pat) for pat in spec.tool_allow):
            _fail(manifest, "this package declares skills but tools.allow "
                            "excludes load_skill, so nothing could load "
                            "them; add \"load_skill\" to tools.allow (or "
                            "drop the skills)")

    try:
        spec.validate()
    except ConfigError as exc:
        raise ConfigError(f"{manifest}: {exc}") from None
    return spec
