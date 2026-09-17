"""CLI entrypoint: argparse -> configured Agent -> REPL or one-shot run."""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import sys
import time
from dataclasses import replace
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from yantra.agent import Agent
from yantra.budget import Budget
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
from yantra.eval_report import compare, read_report, record_run, write_report
from yantra.eval_suite import CASES, SUITE_DIR, find_suite, load_cases
from yantra.evals import (AsyncEvalRunner, CaseOutcome, EvalRunner,
                          OfflineProvider)
from yantra.images import load_image_block
from yantra.mcp import (MCPAuthRequired, MCPError, MCPHttpSession,
                         MCPManager, MCPServerConfig, load_mcp_configs,
                         load_remembered, remembered_path)
from yantra.mcp_oauth import TOKEN_FILE
from yantra.package import MANIFEST, load_package
from yantra.permissions import (SwitchableGate, allow_read_only,
                                 trust_sandbox, yolo)
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
    parser.add_argument("--max-usd", type=float, default=None,
                        metavar="DOLLARS", dest="max_usd",
                        help="per-TURN spending ceiling: the loop stops "
                             "between iterations once a turn has cost this "
                             "much, with a distinct stop reason, and says so "
                             "once beforehand when the next call will not "
                             "fit. Overrides a package's [budget] "
                             "max_usd_per_turn -- the author's number is an "
                             "estimate, and you are the one paying. Needs a "
                             "priced model (local models bill nothing, so it "
                             "never fires)")
    parser.add_argument("--budget-notice", action="store_true",
                        dest="budget_notice",
                        help="also tell the AGENT, once, when the turn is "
                             "nearly out of budget -- so it finishes with "
                             "what it has instead of being cut off "
                             "mid-thought. It is told the DEADLINE, never "
                             "the figures: a model handed a number to "
                             "optimise starts optimising for it. Off by "
                             "default, and yours to give rather than the "
                             "package author's")
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
    parser.add_argument("--eval", action="store_true",
                        help="run the agent package's own eval suite "
                             f"({SUITE_DIR}/{CASES}) and exit non-zero on "
                             "failure; the package is named the usual way "
                             "(--agent DIR, or ./agent.toml)")
    parser.add_argument("--async", dest="eval_async", nargs="?", type=int,
                        const=4, default=None, metavar="N",
                        help="with --eval: drive the suite through "
                             "AsyncEvalRunner, N trajectories at once "
                             "(default 4). Identical grading; lines land in "
                             "completion order and the final report stays in "
                             "file order")
    parser.add_argument("--repeat", type=int, default=1, metavar="N",
                        help="with --eval: run every case N times and judge "
                             "it on the PASS RATE. A trajectory is a die "
                             "roll, so one green run is one sample; a case's "
                             "own min_pass_rate says what fraction it claims "
                             "to hold at, and this buys the evidence. "
                             "Roster-only cases still run once -- they reach "
                             "no model")
    parser.add_argument("--case", action="append", default=[],
                        metavar="PATTERN", dest="case",
                        help="with --eval: run only the cases whose id "
                             "matches (fnmatch; repeatable). This is how you "
                             "spend --repeat N on the one case that needs the "
                             "evidence without paying for it on every "
                             "deterministic one. A filtered run reports as a "
                             "SUBSET, never as the suite's verdict")
    parser.add_argument("--report", metavar="FILE", default=None,
                        help="with --eval: write this run to FILE as JSON "
                             "(green or red -- a red run is the one you will "
                             "want to compare against tomorrow)")
    parser.add_argument("--against", metavar="FILE", default=None,
                        help="with --eval: compare this run to a report "
                             "written earlier and print what moved. Changes "
                             "no verdict and no exit code: a run that got "
                             "worse and is still green is still green")
    parser.add_argument("--no-mcp", action="store_true", dest="no_mcp",
                        help="with --eval: do not start the MCP servers the "
                             "package declares. The agent under test is then "
                             "missing their tools, which the header says out "
                             "loud -- a gate that graded a smaller agent "
                             "quietly would be worse than no gate")
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
        max_usd_per_turn=args.max_usd,
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


def _select_cases(cases: list, patterns: list[str]) -> tuple[list, str | None]:
    """``--case PATTERN`` -> the cases to run, and the label for the header.

    A RUN COUNT IS THE OPERATOR'S MONEY (notes/35), which is why ``repeat``
    never became a key in cases.toml -- and which left one genuinely
    probabilistic case dragging every deterministic one along with it under
    ``--repeat 10``. The missing piece was never a per-case count. It was a
    way to POINT the count: the operator says which cases to spend on, in
    the same breath as how many runs to buy.
    """
    if not patterns:
        return cases, None
    chosen = [c for c in cases
              if any(fnmatch.fnmatch(c.id, pat) for pat in patterns)]
    return chosen, ", ".join(patterns)


