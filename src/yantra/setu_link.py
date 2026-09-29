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

LIVE, NOT ONLY AT STARTUP. ``Setu`` is the session's handle on all of
this (``agent.setu``). ``sync`` makes the MCP servers match what Setu
reports -- a new connection gets its server and tools, a gone one loses
them -- and rewrites the prompt layer; startup is its first call, and
the web page's Connections panel calls it again after a sign-in or a
disconnect ([notes/99](../../notes/99-the-connections-page.md)). Signing
in stays Setu's: the page runs ``setu connect --json`` and relays the
address; Yantra never sees a key, a code, or a client secret.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
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


# ---- the live handle --------------------------------------------------------


@dataclass(slots=True)
class Synced:
    """What one ``sync`` did: servers and their tool counts, and notes."""

    connected: dict[str, int] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Setu:
    """One session's link to Setu: how it was found, what it said last,
    and which MCP servers it started (the only ones ``sync`` may stop)."""

    mode: str
    path: str | None = None
    link: Link | None = None
    #: Why the last look failed, when it did -- the page says it.
    error: str = ""
    servers: set[str] = field(default_factory=set)

    @property
    def program(self) -> str | None:
        """The setu program to run for connect and disconnect: the one
        Setu reports for itself, else the one named, else PATH's."""
        reported = (self.link.data.get("command") if self.link else None) or None
        return reported or self.path or shutil.which("setu")

    def refresh(self) -> Link | None:
        """Ask Setu again. A page asking is the person asking, so a Setu
        that cannot be found is an error here even in ``auto``."""
        try:
            self.link = load("on" if self.mode == "auto" else self.mode, self.path)
            self.error = ""
        except SetuLinkError as exc:
            self.link, self.error = None, str(exc)
        return self.link

    def sync(self, manager: Any, agent: Any) -> Synced:
        """Make the MCP servers match the last report, then rewrite the
        ``connections`` prompt layer. Never raises for one bad server."""
        from yantra.mcp import MCPError
        from yantra.prompt import attach_prompt

        done = Synced()
        link = self.link
        wanted = mcp_configs(link) if link is not None else []
        names = {cfg.name for _, cfg in wanted}
        for name in sorted(self.servers - names):
            try:
                manager.disconnect(name)
            except MCPError:
                pass
            self.servers.discard(name)
            done.dropped.append(name)
        for row, cfg in wanted:
            existing = manager.sessions.get(cfg.name)
            if existing is not None and not same_server(existing.config, cfg):
                done.notes.append(f"'{cfg.name}' is already an mcp server with a different "
                                  f"command; leaving it, and {row['ref']} unconnected")
                continue
            if existing is None:
                try:
                    manager.connect(cfg)
                except MCPError as exc:
                    done.notes.append(f"{row['ref']} unavailable: {exc}")
                    continue
                self.servers.add(cfg.name)
            elif cfg.name not in self.servers and same_server(existing.config, cfg):
                self.servers.add(cfg.name)   # configured by hand as this very command
            kept, removed = apply_verbs(agent.registry, cfg.name, link.verbs(row["connector"]))
            if removed and cfg.name in getattr(manager, "tool_names", {}):
                # the manager's count is what the page shows: tools the
                # manifest refused are not the agent's, so not counted
                manager.tool_names[cfg.name] = [
                    n for n in manager.tool_names[cfg.name] if n not in removed]
            if removed:
                done.notes.append(f"{cfg.name} offered tool(s) its manifest does not list, "
                                  f"not registered: {', '.join(removed)}")
            done.connected[cfg.name] = len(kept)
        if link is not None:
            done.notes += [p for p in link.problems]
        prompt = attach_prompt(agent)
        prompt.set("connections", prompt_text(link) if link is not None else None)
        prompt.apply()
        return done

    def describe(self, manager: Any = None) -> dict[str, Any]:
        """The Connections panel's data. Built from Setu's own report,
        which carries no secret by contract, plus what this session runs."""
        link = self.link
        live = {s["name"]: s for s in (manager.servers() if manager is not None else [])}
        rows = []
        for row in (link.connections if link is not None else []):
            name = (row.get("mcp") or {}).get("name", "")
            server = live.get(name)
            rows.append({**{k: row.get(k) for k in (
                "ref", "connector", "account", "email", "level", "level_label",
                "last_used")}, "server": name,
                "tools": server["tools"] if server else 0,
                "running": bool(server and server["healthy"])})
        return {
            "mode": self.mode,
            "found": link is not None,
            "road": link.road if link is not None else None,
            "error": self.error,
            "version": link.data.get("version") if link is not None else None,
            "connections": rows,
            "connectors": list(link.connectors.values()) if link is not None else [],
            "setup": dict((link.data.get("setup") or {}) if link is not None else {}),
            "problems": list(link.problems) if link is not None else [],
        }


# ---- signing in and out, for a page ---------------------------------------------

#: An account name as a page may pass it: it becomes one argv word, so it
#: may not look like an option; Setu has its own rules on top.
ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")

#: How long ``setu disconnect`` / ``setu config`` may take (a revoke is
#: one request to Google).
COMMAND_TIMEOUT = 30.0


def run_setu(program: str, *args: str, timeout: float = COMMAND_TIMEOUT) -> tuple[bool, str]:
    """One short setu command -> (worked, what it said)."""
    try:
        done = subprocess.run([program, *args], capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        return False, f"no setu program at {program}"
    except subprocess.TimeoutExpired:
        return False, f"`setu {args[0]}` took longer than {int(timeout)}s"
    said = (done.stdout + done.stderr).strip()
    return done.returncode == 0, said


class SignIn:
    """One ``setu connect --json`` in progress. Events arrive on a thread
    as Setu prints them -- started, url, connected, error -- and
    ``on_event`` gets each dict, then a final ``{"event": "done"}``.

    The address in the ``url`` event is Google's sign-in page; its
    redirect comes back to a port Setu opened on THIS computer, which is
    why a page may start one only when it is open on this computer too.
    """

    def __init__(self, program: str, connector: str, account: str, level: str,
                 on_event: Any) -> None:
        if not ACCOUNT_RE.match(account):
            raise SetuLinkError(f"account name {account!r}: letters, digits, '.', '_' "
                                f"and '-', starting with a letter or digit")
        self.ref = f"{connector}:{account}"
        self.on_event = on_event
        self.last: dict[str, Any] = {}
        self.cancelled = False
        self.process = subprocess.Popen(
            [program, "connect", connector, "--as", account, "--level", level, "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.thread = threading.Thread(
            target=self._read, daemon=True, name="yantra-setu-signin")
        self.thread.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue            # not a line of the contract; never guessed at
            if isinstance(event, dict) and event.get("event"):
                self.last = event
                self.on_event(event)
        self.process.wait()
        why = (self.process.stderr.read() if self.process.stderr else "").strip()
        for pipe in (self.process.stdout, self.process.stderr):
            if pipe is not None:
                pipe.close()
        if self.cancelled:
            self.last = {"event": "cancelled"}
            self.on_event(self.last)
        elif self.last.get("event") not in ("connected", "error"):
            self.last = {"event": "error",
                         "message": why.splitlines()[-1] if why else "setu stopped early"}
            self.on_event(self.last)
        self.on_event({"event": "done", "ref": self.ref})

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def cancel(self) -> None:
        """Stop waiting for the person. Setu's port closes with it, and
        nothing was saved -- a sign-in only saves on its last step."""
        if self.running:
            self.cancelled = True
            self.process.terminate()
