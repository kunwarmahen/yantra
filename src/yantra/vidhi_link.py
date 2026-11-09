"""Running the person's routines -- through Vidhi, found at startup.

Vidhi (a separate project) keeps routines a person drew once: a morning
brief that fetches three things side by side and writes them into one
message, a price check that asks before it plans. What an agent does
with one is small on purpose: RUN it, or INSTALL a template with its
blanks filled. It never writes a routine's steps -- the person draws
them on Vidhi's page -- because writing a graph is where a small local
model goes wrong, and picking "morning-brief" and filling ``latitude =
51.5`` is where it goes right. Vidhi ships those tools as an MCP server
(``vidhi mcp --for WHO``); this module finds it, starts it, gives each
write a card in words, and names the person's routines in the prompt.

FOUND LIKE PRAHARI, BY THE SAME CONTRACT. ``vidhi status --json`` prints
what Vidhi is; its ``format`` is the whole contract. ``YANTRA_VIDHI`` or
the flags choose: ``auto`` (default: use it if ``vidhi`` is on PATH, say
nothing if not), ``on`` (--vidhi: loudly if missing), ``off``
(--no-vidhi), or a PATH. An unattended run never looks.

ONE SERVER, FOR ONE PERSON: ``--for`` the person this agent serves
(``local`` here), ``--agent`` the package, and ``--runner``, so a
routine installed from a chat through Dvara runs as that person, with
their accounts.

THE CARD SAYS WHAT A YES COVERS. ``run_routine`` and
``install_template`` arrive as permission cards; each says, in Vidhi's
own words, every call the routine makes, every question to the model,
whether a step runs the agent WITH ITS TOOLS, and what a run costs.

NAMES IN THE PROMPT, NEVER RESULTS. The ``routines`` layer names each of
the person's routines and what it does -- so "do my morning thing" finds
``morning-brief`` -- and never what a run found, which is old by the time
the prompt is read.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from yantra.mcp import MCPServerConfig
from yantra.samay_link import WORD_RE

ENV = "YANTRA_VIDHI"
FORMAT = "vidhi.status.v1"
SERVER = "vidhi"
LOCAL = "local"
STATUS_TIMEOUT = 15.0
#: Vidhi's tools that change something: each gets a card.
WRITES = ("run_routine", "install_template", "set_routine_inputs", "stop_run")
#: How many routines the prompt names; past that, list_routines has them.
NAMED = 12
SHOWN = 8


class VidhiLinkError(Exception):
    """Vidhi was asked for and could not be used."""


def resolve_mode(flag: str | None, env: str | None = None) -> tuple[str, str | None]:
    """(mode, vidhi path). A flag outranks the environment; a path means on."""
    raw = flag if flag is not None else (env if env is not None
                                         else os.environ.get(ENV, ""))
    raw = (raw or "").strip()
    if not raw or raw.lower() == "auto":
        return "auto", None
    if raw.lower() in ("on", "off"):
        return raw.lower(), None
    return "on", os.path.expanduser(raw)


def _status(path: str) -> dict[str, Any]:
    try:
        done = subprocess.run([path, "status", "--json"], capture_output=True, text=True,
                              timeout=STATUS_TIMEOUT)
    except (FileNotFoundError, PermissionError):
        raise VidhiLinkError(f"no vidhi program at {path}") from None
    except subprocess.TimeoutExpired:
        raise VidhiLinkError(f"`{path} status --json` took longer than "
                             f"{int(STATUS_TIMEOUT)}s") from None
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip().splitlines()
        raise VidhiLinkError(f"`{path} status --json` failed: "
                             f"{said[-1] if said else done.returncode}")
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError:
        raise VidhiLinkError(f"`{path} status --json` did not print JSON") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise VidhiLinkError(f"vidhi answered in a format this Yantra does not know "
                             f"({data.get('format') if isinstance(data, dict) else '?'}; "
                             f"want {FORMAT})")
    return data


def load(mode: str, path: str | None = None) -> tuple[dict[str, Any], str] | None:
    """(Vidhi's report, the program asked). None when off, or ``auto`` and
    not there; an error when it was asked for."""
    if mode == "off":
        return None
    program = path or shutil.which("vidhi")
    if not program:
        if mode == "on":
            raise VidhiLinkError(f"Vidhi was asked for but not found: put `vidhi` on PATH, "
                                 f"or name it with {ENV}=/path/to/vidhi")
        return None
    try:
        return _status(program), program
    except VidhiLinkError:
        if mode == "on" or path:
            raise
        return None


def server_config(data: dict[str, Any], person: str = LOCAL, agent: str = "",
                  runner: str = "") -> MCPServerConfig:
    if not WORD_RE.match(person):
        raise VidhiLinkError(f"{person!r} is not a person Vidhi can keep routines for")
    mcp = data["mcp"]
    args = [*(mcp.get("args") or []), "--for", person]
    if agent:
        args += ["--agent", agent]
    if runner:
        args += ["--runner", runner]
    return MCPServerConfig(name=SERVER, command=mcp["command"], args=args)


def announce(data: dict[str, Any], tools: int, program: str) -> str:
    count = sum((data.get("routines") or {}).values())
    where = "running" if data.get("serving") else "not running (vidhi serve)"
    return (f"vidhi: {tools} tool(s), {count} routine(s); {where} -- {program}")


def prompt_text(cards: list[dict[str, Any]], serving: bool = True) -> str:
    """The ``routines`` layer: what Vidhi is for, and the person's own."""
    lines = [
        "Routines (Vidhi): steps the person set up once, which run by themselves -- "
        "fetching side by side, asking the model, asking the person yes or no, telling "
        "them. When they ask for one by name or by what it does (\"do my morning "
        "brief\"), start it with `mcp__vidhi__run_routine`; what it finds reaches them "
        "on their channels, so say it has started rather than waiting. For something a "
        "template already does, offer `install_template` with its blanks filled from "
        "what they said (ask for any blank with no default). You never write a "
        "routine's steps: the person draws them on Vidhi's page. To have one run at a "
        "time or when something happens, a Samay schedule or a Prahari watch starts it "
        "(their `routine` field)."]
    if not serving:
        lines.append("(Vidhi is not running on this computer: a run waits until "
                     "`vidhi serve` starts. Say so.)")
    mine = [c for c in cards if isinstance(c, dict)]
    if mine:
        lines.append("Their routines:")
        for card in mine[:NAMED]:
            state = "" if card.get("state") == "active" else f" ({card.get('state')})"
            lines.append(f"- {card.get('name')}: {card.get('title')}{state} -- "
                         f"{card.get('cost')}")
        if len(mine) > NAMED:
            lines.append(f"- and {len(mine) - NAMED} more (`list_routines`)")
    return "\n".join(lines)


# -- the cards -----------------------------------------------------------------------


def explain_run(args: dict[str, Any], card: dict[str, Any] | None) -> str:
    name = str(args.get("name") or "?")
    lines = [f"Run the routine {name} now"]
    if card:
        lines[0] += f": {card.get('title')}"
        if card.get("state") != "active":
            lines.append(f"  (it is {card.get('state')}: Vidhi will refuse to start it)")
        lines += ["", "What it does, by itself, once started"]
        lines += [f"  - {t}" for t in (card.get("touches") or [])[:SHOWN * 2]]
        lines += ["", f"Cost: {card.get('cost')}"]
        given = args.get("inputs") or {}
        kept = card.get("inputs") or {}
        if given or kept:
            lines += ["", "With"]
            for key in sorted({*kept, *given}):
                value = given.get(key, kept.get(key))
                changed = " (this run only)" if key in given else ""
                lines.append(f"  {key} = {value}{changed}")
    else:
        lines.append("  (Vidhi couldn't say what it does: it may not exist)")
    return "\n".join(lines)


def explain_install(args: dict[str, Any], card: dict[str, Any] | None) -> str:
    name = str(args.get("template") or "?")
    lines = [f"Install the routine {name}"]
    if card is None:
        return "\n".join(lines + ["  (Vidhi has no template by this name)"])
    lines[0] += f": {card.get('title')}"
    origin = {"vidhi": "comes with Vidhi", "mine": "this computer's own"}.get(
        str(card.get("source")), f"from a marketplace, signed by {card.get('signed_by')}")
    lines += [f"  ({origin})", "", "What each run does"]
    lines += [f"  - {t}" for t in (card.get("touches") or [])[:SHOWN * 2]]
    lines += ["", f"Cost: {card.get('cost')}", "", "Filled in"]
    given = args.get("inputs") or {}
    for spec in card.get("inputs") or []:
        value = given.get(spec["name"], spec.get("default", "(not given: Vidhi will ask)"))
        mark = "" if spec["name"] in given else " (its default)"
        lines.append(f"  {spec.get('label') or spec['name']}: {value}{mark}")
    lines += ["", "Installing doesn't run it. Each run is started by you, a schedule or a "
                  "watch."]
    return "\n".join(lines)


def explain_inputs(args: dict[str, Any], card: dict[str, Any] | None) -> str:
    name = str(args.get("name") or "?")
    lines = [f"Change what the routine {name} is filled in with"]
    kept = (card or {}).get("inputs") or {}
    for key, value in (args.get("inputs") or {}).items():
        lines.append(f"  {key}: {kept.get(key, '(none)')} -> {value}")
    return "\n".join(lines)


# -- the live handle ------------------------------------------------------------------


@dataclass(slots=True)
class Vidhi:
    """One session's link to Vidhi."""

    mode: str
    path: str | None = None
    data: dict[str, Any] | None = None
    program: str | None = None
    person: str = LOCAL
    agent: str = ""
    runner: str = ""
    error: str = ""
    tools: list[str] = field(default_factory=list)
    _prompt: Any = None

    def connect(self, manager: Any, agent: Any) -> int:
        """Start the server, give its writes their cards, write the prompt
        layer. Returns how many tools it brought. Raises MCPError."""
        from yantra.prompt import attach_prompt

        assert self.data is not None
        cfg = server_config(self.data, self.person, self.agent, self.runner)
        if manager.sessions.get(SERVER) is None:
            self.tools = [n for n in manager.connect(cfg, origin="vidhi")
                          if n in agent.registry]
        else:
            self.tools = [n for n in agent.registry.names()
                          if n.startswith(f"mcp__{SERVER}__")]
        for name in self.tools:
            tool = agent.registry.get(name)
            hook = self.explain(tool.raw_name)
            if hook is not None:
                tool.explain = hook
        self._prompt = attach_prompt(agent)
        self.write_layer()
        return len(self.tools)

    def write_layer(self) -> None:
        if self._prompt is None or self.data is None:
            return
        self._prompt.set("routines", prompt_text(self.routines(quiet=True),
                                                 bool(self.data.get("serving"))))
        self._prompt.apply()

    def explain(self, verb: str) -> Any:
        if verb == "run_routine":
            return lambda args: explain_run(args, self.card(str(args.get("name") or "")))
        if verb == "install_template":
            return lambda args: explain_install(args, self.template(
                str(args.get("template") or "")))
        if verb == "set_routine_inputs":
            return lambda args: explain_inputs(args, self.card(str(args.get("name") or "")))
        if verb == "stop_run":
            return lambda args: f"Stop the run {args.get('run')} (it ends where it is)"
        return None

    # -- asking the program ---------------------------------------------------------

    def _base(self) -> list[str]:
        if self.data is None or not self.program:
            raise VidhiLinkError(self.error or "Vidhi was not found")
        return [self.program, "--state", str(self.data.get("state") or "")]

    def _json(self, argv: list[str], timeout: float = STATUS_TIMEOUT) -> Any:
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VidhiLinkError(f"vidhi did not answer: {exc}") from None
        lines = (done.stdout or "").strip().splitlines()
        try:
            data = json.loads(lines[-1]) if lines else None
        except json.JSONDecodeError:
            data = None
        if done.returncode != 0 or data is None:
            said = (done.stderr or done.stdout).strip().splitlines()
            raise VidhiLinkError(said[-1] if said else "vidhi said nothing")
        return data

    def _as(self) -> list[str]:
        return [f"--as={self.person}"]

    def routines(self, *, quiet: bool = False) -> list[dict[str, Any]]:
        try:
            found = self._json([*self._base(), "list", *self._as(), "--json"])
        except VidhiLinkError:
            if quiet:
                return []
            raise
        return found if isinstance(found, list) else []

    def card(self, name: str) -> dict[str, Any] | None:
        try:
            found = self._json([*self._base(), "show", f"--as={self.person}", "--json",
                                "--", name])
        except VidhiLinkError:
            return None
        return found if isinstance(found, dict) else None

    def template(self, name: str) -> dict[str, Any] | None:
        try:
            found = self._json([*self._base(), "templates", "--json"])
        except VidhiLinkError:
            return None
        return next((t for t in (found or {}).get("templates") or []
                     if t.get("name") == name), None)

    def describe(self) -> dict[str, Any]:
        data = self.data or {}
        return {"mode": self.mode, "found": self.data is not None, "error": self.error,
                "person": self.person, "serving": bool(data.get("serving")),
                "url": data.get("url"), "routines": dict(data.get("routines") or {}),
                "version": data.get("version"), "tools": len(self.tools)}