def _eval_mcp(spec: AgentSpec, tools, console: Console):
    """Start the servers the package declares, or say why the agent under
    test is smaller than the one that ships.

    A SERVER THE PACKAGE DECLARED AND THE SUITE COULD NOT REACH IS A RED
    SUITE, NOT A SMALLER AGENT. Everywhere else in this CLI an unreachable
    MCP server is a warning -- a dead optional integration should not kill
    an interactive session. A gate is the opposite case: its whole job is
    to answer "does the agent that ships still work", and quietly grading
    one with fewer tools than it ships with answers a different question
    in the same green letters.

    Registered into the runner's BASE registry, before any case copies it,
    so the package's own admission policy still applies per case (a package
    that narrows to a whitelist must name mcp__* to keep them).
    """
    manager = MCPManager(tools)
    for cfg in spec.mcp:
        names = manager.connect(cfg)          # MCPError propagates: see above
        console.print(f"[dim]mcp '{cfg.name}': {len(names)} tool(s) under "
                      f"test -- {', '.join(names)}[/dim]")
    return manager


def _eval_mode(args, spec: AgentSpec, console: Console) -> int:
    """--eval: run the package's own suite, and make the exit code the verdict.

    Builder mode's rule pointed at the agent instead of the project. An
    author's claim that their agent works is worth what a model's claim
    that the tests passed is worth, so a package ships the evidence and
    this runs it: non-zero on any failure, which is all CI needs.

    The suite grades THE PACKAGE -- ``spec=`` hands the runner the same
    AgentSpec a session would build, so the prompt, skills, package tools,
    admission policy and declared servers under test are the shipped ones.
    """
    if spec.root is None:
        print("error: --eval runs an agent package's own suite, and no "
              "package was named: --agent DIR (or cd into one)",
              file=sys.stderr)
        return 2
    suite = find_suite(spec.root)
    if suite is None:
        print(f"error: {spec.root} has no eval suite -- create "
              f"{SUITE_DIR}/{CASES} with a [[case]] for each behavior you "
              f"want held", file=sys.stderr)
        return 2
    try:
        # Graders resolve HERE, before a single token is spent: a typo in
        # case nine must not cost eight cases to discover (eval_suite.py).
        every_case = load_cases(suite)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # The baseline is read BEFORE anything runs. A comparison the operator
    # asked for and cannot have is worth discovering now, not after a
    # suite's worth of real tokens has been spent producing the other half
    # of it -- the same rule graders get (eval_suite.py).
    baseline = None
    if args.against is not None:
        try:
            baseline = read_report(Path(args.against))
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    cases, filtered = _select_cases(every_case, args.case)
    if not cases:
        print(f"error: no case matches {', '.join(args.case)} -- this suite "
              f"has: {', '.join(c.id for c in every_case)}", file=sys.stderr)
        return 2

    # THE PROVIDER IS RESOLVED FROM WHAT THE SELECTED CASES NEED. A suite
    # of nothing but roster assertions grades the tool LIST, which is
    # knowable the moment the agent is built -- so it makes no request,
    # needs no key, and now says so instead of failing at the doorstep.
    needs_model = any(c.needs_a_model for c in cases)
    if needs_model:
        try:
            provider_name = spec.provider or guess_provider()
            settings = load_settings(provider_name)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        model = spec.model or default_model(provider_name)
        provider = get_provider(provider_name, settings,
                                cache_control=bool(spec.cache))
    else:
        provider_name = spec.provider or "none"
        model = spec.model or "(no model needed)"
        provider = OfflineProvider()
        # Two fields the build would otherwise resolve against a provider
        # that is not there: the window is never consulted because nothing
        # is sent, and a dollar ceiling cannot fire on a run that spends
        # nothing (and would refuse to be BUILT against a model slug
        # nobody can price -- notes/34, correctly, and pointlessly here).
        spec = replace(spec, max_usd_per_turn=None,
                       context_window=spec.context_window or 200_000)

    # A SUITE NEVER ASKS, AND A PACKAGE CANNOT OPEN ITS OWN GATE. Nobody
    # is sitting in front of an acceptance run, so "ask" would hang it;
    # and spec.permissions_mode is deliberately ignored, or an author
    # could ship mode = "yolo" and have their own gate graded with the
    # safety off. The operator opens it, on their command line, or not.
    sandbox = autodetect() if args.sandbox else None
    gate = yolo if args.yolo else allow_read_only
    gate_note = ("yolo -- every tool runs unasked" if args.yolo else
                 "read-only tools only; writes and commands are refused "
                 "(--yolo opens it)")
    if sandbox is not None:
        gate_note += f" · bash sandbox: {sandbox.describe}"

    # Reproducibility: a verdict that depends on which directory the
    # operator happened to be standing in is not a gate. The package's own
    # root is the default -- a suite over files the package SHIPS answers
    # the same way on every machine -- and --cwd points it at a wider tree.
    cwd = Path(args.cwd).resolve() if str(args.cwd) != "." else spec.root

    label = f"{spec.name} {spec.version}" if spec.version else (spec.name or "")
    # The package's own ceiling applies per case (one case is one turn), so
    # a suite can go red on cost rather than on behaviour. Said up front,
    # or "crashed: over_budget" three cases later reads as a bug.
    budget_note = ""
    if spec.max_usd_per_turn is not None:
        try:
            ceiling = Budget.for_model(spec.max_usd_per_turn,
                                       provider_name=provider_name, model=model)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        budget_note = "\nbudget: " + ceiling.describe()
        if ceiling.metered:  # the inert line already says it cannot fire
            budget_note += " -- a case that costs more goes red"
    # What the run will cost, before it starts. A roster-only case is
    # counted separately because it is the one kind that is free -- it
    # grades the tool LIST, which is knowable without a model.
    plan = f"{len(cases)} case(s)"
    free_cases = sum(1 for c in cases if not c.needs_a_model)
    if free_cases:
        plan += f" · {free_cases} roster-only"
    if args.repeat > 1:
        plan += f" · {args.repeat} runs each"
    console.print(f"[bold]eval[/bold] {escape(label)} · {plan} · "
                  f"{provider_name} · {model}\n[dim]cwd: {cwd}\n"
                  f"gate: {gate_note}{budget_note}[/dim]")
    if filtered is not None:
        # Never quiet about it. A green line under a filter is a claim
        # about the cases that ran, and the difference between that and a
        # gate is the whole reason the verdict word changes below too.
        console.print(f"[yellow]filtered: {escape(filtered)} -- "
                      f"{len(cases)} of {len(every_case)} case(s); this is "
                      f"not the package's gate[/yellow]")
    if not needs_model:
        console.print("[dim]no provider resolved: every selected case grades "
                      "the roster, so this run makes no request and needs no "
                      "key[/dim]")
    if args.eval_async is not None:
        console.print(f"[dim]mode: async, {args.eval_async} trajectories at "
                      f"once -- lines land as cases finish, not in file "
                      f"order[/dim]")
    if args.repeat > 1:
        console.print(f"[dim]runs: {args.repeat} per case; a case reports "
                      f"once all of its runs are in[/dim]")
    else:
        # A declared rate that cannot be honoured is worth saying out loud:
        # at one run, 0.7 and 1.0 grade identically, and an author who wrote
        # 0.7 believed they had bought something.
        claimed = sum(1 for c in cases if c.min_pass_rate < 1)
        if claimed:
            console.print(f"[dim]note: {claimed} case(s) declare a "
                          f"min_pass_rate below 1.0; one run each can only "
                          f"grade them all-or-nothing -- --repeat N buys the "
                          f"evidence (--case PATTERN points it)[/dim]")

    tools = default_registry(sandbox)
    mcp_manager = None
    if spec.mcp and not args.no_mcp:
        try:
            mcp_manager = _eval_mcp(spec, tools, console)
        except MCPError as exc:
            print(f"error: mcp server declared by this package is "
                  f"unreachable, so the agent under test would be smaller "
                  f"than the one that ships: {exc}", file=sys.stderr)
            return 2
    elif spec.mcp:
        console.print(f"[yellow]mcp: skipped (--no-mcp) -- "
                      f"{len(spec.mcp)} declared server(s) are NOT under "
                      f"test, and neither are their tools[/yellow]")
    console.print()

    def report(outcome: CaseOutcome) -> None:
        _eval_outcome_line(console, outcome)

    try:
        if args.eval_async is not None:
            runner = AsyncEvalRunner(
                provider, model, tools=tools, permissions=gate, cwd=cwd,
                spec=spec, provider_name=provider_name,
                concurrency=args.eval_async,
            )
            outcomes = asyncio.run(runner.run_suite(
                cases, repeat=args.repeat, on_outcome=report))
        else:
            runner = EvalRunner(
                provider, model, tools=tools, permissions=gate,
                cwd=cwd, spec=spec, provider_name=provider_name,
            )
            # Printed as each case lands rather than in one table at the
            # end: a live suite is minutes of silence otherwise, and the
            # first red is the one you want to see soonest.
            outcomes = runner.run_suite(cases, repeat=args.repeat,
                                        on_outcome=report)
    except KeyboardInterrupt:
        console.print("\n[yellow](cancelled -- no verdict)[/yellow]")
        return 130
    except ConfigError as exc:
        # Building the agent, not running it: a package tool that will not
        # import breaks every case, so say it once and stop.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if mcp_manager is not None:
            mcp_manager.shutdown()

    passed = sum(1 for o in outcomes if o.passed)
    green = passed == len(outcomes)
    # A FILTERED RUN IS NOT A GATE, and the word says so. "SUITE GREEN" on
    # a run of one case out of twenty is the sentence somebody pastes into
    # a pull request, and the exit code alone cannot correct it.
    word = "SUBSET" if filtered is not None else "SUITE"
    verdict = (f"[bold green]{word} GREEN[/bold green]" if green
               else f"[bold red]{word} RED[/bold red]")
    spent = sum(o.tokens_used for o in outcomes)
    runs = sum(o.attempts for o in outcomes)
    tally = f"{passed}/{len(outcomes)} passed"
    if runs != len(outcomes):
        tally += f" · {runs} runs"
    if filtered is not None:
        tally += f" · {len(every_case) - len(cases)} case(s) not run"
    console.print(f"\n{verdict} · {tally} · {spent} tokens"
                  + (f" · {free_cases} case(s) cost nothing" if free_cases
                     else ""))

    run = record_run(outcomes, suite=label or (spec.name or "agent"),
                     provider=provider_name, model=model, repeat=args.repeat,
                     cases_in_suite=len(every_case),
                     filtered=args.case or None)
    if args.report is not None:
        try:
            write_report(Path(args.report), run)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        console.print(f"[dim]report: {escape(args.report)}[/dim]")
    if baseline is not None:
        _render_comparison(console, compare(baseline, run))
    # The verdict is THIS run's, and the comparison did not touch it.
    return 0 if green else 1


