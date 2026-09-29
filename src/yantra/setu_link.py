"""The person's connected accounts, found at startup -- through Setu.

Setu (a separate project) keeps a person's sign-ins: Gmail, and whatever
connectors follow. Any MCP client can use a Setu connection by hand --
paste ``setu run gmail:personal`` into its config -- and Yantra could
have stopped there. What it could not do by hand is KNOW what that
server is: which account, at which level, which of its tools only read
and which reach out into the world. Setu knows all of that. This module
asks.

TWO ROADS TO ONE ANSWER. Setu's ``report()`` is the answer; ``setu status
--json`` prints it. When Setu is installed in this Python environment the
function is called directly; otherwise the command is run and its JSON
read. Neither project imports the other's internals -- the JSON's
``format`` is the whole contract, and an unknown format is refused
rather than guessed at. The command road is the default one in practice:
Setu usually lives in its own environment.

FOUND, ANNOUNCED, AND YOURS TO SWITCH OFF. ``YANTRA_SETU`` or the flags
choose:

    auto  (default) use Setu if it can be found; say nothing if it cannot
    on    use it, and say so loudly if it cannot be found (--setu)
    off   never look (--no-setu)
    PATH  the setu program to run, which also means "on" (--setu PATH)

Whatever is found is announced in one line at startup, so a connection
never becomes a set of tools the person did not know the agent had.

EACH CONNECTION IS AN MCP SERVER, named as ``setu mcp-config`` names it
(``gmail-personal``), started as ``setu run <ref>``. A server of the same
name already connected -- from ``--mcp-config`` or the page's remembered
list -- wins; if it is the same command it is simply recognised, and the
rules below still apply to it.

THE MANIFEST DECIDES, NOT THE SERVER. An MCP server's ``readOnlyHint`` is
its own claim about itself. A Setu connector's manifest classes every
tool -- read, write, spend -- and that classification, which the person
could read before signing in, is what counts:

* ``read``   may run without asking (as any read-only tool may);
* ``write``  is always asked about, whatever the server's hint says;
* ``spend``  is asked about every time, and no blanket approval -- not
  ``--yolo``, not the page's yolo mode -- covers it (``Tool.always_ask``);
* a tool the manifest does not list is not registered at all: nobody
  agreed to it.

THE MODEL IS TOLD. A ``connections`` prompt layer says which accounts are
connected and at what level, and which installed connectors are not
connected -- so "anything in my Outlook?" gets "Outlook isn't connected;
run `setu connect outlook`" rather than a guess.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from yantra.mcp import MCPServerConfig

ENV = "YANTRA_SETU"
FORMAT = "setu.status.v1"
MODES = ("auto", "on", "off")
VERB_CLASSES = ("read", "write", "spend")
#: How long ``setu status --json`` may take before startup gives up on it.
STATUS_TIMEOUT = 15.0


class SetuLinkError(Exception):
    """Setu was asked for and could not be used."""


@dataclass(slots=True)
class Link:
    """What Setu said, and which road it said it on."""

    data: dict[str, Any]
    road: str                                  # "import" | the command run
    problems: list[str] = field(default_factory=list)

    @property
    def connections(self) -> list[dict[str, Any]]:
        return [row for row in self.data.get("connections") or [] if row.get("installed", True)]

    @property
    def connectors(self) -> dict[str, dict[str, Any]]:
        return {c["id"]: c for c in self.data.get("connectors") or [] if "id" in c}

    def verbs(self, connector: str) -> dict[str, str]:
        return dict((self.connectors.get(connector) or {}).get("verbs") or {})


def resolve_mode(flag: str | None, env: str | None = None) -> tuple[str, str | None]:
    """(mode, setu path). A flag outranks the environment; a path means on.

    ``flag`` is what the command line said: None (nothing), "off"
    (--no-setu), "on" (bare --setu) or a path (--setu PATH).
    """
    raw = flag if flag is not None else (env if env is not None else os.environ.get(ENV, ""))
    raw = (raw or "").strip()
    if not raw or raw.lower() == "auto":
        return "auto", None
    if raw.lower() in ("on", "off"):
        return raw.lower(), None
    return "on", os.path.expanduser(raw)


def _check(data: Any, road: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SetuLinkError(f"setu ({road}) answered something that is not an object")
    if data.get("format") != FORMAT:
        raise SetuLinkError(f"setu ({road}) speaks {data.get('format')!r}; this Yantra "
                            f"understands {FORMAT!r} -- update one of them")
    return data


def _by_command(path: str) -> Link:
    try:
        done = subprocess.run([path, "status", "--json"], capture_output=True, text=True,
                              timeout=STATUS_TIMEOUT)
    except FileNotFoundError:
        raise SetuLinkError(f"no setu program at {path}") from None
    except subprocess.TimeoutExpired:
        raise SetuLinkError(f"`{path} status --json` took longer than "
                            f"{int(STATUS_TIMEOUT)}s") from None
    if done.returncode != 0:
        why = (done.stderr or done.stdout).strip().splitlines()
        raise SetuLinkError(f"`{path} status --json` failed: {why[-1] if why else done.returncode}")
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError:
        raise SetuLinkError(f"`{path} status --json` did not print JSON") from None
    data = _check(data, path)
    return Link(data=data, road=path, problems=list(data.get("problems") or []))


def _by_import() -> Link | None:
    try:
        from setu.status import report  # type: ignore[import-not-found]
    except ImportError:
        return None
    data = _check(report(), "import")
    return Link(data=data, road="import", problems=list(data.get("problems") or []))


def load(mode: str, path: str | None = None) -> Link | None:
    """Ask Setu, by whichever road is open. None when it is off, or when
    it is ``auto`` and simply not there; an error when it was asked for."""
    if mode == "off":
        return None
    if path:
        return _by_command(path)
    try:
        found = _by_import()
    except SetuLinkError:
        if mode == "on":
            raise
        found = None
    if found is not None:
        return found
    on_path = shutil.which("setu")
    if on_path:
        try:
            return _by_command(on_path)
        except SetuLinkError:
            if mode == "on":
                raise
            return None
    if mode == "on":
        raise SetuLinkError(f"Setu was asked for but not found: install it here, put `setu` on "
                            f"PATH, or name it with {ENV}=/path/to/setu")
    return None


def mcp_configs(link: Link) -> list[tuple[dict[str, Any], MCPServerConfig]]:
    """One MCP server per connection, as ``setu mcp-config`` would write it."""
    out = []
    for row in link.connections:
        mcp = row.get("mcp") or {}
        if not mcp.get("name") or not mcp.get("command"):
            continue
        out.append((row, MCPServerConfig(name=mcp["name"], command=mcp["command"],
                                         args=list(mcp.get("args") or []))))
    return out


def same_server(a: MCPServerConfig, b: MCPServerConfig) -> bool:
    return (a.command, list(a.args or [])) == (b.command, list(b.args or []))


def apply_verbs(registry: Any, server: str, verbs: dict[str, str]) -> tuple[list[str], list[str]]:
    """Classify one server's registered tools by the manifest's verbs.

    Returns (tool names kept, tool names removed because the manifest
    does not list them)."""
    prefix = f"mcp__{server}__"
    kept, removed = [], []
    for name in list(registry.names()):
        if not name.startswith(prefix):
            continue
        klass = verbs.get(name[len(prefix):])
        if klass not in VERB_CLASSES:
            registry.unregister(name)
            removed.append(name)
            continue
        tool = registry.get(name)
        tool.read_only = klass == "read"
        tool.always_ask = klass == "spend"
        kept.append(name)
    return kept, removed


def prompt_text(link: Link) -> str | None:
    """The ``connections`` layer: what is connected, and what could be."""
    rows = link.connections
    connectors = link.connectors
    idle = [c for c in connectors.values() if not c.get("connected")]
    if not rows and not idle:
        return None
    lines = ["# Connected accounts (through Setu)",
             "The person has signed in to these; their tools are prefixed "
             "`mcp__<name>__`. You never see or need a password or key."]
    for row in rows:
        name = (connectors.get(row.get("connector", "")) or {}).get("name", row.get("connector"))
        who = f" as {row['email']}" if row.get("email") else ""
        level = row.get("level_label") or row.get("level")
        lines.append(f"- {name} `{row['mcp']['name']}`{who}: {level}")
    if idle:
        lines.append("Installed but not connected (the person can connect one with "
                     "`setu connect <id>`; you cannot):")
        lines += [f"- {c.get('name', c['id'])} (`{c['id']}`)" for c in idle]
    lines.append("If the person asks about an account that is not listed here, say it is "
                 "not connected rather than guessing.")
    return "\n".join(lines)


def announce(link: Link, connected: dict[str, int]) -> str:
    """The one startup line: which connections, how many tools each."""
    if not connected:
        return f"setu: no connections yet ({link.road})"
    parts = [f"{name} ({count} tool(s))" for name, count in connected.items()]
    return f"setu: {', '.join(parts)} -- via {link.road}"
