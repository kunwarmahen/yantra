"""The person's phone -- through Sparsh, found at startup.

Sparsh (a separate project) works an Android phone, or the emulator,
through ``adb``: it shows the screen as a numbered list a model can read
without seeing a picture, and taps, types and scrolls by number. It
ships the agent's tools as an MCP server (``sparsh mcp``); this module
finds it, starts it, and decides what a person is asked about.

FOUND LIKE SAMAY, BY THE SAME KIND OF CONTRACT. ``sparsh status --json``
prints what Sparsh is: its state folder, the phones ``adb`` sees, the
person's rules, the command that starts the tools, and each tool's
kind. Its ``format`` is the whole contract; an unknown one is refused
rather than guessed at. ``YANTRA_SPARSH`` or the flags choose:

    auto  (default) use Sparsh if `sparsh` is on PATH AND a phone is
          attached; say nothing if not
    on    use it, and say so loudly if it cannot be found (--sparsh)
    off   never look (--no-sparsh)
    PATH  the sparsh program to run, which also means "on" (--sparsh PATH)

THE PHONE'S RULES DECIDE WHAT ASKS, NOT THE SERVER'S HINTS. Sparsh's
taps honestly say they change things, and a card before every tap would
be a card nobody reads by the tenth. Sparsh names three kinds instead:

    read     look, list_apps, describe_hold       never asked about
    act      tap, type_text, scroll, press_key,   not asked about here:
             open_app                             Sparsh holds the risky
                                                  ones itself (Send, Pay,
                                                  Delete, a password,
                                                  Enter beside a Send ...)
    confirm  confirm                              asked EVERY time, even
                                                  under --yolo

A held step comes back to the model as "NOT DONE", with a hold id;
``confirm`` is the only way through it, and its card is Sparsh's own
account of the step and the screen it was on (``describe_hold``) --
so what a person says yes to is the message that will be sent, not a
hold id. A tool Sparsh doesn't name keeps the server's hint.

THE PAGE PEEKS, IT DOESN'T LOOK. The phone panel shows the phones, the
rules in force, and on request the screen as it is now -- the list and
a screenshot -- through ``sparsh look --peek``, a separate program run
that leaves the agent's last look alone. Had it used a plain look, a
person glancing at the panel mid-turn would renumber the screen under
the agent, and its next "tap 7" would be checked against the person's
7. The panel shows everything, apps on the ``never`` list included: it
is the person's own eyes, and nothing it reads reaches the model.

NOT WITH NOBODY WATCHING. A run nobody watches (``--unattended``) gets
no phone: everything it may do by itself would be done on a phone no
one is looking at, and a held step could only be refused.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from yantra.mcp import MCPServerConfig

ENV = "YANTRA_SPARSH"
FORMAT = "sparsh.status.v1"
#: The MCP server's name; its tools are ``mcp__sparsh__*``.
SERVER = "sparsh"
#: How long ``sparsh status --json`` may take (it asks adb for phones).
STATUS_TIMEOUT = 15.0
KINDS = ("read", "act", "confirm")
#: A phone's serial as adb prints it (emulator-5554, R58M..., 192.168.1.5:5555),
#: as it may reach an argv word: never an option.
SERIAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
#: How long a peek may take: a dump (~2.5 s) and a screenshot.
PEEK_TIMEOUT = 30.0


class SparshLinkError(Exception):
    """Sparsh was asked for and could not be used."""


def resolve_mode(flag: str | None, env: str | None = None) -> tuple[str, str | None]:
    """(mode, sparsh path). A flag outranks the environment; a path means on.

    ``flag`` is what the command line said: None (nothing), "off"
    (--no-sparsh), "on" (bare --sparsh) or a path (--sparsh PATH).
    """
    raw = flag if flag is not None else (env if env is not None else os.environ.get(ENV, ""))
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
        raise SparshLinkError(f"no sparsh program at {path}") from None
    except subprocess.TimeoutExpired:
        raise SparshLinkError(f"`{path} status --json` took longer than "
                              f"{int(STATUS_TIMEOUT)}s") from None
    if done.returncode != 0:
        why = (done.stderr or done.stdout).strip().splitlines()
        raise SparshLinkError(f"`{path} status --json` failed: "
                              f"{why[-1] if why else done.returncode}")
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError:
        raise SparshLinkError(f"`{path} status --json` did not print JSON") from None
    if not isinstance(data, dict):
        raise SparshLinkError(f"sparsh ({path}) answered something that is not an object")
    if data.get("format") != FORMAT:
        raise SparshLinkError(f"sparsh ({path}) speaks {data.get('format')!r}; this Yantra "
                              f"understands {FORMAT!r} -- update one of them")
    if not (data.get("mcp") or {}).get("command"):
        raise SparshLinkError(f"sparsh ({path}) did not say how to start its tools")
    return data


def ready_phones(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The phones adb can use now (not offline, not waiting for a yes)."""
    return [p for p in data.get("phones") or [] if p.get("state") == "device"]