def _render_comparison(console: Console, cmp) -> None:
    """What moved between two runs, and nothing about whether that is ok.

    Deliberately not a verdict: no colour on the headline, no summary
    adjective, no exit code. The operator knows whether "12 tokens more
    and one case fixed" is good news; a program that decided for them
    would be wrong on the first day somebody made an honest improvement
    that cost two tokens.
    """
    before, after = cmp.before, cmp.after
    console.print(f"\n[bold]against[/bold] {escape(before.suite)} on "
                  f"{escape(before.where)}, {escape(before.at)} "
                  f"({before.passed}/{len(before.cases)} passed)")
    if before.where != after.where:
        # The most useful comparison this does -- the same suite on a
        # cheaper model -- so it is named, not refused.
        console.print(f"[dim]different model: {escape(before.where)} → "
                      f"{escape(after.where)}[/dim]")
    if not cmp.comparable:
        console.print("[yellow]the two runs did not grade the same cases; "
                      "added/gone below are about the SELECTION, not about "
                      "the package[/yellow]")
    if before.repeat != after.repeat:
        console.print(f"[yellow]different sample sizes: {before.repeat} run(s) "
                      f"per case then, {after.repeat} now -- the counts below "
                      f"are not rates[/yellow]")

    marks = {"fixed": "[green]fixed[/green]", "broke": "[red]broke[/red]",
             "added": "[cyan]added[/cyan]", "gone": "[yellow]gone[/yellow]"}
    moved = 0
    for delta in cmp.deltas:
        if delta.kind in marks:
            moved += 1
            tally = ""
            if delta.before is not None and delta.after is not None:
                tally = f"  {delta.before.tally} → {delta.after.tally}"
            console.print(f"  {marks[delta.kind]}  {escape(delta.id)}{tally}")
        elif delta.rate_moved:
            # Same verdict, different count. A case going 9/10 -> 6/10 is
            # still green and is the most useful line on this page.
            moved += 1
            word = ("[green]up[/green]" if delta.rate_direction == "up"
                    else "[yellow]down[/yellow]")
            console.print(f"  rate {word}  {escape(delta.id)}  "
                          f"{delta.before.tally} → {delta.after.tally}")
    if not moved:
        console.print("  [dim]no case changed verdict or pass count[/dim]")
    spent = cmp.tokens_moved
    if spent:
        console.print(f"[dim]tokens: {before.tokens} → {after.tokens} "
                      f"({spent:+d})[/dim]")


