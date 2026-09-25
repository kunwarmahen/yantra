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
from yantra.confidence import describe, perfect_runs_needed
from yantra.config import (
    _load_dotenv,
    canonical_provider,
    browser_executable,
    browser_login_command,
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
from yantra.eval_report import (SORTS, Matrix, Pool, PriceRecord, SuiteRun,
                                compare, line_up, pool, read_report,
                                record_run, write_pool, write_report)
from yantra.eval_suite import (CASES, SUITE_DIR, find_suite, load_cases,
                               render_case)
from yantra.evals import (AsyncEvalRunner, CaseOutcome, EvalRunner,
                          OfflineProvider, case_from_trajectory)
from yantra.fingerprint import fingerprint, weights
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
from yantra.tools.discover import (ENTRY_POINT_GROUP, entry_point_packs,
                                   package_tool_names)
from yantra.trace import (FULL, REDACTED, SHAPE, TrajectoryLog, flagged,
                          read_word_list, step_note, why_flagged)
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
    parser.add_argument("--provider", type=canonical_provider,
                        choices=["anthropic", "openai", "responses", "ollama"],
                        metavar="{anthropic,openai,responses,ollama,local}",
                        help="defaults to whichever API key is set; 'responses' "
                             "speaks OpenAI's Responses API; 'ollama' runs "
                             "local models at localhost:11434 (no key), and "
                             "'local' is another word for it -- half the "
                             "people reading this want a local model rather "
                             "than a brand")
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
    parser.add_argument("--budget-cap-reply", action="store_true",
                        dest="budget_cap_reply",
                        help="limit each reply to what is left of the "
                             "per-turn ceiling, so no single reply can carry "
                             "a turn past it. The price: an answer can "
                             "arrive CUT OFF, and the turn then ends "
                             "over_budget with what it got. Off by default, "
                             "and yours to choose rather than the package "
                             "author's")
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
    parser.add_argument("--all-runs", action="store_true", dest="all_runs",
                        help="with --eval --repeat N: buy all N runs even "
                             "after a case can no longer reach its "
                             "min_pass_rate. By default it stops there -- "
                             "the verdict is already red -- and says so; "
                             "never on the green side")
    parser.add_argument("--case", action="append", default=[],
                        metavar="PATTERN", dest="case",
                        help="with --eval: run only the cases whose id "
                             "matches (fnmatch; repeatable). This is how you "
                             "spend --repeat N on the one case that needs the "
                             "evidence without paying for it on every "
                             "deterministic one. A filtered run reports as a "
                             "SUBSET, never as the suite's verdict")
    parser.add_argument("--failed", metavar="FILE", nargs="?", const="",
                        default=None, dest="failed",
                        help="with --eval: run only the cases that were RED "
                             "in FILE, a report an earlier --report wrote. "
                             "With no FILE, the report --against names. A "
                             "green report leaves nothing to re-run; a case "
                             "that has since left the suite is named rather "
                             "than dropped")
    parser.add_argument("--report", metavar="FILE", default=None,
                        help="with --eval: write this run to FILE as JSON "
                             "(green or red -- a red run is the one you will "
                             "want to compare against tomorrow)")
    parser.add_argument("--against", metavar="FILE", action="append",
                        default=[],
                        help="with --eval: compare this run to a report "
                             "written earlier and print what moved. Changes "
                             "no verdict and no exit code: a run that got "
                             "worse and is still green is still green. "
                             "REPEATABLE -- two or more reports line up as a "
                             "table instead, one column per run, which is "
                             "how you put three models side by side")
    parser.add_argument("--reports", metavar="FILE", nargs="+", default=None,
                        help="line up reports an earlier --eval --report "
                             "wrote, WITHOUT running anything: two print what "
                             "moved, three or more print a table. Needs no "
                             "package, no provider and no key")
    parser.add_argument("--sort", choices=SORTS, default=None,
                        help="order the rows of a TABLE (--against twice or "
                             "more, --reports with three or more): "
                             "'disagree' puts the cases the runs split on "
                             "first, 'red' the most red cells, 'id' "
                             "alphabetical. Reorders, never hides a row")
    parser.add_argument("--pool", action="store_true",
                        help="with --reports: add the reports up case by "
                             "case instead, and print what the pooled counts "
                             "are evidence of. Runs of a different model or "
                             "package version are pooled separately, never "
                             "summed")
    parser.add_argument("--pool-json", metavar="FILE", default=None,
                        dest="pool_json",
                        help="with --reports: pool them (as --pool does) and "
                             "also write the pooled figures to FILE as JSON, "
                             "for whatever tracks the range over time")
    parser.add_argument("--no-mcp", action="store_true", dest="no_mcp",
                        help="with --eval: do not start the MCP servers the "
                             "package declares. The agent under test is then "
                             "missing their tools, which the header says out "
                             "loud -- a gate that graded a smaller agent "
                             "quietly would be worse than no gate")
    parser.add_argument("--browse-login", metavar="URL", dest="browse_login",
                        default=None,
                        help="one-time LOGIN SETUP for the browser_* tools: "
                             "opens a VISIBLE browser on the "
                             "$YANTRA_BROWSER_PROFILE directory (set it in "
                             ".env first), you log in yourself -- 2FA and "
                             "captchas included -- then close the window. "
                             "Set $YANTRA_BROWSER_EXECUTABLE to a browser you "
                             "already have and the window carries no "
                             "automation, which is what sign-in pages that "
                             "refuse robots are checking for. "
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
    parser.add_argument("--wait-budget", type=float, default=None,
                        metavar="SECONDS", dest="wait_budget",
                        help="with --web: how long ONE TURN may spend, in "
                             "total, waiting for you to approve things. "
                             "Each prompt waits for what is left; once it "
                             "is spent, nothing more is asked that turn. "
                             "Needs --on-timeout. The terminal asks you "
                             "directly, so nothing waits there")
    parser.add_argument("--on-timeout", choices=["deny", "allow"],
                        default=None, dest="on_timeout",
                        help="with --wait-budget: what an unanswered "
                             "approval means. No default -- 'deny' is right "
                             "for a deploy and wrong for a job you left "
                             "running so it would carry on")
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
    parser.add_argument("--trace", metavar="FILE", default=None,
                        help="append every turn of this session to FILE as "
                             "JSONL -- the task, which tools ran (a sub-agent's "
                             "too), and what it cost; the terminal and --web "
                             "alike, and every run of every case under --eval, "
                             "which the report then names. A real failure can "
                             "then become a "
                             "regression case with --fossil. Keeps the SHAPE "
                             "of a turn and not its contents; --trace-full "
                             "adds arguments, results and the answer, which "
                             "is whatever the agent read")
    parser.add_argument("--trace-full", action="store_true",
                        dest="trace_full",
                        help="with --trace: keep tool arguments, tool results "
                             "and the model's answer too. Opt-in, and recorded "
                             "in every line, because a trajectory holds "
                             "whatever the agent read")
    parser.add_argument("--trace-redact", action="append", default=[],
                        metavar="PATTERN", dest="trace_redact",
                        help="with --trace: replace every match with "
                             "[redacted] before a line is written -- in the "
                             "task, and at --trace-full in arguments, "
                             "results and answers too. A regular "
                             "expression, or 'email' or 'token' (API keys, "
                             "GitHub/Slack/AWS tokens, JWTs, bearer "
                             "headers). Repeatable")
    parser.add_argument("--trace-redact-words", action="append", default=[],
                        metavar="FILE", dest="trace_redact_words",
                        help="with --trace: a file of names and phrases, one "
                             "a line (# comments allowed), each replaced "
                             "with [redacted] wherever it appears as a whole "
                             "word, in any case -- what a pattern cannot "
                             "find, like a customer's name. Only the count "
                             "of entries is ever shown. Repeatable")
    parser.add_argument("--fossil", metavar="TRACE_ID", default=None,
                        help="with --trace FILE: print the [[case]] block for "
                             "that recorded turn and exit -- 'every real "
                             "failure leaves a fossil in the suite'. An id "
                             "prefix is enough. Printed rather than written: "
                             "a package's cases.toml belongs to its author "
                             "(append it yourself with >>)")
    parser.add_argument("--trace-prune", metavar="DAYS", type=int,
                        default=None, dest="trace_prune",
                        help="with --trace FILE: remove the turns recorded "
                             "more than DAYS days ago and exit. Never done "
                             "while recording -- a recorder that deletes is "
                             "one nobody can leave on. A line with no "
                             "readable date is kept")
    parser.add_argument("--turns", nargs="?", const="all", default=None,
                        choices=["all", "failed"],
                        help="with --trace FILE: list the recorded turns -- "
                             "id, when, how each ended, the task -- and exit; "
                             "'--turns failed' keeps the ones the cheap "
                             "filter flags (ended badly, or a tool or "
                             "sub-agent failed). A list to choose from, not "
                             "a verdict: pass an id to --fossil")
    parser.add_argument("--mark", nargs=2,
                        metavar=("TRACE_ID", "good|bad|clear"),
                        default=None,
                        help="with --trace FILE: write YOUR verdict on a "
                             "recorded turn into its line and exit. Yantra "
                             "does not decide whether a turn failed; this is "
                             "where the person who did writes it down, for "
                             "--turns and --fossil. 'clear' takes a mark "
                             "back, and a suite's own verdict returns. An id "
                             "prefix is enough")
    parser.add_argument("--why", metavar="TEXT", default=None,
                        help="with --mark: the reason, in your words; "
                             "--fossil uses it as the case's description")
    parser.add_argument("--tool-pack", action="append", default=[],
                        metavar="NAME", dest="tool_pack",
                        help="load the tools an INSTALLED distribution "
                             "publishes under the 'yantra.tools' entry-point "
                             "group (repeatable). Named rather than "
                             "discovered: an agent whose tool list depended "
                             "on what happens to be in the virtualenv would "
                             "be a different agent on every machine. A name "
                             "nothing publishes is an error")
    parser.add_argument("--packs", action="store_true",
                        help="list the tool packs installed in this "
                             "environment -- what --tool-pack could name -- "
                             "and exit. Read from installed metadata: "
                             "nothing is imported, so a broken pack is "
                             "listed rather than run")
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
        tool_packs=tuple(args.tool_pack),
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


def _trace_log(args):
    """``--trace FILE`` -> a TrajectoryLog, or None for the usual session.

    None rather than a null object, because "record nothing" should cost
    nothing: no file handle, no per-event branch inside the tee, and no
    file appearing in a directory nobody asked to have one in.
    """
    if args.trace is None:
        return None
    words = [entry for path in args.trace_redact_words
             for entry in read_word_list(path)]
    return TrajectoryLog(Path(args.trace),
                         detail=FULL if args.trace_full else SHAPE,
                         redact=args.trace_redact, redact_words=words)


def _fossil_mode(args, console: Console) -> int:
    """--fossil ID: a recorded turn as the case it should have left.

    Printed to stdout rather than appended to the package's cases.toml.
    A suite is the author's file -- it has comments in it, and an order,
    and a tool that edits it silently is a tool that surprises somebody
    at the worst moment. ``>> evals/cases.toml`` is one character more
    and entirely theirs.
    """
    if args.trace is None:
        print("error: --fossil reads a recorded turn, so it needs the file "
              "that recorded it: --fossil ID --trace FILE", file=sys.stderr)
        return 2
    try:
        trajectory = TrajectoryLog(Path(args.trace)).get(args.fossil)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    reason = (f"recorded {trajectory.at} on {trajectory.provider}/"
              f"{trajectory.model}; ended {trajectory.outcome}")
    if trajectory.judged_by == "person" and trajectory.why:
        # The person's own words are the best description a regression
        # case can have (notes/74).
        verdict = "good" if trajectory.passed else "bad"
        reason = f"marked {verdict}: {trajectory.why} ({reason})"
    case = case_from_trajectory(trajectory, reason)
    # Written to stdout so it can be redirected; everything else this
    # mode says goes to stderr, or a redirect would capture the advice.
    print(render_case(case), end="")
    print("note: assertions are the SHAPE of that turn -- its task and the "
          "tools it used. Edit the id and description before committing; "
          "the ceiling is what it cost x1.5.", file=sys.stderr)
    if REDACTED in trajectory.task:
        # The case would replay the placeholder, not what was typed
        # (notes/82). Said rather than guessed back: the file never had it.
        print(f"note: the task was scrubbed when it was recorded; put the "
              f"real words back in user_message where it says {REDACTED}",
              file=sys.stderr)
    for child in trajectory.children:
        # Said, not asserted: a child's steps are the delegation's inner
        # workings, and a case that pinned them would break every time the
        # child found a better route to the same answer (notes/63).
        steps = ", ".join(f"{s.name}{step_note(s)}"
                          for s in child.steps) or "no tool calls"
        ended = child.code or "finished"
        print(f"note: sub-agent #{child.number} {child.agent} on "
              f"{child.model}: {steps}; {ended}", file=sys.stderr)
    return 0


def _trace_prune_mode(args, console: Console) -> int:
    """--trace-prune DAYS --trace FILE: drop old turns, say what went.

    A report may name a turn this removes (notes/65); ``--fossil`` then
    says there is no such turn, which is true. Keeping reports and traces
    in step is the operator's retention policy, not a guess made here.
    """
    try:
        pruned = TrajectoryLog(Path(args.trace)).prune(args.trace_prune)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    console.print(f"{escape(str(pruned.path))}: removed {pruned.removed} "
                  f"turn(s) recorded more than {pruned.days} day(s) ago, "
                  f"kept {pruned.kept}")
    if pruned.unreadable:
        console.print(f"[dim]{pruned.unreadable} line(s) with no readable "
                      f"date kept as they were -- age cannot judge them[/dim]")
    return 0


def _mark_mode(args, console: Console) -> int:
    """--mark ID good|bad|clear [--why TEXT] --trace FILE (notes/74, 77)."""
    trace_id, verdict = args.mark
    if verdict not in ("good", "bad", "clear"):
        print(f"error: --mark takes a verdict of good, bad or clear, not "
              f"{verdict!r}", file=sys.stderr)
        return 2
    if verdict == "clear" and args.why is not None:
        print("error: --why is the reason for a verdict, and 'clear' takes "
              "one back; drop --why", file=sys.stderr)
        return 2
    passed = None if verdict == "clear" else verdict == "good"
    try:
        turn = TrajectoryLog(Path(args.trace)).mark(
            trace_id, passed=passed, why=args.why)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    task = f" [dim]({escape(' '.join(turn.task.split())[:60])})[/dim]"
    if verdict == "clear":
        # Say what is left, so nobody has to list the file to find out
        # whether the grader's no came back.
        left = ("unjudged" if turn.passed is None else
                f"back to its grader's {'pass' if turn.passed else 'fail'}")
        console.print(f"{turn.id[:8]} mark cleared, {left}{task}")
        return 0
    console.print(f"{turn.id[:8]} marked {verdict}"
                  + (f": {escape(args.why)}" if args.why else "") + task)
    return 0


def _packs_mode(console: Console) -> int:
    """--packs: what --tool-pack could name, and exit (notes/53).

    A LISTING, NOT A LOAD. ``entry_point_packs`` reads installed metadata
    and imports nothing, so a pack that would blow up on import is still
    listed -- which is when a person most needs to see it is there. It
    follows that the tool names are not shown: those live in the code, and
    finding them out means running it. The entry points are shown instead,
    because they say where to look.
    """
    packs = entry_point_packs()
    if not packs:
        console.print(f"no tool packs installed: nothing in this environment "
                      f"publishes {ENTRY_POINT_GROUP!r}")
        return 0
    for name in sorted(packs):
        entries = packs[name]
        dist = getattr(entries[0], "dist", None)
        version = getattr(dist, "version", None)
        console.print(f"[bold]{escape(name)}[/bold]"
                      + (f" [dim]{escape(str(version))}[/dim]"
                         if version else ""))
        for entry in entries:
            console.print(f"  {escape(entry.name)} = {escape(entry.value)}")
    console.print(f"\n[dim]{len(packs)} pack(s) installed, none loaded. "
                  "An agent gets one only by naming it: --tool-pack NAME, or "
                  "packs = [\"NAME\"] in agent.toml[/dim]")
    return 0


def _turns_mode(args, console: Console) -> int:
    """--turns [failed] --trace FILE: the recording, one line a turn.

    The fossil loop needs an id, and until this the only way to find one
    was to open the JSONL. It decides nothing (notes/57): a turn that
    ended cleanly can still be wrong, and a flagged one may have
    recovered. It shows each turn's cheap signals -- and, for a suite's
    turns, the verdict of the grader somebody wrote (notes/70) -- so a
    person can pick.
    """
    log = TrajectoryLog(Path(args.trace))
    try:
        turns = log.read()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    shown = [t for t in turns if args.turns == "all" or flagged(t)]
    for turn in shown:
        mark = ("[red]✗[/red]" if flagged(turn)
                else "[green]✓[/green]" if turn.judged_by == "person"
                else " ")
        case = f" [dim]case {escape(turn.case)}[/dim]" if turn.case else ""
        why = why_flagged(turn)
        task = " ".join(turn.task.split())
        if len(task) > 60:
            task = task[:57] + "..."
        console.print(f"{mark} {turn.id[:8]}  {turn.at}  "
                      f"{len(turn.steps):>2} tool(s)  {escape(task)}{case}"
                      + (f"\n             [dim]{escape(why)}[/dim]"
                         if why else ""))
    n_flagged = sum(1 for t in turns if flagged(t))
    console.print(f"\n[dim]{len(turns)} turn(s), {n_flagged} flagged"
                  + (f", {log.unreadable} unreadable line(s) skipped"
                     if log.unreadable else "")
                  + " -- by the cheap filter, a case's own grader, or a "
                    "person's --mark; an unflagged turn can still be wrong. "
                    "A case from one: "
                    "--fossil ID "
                    f"--trace {escape(str(log.path))}[/dim]")
    return 0


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


def _failed_cases(cases: list, prior) -> tuple[list, list[str]]:
    """``--failed FILE`` -> the cases that were red in that report, and the
    ids it named that this suite no longer has.

    THE REPORT IS AN INPUT, NOT ONLY AN ANSWER. ``--against`` made a report
    something to be compared with; the question an operator asks first is
    smaller and more practical -- *re-run the ones that broke* -- and it
    needs the same file read at the other end of the run. Nothing else
    about selection changes: the ids come out of the file, the cases come
    out of the suite, and an id in one and not the other is reported rather
    than reconciled.
    """
    red = [c.id for c in prior.cases if not c.passed]
    known = {c.id for c in cases}
    return ([c for c in cases if c.id in set(red)],
            [i for i in red if i not in known])


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
    baselines: list[SuiteRun] = []
    for path in args.against:
        try:
            baselines.append(read_report(Path(path)))
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    # --failed [FILE]: the same file, read at the other end. Resolved
    # BEFORE the provider, because a report full of green is a run that
    # never has to happen.
    pool, filters = every_case, list(args.case)
    if args.failed is not None:
        source = args.failed or (args.against[0] if len(args.against) == 1
                                 else "")
        if not source:
            print("error: --failed with no FILE means the report --against "
                  "names, and there "
                  + ("is no --against" if not args.against else
                     f"are {len(args.against)} of them")
                  + "; pass --failed FILE", file=sys.stderr)
            return 2
        try:
            prior = (baselines[0] if source in args.against and baselines
                     else read_report(Path(source)))
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        pool, gone = _failed_cases(every_case, prior)
        if gone:
            # Loudly, and without stopping: a case that was renamed is the
            # commonest reason, and the run the operator asked for is still
            # worth having.
            console.print(f"[yellow]not in this suite any more: "
                          f"{escape(', '.join(gone))} -- red in "
                          f"{escape(source)}, and gone since[/yellow]")
        if not pool:
            if gone:
                print(f"error: every case that was red in {source} has since "
                      f"left this suite; there is nothing to re-run",
                      file=sys.stderr)
                return 2
            console.print(f"[green]every case in {escape(source)} passed[/green] "
                          f"[dim]-- nothing to re-run[/dim]")
            return 0
        filters.append(f"red in {source}")

    cases, _ = _select_cases(pool, args.case)
    if not cases:
        print(f"error: no case matches {', '.join(args.case)} -- "
              + (f"the cases that were red are: "
                 f"{', '.join(c.id for c in pool)}" if args.failed is not None
                 else f"this suite has: "
                      f"{', '.join(c.id for c in every_case)}"),
              file=sys.stderr)
        return 2
    filtered = " · ".join(filters) if filters else None

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
        # Said up front, both ways (notes/84): a count that stops short is
        # a different kind of sample from one that ran out, and the reader
        # of the lines below should know which they are looking at.
        stop = ("every run is bought (--all-runs)" if args.all_runs else
                "a case stops once it can no longer reach its rate "
                "(--all-runs buys every run)")
        console.print(f"[dim]runs: {args.repeat} per case; a case reports "
                      f"once its runs are in; {stop}[/dim]")
    else:
        # A declared rate that cannot be honoured is worth saying out loud:
        # at one run, 0.7 and 1.0 grade identically, and an author who wrote
        # 0.7 believed they had bought something.
        claimed = sum(1 for c in cases if c.min_pass_rate < 1)
        if claimed:
            most = min(c.min_pass_rate for c in cases if c.min_pass_rate < 1)
            console.print(f"[dim]note: {claimed} case(s) declare a "
                          f"min_pass_rate below 1.0; one run each can only "
                          f"grade them all-or-nothing -- --repeat "
                          f"{perfect_runs_needed(most)} would hold the "
                          f"lowest claim among them at 95%, all green "
                          f"(--case PATTERN points it)[/dim]")

    # --trace under --eval records every run of every case (notes/65), and
    # the report names them. Checked BEFORE the suite: a trace that cannot
    # be written must not cost a suite's worth of tokens to find out.
    trace = _trace_log(args)
    if trace is not None:
        try:
            trace.path.parent.mkdir(parents=True, exist_ok=True)
            trace.path.open("a", encoding="utf-8").close()
        except OSError as exc:
            print(f"error: cannot write trace {trace.path}: {exc}",
                  file=sys.stderr)
            return 2
        console.print(f"[dim]recording runs -> {escape(str(trace.path))} "
                      f"({escape(trace.label)}); a red case names its turns[/dim]")

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
                concurrency=args.eval_async, trace=trace,
            )
            outcomes = asyncio.run(runner.run_suite(
                cases, repeat=args.repeat, on_outcome=report,
                stop_early=not args.all_runs))
        else:
            runner = EvalRunner(
                provider, model, tools=tools, permissions=gate,
                cwd=cwd, spec=spec, provider_name=provider_name,
                trace=trace,
            )
            # Printed as each case lands rather than in one table at the
            # end: a live suite is minutes of silence otherwise, and the
            # first red is the one you want to see soonest.
            outcomes = runner.run_suite(cases, repeat=args.repeat,
                                        on_outcome=report,
                                        stop_early=not args.all_runs)
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
    stopped = [o for o in outcomes if getattr(o, "stopped_early", False)]
    if stopped:
        unbought = sum(o.planned - o.attempts for o in stopped)
        tally += (f" · {len(stopped)} case(s) stopped early, {unbought} "
                  f"run(s) not bought")
    if filtered is not None:
        tally += f" · {len(every_case) - len(cases)} case(s) not run"
    cost = ""
    priced = [o.usd for o in outcomes if o.usd is not None]
    if priced and sum(priced) > 0:
        # Omitted entirely at $0: a local run bills nothing, and printing
        # "$0.0000" beside a two-minute suite reads as a broken meter
        # rather than as the free road working (notes/48).
        cost = f" · ${sum(priced):.4f}"
        if any(o.usd is None for o in outcomes if o.ran_model):
            cost += " (priced models only)"
    console.print(f"\n{verdict} · {tally} · {spent} tokens{cost}"
                  + (f" · {free_cases} case(s) cost nothing" if free_cases
                     else ""))
    _dearest_note(console, outcomes)
    _evidence_note(console, outcomes)

    run = record_run(outcomes, suite=label or (spec.name or "agent"),
                     provider=provider_name, model=model, repeat=args.repeat,
                     cases_in_suite=len(every_case),
                     filtered=filters or None,
                     pricing=(PriceRecord.for_model(provider_name, model)
                              if needs_model else None),
                     package=fingerprint(spec),
                     # The weights behind a local tag, and each case as it
                     # was loaded (notes/72): both can change a rate while
                     # every name stays the same.
                     weights=(_what_answered(provider_name, settings, model,
                                             outcomes)
                              if needs_model else None),
                     definitions={c.id: c.fingerprint for c in every_case})
    if args.report is not None:
        try:
            write_report(Path(args.report), run)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        console.print(f"[dim]report: {escape(args.report)}[/dim]")
    if trace is not None and any(o.traces and not o.passed for o in outcomes):
        console.print(f"[dim]a red run's turn becomes a case with: --fossil "
                      f"ID --trace {escape(str(trace.path))}[/dim]")
    if len(baselines) == 1:
        _render_comparison(console, compare(baselines[0], run))
    elif baselines:
        _render_matrix(console, line_up([*baselines, run]),
                       [Path(p).stem for p in args.against] + ["this run"],
                       sort=args.sort)
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
    if cmp.weights_changed:
        # The name says nothing moved and the provider says otherwise
        # (notes/72 on Ollama, notes/75 on a hosted model).
        if _is_digest(after.weights):
            said = (f"same tag, different weights: {before.weights} → "
                    f"{after.weights} -- {escape(after.model)} was re-pulled "
                    f"between these runs")
        else:
            said = (f"same model name, different snapshot answered: "
                    f"{escape(before.weights)} → {escape(after.weights)}")
        console.print(f"[yellow]{said}, so what moved below may be the "
                      f"model[/yellow]")
    if cmp.package_changed:
        # The version says nothing moved, and the files say otherwise
        # (notes/68). Everything below may be the edit, not the model.
        console.print(f"[yellow]same version, different package: "
                      f"{before.package} → {after.package} -- the agent was "
                      f"edited without a version bump, so what moved below "
                      f"may be the edit[/yellow]")
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
                if not delta.movement_is_evidence:
                    tally += ("  [dim](intervals overlap: not evidence of a "
                              "change)[/dim]")
            if delta.definition_changed:
                tally += "  [dim](the case was edited between these runs)[/dim]"
            if delta.kind == "broke" and delta.after.traces:
                # Where it broke, when the run kept its turns (notes/65).
                tally += (f"  [dim]turns: "
                          f"{' '.join(t[:8] for t in delta.after.traces)}"
                          f"[/dim]")
            console.print(f"  {marks[delta.kind]}  {escape(delta.id)}{tally}")
        elif delta.rate_moved:
            # Same verdict, different count. A case going 9/10 -> 6/10 is
            # still green and is the most useful line on this page -- but
            # only if it moved by more than noise, which the line now says
            # rather than leaving to the reader (notes/47).
            moved += 1
            word = ("[green]up[/green]" if delta.rate_direction == "up"
                    else "[yellow]down[/yellow]")
            noise = ("" if delta.movement_is_evidence
                     else "  [dim](intervals overlap: not evidence of a "
                          "change)[/dim]")
            console.print(f"  rate {word}  {escape(delta.id)}  "
                          f"{delta.before.tally} → {delta.after.tally}{noise}")
    if not moved:
        console.print("  [dim]no case changed verdict or pass count[/dim]")
    spent = cmp.tokens_moved
    if spent:
        console.print(f"[dim]tokens: {before.tokens} → {after.tokens} "
                      f"({spent:+d})[/dim]")
    moved_usd = cmp.usd_moved
    if moved_usd is not None and (before.usd or after.usd):
        # Each side priced on the day it ran, so this is what the two runs
        # ACTUALLY cost rather than what today's table says they would
        # (notes/48). It is also the only figure that means anything when
        # the two runs used different models.
        console.print(f"[dim]cost: ${before.usd:.4f} → ${after.usd:.4f} "
                      f"({moved_usd:+.4f})[/dim]")
    elif before.usd is None and after.usd is not None:
        console.print("[dim]cost: that run recorded no dollar figure "
                      "(unpriced model, or written before costs were "
                      "kept)[/dim]")
    if (before.usd or after.usd) and cmp.prices_moved is not None:
        # The half of a cost line a figure cannot carry: whether the
        # vendor moved, or the agent did (notes/62). Tokens are printed
        # above, so a reader can see which one explains the dollars.
        if cmp.prices_moved:
            console.print(f"[dim]prices: {escape(before.pricing.describe)} → "
                          f"{escape(after.pricing.describe)} -- part of the "
                          f"cost change is the price, not the agent[/dim]")
        else:
            console.print("[dim]prices: the same rates both runs, so the "
                          "cost change is the agent's[/dim]")
    elif before.usd or after.usd:
        console.print("[dim]prices: one of these runs did not write its "
                      "rates down, so nothing can say whether the price or "
                      "the agent moved[/dim]")


def _reports_mode(args, console: Console) -> int:
    """--reports: what the reports on disk say, without making another one.

    NOTE 42 SAID NO, AND WAS RIGHT AT THE TIME. A comparison only existed
    at the end of a run, because the run was the thing being judged and
    the files were JSON so anything else could read them. That held until
    there were four reports on disk and the only way to line them up was
    to pay for a fifth. Reading is not judging: this prints no verdict and
    exits 0 whatever the reports say, so it cannot become a second gate by
    accident. A file that cannot be read is still an error, for the same
    reason as ``--against``: a comparison asked for and not delivered is
    the silent pass.
    """
    paths: list[Path] = []
    for name in args.reports:
        path = Path(name)
        if path.resolve() in {p.resolve() for p in paths}:
            # Twice the same file is twice the same run, which pooling
            # would count as twice the evidence.
            console.print(f"[yellow]{escape(name)} named twice; read "
                          f"once[/yellow]")
            continue
        paths.append(path)
    runs: list[SuiteRun] = []
    for path in paths:
        try:
            runs.append(read_report(path))
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.pool or args.pool_json:
        pools = pool(runs)
        _render_pools(console, pools)
        if args.pool_json:
            try:
                write_pool(Path(args.pool_json), pools)
            except ConfigError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            console.print(f"\n[dim]pool: {escape(args.pool_json)}[/dim]")
        return 0
    if len(runs) < 2:
        print("error: one report is not a comparison; name two or more, or "
              "add --pool to see what its counts are evidence of",
              file=sys.stderr)
        return 2
    if len(runs) == 2:
        _render_comparison(console, compare(runs[0], runs[1]))
    else:
        stems = [p.stem for p in paths]
        labels = stems if len(set(stems)) == len(stems) else [str(p) for p in paths]
        _render_matrix(console, line_up(runs), labels, sort=args.sort)
    return 0


def _is_digest(value: str | None) -> bool:
    """Twelve hex characters: an Ollama weights digest, not a snapshot name."""
    return bool(value) and len(value) == 12 and all(
        c in "0123456789abcdef" for c in value)


def _what_answered(provider_name: str, settings, model: str,
                   outcomes) -> str | None:
    """The report's ``weights``: what model actually answered the suite.

    On Ollama, the digest of the weights behind the tag (notes/72). On a
    hosted provider there is no digest to ask for, so it is what the
    provider NAMED as having answered -- the dated snapshot behind an
    alias, and OpenAI's build fingerprint (notes/75). Several identities
    (an alias that moved mid-suite) are all kept, joined, so the change
    is visible rather than averaged. None when nothing says.
    """
    name = canonical_provider(provider_name)
    if name == "ollama":
        return weights(name, getattr(settings, "base_url", None), model)
    served = sorted({s for o in outcomes for s in getattr(o, "served", [])})
    return " + ".join(served) or None


def _render_pools(console: Console, pools: list[Pool]) -> None:
    """Each (suite, model) pool, one line per case, then what it adds up to.

    Never a verdict (notes/47): ``below`` is a statement about evidence,
    and the exit code is 0 whatever it says.
    """
    pairs = {(group.suite, group.where) for group in pools}
    if len(pairs) > 1:
        console.print(f"[yellow]{len(pairs)} different suite/model pairs in "
                      f"these reports; each is pooled on its own -- runs of "
                      f"different models or package versions are not samples "
                      f"of one rate[/yellow]")
    marks = {"holds": "[green]holds[/green]", "below": "[red]below[/red]",
             "unsettled": "[yellow]unsettled[/yellow]"}
    said_split: set[tuple[str, str]] = set()
    said_weights: set[tuple[str, str, str | None]] = set()
    for group in pools:
        package = (f" [dim](package {group.package or 'unknown'})[/dim]"
                   if group.split_from else "")
        if group.weights_split:
            package += (f" [dim](weights {escape(group.weights or 'unknown')})"
                        f"[/dim]")
        console.print(f"\n[bold]pooled[/bold] {len(group.runs)} run(s) of "
                      f"{escape(group.suite)} on {escape(group.where)}"
                      f"{package}\n[dim]{escape(group.span)}[/dim]")
        if group.split_from and (group.suite, group.where) not in said_split:
            said_split.add((group.suite, group.where))
            console.print(f"  [yellow]{escape(group.suite)} ran as "
                          f"{group.split_from} different packages under one "
                          f"version; each is pooled on its own -- bump the "
                          f"version when the agent changes[/yellow]")
        if (group.weights_split and (group.suite, group.where, group.package)
                not in said_weights):
            said_weights.add((group.suite, group.where, group.package))
            kind = ("weights (re-pulled)" if _is_digest(group.weights)
                    else "snapshots answering under one name")
            console.print(f"  [yellow]{escape(group.where)} pointed at "
                          f"{group.weights_split} different {kind} across "
                          f"these runs; each is pooled on its own[/yellow]")
        if group.split_from and group.package is None:
            console.print("  [dim]these reports predate fingerprints, so "
                          "which package they ran cannot be told; pooled "
                          "apart from the ones that can[/dim]")
        elif group.unknown and group.package is not None:
            console.print(f"  [dim]{group.unknown} report(s) predate "
                          f"fingerprints; counted as package "
                          f"{group.package}, the only one these runs "
                          f"show[/dim]")
        if not group.cases:
            console.print("  [dim]no case in these runs reached a model; "
                          "there is nothing to pool[/dim]")
        width = max([len(c.id) for c in group.cases] + [4])
        said_edited: set[str] = set()
        for case in group.cases:
            lo, hi = case.confidence
            claim = f"claims {case.min_pass_rate:g}"
            if case.claim_changed:
                claim += " (newest; it changed)"
            if case.definitions and case.id not in said_edited:
                # One row per definition of an edited case (notes/72),
                # oldest first, and the reason said once.
                said_edited.add(case.id)
                console.print(f"  [yellow]{escape(case.id)} was edited "
                              f"between these runs ({case.definitions} "
                              f"definitions); each is pooled on its "
                              f"own[/yellow]")
            line = (f"  {escape(case.id).ljust(width)}  "
                    f"{case.tally.rjust(7)} over {case.runs} run(s) · "
                    f"{lo:.2f}..{hi:.2f} · {claim} · "
                    f"{marks[case.standing]}"
                    + (f" [dim](definition {case.definition or 'unknown'})"
                       f"[/dim]" if case.definitions else ""))
            console.print(line)
            cost = _pooled_cost(case)
            if cost:
                console.print(f"    [dim]{cost}[/dim]")
            if case.stopped:
                # Honest counts that lean low (notes/84): a run stops on
                # failures, never on passes, so the reader is told before
                # they read "below" as the whole story.
                console.print(f"    [dim]stopped early in {case.stopped} "
                              f"report(s): a count that stops on failures "
                              f"leans low when pooled (--all-runs buys full "
                              f"samples)[/dim]")
            if case.disagree:
                console.print("    [yellow]two of these runs do not overlap "
                              "at all -- something changed between them, and "
                              "the pooled number averages two different "
                              "agents[/yellow]")
        grew = [c for c in group.cases
                if c.usd_growth is not None and c.usd_growth > 1]
        if grew:
            # The one line a long list of costs hides: which case got
            # dearer fastest. Named, not judged -- a case that now does
            # more work for the same verdict may be worth it.
            worst = max(grew, key=lambda c: c.usd_growth or 0)
            console.print(f"  [dim]dearest move: {escape(worst.id)} costs "
                          f"x{worst.usd_growth:.1f} per run what it did in "
                          f"the oldest priced report"
                          + (" (the rates moved too)" if worst.price_moved
                             else "") + "[/dim]")
        heavier = [c for c in group.cases
                   if c.tokens_growth is not None
                   and c.tokens_growth >= 1 + TOKEN_JITTER]
        if heavier:
            # The same line in tokens, for the road with no dollars and for
            # a case that got hungrier while a price cut hid it (notes/67).
            # Skipped only when it would repeat the dearest line: same case,
            # same factor, so the tokens are the whole of the move.
            heaviest = max(heavier, key=lambda c: c.tokens_growth or 0)
            same = (grew and heaviest is worst
                    and f"{worst.usd_growth:.1f}"
                    == f"{heaviest.tokens_growth:.1f}")
            if not same:
                console.print(f"  [dim]heaviest move: {escape(heaviest.id)} "
                              f"uses x{heaviest.tokens_growth:.1f} the tokens "
                              f"per run it did in the oldest report[/dim]")
        if group.roster_only:
            console.print(f"  [dim]roster only, nothing to pool: "
                          f"{escape(', '.join(group.roster_only))}[/dim]")
        unsettled = [c for c in group.cases if c.standing == "unsettled"]
        if unsettled:
            need = max(perfect_runs_needed(c.min_pass_rate) for c in unsettled)
            console.print(f"  [dim]{len(unsettled)} case(s) unsettled: the "
                          f"evidence spans their claim. More runs narrow it "
                          f"-- {need} all-green runs in one go would hold the "
                          f"hardest claim among them on their own[/dim]")


def _pooled_cost(case) -> str:
    """"$0.0012 → $0.0031 per run (x2.6) · 1,200 → 3,100 tokens per run
    (x2.6)" for one pooled case, or "".

    No dollars for a case no report priced, or one that was free both
    times: a local model's $0 beside every line reads as a broken meter.
    Tokens only once there are two reports to set against each other, and
    only when they moved past run-to-run jitter -- except under a moved
    price, where "about the same tokens" is the line that says the change
    was the vendor's.
    """
    parts: list[str] = []
    first, last = case.usd_first, case.usd_last
    dollars_moved = False
    if first is not None and last is not None and (first or last):
        if case.runs == 1 or first == last:
            parts.append(f"${last:.4f} per run")
        else:
            dollars_moved = True
            line = f"${first:.4f} → ${last:.4f} per run"
            if case.usd_growth is not None:
                line += f" (x{case.usd_growth:.1f})"
            parts.append(line)
    tokens = _pooled_tokens(case, dollars_moved)
    if tokens:
        parts.append(tokens)
    line = " · ".join(parts)
    if line and case.price_moved and dollars_moved:
        line += " -- the rates moved between those reports, not only the agent"
    return line


#: How far a case's tokens per run may move before the pool calls it a
#: move (notes/67). The same case on the same model reads a slightly
#: different amount and writes a slightly different answer every run; on
#: the researcher suite two green runs a minute apart differed by up to
#: 4%, and a line under every case saying "x1.0" was noise.
TOKEN_JITTER = 0.10


def _pooled_tokens(case, dollars_moved: bool) -> str:
    """The token half of a pooled case's line (notes/67), or ""."""
    first, last = case.tokens_first, case.tokens_last
    growth = case.tokens_growth
    if case.runs == 1 or first is None or last is None or growth is None:
        return ""
    if abs(growth - 1) < TOKEN_JITTER:
        return (f"about the same tokens per run (~{round(last):,})"
                if dollars_moved else "")
    return (f"{round(first):,} → {round(last):,} tokens per run "
            f"(x{growth:.1f})")


def _dearest_note(console: Console, outcomes) -> None:
    """The one case that spent the most, and its share of the bill.

    Named, not judged (notes/82): the expensive case is usually the
    interesting one -- a loop that re-reads a file, a sub-agent that never
    needed spawning -- and in a list of twenty lines it is the one the eye
    slides past. Said only when there are two or more priced cases to
    choose between; one case is trivially the dearest.
    """
    priced = [o for o in outcomes if o.usd]
    if len(priced) < 2:
        return
    dearest = max(priced, key=lambda o: o.usd)
    total = sum(o.usd for o in priced)
    console.print(f"[dim]dearest case: {escape(dearest.case_id)} · "
                  f"${dearest.usd:.4f} · {dearest.usd / total:.0%} of what "
                  f"the priced cases cost[/dim]")


def _evidence_note(console: Console, outcomes) -> None:
    """Cases that PASSED on samples too few to hold the claim they made.

    Never a verdict and never an exit code (notes/47). A case that
    declares min_pass_rate = 0.7 and goes 3 for 3 has cleared its
    threshold on evidence that does not separate it from a case holding
    44% of the time, and the person who most needs to know that is the one
    reading a green line.
    """
    thin = [o for o in outcomes if o.passed and not o.claim_is_supported]
    if not thin:
        return
    console.print(f"[yellow]{len(thin)} case(s) passed on evidence that does "
                  f"not reach the rate they claim[/yellow]")
    for outcome in thin:
        lo, _ = outcome.confidence
        need = perfect_runs_needed(outcome.min_pass_rate)
        console.print(f"  [dim]{escape(outcome.case_id)}  "
                      f"{outcome.passes}/{outcome.attempts} · true rate could "
                      f"be as low as {lo:.2f} · claims "
                      f"{outcome.min_pass_rate:g} · --repeat {need} would "
                      f"settle it, all green[/dim]")


def _render_matrix(console: Console, table: Matrix, labels: list[str],
                   sort: str | None = None) -> None:
    """Three or more runs side by side, one column each.

    Not three comparisons stacked: the question a table answers has no
    "before" in it ("which of these models should we use?"), and stacking
    differences makes the reader do the join in their head. The verdict is
    still untouched -- this prints under a run whose exit code was decided
    before any of these files were opened (notes/49).

    THE TOTALS LIVE UNDER THEIR COLUMNS (notes/83). Passed, tokens and
    dollars are a footer, padded by the same arithmetic as the cells, so
    reading down a column ends at that column's sum. A TABLE WIDER THAN
    THE TERMINAL IS SPLIT, NEVER WRAPPED: the columns are cut into blocks
    that fit, each with the case names repeated on its left, because a
    terminal that wraps a row puts half of it under the wrong header.
    """
    if sort is not None:
        table = table.sorted_by(sort)
    console.print(f"\n[bold]across {len(table.runs)} runs[/bold] "
                  f"{escape(table.runs[-1].suite)}")
    for label, run in zip(labels, table.runs, strict=True):
        rates = (f" · at {run.pricing.describe}"
                 if run.usd and run.pricing is not None else "")
        console.print(f"  [dim]{escape(label)}: {escape(run.where)} · "
                      f"{run.at}{rates}[/dim]")
    if not table.comparable:
        console.print("[yellow]not every run graded every case; a blank cell "
                      "is a case that run did not have[/yellow]")
    if sort == "disagree":
        split = sum(1 for i in table.ids if table.disagrees(i))
        console.print(f"[dim]sorted: {split} case(s) the runs disagree on "
                      f"first, then the rest in the last run's order[/dim]"
                      if split else
                      "[dim]sorted: every run that graded a case agreed on "
                      "it, so the order is the last run's[/dim]")
    elif sort == "red":
        console.print("[dim]sorted: most red cells first[/dim]")

    def text_of(cell) -> str:
        if cell is None:
            return "--"                      # this run did not grade it
        mark = "\u2713" if cell.passed else "\u2717"
        return f"{mark} {cell.tally}" if cell.attempts > 1 else mark

    # The footer: one row per total, and dollars only when some run had
    # any -- a table of local runs with "$0" under every column reads as
    # a broken meter (notes/48). Beside a priced run, a free one says
    # "free" and an unpriced one "--": zero and unknown stay apart.
    footer = [("passed", [f"{run.passed}/{len(run.cases)}"
                          for run in table.runs]),
              ("tokens", [str(run.tokens) for run in table.runs])]
    if any(run.usd for run in table.runs):
        footer.append(("cost", ["--" if run.usd is None
                                else f"${run.usd:.4f}" if run.usd
                                else "free" for run in table.runs]))

    rows = table.rows
    width = max([len(i) for i in table.ids]
                + [len(name) for name, _ in footer] + [4])
    columns = [max([len(label)] + [len(text_of(row[i])) for _, row in rows]
                   + [len(values[i]) for _, values in footer])
               for i, label in enumerate(labels)]
    blocks = _column_blocks(columns, console.width - 4 - width)
    if len(blocks) > 1:
        console.print(f"[dim]{len(labels)} columns are wider than this "
                      f"terminal ({console.width}); shown in {len(blocks)} "
                      f"blocks, the case names repeated on each[/dim]")
    for block in blocks:
        if len(blocks) > 1:
            console.print(f"  [dim]columns {block[0] + 1}-{block[-1] + 1} "
                          f"of {len(labels)}[/dim]")
        header = "  ".join(labels[i].rjust(columns[i]) for i in block)
        console.print(f"  [dim]{'case'.ljust(width)}  {escape(header)}[/dim]")
        for case_id, row in rows:
            cells = []
            for i in block:
                cell, body = row[i], text_of(row[i])
                # PAD THE PLAIN TEXT, then colour it. Markup inside a rjust
                # counts the colour codes as characters and shifts every
                # column after this one.
                pad = " " * (columns[i] - len(body))
                if cell is None:
                    cells.append(pad + "[dim]--[/dim]")
                else:
                    colour = "green" if cell.passed else "red"
                    cells.append(f"{pad}[{colour}]{body}[/{colour}]")
            console.print(f"  {escape(case_id).ljust(width)}  "
                          + "  ".join(cells))
        for name, values in footer:
            line = "  ".join(values[i].rjust(columns[i]) for i in block)
            console.print(f"  [dim]{name.ljust(width)}  {line}[/dim]")


def _column_blocks(columns: list[int], room: int) -> list[list[int]]:
    """Column indices cut into runs that fit in ``room`` characters.

    Every block holds at least one column, however narrow the terminal:
    a column wider than the screen still has to be shown somewhere, and
    one wrapped column is readable where ten wrapped ones are not.
    """
    blocks: list[list[int]] = [[]]
    used = 0
    for i, w in enumerate(columns):
        need = w + (2 if blocks[-1] else 0)
        if blocks[-1] and used + need > room:
            blocks.append([])
            used, need = 0, w
        blocks[-1].append(i)
        used += need
    return blocks


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
    elif outcome.attempts > 1 or outcome.stopped_early:
        needed = ("" if outcome.min_pass_rate >= 1
                  else f" (needs {outcome.required_passes})")
        # A stopped case shows the runs it was SET, so "1/4" is never read
        # as a four-run sample that happened to be short (notes/84).
        of = (f" of {outcome.planned} · stopped, out of reach"
              if outcome.stopped_early else "")
        # The interval beside the fraction, because the fraction alone has
        # been read as a rate since the day it was printed (notes/47).
        band = describe(outcome.passes, outcome.attempts)
        detail = (f"{outcome.marks} {outcome.passes}/{outcome.attempts} "
                  f"runs{needed}{of} · {band} · "
                  f"{outcome.duration_seconds:.1f}s · "
                  f"{outcome.tokens_used} tok")
    else:
        run = outcome.runs[-1]
        detail = (f"{run.duration_seconds:.1f}s · {run.tokens_used} tok · "
                  f"{run.iterations_used} it · "
                  f"{', '.join(run.tool_calls_seen) or 'no tools'}")
    if outcome.usd:
        # Beside the tokens, per case (notes/82): the total says what the
        # suite cost, and only this says which case spent it. Left off at
        # $0 and at unknown for the same reason the total is.
        detail += f" · ${outcome.usd:.4f}"
    console.print(f"  {mark}  {escape(outcome.case_id)}  "
                  f"[dim]{escape(detail)}[/dim]")
    for failure in outcome.failures:
        console.print(f"        {failure}", style="red",
                      markup=False, highlight=False)
    red = [r.trace for r in outcome.runs if r.trace and not r.passed]
    if red:
        # The failing runs only: a green run's turn is not what anyone
        # opens the trace file for (notes/65).
        console.print(f"        [dim]turn{'s' if len(red) > 1 else ''}: "
                      f"{' '.join(t[:8] for t in red)}[/dim]")


def _browse_login(url: str, console: Console) -> int:
    """--browse-login URL: headed one-time login on the persistent profile.

    Deliberately provider-free -- no model, no API key, so this runs
    BEFORE main()'s credential resolution. The human beats the login
    wall by hand once; the profile keeps the session for every later
    headless run ([notes/28](../notes/28-browser-tools.md)).
    """
    from yantra.tools.browser import (  # lazy: [browse] extra
        LoginInterrupted, check_profile_reachable, run_login_session)

    profile = browser_profile()
    if profile is None:
        print("error: --browse-login needs somewhere to KEEP the login: put\n"
              "  YANTRA_BROWSER_PROFILE=~/yantra-browser-profile"
              "\nin .env first (see .env.example)", file=sys.stderr)
        return 2
    executable = browser_executable()
    command = browser_login_command(executable)
    try:
        check_profile_reachable(profile, executable)  # before promising
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    console.print(f"[bold]login setup[/bold] · profile {profile}\n"
                  f"{escape(command or 'a visible Chromium')} is opening"
                  f"{f' at {url}' if url else ''} -- log in yourself (2FA "
                  "and\ncaptchas are yours to beat), then CLOSE THE WINDOW. "
                  "Everything you leave\nsigned-in here, the agent finds "
                  "signed-in later.")
    if command is None:
        # The window about to open is Playwright's, and Playwright's
        # window is an AUTOMATED one -- which is the exact thing the
        # large identity providers refuse, headed or not. Say so before
        # the refusal, not after: from inside the browser it reads as a
        # problem with the password.
        console.print(
            "[yellow]note[/yellow] this is Playwright's own Chromium, and "
            "sign-in pages that\ncheck for automation (Google among them) "
            "will refuse it. To log into\nthose, name a browser you already "
            "have -- it then opens with no\nautomation attached at all:\n"
            "  YANTRA_BROWSER_EXECUTABLE=chrome     (or a path, e.g. "
            "/snap/bin/brave)\n"
            "Use the SAME value for agent runs: a profile belongs to the "
            "browser\nthat wrote it.")
    try:
        cookies = run_login_session(profile, url)
    except LoginInterrupted as stop:
        # Ctrl-C asked the browser to quit rather than killing it, so
        # there are two different endings and they get different
        # sentences: one where the login is on disk, and one where the
        # browser would not close and nothing is certain.
        if stop.closed:
            console.print(f"\n[yellow](cancelled)[/yellow] the browser was "
                          f"asked to close and did, so what you signed "
                          f"into is kept -- {stop.cookies} cookies")
        else:
            console.print("\n[yellow](cancelled)[/yellow] the browser was "
                          "killed before it finished closing, so a sign-in "
                          "you just finished may not have reached disk")
        return 130
    except KeyboardInterrupt:
        # Playwright's own window, which has no quit to ask for.
        console.print("\n[yellow](cancelled -- the window was killed rather "
                      "than closed, so a sign-in you just finished may not "
                      "have reached disk)[/yellow]")
        return 130
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not cookies:
        # The old success line printed here unconditionally, on top of a
        # profile that might hold nothing at all. A count can be wrong
        # out loud, which is the point of counting.
        console.print(
            "[yellow]nothing was saved[/yellow] -- the profile holds no "
            "cookies, so the agent\nwill meet the same wall you just "
            "beat. Sign in FULLY, then close the\nwindow (closing it is "
            "what writes the session to disk).")
        return 1
    console.print(f"[green]profile saved[/green] -- {cookies} cookies; "
                  "future browser_* sessions start from these logins")
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
    if args.wait_budget is not None and not args.web:
        print("error: --wait-budget times the browser's approval prompts; "
              "the terminal asks you directly and nothing waits there. "
              "Add --web", file=sys.stderr)
        return 2
    if (args.wait_budget is None) != (args.on_timeout is None):
        print("error: --wait-budget and --on-timeout go together: one says "
              "how long a turn may wait, the other what silence means "
              "(deny or allow), and neither has a default",
              file=sys.stderr)
        return 2
    if args.wait_budget is not None and args.wait_budget <= 0:
        print(f"error: --wait-budget must be positive (got "
              f"{args.wait_budget:g}); to wait without limit, leave it off",
              file=sys.stderr)
        return 2
    if args.trace_full and args.trace is None:
        print("error: --trace-full says what to keep, and --trace says "
              "where; pass --trace FILE", file=sys.stderr)
        return 2
    # The word list is the same kind of flag as the patterns (notes/86),
    # so it keeps the same company and is refused in the same places.
    scrubbing = args.trace_redact or args.trace_redact_words
    flag = ("--trace-redact" if args.trace_redact
            else "--trace-redact-words")
    if scrubbing and args.trace is None:
        print(f"error: {flag} says what to scrub from a recording, "
              "and --trace says where it goes; pass --trace FILE",
              file=sys.stderr)
        return 2
    if scrubbing and (
            args.fossil is not None or args.mark is not None
            or args.turns is not None or args.trace_prune is not None):
        print(f"error: {flag} scrubs turns as they are recorded; a "
              "file already written is not re-scrubbed, so drop it from "
              "--fossil/--mark/--turns/--trace-prune", file=sys.stderr)
        return 2
    if scrubbing:
        try:
            _trace_log(args)          # a bad pattern fails here, not mid-run
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    if args.fossil is not None and (args.eval or args.build or args.web
                                    or args.prompt or args.prompt_positional):
        print("error: --fossil prints one recorded turn as a case and exits; "
              "drop --eval/--build/--web/--prompt/PROMPT", file=sys.stderr)
        return 2
    if args.mark is not None and args.trace is None:
        print("error: --mark writes into a recording, so it needs the file: "
              "--mark ID good|bad|clear --trace FILE", file=sys.stderr)
        return 2
    if args.why is not None and args.mark is None:
        print("error: --why is the reason for a --mark", file=sys.stderr)
        return 2
    if args.mark is not None and (
            args.eval or args.build or args.web or args.prompt
            or args.prompt_positional or args.fossil is not None
            or args.reports is not None or args.turns is not None
            or args.trace_prune is not None or args.trace_full):
        print("error: --mark writes one verdict and exits; drop "
              "--eval/--build/--web/--fossil/--reports/--turns/"
              "--trace-prune/--trace-full/--prompt/PROMPT", file=sys.stderr)
        return 2
    if args.turns is not None and args.trace is None:
        print("error: --turns lists a recording, so it needs the file: "
              "--turns --trace FILE", file=sys.stderr)
        return 2
    if args.turns is not None and (
            args.eval or args.build or args.web or args.prompt
            or args.prompt_positional or args.fossil is not None
            or args.trace_full or args.reports is not None
            or args.trace_prune is not None):
        print("error: --turns lists a recording and exits; drop "
              "--eval/--build/--web/--fossil/--reports/--trace-prune/"
              "--trace-full/--prompt/PROMPT", file=sys.stderr)
        return 2
    if args.packs and (
            args.eval or args.build or args.web or args.prompt
            or args.prompt_positional or args.fossil is not None
            or args.reports is not None or args.turns is not None
            or args.mark is not None or args.trace_prune is not None
            or args.tool_pack):
        print("error: --packs lists what is installed and exits; drop "
              "--eval/--build/--web/--fossil/--reports/--turns/--mark/"
              "--trace-prune/--tool-pack/--prompt/PROMPT", file=sys.stderr)
        return 2
    if args.trace_prune is not None and args.trace is None:
        print("error: --trace-prune removes old turns from a recording, so "
              "it needs the file: --trace-prune DAYS --trace FILE",
              file=sys.stderr)
        return 2
    if args.trace_prune is not None and (
            args.eval or args.build or args.web or args.prompt
            or args.prompt_positional or args.fossil is not None
            or args.trace_full or args.reports is not None):
        print("error: --trace-prune tidies a recording and exits; drop "
              "--eval/--build/--web/--fossil/--reports/--trace-full/"
              "--prompt/PROMPT",
              file=sys.stderr)
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
         or args.no_mcp or args.report or args.against or args.all_runs
         or args.failed is not None) and not args.eval):
        print("error: --async, --repeat, --all-runs, --case, --failed, "
              "--no-mcp, --report and --against belong to --eval -- they say "
              "how an "
              "acceptance "
              "suite is driven, and a session has one trajectory",
              file=sys.stderr)
        return 2
    if args.sort is not None and not (
            (args.eval and len(args.against) >= 2)
            or (args.reports is not None and len(args.reports) >= 3
                and not (args.pool or args.pool_json))):
        # Two runs are a difference and a pool is a list by case; neither
        # has rows to order, and a flag that did nothing would read as one
        # that had worked (notes/83).
        print("error: --sort orders the rows of a table, which needs three "
              "or more runs: --eval with --against twice or more, or "
              "--reports with three or more files and no --pool",
              file=sys.stderr)
        return 2
    if (args.pool or args.pool_json) and args.reports is None:
        print("error: --pool and --pool-json add up reports; name them with "
              "--reports FILE [FILE ...]", file=sys.stderr)
        return 2
    if args.reports is not None and (args.eval or args.build or args.web
                                     or args.prompt or args.prompt_positional
                                     or args.fossil is not None):
        print("error: --reports reads reports and runs nothing; drop "
              "--eval/--build/--web/--fossil/--prompt/PROMPT (to compare a "
              "NEW run, use --eval --against FILE)", file=sys.stderr)
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

    if args.packs:
        return _packs_mode(console)

    # A recording is a file too (notes/67).
    if args.trace_prune is not None:
        return _trace_prune_mode(args, console)
    if args.turns is not None:
        return _turns_mode(args, console)
    if args.mark is not None:
        return _mark_mode(args, console)

    # Reports are files. Reading them needs no package, no provider and no
    # key, so this goes before any of those are resolved (notes/62).
    if args.reports is not None:
        return _reports_mode(args, console)

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

    # Reading a recorded turn back needs no provider either -- it is a
    # file and a printer -- so it goes beside the acceptance run rather
    # than behind the session wiring.
    if args.fossil is not None:
        return _fossil_mode(args, console)

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
        if args.wait_budget is not None:
            web_session.set_wait_budget(args.wait_budget,
                                        on_timeout=args.on_timeout)

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
    # The same disclosure for tools that arrived by pip. Named by PACK
    # rather than by tool: what the operator is being told is whose code
    # ran, and the tools themselves are in the roster like any other.
    if spec.tool_packs:
        console.print(f"[dim]tool packs: {', '.join(spec.tool_packs)}[/dim]")
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
        agent.budget.cap_reply = args.budget_cap_reply
        console.print(f"[dim]budget: {agent.budget.describe()}[/dim]")
    elif args.budget_notice or args.budget_cap_reply:
        console.print("[yellow]--budget-notice and --budget-cap-reply do "
                      "nothing without a ceiling: set --max-usd, or run a "
                      "package with [budget] max_usd_per_turn[/yellow]")

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
                mcp=mcp_manager, trace=_trace_log(args))

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
            web_session.trace = repl.trace   # --trace records the browser too
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
