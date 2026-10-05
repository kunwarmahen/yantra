"""Doing things later -- through Samay, found at startup.

Samay (a separate project) keeps timed jobs: "check my mail every two
hours and tell me if anything needs me". It never does the work itself;
at each time it hands the prompt to a Yantra turn and writes down what
came back. What it needs from an agent is the offer -- "shall I do this
every morning?" -- and the person's yes. Samay ships the tools for that
as an MCP server (``samay mcp --for WHO``); this module finds it, starts
it, and makes sure the yes is given to words a person can read.

FOUND LIKE SETU, BY THE SAME CONTRACT. ``samay status --json`` prints
what Samay is: where its state lives, whether its clock is running, and
the command that starts the agent's tools. Its ``format`` is the whole
contract; an unknown one is refused rather than guessed at. There is no
import road, unlike Setu's: Samay's report needs its own state folder
and store opened first, which is Samay's business, so the command is
always run. ``YANTRA_SAMAY`` or the flags choose:

    auto  (default) use Samay if `samay` is on PATH; say nothing if not
    on    use it, and say so loudly if it cannot be found (--samay)
    off   never look (--no-samay)
    PATH  the samay program to run, which also means "on" (--samay PATH)

ONE SERVER, FOR ONE PERSON. It is registered as ``samay`` and started
with ``--for`` the person this agent serves -- ``local`` for a session at
this computer -- and ``--agent`` the package this session runs, so a
schedule made while talking to your mail agent runs your mail agent.
The model has no argument with which to name anybody else. A host that
serves several people passes its own ``person`` (``connect``'s
argument); Yantra still never learns who that host is.

THE CARD SAYS WHAT A YES COVERS. Samay marks its writes as writes, so a
``create_schedule`` arrives as a permission card -- and its raw
arguments (a prompt, a JSON ``when``, a list of globs) are not something
a person can say yes to with their eyes open. ``explain_create`` puts it
in words instead: when, in Samay's own sentence with the next times;
what will be done; when they will be told; which tools it may use
without asking, each glob shown with what it matches IN THIS AGENT --
and a name that matches nothing said in capitals, because a small model
makes tool names up; and what it can read without anybody asking,
which includes every account connected through Setu. A scheduled run
reaches what this agent reaches; the card is where a person hears that.

THE MODEL IS TOLD, BRIEFLY. A ``schedules`` prompt layer says the tools
exist, to preview first and say the sentence, and to create only after a
yes. When to offer is the model's call -- this is plumbing, and the
agent decides.

THE PAGE'S PANEL USES THE PROGRAM, NOT THE PAGE'S API. ``samay list
--json`` and its neighbours work whether ``samay serve`` is running or
not, need no token, and are the same JSON Samay's own page is drawn
from (``Samay.call``). Run-now starts ``samay run-now`` and does not
wait: a run is an agent turn, minutes long.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any

from yantra.mcp import MCPServerConfig

ENV = "YANTRA_SAMAY"
FORMAT = "samay.status.v1"
#: The MCP server's name; its tools are ``mcp__samay__*``.
SERVER = "samay"
#: The person on a session at this computer -- Samay's own word for it.
LOCAL = "local"
#: How long ``samay status --json`` and the panel's calls may take.
STATUS_TIMEOUT = 15.0
#: A schedule id, or a person, as it may reach an argv word: never an option.
WORD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:-]{0,63}$")
#: The verbs the panel may run, and what each needs.
VERBS = {"list": False, "runs": True, "pause": True, "resume": True, "rm": True}
#: notify, in the words the card uses.
NOTIFY_WORDS = {"when_new": "only when there is something new",
                "always": "after every run",
                "never": "never -- it is kept for you to read"}
#: How many matched tools the card names before it counts the rest.
SHOWN = 6


class SamayLinkError(Exception):
    """Samay was asked for and could not be used."""


def resolve_mode(flag: str | None, env: str | None = None) -> tuple[str, str | None]:
    """(mode, samay path). A flag outranks the environment; a path means on.

    ``flag`` is what the command line said: None (nothing), "off"
    (--no-samay), "on" (bare --samay) or a path (--samay PATH).
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
        raise SamayLinkError(f"no samay program at {path}") from None
    except subprocess.TimeoutExpired:
        raise SamayLinkError(f"`{path} status --json` took longer than "
                             f"{int(STATUS_TIMEOUT)}s") from None
    if done.returncode != 0:
        why = (done.stderr or done.stdout).strip().splitlines()
        raise SamayLinkError(f"`{path} status --json` failed: "
                             f"{why[-1] if why else done.returncode}")
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError:
        raise SamayLinkError(f"`{path} status --json` did not print JSON") from None
    if not isinstance(data, dict):
        raise SamayLinkError(f"samay ({path}) answered something that is not an object")
    if data.get("format") != FORMAT:
        raise SamayLinkError(f"samay ({path}) speaks {data.get('format')!r}; this Yantra "
                             f"understands {FORMAT!r} -- update one of them")
    mcp = data.get("mcp") or {}
    if not mcp.get("command"):
        raise SamayLinkError(f"samay ({path}) did not say how to start its tools")
    return data