def load(mode: str, path: str | None = None) -> tuple[dict[str, Any], str] | None:
    """(Sparsh's report, the program asked). None when off, or when it is
    ``auto`` and Sparsh or a phone is simply not there; an error when it
    was asked for and cannot be used."""
    if mode == "off":
        return None
    program = path or shutil.which("sparsh")
    if not program:
        if mode == "on":
            raise SparshLinkError(f"Sparsh was asked for but not found: put `sparsh` on "
                                  f"PATH, or name it with {ENV}=/path/to/sparsh")
        return None
    try:
        data = _status(program)
    except SparshLinkError:
        if mode == "on" or path:
            raise
        return None
    if mode == "auto" and not path and not ready_phones(data):
        return None                   # nothing to work: no tools, no prompt
    return data, program


def server_config(data: dict[str, Any]) -> MCPServerConfig:
    mcp = data["mcp"]
    return MCPServerConfig(name=SERVER, command=mcp["command"], args=list(mcp.get("args") or []))


def _phone_words(data: dict[str, Any]) -> str:
    phones = ready_phones(data)
    if not phones:
        why = data.get("adb")
        return ("no phone attached" if why in (None, "ok") else f"no phone ({why})")
    return ", ".join(f"{p.get('serial')} ({p.get('model') or '?'})" for p in phones)


def announce(data: dict[str, Any], tools: int, program: str) -> str:
    """The one startup line: tools, which phone, and the apps kept out."""
    never = (data.get("rules") or {}).get("never") or []
    kept = f"; kept out of {', '.join(never)}" if never else ""
    return f"sparsh: {tools} tool(s); phone {_phone_words(data)}{kept} -- via {program}"


def prompt_text(data: dict[str, Any]) -> str:
    """The ``phone`` layer: the tools exist, and how to use them well."""
    phones = ready_phones(data)
    lines = ["# The person's phone (through Sparsh)"]
    if not phones:
        lines.append("The `mcp__sparsh__` tools work an Android phone, but none is "
                     "attached right now: if asked to use it, say the phone needs "
                     "plugging in (with USB debugging on) or the emulator starting.")
        return "\n".join(lines)
    lines += [
        f"You can work the person's Android phone ({_phone_words(data)}) with the "
        "`mcp__sparsh__` tools, when they ask for something done on it.",
        "1. `mcp__sparsh__look` shows the screen as numbered lines. Act BY NUMBER: "
        "`tap`, `type_text` (`into` a field), `scroll` (`on` a list). Every action "
        "returns the new screen -- read it before the next step instead of looking again.",
        "2. `open_app` starts an app by name; `press_key` back or home leaves one.",
        "3. A step that comes back \"NOT DONE -- this needs the person's yes\" was held "
        "(sending, paying, deleting, a password ...). Tell the person in one sentence "
        "what it will do, then call `mcp__sparsh__confirm` with its hold: they are asked. "
        "Never try to get round a hold another way.",
        "4. \"The screen changed\" means nothing was done: use the screen it gives you.",
    ]
    return "\n".join(lines)


def classify(tool: Any, kinds: dict[str, str]) -> str | None:
    """Set one wrapped tool's asking by Sparsh's kind for it; returns the
    kind, or None for a tool Sparsh didn't name (it keeps its hint)."""
    kind = kinds.get(tool.raw_name)
    if kind not in KINDS:
        return None
    # "act" runs unasked because the phone's rules hold what is risky
    # (module docstring) -- not because it changes nothing.
    tool.read_only = kind in ("read", "act")
    tool.always_ask = kind == "confirm"
    return kind


