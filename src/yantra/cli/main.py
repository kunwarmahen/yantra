"""CLI entrypoint: argparse -> configured Agent -> REPL or one-shot run."""

from __future__ import annotations

import argparse
import fnmatch
import sys
import time
from dataclasses import replace
from pathlib import Path

from rich.console import Console

from yantra.agent import Agent
from yantra.builder import BUILD_SYSTEM, BuildSpec, default_checks, run_build
from yantra.cli.render import Renderer, SubagentTee
from yantra.cli.repl import Repl, confirm_gate
from yantra.config import (
    _load_dotenv,
    browser_profile,
    default_env_context,
    default_model,
    default_tool_select,
    guess_provider,
    disabled_skill_patterns,
    disabled_tool_patterns,
    load_settings,
)
from yantra.errors import ConfigError, ImageError, ToolError, UserUnavailable
from yantra.images import load_image_block
from yantra.mcp import (MCPAuthRequired, MCPError, MCPHttpSession,
                         MCPManager, MCPServerConfig, load_mcp_configs,
                         load_remembered, remembered_path)
from yantra.mcp_oauth import TOKEN_FILE
from yantra.package import MANIFEST, load_package
from yantra.permissions import SwitchableGate, trust_sandbox, yolo
from yantra.providers import get_provider
from yantra.sandbox import autodetect
from yantra.spec import AgentSpec
from yantra.session import SessionStore, apply_payload
from yantra.subagent import SpawnSubagent, SubagentSpawner
from yantra.tools import default_registry
from yantra.tools.ask_user import AskUser, TerminalChannel
from yantra.tools.discover import package_tool_names
from yantra.tools.selector import (
    AUTO_SELECTION_THRESHOLD,
    DEFAULT_TOOLS_PER_TURN,
    enable_selection,
)





