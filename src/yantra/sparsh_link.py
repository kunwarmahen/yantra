"""The person's phone -- through Sparsh, found at startup.

Sparsh (a separate project) works an Android phone or the emulator
through ``adb``, or an iPhone through WebDriverAgent: it shows the
screen as a numbered list a model can read without seeing a picture,
and taps, types and scrolls by number. It ships the agent's tools as an
MCP server (``sparsh mcp``); this module finds it, starts it, and
decides what a person is asked about.

FOUND LIKE SAMAY, BY THE SAME KIND OF CONTRACT. ``sparsh status --json``
prints what Sparsh is: its state folder, the phones ``adb`` sees, the
person's rules, the command that starts the tools, and each tool's
kind. Its ``format`` is the whole contract; an unknown one is refused
rather than guessed at. ``YANTRA_SPARSH`` or the flags choose:

    auto  (default) use Sparsh if `sparsh` is on PATH AND a phone is
          attached; say nothing if not -- and with Sparsh but no phone,
          wait DORMANT: no tools, no prompt, until a phone is attached and
          the person says to use it (the page's panel, or /phone use)
    on    use it, and say so loudly if it cannot be found (--sparsh)
    off   never look (--no-sparsh)
    PATH  the sparsh program to run, which also means "on" (--sparsh PATH)
    auto:PATH  auto, with this sparsh program instead of the one on PATH
          (what Sarathi passes: found by it, still dormant with no phone)

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
hold id. That card is a sentence and the screen as the step was held,
what it would tap ringed -- never the model's numbered list, which on
a real phone was forty-one lines of a dialler's keys. A tool Sparsh
doesn't name keeps the server's hint.

THE PAGE PEEKS, IT DOESN'T LOOK. The phone panel shows the phones, the
rules in force, and on request the screen as it is now -- the list and
a screenshot -- through ``sparsh look --peek``, a separate program run
that leaves the agent's last look alone. Had it used a plain look, a
person glancing at the panel mid-turn would renumber the screen under
the agent, and its next "tap 7" would be checked against the person's
7. The panel shows everything, apps on the ``never`` list included: it
is the person's own eyes, and nothing it reads reaches the model.

SCREENSHOTS ONLY WHERE THE LIST FALLS SHORT, AND NOT TO A CLOUD MODEL
UNASKED. The model works from the numbered list. A screen that gives
the list nothing (Settings' About page, which never goes still; an app
drawn as one picture) or only part (Google Maps' places, unnamed boxes)
can come with a screenshot, and the model can ask for one (``look``
with ``picture``): Sparsh attaches it when started with ``--shots``,
and the tools here pass it on to the model. ``YANTRA_PHONE_SHOTS``
decides whether that happens:

    auto  (default) yes for a local model that says it can see (Ollama's
          ``/api/show`` lists "vision"); no for a cloud model -- a
          phone's screen is the person's messages, names and codes
    on    yes, cloud models included: the person's own choice
    off   never

SETU'S PHONE CONNECTIONS ARE RULES FOR AN APP. A site connected on Setu's
phone road (``setu connect x --phone``) reaches the phone through its
own app; ``app_rules_of`` turns those connections into Sparsh's per-app
rules (``SPARSH_APP_RULES``: refuse, ask, pace), handed over when the
server starts. They only tighten. A connection made after the start
reaches the phone at the next one.

It is asked again on every call, so a switch to a cloud model mid-session
stops the pictures (a switch to a local one doesn't start them: Sparsh
was started without ``--shots``).

A HELD STEP SHOWS THE PERSON WHERE. On a screen that came with a
picture the model may ``tap_at`` a spot on it; Sparsh holds that every
time (and any typing there). Every held step comes through ``confirm``,
whose card carries the screen with what it would tap ringed -- for a
tap by position, the spot -- from ``describe_hold``
(``PermissionRequest.picture``). The person answers by looking. That
picture is the person's: it goes on the card, never to the model.

NOT WITH NOBODY WATCHING, FROM HERE. A run Yantra starts with nobody
watching (``--unattended``) gets no phone: everything it may do by
itself would be done on a phone no one is looking at, and a held step
could only be refused. A host that checks the phone is free and can
reach its person (Dvara) may give a scheduled run the phone, with the
steps its person granted (``grants``).
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
from urllib.parse import urlparse
from dataclasses import dataclass, field
from typing import Any

import httpx

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
SERIAL_RE = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._:-]{0,63}"
                       r"|https?://[A-Za-z0-9.:\[\]-]{1,80}/?)$")  # or an iPhone's WDA
#: How long a peek may take: a dump (~2.5 s) and a screenshot.
PEEK_TIMEOUT = 30.0
#: Sparsh's name for the per-app rules a harness adds (Setu's phone road).
APP_RULES = "SPARSH_APP_RULES"
#: Sparsh's name for a schedule's grants (held taps it may do unasked).
GRANTS = "SPARSH_GRANTS"
#: auto / on / off: screenshots of unreadable screens to the model.
SHOTS_ENV = "YANTRA_PHONE_SHOTS"
#: How long asking the local server whether a model can see may take.
SEE_TIMEOUT = 3.0


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
    if raw.lower().startswith("auto:") and raw[5:].strip():
        return "auto", os.path.expanduser(raw[5:].strip())
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


def load(mode: str, path: str | None = None,
         need_phone: bool = True) -> tuple[dict[str, Any], str] | None:
    """(Sparsh's report, the program asked). None when off, or when it is
    ``auto`` and Sparsh -- or, with ``need_phone``, a phone -- is simply
    not there; an error when it was asked for and cannot be used."""
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
    if need_phone and mode == "auto" and not ready_phones(data):
        return None                   # nothing to work: no tools, no prompt
    return data, program


def server_config(data: dict[str, Any], shots: bool = False,
                  app_rules: dict[str, Any] | None = None,
                  grants: list[str] | None = None) -> MCPServerConfig:
    """``grants``: held taps a schedule may do without a yes, as the person
    accepted them ("send in Messages when the screen shows 555-0123"),
    for a run nobody watches -- Sparsh checks each one (its rules.py)."""
    mcp = data["mcp"]
    args = list(mcp.get("args") or [])
    if shots and data.get("shots"):
        args.append(str(data["shots"]))
    env = {APP_RULES: json.dumps(app_rules)} if app_rules else {}
    if grants:
        env[GRANTS] = json.dumps(list(grants))
    return MCPServerConfig(name=SERVER, command=mcp["command"], args=args, env=env or None)


def app_rules_of(agent: Any) -> dict[str, Any]:
    """Setu's phone connections, as Sparsh's per-app rules (setu_link)."""
    setu = getattr(agent, "setu", None)
    if setu is None or getattr(setu, "link", None) is None:
        return {}
    from yantra.setu_link import phone_rules

    return phone_rules(setu.link, setu.allow)


def shots(provider: str, base_url: str, model: str,
          setting: str | None = None) -> tuple[bool, str]:
    """(send screenshots of unreadable screens to this model?, why), in
    words for the startup line. See SCREENSHOTS in the module docstring."""
    raw = (setting if setting is not None else os.environ.get(SHOTS_ENV, "")).strip().lower()
    raw = raw or "auto"
    if raw == "off":
        return False, f"screenshots off ({SHOTS_ENV}=off)"
    if raw == "on":
        return True, f"screenshots on ({SHOTS_ENV}=on)"
    if raw != "auto":
        return False, f"screenshots off ({SHOTS_ENV}={raw!r} is not auto, on or off)"
    if not _is_local(provider, base_url):
        return False, f"no screenshots to a cloud model ({SHOTS_ENV}=on to allow)"
    if not _can_see(base_url, model):
        return False, f"no screenshots: {model} can't see pictures"
    return True, "screenshots on (local model)"


def _is_local(provider: str, base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return provider == "ollama" or host in ("localhost", "127.0.0.1", "::1")


def _can_see(base_url: str, model: str) -> bool:
    """Whether a local Ollama model lists "vision" among its capabilities.
    Unknown (another server, no answer) is no: a picture sent to a model
    that can't take one fails the turn."""
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        response = httpx.post(f"{root}/api/show", json={"model": model},
                              timeout=SEE_TIMEOUT)
        return "vision" in (response.json().get("capabilities") or [])
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return False


def _phone_words(data: dict[str, Any]) -> str:
    phones = ready_phones(data)
    if not phones:
        why = data.get("adb")
        return ("no phone attached" if why in (None, "ok") else f"no phone ({why})")
    return ", ".join(f"{p.get('serial')} ({p.get('model') or '?'})" for p in phones)


def _is_iphone(phone: dict[str, Any]) -> bool:
    # Sparsh names an iPhone by its WebDriverAgent's address.
    return str(phone.get("serial") or "").startswith(("http://", "https://"))


def _kind_words(phones: list[dict[str, Any]]) -> str:
    kinds = {"iPhone" if _is_iphone(p) else "Android phone" for p in phones}
    return kinds.pop() if len(kinds) == 1 else "phones"


def announce(data: dict[str, Any], tools: int, program: str, shots: str = "") -> str:
    """The one startup line: tools, which phone, and the apps kept out."""
    never = (data.get("rules") or {}).get("never") or []
    kept = f"; kept out of {', '.join(never)}" if never else ""
    # Sparsh's own sentence when an iPhone's signature is about to run
    # out: said here, at the start, and never as a question.
    soon = ((data.get("wda") or {}).get("note") or "").strip()
    soon = f"; {soon}" if soon else ""
    shots = f"; {shots}" if shots else ""
    return (f"sparsh: {tools} tool(s); phone {_phone_words(data)}{kept}{shots}{soon}"
            f" -- via {program}")


def prompt_text(data: dict[str, Any], shots: bool = False) -> str:
    """The ``phone`` layer: the tools exist, and how to use them well."""
    phones = ready_phones(data)
    lines = ["# The person's phone (through Sparsh)"]
    if not phones:
        lines.append("The `mcp__sparsh__` tools work a phone (Android or iPhone), but "
                     "none is ready right now: if asked to use it, say the phone needs "
                     "connecting -- an Android phone plugged in with USB debugging on, "
                     "the emulator started, or an iPhone with WebDriverAgent running.")
        return "\n".join(lines)
    lines += [
        f"You can work the person's {_kind_words(phones)} ({_phone_words(data)}) with the "
        "`mcp__sparsh__` tools, when they ask for something done on it.",
        "1. `mcp__sparsh__look` shows the screen as numbered lines. Act BY NUMBER: "
        "`tap`, `type_text` (`into` a field), `scroll` (`on` a list). Every action "
        "returns the new screen -- read it before the next step instead of looking again.",
        "2. `open_app` starts an app by name; `press_key` back or home leaves one.",
        "3. A step that comes back \"NOT DONE -- this needs the person's yes\" was held "
        "(sending, paying, deleting, a password ...). Tell the person in one sentence "
        "what it will do, then call `mcp__sparsh__confirm` with its hold IN THE SAME "
        "ANSWER: that call is the question, and they answer it with a button. Don't ask "
        "in words and wait for their reply -- by then the hold is gone. "
        "Never try to get round a hold another way. When `confirm` comes back "
        "\"Done:\", the step WAS carried out; what follows is the phone's own answer "
        "to it (\"Turn off airplane mode to make a call\") -- tell the person that, "
        "never that the hold lapsed.",
        "4. \"The screen changed\" means nothing was done: use the screen it gives you.",
    ]
    if shots:
        lines.append("A screen the list can't read, or reads only in part (unnamed rows, "
                     "a map, a blank web page), comes with a screenshot: read what you "
                     "need from it. If the list seems to be missing what you need, "
                     "`look` with `picture` true asks for one -- don't open rows one by "
                     "one to find out what they are. To act on what only the picture "
                     "shows, `tap_at` its spot -- x and y from 0 to 1000 across and down "
                     "the picture. It is held for the person, who sees the spot ringed.")
    if any(_is_iphone(p) for p in phones):
        lines.append("5. An iPhone has no Back key: `press_key` back swipes in from the "
                     "left edge. If the screen doesn't change, tap the app's own Back, "
                     "Cancel or Close instead.")
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
    """The card for ``confirm``: Sparsh's own words for the held step --
    and, for a step on a screen with no list, its picture, which is set
    as the tool's ``card_picture`` (asked right after the words)."""
    session = tool.session
    pictures: dict[str, Any] = {}

    def card(args: dict[str, Any]) -> str:
        hold = str(args.get("hold") or "")
        pictures.clear()
        try:
            said, failed, images = session.call_tool_full(
                "describe_hold", {"hold": hold}, images=True)
        except Exception as exc:             # the card must still show
            said, failed, images = str(exc), True, []
        if failed:
            return (f"Do a step on the phone that was held for your yes ({hold}).\n"
                    f"  SPARSH CANNOT DESCRIBE IT: {said}")
        if images:
            pictures[hold] = images[0]
            said += "\n(The picture shows the phone's screen; what it would tap is ringed.)"
        return "Do this on the phone?\n" + said

    tool.card_picture = lambda args: pictures.get(str(args.get("hold") or ""))
    tool.nothing_to_ask = gone(session)
    return card


def gone(session: Any) -> Any:
    """``confirm``'s ``nothing_to_ask`` (permissions.py): a hold Sparsh no
    longer has is refused to the model, not asked of the person. A hold
    lives only as long as the server that made it -- Dvara stops its
    servers when a turn ends -- so a yes sought in words, in a later
    message, names one that is gone."""

    def check(args: dict[str, Any]) -> str | None:
        hold = str(args.get("hold") or "")
        said, failed, _ = session.call_tool_full("describe_hold", {"hold": hold})
        if not (failed and "no step is waiting" in said):
            return None
        return (f"Not asked, and nothing was done: {said}. A held step lasts only "
                "while these tools run, which can end with your answer. Do the step "
                "again, and when it is held call confirm in that same answer -- the "
                "confirm is the question; don't ask in words first.")

    return check


@dataclass(slots=True)
class Sparsh:
    """One session's link to Sparsh."""

    mode: str
    path: str | None = None
    data: dict[str, Any] | None = None
    program: str | None = None
    error: str = ""
    tools: list[str] = field(default_factory=list)
    #: False while DORMANT: Sparsh is here but no phone was at the start,
    #: so there are no tools and no prompt yet (``use_now``).
    connected: bool = False
    #: Screenshots of unreadable screens to the model, and why (``shots``).
    shots: bool = False
    shots_why: str = ""

    def connect(self, manager: Any, agent: Any, grants: list[str] | None = None) -> int:
        """Start the server, set what asks, write the prompt layer.
        Returns how many tools it brought. Raises MCPError. ``grants``:
        a schedule's, for a run whose harness (Dvara) let it have the phone."""
        from yantra.prompt import attach_prompt

        assert self.data is not None
        if SERVER not in manager.sessions:
            config = server_config(self.data, self.shots, app_rules_of(agent), grants)
            self.tools = [n for n in manager.connect(config, origin="sparsh")
                          if n in agent.registry]
            if self.sees():
                for name in self.tools:
                    agent.registry.get(name).images = self._still_allowed(agent)
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
        prompt.set("phone", prompt_text(self.data, self.sees()))
        prompt.apply()
        self.connected = True
        return len(self.tools)

    def _still_allowed(self, agent: Any) -> Any:
        """Asked on every call: the model can change mid-session (/model,
        /provider, the page), and a switch to a cloud model must stop the
        pictures. Only the answer for the model in use is kept."""
        said: dict[tuple[str, str, str], bool] = {}

        def allowed() -> bool:
            provider = agent.provider
            road = (getattr(provider, "name", ""),
                    getattr(getattr(provider, "settings", None), "base_url", ""),
                    str(agent.model))
            if road not in said:
                said.clear()
                said[road] = shots(*road)[0]
            return said[road]
        return allowed

    def sees(self) -> bool:
        """Whether the model is shown screenshots (this Sparsh can send them)."""
        return self.shots and bool((self.data or {}).get("shots"))

    def use_now(self, manager: Any, agent: Any) -> str:
        """A phone attached after the start: ask Sparsh again and, with a
        phone ready, start the tools now. Returns the startup line it
        would have printed. Raises SparshLinkError (nothing to use) and
        MCPError (the server would not start)."""
        self.refresh()
        if self.data is None:
            raise SparshLinkError(self.error or "Sparsh was not found")
        if self.connected:
            return f"sparsh: already in use -- phone {_phone_words(self.data)}"
        if not ready_phones(self.data):
            raise SparshLinkError("no phone is ready: plug one in with USB debugging on "
                                  "(and allow it on the phone), or start the emulator")
        count = self.connect(manager, agent)
        return announce(self.data, count, self.program or "sparsh", self.shots_words())

    def shots_words(self) -> str:
        if self.shots and not (self.data or {}).get("shots"):
            return "no screenshots: this Sparsh can't send them (update it)"
        return self.shots_why

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
                "tools": len(self.tools), "connected": self.connected}

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

    def recent(self, serial: str, n: int = 20) -> list[dict[str, Any]]:
        """What was done on one phone, newest first (``sparsh log``): the
        agent's steps and the person's own, held and refused ones too."""
        if self.data is None or not self.program:
            raise SparshLinkError(self.error or "Sparsh was not found")
        if not SERIAL_RE.match(serial or ""):
            raise SparshLinkError(f"which phone? {serial!r} is not a phone's serial")
        argv = [self.program, "log", "--json", "-n", str(max(1, min(int(n), 200))),
                "--serial", serial]
        if self.data.get("state"):
            argv += ["--state", str(self.data["state"])]
        try:
            done = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=STATUS_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SparshLinkError(f"sparsh log: {exc}") from None
        if done.returncode != 0:
            why = (done.stderr or done.stdout).strip().splitlines()
            raise SparshLinkError(why[-1].removeprefix("sparsh: ") if why
                                  else "sparsh log failed")
        try:
            return json.loads(done.stdout)
        except json.JSONDecodeError:
            raise SparshLinkError("sparsh log did not print JSON") from None
