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

A RECIPE SAYS WHICH ACCOUNT IT NEEDS. A learned skill's ``needs:`` may
name ``setu:gmail``; when it loads, the model is told which server's
tools that is, or that it is not connected and the person has to connect
it -- rather than a recipe followed four steps into a missing account.
The skill's write-up is handed the connector ids to name, and the save
question says whether each is connected (``resolve_needs``).

SEVERAL ACCOUNTS, ONE SET OF TOOLS. Each connection still runs as its
own server, so one process only ever holds one account's token. When a
connector has two or more, their tools are merged into one set named
for the connector (``mcp__gmail__search_threads``) with an ``account``
argument listing them. A read may leave it out, or say ``all``, and
gets every account's answer, labelled; a write or a spend has no
default, because sending from the wrong address is a real mistake, and
its approval says which account (``AccountTool``). One account: nothing
is merged and nothing changes.

A PACKAGE GETS WHAT IT ASKED FOR, AND WHAT YOU ALLOWED. An agent
somebody else wrote sees none of your accounts unless its agent.toml
says ``[connections] needs = ["gmail:read"]``, and you said yes once to
that package at that level (``load_approved``). The level is a ceiling:
``read`` keeps only the manifest's read tools, ``write`` adds writes,
``spend`` everything. Your own sessions see every connection, as
before. A need whose account is not connected yet waits on the session
(``Setu.needs``), and the first look that finds it connected asks then
(``Setu.offer``) -- sign in on the page mid-session and the question
follows, rather than the next launch.

WITHDRAWN MEANS NOT STARTED. When Setu keeps a signed catalog and it
says the installed version of a connector was withdrawn, ``sync`` starts
none of that connector's connections and says why. The connection
itself stays in Setu, so an update brings it back.

A SITE WITH NO API IS A PROFILE, NOT A SERVER. A connection on Setu's
browser road (Amazon, X) has no ``mcp``: Setu reports the browser
profile the person signed in to, and the manifest's rules for the site.
``sync`` gives it a set of tools of its own -- ``amazon_open``,
``amazon_follow``... -- on a browser session that keeps to those rules
(tools/site.py). The level decides which: Read only gets the verbs that
cannot press anything; the write level adds click and fill, asked about
every time. Nothing on that road spends: those pages go to the person.

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

import copy
import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yantra.errors import ToolError
from yantra.mcp import MCPServerConfig
from yantra.tools.base import Tool, ToolContext

ENV = "YANTRA_SETU"
FORMAT = "setu.status.v1"
MODES = ("auto", "on", "off")
VERB_CLASSES = ("read", "write", "spend")
#: How far a level reaches: a package allowed to write may also read.
CLASS_RANK = {klass: n for n, klass in enumerate(VERB_CLASSES)}
#: What a level lets a package do, in the words its question uses.
CLASS_WORDS = {"read": "read", "write": "read and change things in",
               "spend": "read, change and spend money through"}
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


def apply_verbs(registry: Any, server: str, verbs: dict[str, str],
                ceiling: str | None = None) -> tuple[list[str], list[str]]:
    """Classify one server's registered tools by the manifest's verbs.

    Returns (tool names kept, tool names removed because the manifest
    does not list them). A ``ceiling`` -- the level a package was allowed
    -- also unregisters every tool above it, without counting it as
    removed: the manifest lists it, the package just did not ask.

    A manifest's ``"*"`` classes every tool it does not name -- a bridge
    to a server whose tool names are its own (Home Assistant's MCP
    server). Setu allows only write or spend there, and so does this: a
    ``"*"`` of read is ignored, and unnamed tools are dropped as before."""
    prefix = f"mcp__{server}__"
    kept, removed = [], []
    default = verbs.get("*") if verbs.get("*") in ("write", "spend") else None
    for name in list(registry.names()):
        if not name.startswith(prefix):
            continue
        klass = verbs.get(name[len(prefix):]) or default
        if klass not in VERB_CLASSES:
            registry.unregister(name)
            removed.append(name)
            continue
        if ceiling is not None and CLASS_RANK[klass] > CLASS_RANK[ceiling]:
            registry.unregister(name)
            continue
        tool = registry.get(name)
        tool.read_only = klass == "read"
        tool.always_ask = klass == "spend"
        kept.append(name)
    return kept, removed


def account_of(row: dict[str, Any]) -> str:
    """A connection's account label: ``personal`` in ``gmail:personal``."""
    return row.get("account") or str(row.get("ref", "")).partition(":")[2] or "default"