def _eval_outcome_line(console: Console, outcome: CaseOutcome) -> None:
    """One case's verdict, plus a line per distinct failure.

    Three shapes, because three things are worth different detail: a case
    that reached no model has no trajectory to describe, a repeated case is
    a RATE (and which runs failed, in order, is half the diagnosis), and a
    single run is the one-line receipt this gate has always printed.
    """
    mark = "[green]PASS[/green]" if outcome.passed else "[red]FAIL[/red]"
    if not outcome.ran_model:
        # Two different things reach no model, and conflating them would
        # misreport both: a case whose only assertions are about the roster,
        # and a case with a task whose roster assertion failed before the
        # task was paid for.
        detail = ("roster only · no model call · 0 tok" if outcome.passed
                  else "roster failed · no model call · 0 tok")
    elif outcome.attempts > 1:
        needed = ("" if outcome.min_pass_rate >= 1
                  else f" (needs {outcome.required_passes})")
        detail = (f"{outcome.marks} {outcome.passes}/{outcome.attempts} "
                  f"runs{needed} · {outcome.duration_seconds:.1f}s · "
                  f"{outcome.tokens_used} tok")
    else:
        run = outcome.runs[-1]
        detail = (f"{run.duration_seconds:.1f}s · {run.tokens_used} tok · "
                  f"{run.iterations_used} it · "
                  f"{', '.join(run.tool_calls_seen) or 'no tools'}")
    console.print(f"  {mark}  {escape(outcome.case_id)}  "
                  f"[dim]{escape(detail)}[/dim]")
    for failure in outcome.failures:
        console.print(f"        {failure}", style="red",
                      markup=False, highlight=False)


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
    if args.eval and (args.build or args.prompt or args.prompt_positional
                      or args.web):
        print("error: --eval runs the package's suite and reports a verdict; "
              "drop --build/--prompt/PROMPT/--web", file=sys.stderr)
        return 2
    if args.repeat < 1:
        print(f"error: --repeat must be at least 1 (got {args.repeat}); a "
              f"suite that runs nothing passes everything", file=sys.stderr)
        return 2
    if args.eval_async is not None and args.eval_async < 1:
        print(f"error: --async must be at least 1 (got {args.eval_async})",
              file=sys.stderr)
        return 2
    if ((args.eval_async is not None or args.repeat != 1 or args.case
         or args.no_mcp or args.report or args.against) and not args.eval):
        print("error: --async, --repeat, --case, --no-mcp, --report and "
              "--against belong to --eval -- they say how an acceptance "
              "suite is driven, and a session has one trajectory",
              file=sys.stderr)
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
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # An acceptance run resolves its OWN provider, and may resolve none at
    # all: a suite of nothing but roster assertions reaches no model, and
    # needing a key to discover that was the last thing standing between
    # "this gate costs zero tokens" and "this gate costs zero". Dispatched
    # before the resolution below for exactly that reason, and before any
    # of the session wiring further down, which an acceptance run has no
    # use for either.
    if args.eval:
        return _eval_mode(args, spec, console)

    try:
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
    # Sub-agents the package declared, named for the same reason: each one
    # is a tool that will spend money on a model call, and the operator
    # should not have to read agent.toml to find out they exist.
    if spec.subagents:
        console.print("[dim]sub-agents: " + ", ".join(
            f"{sub.name} ({sub.model})" if sub.model else sub.name
            for sub in spec.subagents) + "[/dim]")
    # A tool the package's allow/deny turned away is reported, never silent:
    # "why is there no bash" must have an answer on screen.
    if refused := agent.registry.refused_names():
        console.print(f"[dim]package excludes: {', '.join(refused)}[/dim]")

    # A ceiling nobody mentions is a ceiling nobody trusts -- and the
    # inert case (a local model, which bills nothing) has to say so out
    # loud, or an operator reads silence as protection.
    if agent.budget is not None:
        # The operator's call, applied after the build because it is not
        # part of what the PACKAGE describes (budget.notice).
        agent.budget.notify_agent = args.budget_notice
        console.print(f"[dim]budget: {agent.budget.describe()}[/dim]")
    elif args.budget_notice:
        console.print("[yellow]--budget-notice does nothing without a "
                      "ceiling: set --max-usd, or run a package with "
                      "[budget] max_usd_per_turn[/yellow]")

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
        # Symmetry with the line above: this process opened a connection
        # pool and gives it back, rather than leaving it to interpreter
        # teardown ([notes/38](../notes/38-giving-it-back.md)).
        agent.provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