def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yantra",
        description="Yantra -- a from-scratch LLM agent harness (learning project).",
    )
    parser.add_argument("--provider",
                        choices=["anthropic", "openai", "responses", "ollama"],
                        help="defaults to whichever API key is set; 'responses' "
                             "speaks OpenAI's Responses API; 'ollama' runs "
                             "local models at localhost:11434 (no key)")
    parser.add_argument("--model", help="model slug (default from env / per-provider)")
    parser.add_argument("--system", help="system prompt")
    parser.add_argument("--max-iterations", type=int, default=None,
                        help="tool round-trips allowed per turn (default 25)")
    parser.add_argument("--context-window", type=int, default=None,
                        metavar="TOKENS",
                        help="model context window for auto-compaction "
                             "(default: per provider -- 200000 cloud, 8192 ollama)")
    parser.add_argument("--cwd", default=".", help="sandbox root for tools (default: here)")
    parser.add_argument("--cache", action="store_true",
                        help="prompt caching (anthropic dialect): mark the "
                             "request prefix with cache breakpoints so long "
                             "sessions bill cached tokens at ~0.1x")
    parser.add_argument("--agent", metavar="DIR",
                        help="run an AGENT PACKAGE: a directory with an "
                             "agent.toml (prompt, tools, skills, servers, "
                             "policy). Flags here override the package. "
                             "Omitted, ./agent.toml is used when present.")
    parser.add_argument("--yolo", action="store_true",
                        help="skip permission prompts -- tools run without asking")
    parser.add_argument("--sandbox", action="store_true",
                        help="confine bash with bubblewrap (no network, host "
                             "read-only, workspace-only writes); falls back "
                             "to the plain subprocess when bwrap is missing. "
                             "A confined bash is auto-approved by the gate.")
    parser.add_argument("--build", metavar="SPEC",
                        help="one-shot BUILD MODE: hand SPEC to a fresh agent "
                             "in a scratch workspace, then independently "
                             "re-verify the acceptance commands (unittest "
                             "gate). Exit 0 = build green, 1 = red. "
                             "Use --cwd to choose the workspace explicitly.")
    parser.add_argument("--browse-login", metavar="URL", dest="browse_login",
                        default=None,
                        help="one-time LOGIN SETUP for the browser_* tools: "
                             "opens a VISIBLE Chromium on the "
                             "$YANTRA_BROWSER_PROFILE directory (set it in "
                             ".env first), you log in yourself -- 2FA and "
                             "captchas included -- then close the window. "
                             "Every later headless session on that profile "
                             "starts logged-in. No model, no API key needed")
    parser.add_argument("--mcp-login", metavar="NAME", dest="mcp_login",
                        default=None,
                        help="one-time LOGIN for an authenticated MCP server: "
                             "runs the OAuth flow in your browser and saves "
                             "the token under ~/.local/state/yantra. NAME "
                             "must be a server in --mcp-config or already "
                             "remembered. No model, no API key needed")
    parser.add_argument("--prompt", help="one-shot mode: run this prompt and exit")
    parser.add_argument("--image", action="append", default=[], metavar="PATH",
                        help="attach an image (png/jpeg/gif/webp, <=5 MB) to "
                             "the one-shot prompt; repeat for several. "
                             "Requires --prompt / a positional PROMPT")
    parser.add_argument("--resume", action="store_true",
                        help="restore the newest checkpointed session "
                             "(.yantra/session.sqlite3 under --cwd) before "
                             "the first prompt")
    parser.add_argument("--mcp-config", action="append", default=[],
                        metavar="FILE",
                        help='MCP config file to connect (repeatable): '
                             '{"servers": {"name": {"command": ..., "args": [...]}}}. '
                             'Tools register as mcp__<server>__<tool>.')
    parser.add_argument("--subagents", action="store_true",
                        help="register spawn_subagent so the model can delegate "
                             "self-contained subtasks to fresh-context child "
                             "agents (budget: 5/session); child streams are "
                             "teed to the terminal live")
    parser.add_argument("--web", action="store_true",
                        help="serve a local browser UI instead of the "
                             "terminal REPL (chat, live streaming, approval "
                             "buttons, session controls). Needs the web "
                             "extra: uv sync --extra web")
    parser.add_argument("--host", default="127.0.0.1", metavar="ADDR",
                        help="bind address for --web (default: localhost only)")
    parser.add_argument("--port", type=int, default=8321, metavar="PORT",
                        help="port for --web (default: 8321)")
    parser.add_argument("--tool-select", type=int, default=None, metavar="K",
                        dest="tool_select",
                        help="dynamic tool loading: send only the K best-"
                             "matching tools each turn (BM25 over names/"
                             "descriptions; core tools + "
                             "list_available_tools always load). Auto-"
                             "enables at K=12 above 20 tools; 0 forces it "
                             "off. Same as $YANTRA_TOOLS_PER_TURN")
    parser.add_argument("--env-context", choices=["off", "local", "full"],
                        default=None, dest="env_context",
                        help="session awareness: inject auto-detected "
                             "context (time/timezone, host, working dir; "
                             "'full' adds your city via ONE public-IP "
                             "lookup to ipinfo.io) into the system prompt, "
                             "plus a try-tools-before-asking policy line. "
                             "Default from $YANTRA_ENV_CONTEXT (full)")
    parser.add_argument("--skills-dir", action="append", default=[],
                        metavar="DIR", dest="skills_dir",
                        help="extra directory to load skills from (repeatable). "
                             "Searched BEFORE the implicit roots "
                             "(.yantra/skills, skills/, ~/.yantra/skills). "
                             "Same as $YANTRA_SKILLS_PATH")
    parser.add_argument("--no-skills", action="store_true", dest="no_skills",
                        help="do not load skills at all: no roster in the "
                             "system prompt, no load_skill tool")
    parser.add_argument("prompt_positional", nargs="?", metavar="PROMPT",
                        help="same as --prompt (yantra \"what is in README.md?\")")
    return parser


def _cli_spec(args) -> AgentSpec:
    """What the COMMAND LINE asked for, and nothing it merely defaulted to.

    Every field left as None here is a field the operator did not mention,
    which is what lets ``merge`` hand the decision back to the package. The
    two store_true flags become None rather than False for the same reason:
    "--yolo absent" is not "the package may not ask for yolo".
    """
    return AgentSpec(
        provider=args.provider,
        model=args.model,
        system=args.system,
        max_iterations=args.max_iterations,
        context_window=args.context_window,
        cache=True if args.cache else None,
        skills=False if args.no_skills else None,
        skill_dirs=tuple(Path(d).expanduser() for d in args.skills_dir),
        permissions_mode="yolo" if args.yolo else None,
        env_context=args.env_context,
        cwd=Path(args.cwd),
    )