def prompt_text(link: Link, allow: dict[str, str] | None = None,
                merged: frozenset[str] = frozenset(),
                sites: dict[str, str] | None = None) -> str | None:
    """The ``connections`` layer: what is connected, and what could be.
    ``allow`` narrows it to what a package may use; ``merged`` names the
    connectors whose accounts share one set of tools; ``sites`` maps a
    browser-road connection to its tools' prefix."""
    sites = sites or {}
    connectors = link.connectors
    rows = [r for r in link.connections if allow is None or r.get("connector") in allow]
    idle = [c for c in connectors.values() if not c.get("connected")
            and (allow is None or c["id"] in allow)]
    if not rows and not idle:
        return None
    lines = ["# Connected accounts (through Setu)",
             "The person has signed in to these; their tools are prefixed "
             "`mcp__<name>__`. You never see or need a password or key."]
    done: set[str] = set()
    for row in rows:
        cid = row.get("connector", "")
        name = (connectors.get(cid) or {}).get("name", cid)
        if cid in merged:
            if cid in done:
                continue
            done.add(cid)
            accounts = [r for r in rows if r.get("connector") == cid]
            each = "; ".join(
                f"`{account_of(r)}`" + (f" as {r['email']}" if r.get("email") else "")
                + f": {r.get('level_label') or r.get('level')}" for r in accounts)
            lines.append(f"- {name}, {len(accounts)} accounts ({each}). Its tools are "
                         f"`mcp__{cid}__*` and take `account`: name it for anything that "
                         f"sends or changes; a read may leave it out to use every account.")
            continue
        level = row.get("level_label") or row.get("level")
        if row.get("browser"):
            prefix = sites.get(row.get("ref", ""))
            if prefix is None:
                continue                   # not started (withdrawn, refused)
            lines.append(f"- {name} through the person's own signed-in browser: tools "
                         f"`{prefix}_*` (not `mcp__`): {level}. Buying, paying and what "
                         f"cannot be undone are never yours: hand that page over with "
                         f"`{prefix}_handoff`.")
            guide = ((connectors.get(cid) or {}).get("browser") or {}).get("guide") or ""
            home = ((row.get("browser") or {}).get("home") or "").rstrip("/")
            lines += [f"  {line.strip().replace('{home}', home)}"
                      for line in guide.splitlines() if line.strip()]
            continue
        who = f" as {row['email']}" if row.get("email") else ""
        lines.append(f"- {name} `{row['mcp']['name']}`{who}: {level}")
    if allow is not None:
        lines.append("This agent may use only these, at the level the person allowed.")
    if idle:
        lines.append("Installed but not connected (the person can connect one with "
                     "`setu connect <id>`; you cannot):")
        lines += [f"- {c.get('name', c['id'])} (`{c['id']}`)" for c in idle]
    lines.append("If the person asks about an account that is not listed here, say it is "
                 "not connected rather than guessing.")
    return "\n".join(lines)


def announce(link: Link, connected: dict[str, int],
             allow: dict[str, str] | None = None) -> str:
    """The one startup line: which connections, how many tools each."""
    if not connected and allow is not None and link.connections:
        return f"setu: none of your connections for this agent ({link.road})"
    if not connected:
        return f"setu: no connections yet ({link.road})"
    parts = [f"{name} ({count} tool(s))" for name, count in connected.items()]
    return f"setu: {', '.join(parts)} -- via {link.road}"


# ---- what a learned skill needs ---------------------------------------------