def load(mode: str, path: str | None = None) -> tuple[dict[str, Any], str] | None:
    """(Samay's report, the program asked). None when off, or when it is
    ``auto`` and simply not there; an error when it was asked for."""
    if mode == "off":
        return None
    program = path or shutil.which("samay")
    if not program:
        if mode == "on":
            raise SamayLinkError(f"Samay was asked for but not found: put `samay` on PATH, "
                                 f"or name it with {ENV}=/path/to/samay")
        return None
    try:
        return _status(program), program
    except SamayLinkError:
        if mode == "on" or path:
            raise
        return None


def server_config(data: dict[str, Any], person: str = LOCAL,
                  agent: str = "") -> MCPServerConfig:
    """The MCP server, as Samay says to start it, for one person."""
    if not WORD_RE.match(person):
        raise SamayLinkError(f"{person!r} is not a person Samay can keep schedules for")
    mcp = data["mcp"]
    args = [*(mcp.get("args") or []), "--for", person]
    if agent:
        args += ["--agent", agent]
    return MCPServerConfig(name=SERVER, command=mcp["command"], args=args)


def announce(data: dict[str, Any], tools: int, program: str) -> str:
    """The one startup line: tools, schedules, and whether the clock runs."""
    counts = data.get("schedules") or {}
    clock = ("its clock is running" if data.get("serving")
             else "its clock is NOT running -- nothing runs on time until `samay serve` is")
    return (f"samay: {tools} tool(s), {counts.get('active', 0)} active schedule(s); "
            f"{clock} -- via {program}")


def prompt_text(data: dict[str, Any]) -> str:
    """The ``schedules`` layer: the tools exist, and how to offer."""
    lines = [
        "# Doing things later (through Samay)",
        "You can offer to do something later, or on a repeat, with the `mcp__samay__` "
        "tools -- when the person asks for something that has to happen at a time, or "
        "that is worth checking again. Not for every answer.",
        "1. Call `mcp__samay__preview_schedule` first and tell the person its sentence, "
        "what will be done, and when they will be told.",
        "2. Only after they say yes, call `mcp__samay__create_schedule`. Its `prompt` is "
        "read later by you with NOBODY watching: make it a complete instruction. In "
        "`allow_tools`, name only tools you actually have (exact names, or globs like "
        "`browser_*`).",
        "The person can see, pause and delete schedules on the web page's Schedules "
        "panel, or with `samay list`.",
    ]
    if not data.get("serving"):
        lines.append("Samay's clock is not running on this computer: when you make a "
                     "schedule, say it will not run until the person starts `samay serve`.")
    return "\n".join(lines)


# ---- what the person is saying yes to -------------------------------------------


def match_tools(patterns: list[str], registry: Any) -> list[tuple[str, list[str]]]:
    """Each allow_tools entry, with the tools of this agent it matches."""
    names = list(registry.names())
    return [(str(p), [n for n in names if fnmatch.fnmatchcase(n, str(p))])
            for p in patterns]


def _setu_reach(setu: Any) -> tuple[list[str], dict[str, str]]:
    """(the accounts this agent reaches through Setu, in words; tool name
    prefix -> the account(s) it acts on). Empty when Setu is off or found
    nothing; a package's session counts only what it was allowed. A
    connector with several accounts shares one set of tools, so its
    prefix names every one of them."""
    link = getattr(setu, "link", None)
    if link is None:
        return [], {}
    words: list[str] = []
    accounts: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for row in link.connections:
        if setu.allow is not None and row.get("connector") not in setu.allow:
            continue
        site = setu.sites.get(row.get("ref", ""))
        server = (row.get("mcp") or {}).get("name", "")
        if site is None and server not in setu.servers:
            continue                          # not started this session
        name = (link.connectors.get(row.get("connector")) or {}).get("name") \
            or row.get("connector", "")
        who = row.get("email") or row.get("account") or row.get("ref", "")
        words.append(f"{name} ({who})")
        own = [f"{site.prefix}_"] if site is not None else []
        own += [f"mcp__{server}__", f"mcp__{row.get('connector')}__"] if server else []
        for prefix in own:                    # the last is the merged tools' prefix
            names[prefix] = name
            accounts.setdefault(prefix, []).append(who)
    return words, {p: f"{names[p]} ({'; '.join(who)})" for p, who in accounts.items()}