def _resolve_spec(args) -> AgentSpec:
    """The package's spec with the command line layered over it.

    ``--agent DIR`` is explicit; with no flag, ``./agent.toml`` is picked
    up when it happens to be there, which is what makes a package
    directory feel like a project you can just cd into. Nothing is
    searched for upwards: a manifest that silently governed a session from
    three directories up would be a surprise nobody asked for.

    The two environment-backed defaults are applied AFTER the merge, or
    they would outrank the package they are supposed to fall behind.
    """
    base = AgentSpec()
    where = args.agent
    if where is None:
        implicit = Path(args.cwd) / MANIFEST
        if implicit.is_file():
            where = str(implicit)
    if where is not None:
        base = load_package(Path(where))

    spec = base.merge(_cli_spec(args))
    if spec.env_context is None:
        spec = replace(spec, env_context=default_env_context())
    if spec.skills is None:
        spec = replace(spec, skills=True)
    return spec


def enable_subagents(agent: Agent, console: Console) -> SubagentSpawner:
    """--subagents wiring: register the spawn tool and tee child streams to
    the terminal. Factored out of main() so tests (and embedders) can set
    sub-agents up without a full CLI parse."""
    spawner = getattr(agent, "subagents", None) or SubagentSpawner(agent)
    spawner.on_child_event = SubagentTee(console)
    if SpawnSubagent.name not in agent.registry:
        agent.registry.register(SpawnSubagent(spawner))
    # Published so delegated skills share this budget rather than opening a
    # second one ([notes/30](../notes/30-skills.md)).
    agent.subagents = spawner
    return spawner


def _build_mode(args, provider_name: str, settings, model: str,
                console: Console) -> int:
    """--build SPEC: one-shot autonomous build + independent verification.

    Gate policy mirrors the trust story end-to-end: when bwrap confines
    bash, ONLY bash is auto-approved (fs writes still ask); without a
    real sandbox this is exactly the demo's honest yolo-with-warning.
    """
    sandbox = autodetect() if args.sandbox else None
    explicit_cwd = Path(args.cwd)
    workspace = (explicit_cwd if str(args.cwd) != "."
                 else Path(".yantra/builds") / time.strftime("%Y%m%d-%H%M%S"))

    if sandbox is not None and sandbox.confined:
        gate = trust_sandbox(confirm_gate(console), sandbox)
        gate_note = f"sandboxed bash auto-approved ({sandbox.describe}); other writes ask"
    elif args.yolo:
        gate = yolo
        gate_note = "yolo (autonomous build -- bash unsandboxed by choice)"
    else:
        gate = yolo
        gate_note = ("yolo (autonomous build needs it while bash is "
                     "unsandboxed -- rerun with --sandbox to confine bash)")

    spec = BuildSpec(task=args.build, checks=default_checks())

    def factory(ws: Path) -> Agent:
        return Agent(
            get_provider(provider_name, settings),
            model=model,
            system=BUILD_SYSTEM,
            tools=default_registry(sandbox),
            permissions=gate,
            cwd=ws,
            # --max-iterations now defaults to None so an agent package can
            # own it; build mode has no package, so it restates the default.
            max_iterations=(args.max_iterations
                            if args.max_iterations is not None else 25),
        )

    console.print(f"[bold]{provider_name}[/bold] · {model} · "
                  f"workspace {workspace}\n[dim]gate: {gate_note}[/dim]\n")
    try:
        result = run_build(factory, spec, workspace, on_event=Renderer(console))
    except KeyboardInterrupt:
        console.print("\n[yellow](cancelled)[/yellow]")
        return 130

    console.print("\n[bold]independent verification[/bold]")
    for outcome in result.checks:
        mark = "[green]PASS[/green]" if outcome.passed else "[red]FAIL[/red]"
        shown = " ".join(Path(a).name if i == 1 else a
                         for i, a in enumerate(outcome.argv))
        console.print(f"  {mark}  $ {shown}"
                      + ("" if outcome.passed else
                         f"  (exit {outcome.actual_exit}, "
                         f"expected {outcome.expect_exit})"))
        if not outcome.passed and outcome.tail:
            console.print(f"        {outcome.tail}", style="red",
                          markup=False, highlight=False)
    for name in result.tampered_tests:
        console.print(f"  [red]FAIL[/red]  {name} was MODIFIED -- the tests "
                      "are the contract; fixing them is cheating")

    usage = f"{result.usage_in} in / {result.usage_out} out"
    console.print(f"\n[bold]{'BUILD GREEN' if result.ok else 'BUILD RED'}[/bold]"
                  f" · {result.elapsed_seconds:.1f}s · {len(result.files)} files:"
                  f" {', '.join(result.files)}\ntokens: {usage}")
    return 0 if result.ok else 1