#: A connection named in a skill's ``needs:`` -- ``setu:gmail``. Anything
#: else in the line is words for a person, and stays that.
NEED_RE = re.compile(r"\bsetu:([a-z0-9][a-z0-9_-]*)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Need:
    """One ``setu:<connector>`` a skill names, against what Setu reports."""

    connector: str
    name: str                      # the connector's own name, or the id
    known: bool                    # Setu has this connector installed
    servers: tuple[str, ...] = ()  # MCP servers of its connections, if any

    @property
    def connected(self) -> bool:
        return bool(self.servers)

    def describe(self) -> dict[str, Any]:
        return {"connector": self.connector, "name": self.name, "known": self.known,
                "connected": self.connected, "servers": list(self.servers)}


def resolve_needs(text: str, link: Link | None) -> list[Need]:
    """Each ``setu:<id>`` in ``text``, looked up in Setu's last report.
    No link means nothing is known: every need comes back unconnected."""
    wanted = list(dict.fromkeys(m.lower() for m in NEED_RE.findall(text or "")))
    if not wanted:
        return []
    connectors = link.connectors if link is not None else {}
    rows = link.connections if link is not None else []
    out = []
    for cid in wanted:
        info = connectors.get(cid)
        servers = tuple((row.get("mcp") or {}).get("name", "") for row in rows
                        if row.get("connector") == cid and (row.get("mcp") or {}).get("name"))
        out.append(Need(cid, (info or {}).get("name") or cid, info is not None, servers))
    return out


def need_lines(needs: list[Need]) -> list[str]:
    """What load_skill tells the model about a recipe's connections: which
    server to use when there is one, and to stop and say so when there is
    not -- a recipe run without its account fails four steps in, and the
    fix is the person's (``setu connect``), never the model's."""
    lines = []
    for need in needs:
        if len(need.servers) > 1:
            lines.append(f"Needs {need.name} (setu:{need.connector}): connected, "
                         f"{len(need.servers)} accounts -- use its tools "
                         f"(mcp__{need.connector}__*) and say which `account`.")
        elif need.connected:
            tools = ", ".join(f"mcp__{s}__*" for s in need.servers)
            lines.append(f"Needs {need.name} (setu:{need.connector}): connected -- "
                         f"use its tools ({tools}).")
        else:
            how = (f"they can run `setu connect {need.connector}`" if need.known
                   else f"Setu has no {need.connector} connector installed here")
            lines.append(f"Needs {need.name} (setu:{need.connector}), which is NOT "
                         f"connected: do not work around it. Tell the person the "
                         f"recipe needs {need.name} connected ({how}).")
    return lines


def recipes_by_connector(skills: Any) -> dict[str, list[dict[str, Any]]]:
    """The recipes on this computer that need each connector -- learned
    for you or installed from somebody (skills/share.py) -- for the
    Connections page to list under it."""
    out: dict[str, list[dict[str, Any]]] = {}
    for skill in (skills or ()):
        if not getattr(skill, "is_learned", False):
            continue
        for cid in dict.fromkeys(m.lower() for m in NEED_RE.findall(skill.needs or "")):
            out.setdefault(cid, []).append({"name": skill.name,
                                            "shared": bool(getattr(skill, "shared", ""))})
    return out


def connectors_hint(link: Link | None) -> str:
    """For a skill's write-up: the ids a NEEDS line may name. Empty when
    Setu reports no connectors, so the prompt says nothing about Setu."""
    connectors = link.connectors if link is not None else {}
    if not connectors:
        return ""
    names = ", ".join(f"setu:{cid} ({c.get('name', cid)})"
                      for cid, c in sorted(connectors.items()))
    return (f"When the steps used one of these connected accounts, name it in "
            f"NEEDS exactly as written here: {names}.\n")


# ---- what a package asks for -------------------------------------------------

#: One ``[connections] needs`` entry: a connector id, then the level.
NEED_SPEC_RE = re.compile(r"^([a-z0-9][a-z0-9_-]*)(?::([a-z]+))?$")


def parse_need(text: str) -> str:
    """``gmail`` or ``gmail:write`` -> ``gmail:write``; read when no level
    is said, since a package asking for less is the safe misreading."""
    found = NEED_SPEC_RE.match(str(text).strip().lower())
    if not found or (found.group(2) or "read") not in VERB_CLASSES:
        raise ValueError(f"{text!r}: expected a connector id, optionally with a level "
                         f"({', '.join(VERB_CLASSES)}), like \"gmail:read\"")
    return f"{found.group(1)}:{found.group(2) or 'read'}"


def needs_allow(needs: list[str] | tuple[str, ...]) -> dict[str, str]:
    """Granted needs -> connector: the highest level granted for it."""
    allow: dict[str, str] = {}
    for need in needs:
        cid, _, klass = need.partition(":")
        if cid not in allow or CLASS_RANK[klass] > CLASS_RANK[allow[cid]]:
            allow[cid] = klass
    return allow


def approvals_path() -> Path:
    """Where the yes you gave each package is kept: beside memory, under
    the state home, not in any project -- it is yours, not the package's."""
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "yantra" / "connections-approved.json"


def package_key(name: str, root: Path | str | None) -> str:
    """A package by name AND place: another package borrowing a trusted
    one's name, somewhere else on disk, is asked again."""
    return f"{name} @ {Path(root).resolve() if root else '?'}"


def load_approved(key: str, path: Path | None = None) -> set[str]:
    path = path or approvals_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return set()
    return {str(n) for n in (data.get(key) or [])} if isinstance(data, dict) else set()


def save_approved(key: str, need: str, path: Path | None = None) -> None:
    path = path or approvals_path()
    try:
        data = json.loads(path.read_text())
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        data = {}
    data[key] = sorted({*data.get(key, []), need})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def forget_approved(key: str, need: str | None = None,
                    path: Path | None = None) -> list[str]:
    """Take back a yes: one need, or every one this package had. Returns
    what was removed; the next launch asks again."""
    path = path or approvals_path()
    try:
        data = json.loads(path.read_text())
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return []
    had = list(data.get(key) or [])
    gone = [n for n in had if need is None or n == need]
    kept = [n for n in had if n not in gone]
    if kept:
        data[key] = kept
    else:
        data.pop(key, None)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
    return gone


# ---- several accounts, one set of tools ---------------------------------------


class AccountTool(Tool):
    """One connector's tool across several accounts, with ``account`` added.

    Each account's own MCP tool stays as it was -- its own process, its
    own token -- and is called through this one. The model sees one
    ``search_threads`` instead of one per account, and picks by name.
    """

    name = ""
    description = ""
    parameters: dict[str, Any] = {}

    def __init__(self, connector: str, raw: str, accounts: dict[str, Tool],
                 emails: dict[str, str], live: Any) -> None:
        first = next(iter(accounts.values()))
        self.connector, self.raw = connector, raw
        self.accounts, self.emails, self.live = accounts, emails, live
        self.name = f"mcp__{connector}__{raw}"
        self.read_only = first.read_only
        self.always_ask = first.always_ask
        schema = copy.deepcopy(first.parameters) or {"type": "object"}
        schema.setdefault("type", "object")
        listed = ", ".join(f"{a} = {emails[a]}" if emails.get(a) else a for a in accounts)
        schema.setdefault("properties", {})["account"] = {
            "type": "string",
            "enum": [*accounts, *(["all"] if self.read_only else [])],
            "description": f"Which account: {listed}."
                           + (" Leave out, or \"all\", for every account." if self.read_only
                              else " Required: there is no default."),
        }
        if not self.read_only:
            schema["required"] = [*dict.fromkeys([*schema.get("required", []), "account"])]
        self.parameters = schema
        self.description = (f"{first.description} [{len(accounts)} accounts: {listed}; "
                            f"pick one with `account`]")

    def _pick(self, args: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        rest = dict(args)
        account = rest.pop("account", None)
        if account in (None, "", "all"):
            if self.read_only:
                return list(self.accounts), rest
            raise ToolError(f"say which account to use ({', '.join(self.accounts)}); "
                            f"one that sends or changes things has no default")
        if account not in self.accounts:
            raise ToolError(f"no account {account!r}; choose one of "
                            f"{', '.join(self.accounts)}")
        return [account], rest

    def _who(self, account: str) -> str:
        email = self.emails.get(account)
        return f"{account} ({email})" if email else account

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        try:
            names, rest = self._pick(args)
        except ToolError as exc:
            return f"{self.name}: {exc}"
        who = ", ".join(self._who(a) for a in names)
        return f"From: {who} -- {self.accounts[names[0]].summary(rest, ctx)}"

    def _one(self, account: str, args: dict[str, Any], ctx: ToolContext) -> str:
        tool = self.accounts[account]
        if not self.live(tool):
            raise ToolError(f"the {account} account's server is not running in this session")
        out = tool.run(args, ctx)
        return out if isinstance(out, str) else out.text

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        names, rest = self._pick(args)
        if len(names) == 1:
            return self._one(names[0], rest, ctx)
        parts = []
        for account in names:
            try:
                parts.append(f"## {self._who(account)}\n{self._one(account, rest, ctx)}")
            except ToolError as exc:
                parts.append(f"## {self._who(account)}\nfailed: {exc}")
        return "\n\n".join(parts)


# ---- the live handle --------------------------------------------------------


@dataclass(slots=True)
class Synced:
    """What one ``sync`` did: servers and their tool counts, and notes."""

    connected: dict[str, int] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SiteLink:
    """One browser-road connection in this session: its tools' prefix, the
    tools registered, its browser session, and what it was started for."""

    prefix: str
    names: list[str]
    session: Any
    stamp: tuple[str, ...]


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
    #: What each started server was started for -- level and account --
    #: so a level changed in Setu restarts it: a connector reads its level
    #: once, when it starts, and offers that level's tools for its life.
    started: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: What a package may use, connector -> highest level; None for a
    #: session of your own, which may use everything.
    allow: dict[str, str] | None = None
    #: The merged tools of connectors with several accounts, by name.
    merged: dict[str, AccountTool] = field(default_factory=dict)
    #: A package's session: its name, the key its yes is kept under, the
    #: needs granted, and the questions nobody has answered yet -- the
    #: page puts those as Allow / Not now.
    package: str | None = None
    package_key: str | None = None
    granted: list[str] = field(default_factory=list)
    asks: list[dict[str, str]] = field(default_factory=list)
    #: Everything the package asked for, and what was said no to this
    #: session -- a no is not asked twice in one sitting, nor kept.
    needs: list[str] = field(default_factory=list)
    declined: set[str] = field(default_factory=set)
    #: Browser-road connections' tool sets, by ref (tools/site.py).
    sites: dict[str, SiteLink] = field(default_factory=dict)
    #: The times of what Setu's report says to watch, at the last look
    #: (``moved``). None until the first look.
    watched: tuple | None = None

    @property
    def program(self) -> str | None:
        """The setu program to run for connect and disconnect: the one
        Setu reports for itself, else the one named, else PATH's."""
        reported = (self.link.data.get("command") if self.link else None) or None
        return reported or self.path or shutil.which("setu")

    def _stamp(self) -> tuple | None:
        """The times of the vault and of each added site's file: any
        connect, disconnect, level, new site or kept guide moves one."""
        paths = (self.link.data.get("watch") or []) if self.link is not None else []
        if not paths:
            return None         # a Setu that says nothing to watch: never moved
        stamp = []
        for raw in paths:
            if not raw:
                continue
            where = Path(raw)
            try:
                if where.is_dir():
                    stamp.append(tuple(sorted((p.name, p.stat().st_mtime_ns)
                                              for p in where.glob("*.toml"))))
                else:
                    stamp.append(where.stat().st_mtime_ns)
            except OSError:
                stamp.append(None)
        return tuple(stamp)

    def moved(self) -> bool:
        """Whether Setu would say something new -- an account connected in
        another terminal, a level changed, a guide kept -- since the last
        look. Only file times are read; ``refresh`` is the real look."""
        now = self._stamp()
        if self.watched is None:
            self.watched = now
            return False
        return now != self.watched

    def refresh(self) -> Link | None:
        """Ask Setu again. A page asking is the person asking, so a Setu
        that cannot be found is an error here even in ``auto``."""
        try:
            self.link = load("on" if self.mode == "auto" else self.mode, self.path)
            self.error = ""
            self.watched = self._stamp()
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
        self._unmerge(agent.registry, manager)
        wanted = [(row, cfg) for row, cfg in (mcp_configs(link) if link is not None else [])
                  if self.allow is None or row.get("connector") in self.allow]
        # a version Setu's catalog withdrew is not started, whoever allowed it;
        # the connection stays in Setu, so an update can use it again
        for row, _ in wanted:
            reason = (link.connectors.get(row.get("connector")) or {}).get("yanked")
            if reason:
                done.notes.append(f"{row['ref']} not started: its connector was withdrawn "
                                  f"by Setu -- {reason}")
        wanted = [(row, cfg) for row, cfg in wanted
                  if not (link.connectors.get(row.get("connector")) or {}).get("yanked")]
        names = {cfg.name for _, cfg in wanted}
        for name in sorted(self.servers - names):
            try:
                manager.disconnect(name)
            except MCPError:
                pass
            self.servers.discard(name)
            self.started.pop(name, None)
            done.dropped.append(name)
        for row, cfg in wanted:
            existing = manager.sessions.get(cfg.name)
            stamp = (str(row.get("level") or ""), str(row.get("email") or ""))
            if (existing is not None and cfg.name in self.servers
                    and self.started.get(cfg.name, stamp) != stamp):
                # signed in again at another level: the running connector
                # still offers the old level's tools until it restarts
                try:
                    manager.disconnect(cfg.name)
                except MCPError:
                    pass
                self.servers.discard(cfg.name)
                existing = None
                done.notes.append(f"{row['ref']} restarted at "
                                  f"{row.get('level_label') or row.get('level')}")
            if existing is not None and not same_server(existing.config, cfg):
                done.notes.append(f"'{cfg.name}' is already an mcp server with a different "
                                  f"command; leaving it, and {row['ref']} unconnected")
                continue
            if existing is None:
                try:
                    manager.connect(cfg, origin="setu")
                except MCPError as exc:
                    done.notes.append(f"{row['ref']} unavailable: {exc}")
                    continue
                self.servers.add(cfg.name)
            elif cfg.name not in self.servers and same_server(existing.config, cfg):
                self.servers.add(cfg.name)   # configured by hand as this very command
            self.started[cfg.name] = stamp
            ceiling = None if self.allow is None else self.allow[row["connector"]]
            kept, removed = apply_verbs(agent.registry, cfg.name,
                                        link.verbs(row["connector"]), ceiling)
            if cfg.name in getattr(manager, "tool_names", {}):
                # the manager's count is what the page shows: tools the
                # manifest refused, or above a package's level, are not
                # the agent's, so not counted
                manager.tool_names[cfg.name] = [
                    n for n in manager.tool_names[cfg.name] if n in kept]
            if removed:
                done.notes.append(f"{cfg.name} offered tool(s) its manifest does not list, "
                                  f"not registered: {', '.join(removed)}")
            done.connected[cfg.name] = len(kept)
        self._sync_sites(agent.registry, link, done)
        if link is not None:
            done.notes += [p for p in link.problems]
            self._merge(agent.registry, manager,
                        [(row, cfg.name) for row, cfg in wanted if cfg.name in done.connected],
                        done)
        refresh = getattr(manager, "_refresh_catalog", None)
        if self.merged and callable(refresh):
            refresh()
        prompt = attach_prompt(agent)
        merged = frozenset(t.connector for t in self.merged.values())
        prompt.set("connections", prompt_text(
            link, self.allow, merged, {ref: site.prefix for ref, site in self.sites.items()})
                   if link is not None else None)
        prompt.apply()
        return done

    def _sync_sites(self, registry: Any, link: Link | None, done: Synced) -> None:
        """Make the browser-road tool sets match the report: one per
        connection, at its level, under a package's ceiling. A level or
        profile changed in Setu rebuilds that set; a gone one closes."""
        from yantra.tools.site import SiteSession, prefix_for, rules_from, site_tools

        rows = [r for r in (link.connections if link is not None else [])
                if r.get("browser") and (self.allow is None or r.get("connector") in self.allow)
                and not (link.connectors.get(r.get("connector")) or {}).get("yanked")]
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.get("connector", "")] = counts.get(row.get("connector", ""), 0) + 1
        wanted: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {}
        for row in rows:
            where = row.get("browser") or {}
            wanted[row["ref"]] = (row, (
                str(row.get("level") or ""), str(where.get("profile") or ""),
                str(where.get("executable") or ""),
                str(None if self.allow is None else self.allow.get(row.get("connector"))),
                str(counts[row.get("connector", "")] > 1)))
        for ref in list(self.sites):
            if ref not in wanted or wanted[ref][1] != self.sites[ref].stamp:
                site = self.sites.pop(ref)
                for name in site.names:
                    registry.unregister(name)
                try:
                    site.session.close()
                except Exception:
                    pass                    # a session that never launched
                if ref not in wanted:
                    done.dropped.append(site.prefix)
                else:
                    done.notes.append(f"{ref} restarted at "
                                      f"{wanted[ref][0].get('level_label') or 'a new level'}")
        for ref, (row, stamp) in wanted.items():
            if ref in self.sites:
                done.connected[self.sites[ref].prefix] = len(self.sites[ref].names)
                continue
            card = link.connectors.get(row.get("connector")) or {}
            where = row.get("browser") or {}
            if not where.get("profile"):
                done.notes.append(f"{ref} has no browser profile; sign in again")
                continue
            prefix = prefix_for(row.get("connector", ""), account_of(row),
                                counts[row.get("connector", "")] > 1)
            session = SiteSession(rules_from(card, row), Path(where["profile"]),
                                  where.get("executable") or None)
            ceiling = None if self.allow is None else self.allow.get(row.get("connector"))
            tools = site_tools(prefix, session, dict(card.get("verbs") or {}),
                               str(row.get("level") or "read"), ceiling)
            taken = [t.name for t in tools if t.name in registry]
            if taken:
                done.notes.append(f"{ref}: {', '.join(taken)} already a tool; not connected")
                continue
            names = []
            for tool in tools:
                registry.register(tool)
                if tool.name in registry:     # a package's allow list may refuse it
                    names.append(tool.name)
            self.sites[ref] = SiteLink(prefix=prefix, names=names, session=session,
                                       stamp=stamp)
            done.connected[prefix] = len(names)

    def question(self, need: str) -> dict[str, str] | None:
        """The question a need puts, naming the accounts it would reach;
        None while its connector has no account connected."""
        link = self.link
        if link is None:
            return None
        cid, _, klass = need.partition(":")
        rows = [r for r in link.connections if r.get("connector") == cid]
        if not rows:
            return None
        name = (link.connectors.get(cid) or {}).get("name", cid)
        who = ", ".join(r.get("email") or r.get("ref", cid) for r in rows)
        return {"need": need, "connector": cid, "name": name, "level": klass,
                "question": f"{self.package} wants to {CLASS_WORDS[klass]} your "
                            f"{name} ({who}). Allow?"}

    def offer(self) -> list[dict[str, str]]:
        """A package's needs that became askable since the last look --
        an account connected mid-session -- added to ``asks`` and
        returned. Granted, declined and already-asked needs are skipped."""
        if self.package_key is None:
            return []
        asked = {a["need"] for a in self.asks}
        new = []
        for need in self.needs:
            if need in self.granted or need in self.declined or need in asked:
                continue
            ask = self.question(need)
            if ask is not None:
                new.append(ask)
        self.asks += new
        return new

    def answer(self, need: str, yes: bool) -> None:
        """The person answered a package's question. A yes is kept, as in
        the terminal; a no only clears the question for this session.
        The caller syncs, so the tools follow."""
        ask = next((a for a in self.asks if a["need"] == need), None)
        if ask is None or self.package_key is None:
            raise SetuLinkError(f"nothing is asking for {need!r}")
        self.asks.remove(ask)
        if yes:
            save_approved(self.package_key, need)
            self.granted.append(need)
            self.allow = needs_allow(self.granted)
        else:
            self.declined.add(need)

    def forget(self, need: str | None = None) -> list[str]:
        """Take back a yes (one need, or all) -- for good, and for this
        session too: the caller syncs and the tools go."""
        if self.package_key is None:
            raise SetuLinkError("this is your own session; nothing was allowed to forget")
        gone = forget_approved(self.package_key, need)
        self.granted = [g for g in self.granted if need is not None and g != need]
        self.allow = needs_allow(self.granted)
        # taken back means not asked again this sitting; the next launch asks
        self.declined.update(gone)
        return gone

    def _unmerge(self, registry: Any, manager: Any) -> None:
        """Put each account's own tools back, so a sync starts from what
        the servers offer. Only tools whose server is still the same live
        session return; a reconnected server registered fresh ones."""
        for name, tool in self.merged.items():
            registry.unregister(name)
            for original in tool.accounts.values():
                session = getattr(original, "session", None)
                server = session.config.name if session is not None else None
                if manager.sessions.get(server) is session and original.name not in registry:
                    registry.register(original)
        self.merged.clear()

    def _merge(self, registry: Any, manager: Any,
               pairs: list[tuple[dict[str, Any], str]], done: Synced) -> None:
        """Connectors with two or more connected accounts: one tool each,
        with ``account``, in place of one per account."""
        by_connector: dict[str, list[tuple[dict[str, Any], str]]] = {}
        for row, server in pairs:
            by_connector.setdefault(row.get("connector", ""), []).append((row, server))

        def live(tool: Any) -> bool:
            session = getattr(tool, "session", None)
            return session is not None and \
                manager.sessions.get(session.config.name) is session

        for cid, group in by_connector.items():
            if len(group) < 2:
                continue
            raws: dict[str, None] = {}
            for _, server in group:
                prefix = f"mcp__{server}__"
                raws.update((n[len(prefix):], None) for n in registry.names()
                            if n.startswith(prefix))
            for raw in raws:
                accounts, emails = {}, {}
                for row, server in group:
                    name = f"mcp__{server}__{raw}"
                    if name in registry:
                        accounts[account_of(row)] = registry.get(name)
                        emails[account_of(row)] = row.get("email") or ""
                tool = AccountTool(cid, raw, accounts, emails, live)
                if tool.name in registry:
                    done.notes.append(f"'{tool.name}' is already a tool; {cid}'s accounts "
                                      f"keep their own {raw} tools")
                    continue
                for original in accounts.values():
                    registry.unregister(original.name)
                registry.register(tool)
                if tool.name in registry:
                    self.merged[tool.name] = tool
                else:                      # refused by a package's allow list
                    for original in accounts.values():
                        registry.register(original)

    def describe(self, manager: Any = None) -> dict[str, Any]:
        """The Connections panel's data. Built from Setu's own report,
        which carries no secret by contract, plus what this session runs."""
        link = self.link
        live = {s["name"]: s for s in (manager.servers() if manager is not None else [])}
        merged = {t.connector for t in self.merged.values()}
        rows = []
        for row in (link.connections if link is not None else []):
            site = self.sites.get(row.get("ref", ""))
            if row.get("browser"):
                rows.append({**{k: row.get(k) for k in (
                    "ref", "connector", "account", "email", "level", "level_label",
                    "last_used")}, "server": "", "road": "browser",
                    "tools_as": f"{site.prefix}_" if site else "",
                    "tools": len(site.names) if site else 0,
                    "running": site is not None})
                continue
            name = (row.get("mcp") or {}).get("name", "")
            server = live.get(name)
            rows.append({**{k: row.get(k) for k in (
                "ref", "connector", "account", "email", "level", "level_label",
                "last_used")}, "server": name,
                "tools_as": f"mcp__{row.get('connector')}__"
                            if row.get("connector") in merged else f"mcp__{name}__",
                "tools": server["tools"] if server else 0,
                "running": bool(server and server["healthy"])})
        return {
            "mode": self.mode,
            "allow": dict(self.allow) if self.allow is not None else None,
            "package": self.package,
            "granted": list(self.granted),
            "asks": [dict(a) for a in self.asks],
            "found": link is not None,
            "road": link.road if link is not None else None,
            "error": self.error,
            "version": link.data.get("version") if link is not None else None,
            "connections": rows,
            "connectors": list(link.connectors.values()) if link is not None else [],
            "setup": dict((link.data.get("setup") or {}) if link is not None else {}),
            "problems": list(link.problems) if link is not None else [],
            # the signed catalog's word, when Setu keeps one: recipes listed
            # there, and where it came from (labels ride on each connector)
            "catalog": (link.data.get("catalog") if link is not None else None),
        }


# ---- signing in and out, for a page ---------------------------------------------

#: An account name as a page may pass it: it becomes one argv word, so it
#: may not look like an option; Setu has its own rules on top.
#: An address for ``setu connect --site``: a host, maybe a scheme and a
#: port. Setu checks it properly; this keeps flags and spaces out of argv.
SITE_RE = re.compile(r"^(https?://)?[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z0-9.-]+(:\d+)?/?$")
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

    The address in the ``url`` event is the site's sign-in page (Google's,
    or a Home Assistant's own login); its
    redirect comes back to a port Setu opened on THIS computer, which is
    why a page may start one only when it is open on this computer too.

    With ``site``, it is ``setu connect --site``: a site Setu has no
    connector for, whose rules Setu writes once the person has signed in.
    Setu may then send ``ask`` -- its look at the page could not tell
    whether the person signed in -- and waits for ``answer``. The ref is
    the address until ``connected`` names the site's new id.
    """

    def __init__(self, program: str, connector: str, account: str, level: str,
                 on_event: Any, site: str | None = None) -> None:
        if not ACCOUNT_RE.match(account):
            raise SetuLinkError(f"account name {account!r}: letters, digits, '.', '_' "
                                f"and '-', starting with a letter or digit")
        if site is not None and not SITE_RE.match(site):
            raise SetuLinkError(f"{site!r} is not a site's address (try: example.com)")
        self.ref = f"{site or connector}:{account}"
        self.on_event = on_event
        self.last: dict[str, Any] = {}
        self.cancelled = False
        if site is not None:
            argv = [program, "connect", "--site", site, "--as", account, "--json"]
            argv += ["--level", level] if level else []
        else:
            argv = [program, "connect", connector, "--as", account, "--level", level, "--json"]
        self.process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
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
                if event.get("event") == "connected" and event.get("ref"):
                    self.ref = str(event["ref"])
                self.last = event
                self.on_event(event)
        self.process.wait()
        why = (self.process.stderr.read() if self.process.stderr else "").strip()
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
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

    def answer(self, yes: bool) -> None:
        """The person's answer to Setu's ``ask``: one line on its stdin."""
        if self.last.get("event") != "ask" or not self.running:
            raise SetuLinkError("Setu is not waiting for an answer")
        assert self.process.stdin is not None
        self.last = {**self.last, "event": "answered"}
        self.process.stdin.write("yes\n" if yes else "no\n")
        self.process.stdin.flush()

    def cancel(self) -> None:
        """Stop waiting for the person. Setu's port closes with it, and
        nothing was saved -- a sign-in only saves on its last step."""
        if self.running:
            self.cancelled = True
            self.process.terminate()