def explain_create(args: dict[str, Any], *, registry: Any, setu: Any = None,
                   sentence: str = "", problem: str = "") -> str:
    """The permission card for ``create_schedule``, in plain words.

    ``sentence`` is Samay's own reading of ``when`` (preview_schedule);
    ``problem`` is why it could not read it, if it could not. What the
    run may do unasked comes before the prompt: a long prompt read first
    pushes the line that matters off the bottom of the card.
    """
    when = args.get("when")
    raw_when = json.dumps(when) if not isinstance(when, str) else when
    notify = str(args.get("notify") or "when_new")
    allow = args.get("allow_tools") or []
    allow = [allow] if isinstance(allow, str) else list(allow)
    lines = ["Save a schedule. At each time this agent runs it with NOBODY watching.",
             f"  when:      {sentence or raw_when}"]
    if problem:
        lines.append(f"             SAMAY CANNOT READ THIS 'when': {problem}")
    if args.get("tz"):
        lines.append(f"  time zone: {args['tz']}")
    lines.append(f"  tells you: {NOTIFY_WORDS.get(notify, notify)}")
    reach, prefixes = _setu_reach(setu)
    if allow:
        lines.append("  without asking, it may also use:")
        changes: dict[str, list[str]] = {}
        for pattern, hits in match_tools(allow, registry):
            if not hits:
                lines.append(f"    {pattern}: NO TOOL BY THIS NAME in this agent -- it "
                             f"matches nothing")
                continue
            shown = ", ".join(hits[:SHOWN]) + (f" +{len(hits) - SHOWN} more"
                                               if len(hits) > SHOWN else "")
            lines.append(f"    {pattern}: {shown}" if pattern not in hits
                         else f"    {pattern}")
            for name in hits:
                tool = registry.get(name)
                if getattr(tool, "always_ask", False):
                    lines.append(f"      {name} spends money: it is asked about every "
                                 f"time, and with nobody there it will be refused")
                    continue
                if getattr(tool, "read_only", False):
                    continue
                prefix = next((p for p in prefixes if name.startswith(p)), None)
                if prefix is not None:
                    changes.setdefault(prefixes[prefix], []).append(name[len(prefix):])
        for label, verbs in changes.items():
            lines.append(f"    THIS CHANGES THINGS IN YOUR {label} WITHOUT ASKING: "
                         f"{', '.join(dict.fromkeys(verbs))}")
    else:
        lines.append("  without asking, it may use only tools that read.")
    if reach:
        lines.append("  through Setu, it can read without asking: " + "; ".join(reach))
    lines.append("  Anything else that changes something is refused while nobody is there.")
    prompt = " ".join(str(args.get("prompt") or "").split())
    lines.append(f"  does:      {prompt or '(nothing -- no prompt given)'}")
    return "\n".join(lines)


def explain_change(verb: str, args: dict[str, Any], listing: str = "") -> str:
    """The card for pause / resume / delete: the schedule, in its words."""
    schedule = str(args.get("id") or "?")
    found = next((line for line in listing.splitlines() if line.startswith(f"{schedule} ")),
                 "")
    head = {"pause_schedule": f"Pause schedule {schedule} (it is kept; resume any time)",
            "resume_schedule": f"Resume schedule {schedule}, from now",
            "delete_schedule": f"Delete schedule {schedule} AND its history, for good"
            }.get(verb, f"{verb} {schedule}")
    return head + (f"\n  {found}" if found else "")


def explain(tool: Any, registry: Any, setu_of: Any) -> Any:
    """The ``explain`` hook for one of Samay's write tools (mcp.py), or
    None for the reads, which are never asked about."""
    verb = tool.raw_name
    session = tool.session

    def ask(name: str, args: dict[str, Any]) -> tuple[str, bool]:
        try:
            return session.call_tool(name, args)
        except Exception as exc:              # the card must still show
            return str(exc), True

    if verb == "create_schedule":
        def card(args: dict[str, Any]) -> str:
            preview = {"when": args.get("when")}
            if args.get("tz"):
                preview["tz"] = args["tz"]
            said, failed = ask("preview_schedule", preview)
            return explain_create(args, registry=registry, setu=setu_of(),
                                  sentence="" if failed else said,
                                  problem=said if failed else "")
        return card
    if verb in ("pause_schedule", "resume_schedule", "delete_schedule"):
        def change(args: dict[str, Any]) -> str:
            listing, failed = ask("list_schedules", {})
            return explain_change(verb, args, "" if failed else listing)
        return change
    return None


