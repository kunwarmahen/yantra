"""Telling the person when something happens -- through Prahari, found at
startup.

Prahari (a separate project) watches for what a person is waiting for:
a price under a line, a mail from the bank, a page that changed. It
looks with NO MODEL -- one tool call, ``yantra --call`` (cli/main.py,
notes/139) -- and wakes the agent only when the thing happens. What it
needs from an agent is the RECIPE (which tool to look with, what to pick
out of its answer, when that matters, how often) and the person's yes.
Prahari ships the tools for that as an MCP server (``prahari mcp --for
WHO``); this module finds it, starts it, and gives each write a card in
words.

FOUND LIKE SAMAY, BY THE SAME CONTRACT. ``prahari status --json`` prints
what Prahari is; its ``format`` is the whole contract, and an unknown
one is refused rather than guessed at. ``YANTRA_PRAHARI`` or the flags
choose: ``auto`` (default: use it if ``prahari`` is on PATH, say nothing
if not), ``on`` (--prahari: loudly if missing), ``off`` (--no-prahari),
or a PATH. An unattended run never looks: nobody is there to say yes.

ONE SERVER, FOR ONE PERSON, as for Samay: ``--for`` the person this
agent serves (``local`` here) and ``--agent`` the package this session
runs, so a watch made while talking to your mail agent looks with, and
wakes, your mail agent. A host serving several people (Dvara) makes a
``Prahari`` per turn with that turn's person and road.

THE CARD SAYS WHAT A YES COVERS. A ``create_watch`` arrives as a
permission card, and its raw arguments -- a tool name, JSON arguments, a
pick pattern -- are not something a person can say yes to with their
eyes open. ``explain_create`` puts it in words: how often it looks and
with what call, AND WHETHER THAT CALL ONLY READS -- because a yes to a
watch is a yes to running exactly that call on a timer, and a call that
is not read-only (a fetch of an address, anything that changes
something) is said in capitals; when it tells you; and what the agent
is asked to do then, with what it may use unasked.

THE MODEL IS TOLD, BRIEFLY. A ``watches`` prompt layer says the tools
exist, how a watch differs from a schedule, to preview first and fix the
pick from what the tool actually said, and to create only after a yes.

THE PAGE'S PANEL USES THE PROGRAM, AS SAMAY'S DOES. ``prahari list
--json``, ``looks``, ``pause``, ``resume`` and ``rm`` work whether
``prahari serve`` is running or not and need no token: the Watches panel
on Yantra's page is drawn from the same JSON as Prahari's own page
(``Prahari.call``). Look now starts ``prahari look`` and does not wait --
a look may take minutes on a page like WhatsApp Web -- and what it saw
shows in the watch's looks. ``seen`` is the panel's chip: what this
person's watches fired on lately, from Prahari's own record, so it is
the same whether or not anything was sent anywhere.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Any

from yantra.mcp import MCPServerConfig
from yantra.samay_link import WORD_RE, _grant, _setu_reach, match_tools

ENV = "YANTRA_PRAHARI"
FORMAT = "prahari.status.v1"
#: The MCP server's name; its tools are ``mcp__prahari__*``.
SERVER = "prahari"
LOCAL = "local"
STATUS_TIMEOUT = 15.0
#: How long a preview may take: it LOOKS, and Prahari gives a look 180s
#: (its looking.LOOK_LIMIT) -- a page like WhatsApp Web takes most of a
#: minute. The 30s every other MCP call gets would cut it off.
LOOK_WAIT = 200.0
#: Prahari's tools that look before they answer.
LOOKING = ("preview_watch",)
#: How much of a then-prompt the card shows.
THEN_SHOWN = 240
SHOWN = 6
#: The panel's verbs, and whether each names one watch.
VERBS = {"list": False, "looks": True, "pause": True, "resume": True, "rm": True}
#: How often, as the panel's box takes it: "30m", "3h", "1d", "90 min".
EVERY_RE = re.compile(r"^\d{1,4}\s*[a-z]{1,8}$")
#: The looks a person should hear about on the page: it mattered, or it
#: needs them. A quiet look, a learning one and a broken one are left out
#: (a watch broken too often pauses, and its row says why).
SEEN = ("fired", "limited", "needs_person")

#: A test in the words the card uses (the value goes in {}).
WHEN_WORDS = {"below": "is below {}", "above": "is above {}",
              "at_most": "is {} or less", "at_least": "is {} or more",
              "equals": "is {!r}", "contains": "says {!r}", "lacks": "stops saying {!r}",
              "matches": "matches {!r}", "changed": "changes", "new": "has something new",
              "any": "comes in", "ask": "makes a model say yes to {!r}"}


class PrahariLinkError(Exception):
    """Prahari was asked for and could not be used."""


def resolve_mode(flag: str | None, env: str | None = None) -> tuple[str, str | None]:
    """(mode, prahari path). A flag outranks the environment; a path means on."""
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
        raise PrahariLinkError(f"no prahari program at {path}") from None
    except subprocess.TimeoutExpired:
        raise PrahariLinkError(f"`{path} status --json` took longer than "
                               f"{int(STATUS_TIMEOUT)}s") from None
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip().splitlines()
        raise PrahariLinkError(f"`{path} status --json` failed: "
                               f"{said[-1] if said else done.returncode}")
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError:
        raise PrahariLinkError(f"`{path} status --json` did not print JSON") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise PrahariLinkError(f"prahari answered in a format this Yantra does not know "
                               f"({data.get('format') if isinstance(data, dict) else '?'}; "
                               f"want {FORMAT})")
    return data


def load(mode: str, path: str | None = None) -> tuple[dict[str, Any], str] | None:
    """(Prahari's report, the program asked). None when off, or when it is
    ``auto`` and simply not there; an error when it was asked for."""
    if mode == "off":
        return None
    program = path or shutil.which("prahari")
    if not program:
        if mode == "on":
            raise PrahariLinkError(f"Prahari was asked for but not found: put `prahari` "
                                   f"on PATH, or name it with {ENV}=/path/to/prahari")
        return None
    try:
        return _status(program), program
    except PrahariLinkError:
        if mode == "on" or path:
            raise
        return None


def server_config(data: dict[str, Any], person: str = LOCAL, agent: str = "",
                  runner: str = "") -> MCPServerConfig:
    if not WORD_RE.match(person):
        raise PrahariLinkError(f"{person!r} is not a person Prahari can keep watches for")
    mcp = data["mcp"]
    args = [*(mcp.get("args") or []), "--for", person]
    if agent:
        args += ["--agent", agent]
    if runner:
        args += ["--runner", runner]
    return MCPServerConfig(name=SERVER, command=mcp["command"], args=args)


def announce(data: dict[str, Any], tools: int, program: str) -> str:
    counts = data.get("watches") or {}
    state = ("it is watching" if data.get("serving")
             else "it is NOT running -- nothing is looked at until `prahari serve` is")
    return (f"prahari: {tools} tool(s), {counts.get('active', 0)} active watch(es); "
            f"{state} -- via {program}")


SEEN_HERE = ("The person can see, pause and delete watches on Prahari's page, or with "
             "`prahari list`.")
OFF_HERE = ("Prahari is not running on this computer: when you make a watch, say it "
            "will not look until the person starts `prahari serve`.")


def prompt_text(data: dict[str, Any], seen_at: str = SEEN_HERE,
                off: str = OFF_HERE) -> str:
    """The ``watches`` layer: the tools exist, and how to write a recipe."""
    lines = [
        "# Telling the person when something happens (through Prahari)",
        "When the person wants to know WHEN something happens -- a price drops under a "
        "line, a mail from someone arrives, a page or a status changes, something is "
        "back in stock -- offer a watch with the `mcp__prahari__` tools. A watch looks "
        "on its own with NO model and wakes you only when it matters. Something done AT "
        "A TIME -- a reminder at 6, a summary every morning -- is a schedule instead "
        "(`mcp__samay__`), not a watch: a watch's `every` is only how often it looks.",
        "1. The check is ONE call of a tool you have that READS, with exact arguments: "
        "a mail search with the sender in the query, a site's open, a page's address. "
        "Put everything that narrows it in the arguments.",
        "2. Call `mcp__prahari__preview_watch` first. If its pick found nothing, read the "
        "tool's answer it shows you and fix the pick (a pattern with a group, or a JSON "
        "path) before going on.",
        "3. Tell the person the sentence and what it sees now; only after they say yes, "
        "call `mcp__prahari__create_watch` with a `name` the person will know it by -- "
        "what and the line, \"Flight to Delhi under $571\", never the tool's name. "
        "Leave `then` out to just tell them what was "
        "seen (no model at all); give `then` only when there is work to do when it "
        "fires, written as a complete instruction to yourself.",
        "4. `every`: as often as the thing really changes -- mail 15m-1h, a price "
        "1h-6h, a page a day.",
        "5. For something that can tell Prahari itself -- an Android phone's "
        "notification (MacroDroid or Tasker), a Home Assistant automation, a webhook -- "
        "make a watch that listens (`listen`: one word) instead of a check, and give "
        "the person the address and their own key from `mcp__prahari__inlet_address`.",
        "6. Mail they want to hear about AT ONCE: a mailbox Prahari keeps open (the "
        "person adds it at the computer, `prahari mailbox add NAME`) knocks on its NAME "
        "with each new mail's sender and subject -- listen for it, or keep the mail "
        "search as the check and add `wake`: NAME so it looks the moment mail lands. "
        "Gmail's own push is `wake` plus `renew` with `mcp__gmail__watch_mailbox`, only "
        "when the person has a Pub/Sub topic for it.",
        "7. A watch that paused because its pick found nothing: preview a new pick and "
        "call `mcp__prahari__update_watch`.",
        seen_at,
    ]
    if not data.get("serving"):
        lines.append(off)
    return "\n".join(lines)


# ---- what the person is saying yes to -------------------------------------------


def _words(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _when_words(when: Any) -> str:
    if isinstance(when, dict) and len(when) == 1:
        (test, value), = when.items()
        shape = WHEN_WORDS.get(test)
        if shape is not None:
            return shape.format(value) if "{" in shape else shape
    return _words(when)


def _pick_words(pick: Any) -> str:
    if not pick:
        return "the whole answer"
    if isinstance(pick, dict) and len([k for k in pick if k != "key"]) == 1:
        kind = next(k for k in pick if k != "key")
        value = pick[kind]
        return {"number": f"the number {'found by ' + repr(value) if value else '(the first one)'}",
                "text": f"the text {'found by ' + repr(value) if value else ''}".strip(),
                "json": f"the value at {value}",
                "items": f"the items found by {value!r}",
                "items_json": f"the items at {value}"}.get(kind, _words(pick))
    return _words(pick)


def explain_create(args: dict[str, Any], *, registry: Any, setu: Any = None,
                   preview: str = "", where: str = "") -> str:
    """The permission card for ``create_watch``, in plain words.

    LAID OUT LIKE A FORM, as Samay's card is: what it looks at and how
    often, when it tells you, then what. Text, because it goes wherever a
    question goes (this page, a terminal, Telegram through Dvara).

    THE CALL IS SAID AS IT WILL RUN, AND WHETHER IT ONLY READS. A yes to a
    watch is a yes to running exactly that call, on a timer, with nobody
    asked again. A tool not marked read-only here -- a fetch of an address,
    anything that changes something -- is said in capitals, and a tool
    this agent doesn't have is too: it would fail every look.

    ``preview`` is Prahari's own words from ``preview_watch``, when the
    card could get them; ``where`` is where the news goes.
    """
    name = " ".join(str(args.get("name") or "").split()) or "(no name)"
    check = args.get("check") if isinstance(args.get("check"), dict) else {}
    listen = str(args.get("listen") or "").strip()
    lines = [f"New watch: {name}", ""]
    if listen:
        lines += ["What it listens for",
                  f"  anything sent to Prahari's inlet as '{listen}' (a phone app, a "
                  "webhook): read as text from outside"]
    else:
        tool = str(check.get("tool") or "?")
        call_args = check.get("args") or {}
        every = str(args.get("every") or "1h")
        lines += ["What it looks at, with no model",
                  f"  every {every}: {tool} {json.dumps(call_args, ensure_ascii=False)}"]
        if tool not in registry:
            lines.append("  NO TOOL BY THIS NAME in this agent -- every look would fail")
        elif not getattr(registry.get(tool), "read_only", False):
            lines.append(f"  {tool} IS NOT READ-ONLY: saying yes lets Prahari run exactly "
                         f"this call every {every}, with nobody asked again")
        wake = str(args.get("wake") or "").strip()
        if wake:
            lines.append(f"  and at once whenever '{wake}' knocks at Prahari's inlet (a "
                         "mailbox, Gmail's push): the knock only says look, it is not read")
        renew = args.get("renew") if isinstance(args.get("renew"), dict) else {}
        if renew:
            rtool = str(renew.get("tool") or "?")
            lines.append(f"  kept knocking by, now and once a day: {rtool} "
                         f"{json.dumps(renew.get('args') or {}, ensure_ascii=False)}")
            if rtool not in registry:
                lines.append("  NO TOOL BY THIS NAME in this agent -- the push would "
                             "never start")
            elif not getattr(registry.get(rtool), "read_only", False):
                lines.append(f"  {rtool} IS NOT READ-ONLY: saying yes lets Prahari run "
                             "exactly this call once a day, with nobody asked again")
    lines += ["Tells you when",
              f"  {_pick_words(args.get('pick'))} {_when_words(args.get('when'))}"]
    if preview:
        lines += [f"  now: {' '.join(preview.split())[:300]}"]
    then = " ".join(str(args.get("then") or "").split())
    if then:
        if len(then) > THEN_SHOWN:
            then = then[:THEN_SHOWN - 1] + "… (all of it under raw arguments)"
        lines += ["Then it asks me, with nobody watching", f"  {then}"]
        allow = args.get("allow_tools") or []
        allow = [allow] if isinstance(allow, str) else list(allow)
        lines.append("  may use without asking: " + (
            "; ".join(_allowed(allow, registry)) if allow else "only tools that read"))
        reach, _ = _setu_reach(setu)
        if reach:
            lines.append("  through Setu, it can read: " + "; ".join(reach))
        lines.append("  what the watch found is given to me as text from outside, "
                     "never as instructions")
    else:
        lines += ["Then", "  you get a line saying what it saw; no model is asked"]
    most = args.get("max_per_day") or 10
    lines += ["", f"At most {most} times a day." + (f" News goes {where}." if where
                                                     else "")]
    return "\n".join(lines)


def _allowed(allow: list[str], registry: Any) -> list[str]:
    out = []
    for pattern, hits in match_tools(allow, registry):
        grant = _grant(pattern)
        if grant is None:
            out.append(f"{pattern} (NOT A TOOL NAME -- refused)")
        elif not hits:
            out.append(f"{pattern} (NO TOOL BY THIS NAME)")
        else:
            shown = ", ".join(hits[:SHOWN]) + (f" +{len(hits) - SHOWN} more"
                                               if len(hits) > SHOWN else "")
            out.append(shown + (f" only with {grant.words()}" if grant.args else ""))
    return out


def explain_change(verb: str, args: dict[str, Any], listing: str = "") -> str:
    """The card for update / pause / resume / delete / allow: the watch, in
    its words, and what changes."""
    watch = str(args.get("id") or "?")
    found = next((line for line in listing.splitlines() if line.startswith(f"{watch} ")),
                 "")
    head = {"pause_watch": f"Pause watch {watch} (kept; resume any time)",
            "resume_watch": f"Resume watch {watch}: it looks again soon",
            "delete_watch": f"Delete watch {watch}, its looks and what it saw, for good",
            "update_watch": f"Change watch {watch}",
            "allow_for_watch": f"Let watch {watch}'s then-turn use without asking: "
                               + ", ".join(map(str, args.get("allow_tools") or []))
            }.get(verb, f"{verb} {watch}")
    lines = [head] + ([f"  {found}"] if found else [])
    if verb == "update_watch":
        for key in ("name", "check", "pick", "when", "every", "wake", "renew", "then",
                    "max_per_day"):
            if key in args:
                lines.append(f"  {key}: {_words(args[key])}")
        if {"check", "pick", "when", "every"} & set(args):
            lines.append("  its memory starts over: the next look only learns")
    return "\n".join(lines)


def let_go_of_check(registry: Any, args: dict[str, Any]) -> None:
    """Before Prahari LOOKS: close this session's own browser on the
    profile the check uses.

    Prahari looks with a second Yantra (``yantra --call``), and one
    browser may use a profile at a time. A chat that had just read
    WhatsApp to find the group held its profile for the rest of the turn,
    so every try waited 120s on a lock the chat itself held, and the
    person was told some other program had WhatsApp open. The check's
    next call from the chat simply opens it again."""
    check = args.get("check")
    name = check.get("tool") if isinstance(check, dict) else None
    if not isinstance(name, str) or name not in registry:
        return
    from yantra.tools.browser import BrowserSession

    tool = registry.get(name)
    for held in (getattr(tool, "session", None), getattr(tool, "browser", None)):
        if isinstance(held, BrowserSession):
            held.let_go()


def explain(tool: Any, registry: Any, setu_of: Any, where: Any = None) -> Any:
    """The ``explain`` hook for one of Prahari's write tools, or None."""
    verb = tool.raw_name
    session = tool.session

    def ask(name: str, args: dict[str, Any]) -> tuple[str, bool]:
        if name in LOOKING:
            let_go_of_check(registry, args)
        try:
            return session.call_tool(name, args,
                                     timeout=LOOK_WAIT if name in LOOKING else None)
        except Exception as exc:              # the card must still show
            return str(exc), True

    if verb == "create_watch":
        def card(args: dict[str, Any]) -> str:
            # What it sees now, in Prahari's words (a look with nothing
            # granted: only a tool that reads is tried).
            said = ""
            if not args.get("listen"):
                text, failed = ask("preview_watch", {k: args[k] for k in (
                    "check", "pick", "when", "every", "wake") if k in args})
                said = "" if failed else (text.splitlines()[1] if "\n" in text else "")
            return explain_create(args, registry=registry, setu=setu_of(), preview=said,
                                  where=where() if where is not None else "")
        return card
    if verb in ("update_watch", "pause_watch", "resume_watch", "delete_watch",
                "allow_for_watch"):
        def change(args: dict[str, Any]) -> str:
            listing, failed = ask("list_watches", {})
            return explain_change(verb, args, "" if failed else listing)
        return change
    return None


def looking_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a look started from the page runs in.

    A look is ``yantra --call``, and Prahari finds that Yantra by
    ``PRAHARI_YANTRA`` -- which ``prahari serve`` is given, and this
    page's process often is not. So when nothing names one, the look gets
    THIS Yantra, started where this page was: the same program, the same
    ``.env``, the same accounts the page reads with."""
    env = dict(os.environ if environ is None else environ)
    if not env.get("PRAHARI_YANTRA", "").strip():
        beside = os.path.join(os.path.dirname(sys.executable), "yantra")
        own = beside if os.path.exists(beside) else shutil.which("yantra")
        if own:
            env["PRAHARI_YANTRA"] = own
            env.setdefault("PRAHARI_YANTRA_HOME", os.getcwd())
    return env


# ---- the live handle ------------------------------------------------------------


@dataclass(slots=True)
class Prahari:
    """One session's link to Prahari."""

    mode: str
    path: str | None = None
    data: dict[str, Any] | None = None
    program: str | None = None
    person: str = LOCAL
    agent: str = ""
    runner: str = ""
    seen_at: str = SEEN_HERE
    off: str = OFF_HERE
    error: str = ""
    tools: list[str] = field(default_factory=list)

    def connect(self, manager: Any, agent: Any) -> int:
        """Start the server, give its writes their cards, write the prompt
        layer. Returns how many tools it brought. Raises MCPError."""
        from yantra.prompt import attach_prompt

        assert self.data is not None
        cfg = server_config(self.data, self.person, self.agent, self.runner)
        if manager.sessions.get(SERVER) is None:
            self.tools = [n for n in manager.connect(cfg, origin="prahari")
                          if n in agent.registry]
        else:
            self.tools = [n for n in agent.registry.names()
                          if n.startswith("mcp__prahari__")]
        for name in self.tools:
            tool = agent.registry.get(name)
            if tool.raw_name in LOOKING:
                tool.timeout = LOOK_WAIT
                tool.before = (lambda args, registry=agent.registry:
                               let_go_of_check(registry, args))
            hook = explain(tool, agent.registry, lambda: getattr(agent, "setu", None),
                           self.news_goes)
            if hook is not None:
                tool.explain = hook
                tool.run = self._then_refresh(tool.run)
        prompt = attach_prompt(agent)
        prompt.set("watches", prompt_text(self.data, self.seen_at, self.off))
        prompt.apply()
        return len(self.tools)

    def _then_refresh(self, run: Any) -> Any:
        """A write's run, followed by a fresh look at Prahari -- so the
        page's count is right when the turn ends, not at the next launch."""
        def wrapped(args: dict[str, Any], ctx: Any) -> Any:
            try:
                return run(args, ctx)
            finally:
                self.refresh()
        return wrapped

    def refresh(self) -> dict[str, Any] | None:
        """Ask Prahari again (the panel does, for whether it is watching and
        the counts). A page asking is the person asking: not found is said."""
        try:
            found = load("on" if self.mode == "auto" else self.mode,
                         self.path or self.program)
        except PrahariLinkError as exc:
            self.error = str(exc)
            return self.data
        if found is not None:
            self.data, self.program = found
            self.error = ""
        return self.data

    def call(self, verb: str, watch: str | None = None, *, limit: int = 20) -> Any:
        """One of the panel's verbs, through the prahari program, as JSON."""
        if verb not in VERBS:
            raise PrahariLinkError(f"no verb {verb!r}")
        if VERBS[verb] and (not watch or not WORD_RE.match(watch)):
            raise PrahariLinkError(f"which watch? {watch!r} is not an id")
        argv = [*self._base(), verb, *([watch] if VERBS[verb] else [])]
        if verb == "looks":
            argv += ["--limit", str(max(1, min(int(limit), 200)))]
        return self._json(argv, verb)

    def _json(self, argv: list[str], verb: str) -> Any:
        try:
            done = subprocess.run([*argv, "--json"], capture_output=True, text=True,
                                  timeout=STATUS_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PrahariLinkError(f"prahari {verb}: {exc}") from None
        if done.returncode != 0:
            why = (done.stderr or done.stdout).strip().splitlines()
            raise PrahariLinkError(why[-1].removeprefix("error: ") if why
                                   else f"prahari {verb} failed")
        try:
            return json.loads(done.stdout)
        except json.JSONDecodeError:
            raise PrahariLinkError(f"prahari {verb} did not print JSON") from None

    def seen(self, limit: int = 30) -> list[dict[str, Any]]:
        """What this person's watches saw lately that they should hear
        about, newest first: the page's chip. Each look with its watch's
        name and sentence."""
        listed = {w["id"]: w for w in self.call("list")
                  if isinstance(w, dict) and w.get("owner") == self.person}
        if not listed:
            return []
        try:
            looks = self._json([*self._base(), "looks", "--limit",
                                str(max(1, min(int(limit), 200)))], "looks")
        except PrahariLinkError:
            looks = []
        out = []
        for look in looks if isinstance(looks, list) else []:
            watch = listed.get(look.get("watch"))
            if watch is None or look.get("outcome") not in SEEN:
                continue
            out.append({**look, "name": watch.get("name", ""),
                        "sentence": watch.get("sentence", "")})
        return out

    def every(self, watch: str, step: str) -> Any:
        """How often a watch looks, changed from the panel: ``prahari edit
        ID --every STEP``, which keeps what it saw. Prahari says no to a
        step too short, in words."""
        if not WORD_RE.match(watch):
            raise PrahariLinkError(f"which watch? {watch!r} is not an id")
        step = step.strip()
        if not EVERY_RE.match(step):
            raise PrahariLinkError(f"{step!r} isn't how often: say 30m, 3h or 1d")
        return self._json([*self._base(), "edit", watch, "--every", step], "edit")

    def look_now(self, watch: str) -> None:
        """Start one look and do not wait for it: a look may take minutes,
        and Prahari records it whether or not this page is still open."""
        if not WORD_RE.match(watch):
            raise PrahariLinkError(f"which watch? {watch!r} is not an id")
        try:
            child = subprocess.Popen([*self._base(), "look", watch], env=looking_env(),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            raise PrahariLinkError(f"prahari look: {exc}") from None
        # reaped when it ends, so it never lingers as a zombie of this page
        threading.Thread(target=child.wait, daemon=True, name="yantra-prahari-look").start()

    def _base(self) -> list[str]:
        if self.data is None or not self.program:
            raise PrahariLinkError(self.error or "Prahari was not found")
        return [self.program, "--state", str(self.data.get("state") or "")]

    def describe(self) -> dict[str, Any]:
        """For the page's chip and panel: never a key (the inlet's is
        Prahari's own to show, on its page or by `prahari inlet`)."""
        data = self.data or {}
        return {"mode": self.mode, "found": self.data is not None, "error": self.error,
                "person": self.person, "serving": bool(data.get("serving")),
                "url": data.get("url"), "counts": dict(data.get("watches") or {}),
                "news": self.news_goes(), "version": data.get("version"),
                "tools": len(self.tools)}

    def news_goes(self) -> str:
        """Where a fire reaches the person, for the card and the panel. At
        this computer it is always on Yantra's page (the Watches panel reads
        Prahari's record), and through Dvara too when the running Prahari
        sends there."""
        data = self.data or {}
        if self.person != LOCAL:
            return "to your chat"
        if data.get("dvara") and data.get("local_to"):
            return (f"to {data['local_to']}'s chat through Dvara, and to Yantra's "
                    "page (its Watches panel)")
        return "to Yantra's page (its Watches panel) and Prahari's own (no Dvara set)"