def _browse_login(url: str, console: Console) -> int:
    """--browse-login URL: headed one-time login on the persistent profile.

    Deliberately provider-free -- no model, no API key, so this runs
    BEFORE main()'s credential resolution. The human beats the login
    wall by hand once; the profile keeps the session for every later
    headless run ([notes/28](../notes/28-browser-tools.md)).
    """
    from yantra.tools.browser import run_login_session  # lazy: [browse] extra

    profile = browser_profile()
    if profile is None:
        print("error: --browse-login needs somewhere to KEEP the login: put\n"
              "  YANTRA_BROWSER_PROFILE=~/.local/state/yantra/browser-profile"
              "\nin .env first (see .env.example)", file=sys.stderr)
        return 2
    console.print(f"[bold]login setup[/bold] · profile {profile}\n"
                  f"a visible Chromium is opening{f' at {url}' if url else ''} "
                  "-- log in yourself (2FA and\ncaptchas are yours to beat), "
                  "then CLOSE THE WINDOW. Everything you leave\nsigned-in "
                  "here, the agent finds signed-in later.")
    try:
        run_login_session(profile, url)
    except KeyboardInterrupt:
        console.print("\n[yellow](cancelled -- whatever you logged into "
                      "before now is already saved)[/yellow]")
        return 130
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    console.print("[green]profile saved[/green] -- future browser_* "
                  "sessions start from these logins")
    return 0


def _find_mcp_config(name: str, config_paths: list[str],
                     cwd: str) -> MCPServerConfig | None:
    """Look the server up wherever a launch would have found it."""
    for path in config_paths:
        for cfg in load_mcp_configs(Path(path)):
            if cfg.name == name:
                return cfg
    for cfg in load_remembered(remembered_path(Path(cwd))):
        if cfg.name == name:
            return cfg
    return None