def explain_confirm(tool: Any) -> Any:
    """The card for ``confirm``: Sparsh's own words for the held step."""
    session = tool.session

    def card(args: dict[str, Any]) -> str:
        hold = str(args.get("hold") or "")
        try:
            said, failed = session.call_tool("describe_hold", {"hold": hold})
        except Exception as exc:             # the card must still show
            said, failed = str(exc), True
        if failed:
            return (f"Do a step on the phone that was held for your yes ({hold}).\n"
                    f"  SPARSH CANNOT DESCRIBE IT: {said}")
        return "Do this on the phone?\n" + said
    return card


@dataclass(slots=True)
class Sparsh:
    """One session's link to Sparsh."""

    mode: str
    path: str | None = None
    data: dict[str, Any] | None = None
    program: str | None = None
    error: str = ""
    tools: list[str] = field(default_factory=list)

    def connect(self, manager: Any, agent: Any) -> int:
        """Start the server, set what asks, write the prompt layer.
        Returns how many tools it brought. Raises MCPError."""
        from yantra.prompt import attach_prompt

        assert self.data is not None
        if SERVER not in manager.sessions:
            self.tools = [n for n in manager.connect(server_config(self.data), origin="sparsh")
                          if n in agent.registry]
        else:
            # configured by hand (--mcp-config): recognised, and classed
            self.tools = [n for n in agent.registry.names()
                          if n.startswith(f"mcp__{SERVER}__")]
        kinds = self.data.get("tools") or {}
        for name in self.tools:
            tool = agent.registry.get(name)
            if classify(tool, kinds) == "confirm":
                tool.explain = explain_confirm(tool)
        prompt = attach_prompt(agent)
        prompt.set("phone", prompt_text(self.data))
        prompt.apply()
        return len(self.tools)

    def refresh(self) -> dict[str, Any] | None:
        """Ask Sparsh again -- a phone plugged in or taken away since the
        start shows up. A page asking is the person asking: not found is
        an error here, even in auto."""
        try:
            found = load("on" if self.mode == "auto" else self.mode, self.path or self.program)
        except SparshLinkError as exc:
            self.error = str(exc)
            return self.data
        if found is not None:
            self.data, self.program = found
            self.error = ""
        return self.data

    def describe(self) -> dict[str, Any]:
        """For the page's header and panel: phones, rules, never a screen."""
        data = self.data or {}
        rules = data.get("rules") or {}
        return {"mode": self.mode, "found": self.data is not None, "error": self.error,
                "program": self.program, "version": data.get("version"),
                "adb": data.get("adb"), "phones": list(data.get("phones") or []),
                "ready": [p.get("serial") for p in ready_phones(data)],
                "rules": {"path": rules.get("path"), "exists": bool(rules.get("exists")),
                          "never": list(rules.get("never") or []),
                          "ask": list(rules.get("ask") or [])},
                "tools": len(self.tools)}

    def peek(self, serial: str, shot: bool = True) -> dict[str, Any]:
        """The screen of one phone as it is now, for the person: Sparsh's
        list as JSON and, with ``shot``, the screenshot as base64 PNG.
        Never remembered as the agent's last look (module docstring)."""
        if self.data is None or not self.program:
            raise SparshLinkError(self.error or "Sparsh was not found")
        if not SERIAL_RE.match(serial or ""):
            raise SparshLinkError(f"which phone? {serial!r} is not a phone's serial")
        argv = [self.program, "look", "--peek", "--json", "--serial", serial]
        if self.data.get("state"):
            argv += ["--state", str(self.data["state"])]
        with tempfile.TemporaryDirectory(prefix="yantra-peek-") as tmp:
            png = Path(tmp) / "screen.png"
            if shot:
                argv += ["--shot", str(png)]
            try:
                done = subprocess.run(argv, capture_output=True, text=True,
                                      timeout=PEEK_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SparshLinkError(f"sparsh look: {exc}") from None
            if done.returncode != 0:
                why = (done.stderr or done.stdout).strip().splitlines()
                raise SparshLinkError(why[-1].removeprefix("sparsh: ") if why
                                      else "sparsh look failed")
            try:
                screen = json.loads(done.stdout)
            except json.JSONDecodeError:
                raise SparshLinkError("sparsh look did not print JSON") from None
            image = (base64.b64encode(png.read_bytes()).decode()
                     if shot and png.exists() else None)
        return {"serial": serial, "screen": screen, "shot": image}