# ---- the live handle ------------------------------------------------------------


@dataclass(slots=True)
class Samay:
    """One session's link to Samay: how it was found, what it said, and
    for whom its server was started."""

    mode: str
    path: str | None = None
    data: dict[str, Any] | None = None
    program: str | None = None
    person: str = LOCAL
    agent: str = ""
    error: str = ""
    tools: list[str] = field(default_factory=list)

    def refresh(self) -> dict[str, Any] | None:
        """Ask Samay again (the panel does, for the clock and the counts).
        A page asking is the person asking: not found is an error here."""
        try:
            found = load("on" if self.mode == "auto" else self.mode,
                         self.path or self.program)
        except SamayLinkError as exc:
            self.error = str(exc)
            return self.data
        if found is not None:
            self.data, self.program = found
            self.error = ""
        return self.data

    def connect(self, manager: Any, agent: Any) -> int:
        """Start the server, give its writes their cards, write the prompt
        layer. Returns how many tools it brought. Raises MCPError."""
        from yantra.prompt import attach_prompt

        assert self.data is not None
        cfg = server_config(self.data, self.person, self.agent)
        existing = manager.sessions.get(SERVER)
        if existing is None:
            self.tools = list(manager.connect(cfg, origin="samay"))
        else:
            # configured by hand (--mcp-config): recognised, and carded
            self.tools = [n for n in agent.registry.names() if n.startswith("mcp__samay__")]
        for name in self.tools:
            tool = agent.registry.get(name)
            hook = explain(tool, agent.registry, lambda: getattr(agent, "setu", None))
            if hook is not None:
                tool.explain = hook
                tool.run = self._then_refresh(tool.run)
        prompt = attach_prompt(agent)
        prompt.set("schedules", prompt_text(self.data))
        prompt.apply()
        return len(self.tools)

    def _then_refresh(self, run: Any) -> Any:
        """A write's run, followed by a fresh look -- so the page's count
        is right when the turn ends, not at the next launch."""
        def wrapped(args: dict[str, Any], ctx: Any) -> Any:
            try:
                return run(args, ctx)
            finally:
                self.refresh()
        return wrapped

    def call(self, verb: str, schedule: str | None = None, *,
             limit: int = 20) -> Any:
        """One of the panel's verbs, through the samay program, as JSON."""
        if verb not in VERBS:
            raise SamayLinkError(f"no verb {verb!r}")
        if VERBS[verb] and (not schedule or not WORD_RE.match(schedule)):
            raise SamayLinkError(f"which schedule? {schedule!r} is not an id")
        argv = [*self._base(), verb, *([schedule] if VERBS[verb] else [])]
        if verb == "runs":
            argv += ["--limit", str(max(1, min(int(limit), 200)))]
        try:
            done = subprocess.run([*argv, "--json"], capture_output=True, text=True,
                                  timeout=STATUS_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SamayLinkError(f"samay {verb}: {exc}") from None
        if done.returncode != 0:
            why = (done.stderr or done.stdout).strip().splitlines()
            raise SamayLinkError(why[-1] if why else f"samay {verb} failed")
        try:
            return json.loads(done.stdout)
        except json.JSONDecodeError:
            raise SamayLinkError(f"samay {verb} did not print JSON") from None

    def run_now(self, schedule: str) -> None:
        """Start one run and do not wait for it: it is an agent turn, and
        Samay records it whether or not this page is still open."""
        if not WORD_RE.match(schedule):
            raise SamayLinkError(f"which schedule? {schedule!r} is not an id")
        try:
            child = subprocess.Popen([*self._base(), "run-now", schedule],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            raise SamayLinkError(f"samay run-now: {exc}") from None
        # reaped when it ends, so it never lingers as a zombie of this page
        threading.Thread(target=child.wait, daemon=True, name="yantra-samay-run").start()

    def _base(self) -> list[str]:
        if self.data is None or not self.program:
            raise SamayLinkError(self.error or "Samay was not found")
        return [self.program, "--state", str(self.data.get("state") or "")]

    def describe(self) -> dict[str, Any]:
        """For the page's header and panel: never a secret, by Samay's contract."""
        data = self.data or {}
        return {"mode": self.mode, "found": self.data is not None, "error": self.error,
                "person": self.person, "serving": bool(data.get("serving")),
                "url": data.get("url"), "counts": dict(data.get("schedules") or {}),
                "version": data.get("version"), "tools": len(self.tools)}