def _mcp_login(name: str, args, console: Console) -> int:
    """--mcp-login NAME: the OAuth walk, then a token on disk.

    Provider-free like --browse-login: authenticating a server has
    nothing to do with which model you were going to talk to, so this
    runs before any credential resolution.
    """
    from yantra.mcp_oauth import MCPAuthError, login

    try:
        cfg = _find_mcp_config(name, args.mcp_config, args.cwd)
    except MCPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if cfg is None:
        print(f"error: no mcp server named {name!r} in --mcp-config or "
              f"{remembered_path(Path(args.cwd))}\n"
              "add it first (yantra --mcp-config FILE, or the web panel)",
              file=sys.stderr)
        return 2
    if not cfg.url:
        print(f"error: {name!r} is a stdio server (it runs a command); "
              "there is nothing to log in to. Credentials for those go in "
              "its 'env'", file=sys.stderr)
        return 2

    # The challenge has to come from the server itself -- it names the
    # metadata document, and guessing that URL is how clients break when
    # a vendor moves it.
    session = MCPHttpSession(cfg, timeout=20.0)
    try:
        session.start()
    except MCPAuthRequired as exc:
        challenge = exc.challenge
    except MCPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    else:
        session.close()
        console.print(f"[green]{name} needs no login[/green] -- it answered "
                      "the handshake without one")
        return 0
    finally:
        session.close()

    console.print(f"[bold]mcp login[/bold] · {name} · {cfg.url}\n"
                  "a browser window is opening -- sign in there, then come "
                  "back. Nothing is stored but the token itself, under\n"
                  f"{TOKEN_FILE} (mode 0600).")
    try:
        token = login(name, cfg.url, challenge,
                      on_url=lambda url: console.print(
                          f"[dim]if no window opened, paste this:\n{url}"
                          f"[/dim]"))
    except KeyboardInterrupt:
        console.print("\n[yellow](cancelled -- nothing saved)[/yellow]")
        return 130
    except MCPAuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    expiry = ("no stated expiry" if token.expires_at is None
              else f"expires in {int(token.expires_at - time.time())}s")
    console.print(f"[green]signed in[/green] -- token saved ({expiry}"
                  + (", refreshable" if token.refresh_token else "") + ")")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    console = Console()

    if args.image and args.prompt is None and not args.prompt_positional:
        print("error: --image needs a prompt to attach to (--prompt or a "
              "positional PROMPT); interactive image input is not "
              "supported yet", file=sys.stderr)
        return 2
    if args.mcp_login is not None and (args.build or args.prompt
                                       or args.prompt_positional
                                       or args.web or args.browse_login):
        print("error: --mcp-login is its own mode: drop --build/--prompt/"
              "PROMPT/--web/--browse-login", file=sys.stderr)
        return 2
    if args.browse_login is not None and (args.build or args.prompt
                                          or args.prompt_positional
                                          or args.web):
        print("error: --browse-login is its own mode: drop --build/--prompt/"
              "PROMPT/--web", file=sys.stderr)
        return 2
    if args.build and (args.prompt or args.prompt_positional):
        print("error: --build takes the spec itself; drop --prompt/PROMPT",
              file=sys.stderr)
        return 2
    if args.web and (args.build or args.prompt or args.prompt_positional):
        print("error: --web serves the interactive UI; drop --build/--prompt/"
              "PROMPT (send messages from the browser instead)",
              file=sys.stderr)
        return 2

    # Login setup never touches a model, so it must work without any API
    # key -- dispatched before provider resolution on purpose.
    if args.browse_login is not None:
        _load_dotenv()  # the knob usually lives in .env
        return _browse_login(args.browse_login, console)

    # Authenticating a server says nothing about which model you meant to
    # use, so this runs before provider resolution too.
    if args.mcp_login is not None:
        _load_dotenv()
        return _mcp_login(args.mcp_login, args, console)

    try:
        spec = _resolve_spec(args)
        provider_name = spec.provider or guess_provider()
        settings = load_settings(provider_name)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    model = spec.model or default_model(provider_name)

    if args.build is not None:
        return _build_mode(args, provider_name, settings, model, console)

    sandbox = autodetect() if args.sandbox else None
    if sandbox is not None:
        console.print(f"[dim]bash sandbox: {sandbox.describe}[/dim]")

    # --web swaps the terminal for a browser: same agent construction below,
    # but the permission gate round-trips over the websocket instead of the
    # rich prompt, and ask_user's channel reaches the human through the page.
    web_session = None
    if args.web:
        try:
            from yantra.web.server import WebSession  # lazy: optional extra
        except ImportError:
            print("error: --web needs the web extra: uv sync --extra web",
                  file=sys.stderr)
            return 2
        web_session = WebSession()

    # The ask-gate is whatever surface the human sits on (terminal y/n/e or
    # browser modal); sandbox-trust composes into it, then a SwitchableGate
    # owns the runtime mode -- /yolo (REPL) and the mode chip (web) flip
    # between asking and bypassing without a restart.
    if web_session is not None:
        ask_gate = web_session.permission_gate()
    else:
        ask_gate = confirm_gate(console)
    if sandbox is not None and sandbox.confined:
        # containment earns autonomy: confined bash runs without asking,
        # every other tool keeps the ask-gate ([notes/16](../notes/16-sandboxing.md))
        ask_gate = trust_sandbox(ask_gate, sandbox)
        console.print("[dim]sandboxed bash auto-approved; other writes ask[/dim]")
    # The package may ask for yolo too; --yolo, if given, already won
    # the merge, so one attribute answers for both.
    gate = SwitchableGate(ask_gate, mode=spec.permissions_mode or "ask")

    # ONE call owns the assembly order (see spec.py): the admission policy
    # before any tool is registered, the prompt layers before env_context
    # appends to them, skills before the catalog the MCP block builds below.
    try:
        agent = spec.build(
            permissions=gate, sandbox=sandbox, cwd=Path(args.cwd),
            # Already resolved above, for build-mode dispatch and the banner.
            provider_name=provider_name, model=model,
            provider=get_provider(provider_name, settings,
                                  cache_control=bool(spec.cache)),
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if spec.root is not None:
        label = f"{spec.name} {spec.version}" if spec.version else spec.name
        # A package inside the working tree prints as a relative path: the
        # absolute one wraps onto three lines and says nothing extra.
        try:
            where = spec.root.relative_to(Path(args.cwd).resolve())
        except ValueError:
            where = spec.root
        console.print(f"[dim]agent: {label} -- {where}[/dim]")
    # Tools the package brought with it are named, never silent either --
    # loading them executed somebody else's Python on this machine, and the
    # operator is entitled to see that it happened and what it added.
    if brought := package_tool_names(agent.registry):
        console.print(f"[dim]package tools: {', '.join(brought)}[/dim]")
    # A tool the package's allow/deny turned away is reported, never silent:
    # "why is there no bash" must have an answer on screen.
    if refused := agent.registry.refused_names():
        console.print(f"[dim]package excludes: {', '.join(refused)}[/dim]")

    env_ctx = getattr(agent, "env_context", None)
    if env_ctx is not None and env_ctx.geo_error:
        console.print(f"[dim]env context: location lookup failed "
                      f"({env_ctx.geo_error}); continuing without[/dim]")

    if args.subagents:
        enable_subagents(agent, console)

    # ask_user: the model can consult its human mid-turn. The CHANNEL decides
    # what happens when nobody is home -- browser/websocket and TTY stdin
    # block until answered; piped stdin registers no channel, so an ask fails
    # the turn loudly (UserUnavailable) instead of guessing or hanging.
    # Build agents get nothing: builds are autonomous by definition.
    if web_session is not None:
        agent.registry.register(AskUser(web_session.channel))
    elif sys.stdin.isatty() and sys.stdout.isatty():
        agent.registry.register(AskUser(TerminalChannel()))
    else:
        agent.registry.register(AskUser(None))

    # Skills themselves are attached by spec.build -- discovery, the roster
    # layer, load_skill -- because a package declares its own, and because
    # the order matters (before the MCP block below, whose catalog cannot
    # pin a tool that does not exist yet). What stays here is the part a
    # package has no say in: the OPERATOR's kill-switch, and reporting.
    skills = getattr(agent, "skills", None)
    if skills is not None:
        # Operator kill-switch, the skills twin of YANTRA_DISABLED_TOOLS:
        # globs pull skills out of the roster before the model ever sees
        # one. Reversible in-session with /skills on NAME.
        if patterns := disabled_skill_patterns():
            present = skills.names()
            doomed = sorted({n for pat in patterns
                             for n in present if fnmatch.fnmatch(n, pat)})
            for name in doomed:
                skills.disable(name)
            if doomed:
                skills.reapply()
                console.print(f"[dim]skills disabled: {', '.join(doomed)}[/dim]")
            unmatched = [pat for pat in patterns
                         if not any(fnmatch.fnmatch(n, pat) for n in present)]
            if unmatched:
                console.print(f"[yellow]no skills match YANTRA_DISABLED_"
                              f"SKILLS entry: {', '.join(unmatched)}[/yellow]")
        if len(skills):
            console.print(f"[dim]skills: {len(skills)} loaded -- "
                          f"{', '.join(skills.names())}[/dim]")
        for broken_skill in skills.found.broken:
            console.print(f"[yellow]skill {broken_skill.path}: "
                          f"{broken_skill.reason}[/yellow]")

    store = SessionStore(Path(args.cwd) / ".yantra" / "session.sqlite3")
    # The manager owns live MCP connections so servers can be added,
    # removed, and toggled MID-session (web panel + REPL /mcp), not just
    # wired at startup. It doubles as the startup bookkeeper below.
    mcp_manager = MCPManager(agent.registry, agent=agent,
                             memory_path=remembered_path(Path(args.cwd)))
    repl = Repl(agent, console, store=store, sandbox=sandbox,
                mcp=mcp_manager)

    # MCP servers: parse errors are fatal (exit 2); connection failures
    # only warn -- a dead optional integration shouldn't kill the
    # session. The manager's shutdown in the finally below closes every
    # opened session on EVERY exit path, including early returns here.
    try:
        for path in args.mcp_config:
            try:
                configs = load_mcp_configs(path)
            except MCPError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            for cfg in configs:
                try:
                    names = mcp_manager.connect(cfg)
                except MCPError as exc:
                    console.print(f"[yellow]mcp '{cfg.name}' unavailable: "
                                  f"{exc}[/yellow]")
                    continue
                console.print(f"[dim]mcp '{cfg.name}': {len(names)} tool(s) "
                              f"-- {', '.join(names)}[/dim]")

        # Servers saved by earlier sessions' "remember this server"
        # reconnect here, after explicit --mcp-config files (a name
        # already connected via flags wins; its saved entry just sits).
        try:
            saved = load_remembered(mcp_manager.memory_path)
        except MCPError as exc:
            console.print(f"[yellow]{exc}; ignoring remembered "
                          "servers[/yellow]")
            saved = []
        for cfg in saved:
            if cfg.name in mcp_manager.sessions:
                continue
            try:
                names = mcp_manager.connect(cfg)
            except MCPError as exc:
                console.print(f"[yellow]mcp '{cfg.name}' unavailable: "
                              f"{exc}[/yellow]")
                continue
            console.print(f"[dim]mcp '{cfg.name}' (remembered): "
                          f"{len(names)} tool(s)[/dim]")

        # Operator kill-switch: YANTRA_DISABLED_TOOLS globs unregister
        # tools AFTER MCP registration (so whole mcp__ servers can go)
        # and BEFORE catalog building -- a disabled tool must neither be
        # sent, executed, suggested by discovery, nor pinned.
        patterns = disabled_tool_patterns()
        if patterns:
            present = agent.registry.names()
            doomed = sorted({n for pat in patterns
                             for n in present if fnmatch.fnmatch(n, pat)})
            for name in doomed:
                agent.registry.unregister(name)
            unmatched = [pat for pat in patterns
                         if not any(fnmatch.fnmatch(n, pat) for n in present)]
            if doomed:
                console.print(f"[dim]disabled {len(doomed)} tool(s): "
                              f"{', '.join(doomed)}[/dim]")
            if unmatched:
                console.print(f"[yellow]no tools match YANTRA_DISABLED_"
                              f"TOOLS entry: {', '.join(unmatched)}[/yellow]")

        # Dynamic tool loading (book ch12): decided AFTER MCP registration
        # so external tools count toward the cliff threshold. Precedence
        # is flag > env > auto: --tool-select K (backed by
        # $YANTRA_TOOLS_PER_TURN) forces it; 0 forces it off; both unset
        # auto-enables past the threshold.
        width = args.tool_select
        if width is None:
            width = spec.tools_per_turn      # [tools] per_turn in agent.toml
        if width is None:
            width = default_tool_select()
        if width is None and len(agent.registry) > AUTO_SELECTION_THRESHOLD:
            width = DEFAULT_TOOLS_PER_TURN
        if width:
            catalog, _ = enable_selection(agent.registry)
            agent.tool_catalog = catalog
            agent.tools_per_turn = width
            console.print(f"[dim]tool selection: top {width} of "
                          f"{len(agent.registry) - 1}+discovery each turn "
                          f"(--tool-select 0 to disable)[/dim]")

        if args.resume:
            payload = store.load_latest("default")
            if payload is None:
                console.print("[yellow]--resume: no checkpoint found; "
                              "starting fresh[/yellow]")
            else:
                try:
                    console.print(apply_payload(
                        agent, payload,
                        settings_loader=load_settings,
                        provider_factory=get_provider,
                    ))
                except Exception as exc:
                    console.print(f"[red]--resume failed: {exc}; starting fresh[/red]")

        if web_session is not None:
            from yantra.web.server import launch
            return launch(web_session, agent, store,
                          host=args.host, port=args.port, mcp=mcp_manager)

        if args.prompt is not None or args.prompt_positional:
            try:
                images = [load_image_block(Path(p)) for p in args.image]
            except ImageError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            try:
                prompt = args.prompt or args.prompt_positional
                repl.run_turn(prompt, images=images)  # same rendering path as the REPL
            except KeyboardInterrupt:
                console.print("\n[yellow](cancelled)[/yellow]")
                return 130
            except UserUnavailable as exc:
                console.print(f"\n[red]turn failed: {exc}[/red]")
                return 1
            except Exception as exc:
                console.print(f"\n[red]{exc}[/red]")
                return 1
            return 0

        repl.run()
        return 0
    finally:
        mcp_manager.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
