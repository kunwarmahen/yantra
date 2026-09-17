"""Evals: trajectory-level checks that the harness produces RIGHT BEHAVIOR.

Unit tests pin mechanics with scripted providers (deterministic, free,
every commit). Evals ask a different question -- did a REAL model, over
a whole trajectory, finish the task, use the right tools, stay in
budget? -- so they cost money, are probabilistic, and belong on a
merge/nightly cadence, not per-commit. "Agent complexity is only
justified when you can define precise task-success criteria" (Hamel
Husain, via book ch19); without evals, agent features are debt.

Four metric classes, all checked per case and ACCUMULATED (a case can
fail for five reasons at once -- seeing all five is how you fix it):

* completion   -- it finished (crashes and iteration caps become
                 failed results, never raised exceptions)
* correctness  -- task-specific, deterministic where possible
                 (``check_answer``); reserve LLM judging (``judge``)
                 for genuinely subjective criteria
* process      -- required tools used, forbidden tools avoided
* cost         -- token and iteration ceilings; a correct answer at
                 50K tokens is worse than the same answer at 5K

The recording trick: the runner wraps every tool in a proxy that
appends each ACTUAL execution to a shared list before delegating, so
process checks see what really ran -- including work a sub-agent did
through the same tool instances (documented conflation: delegation is
the parent's doing).

Every real failure in production should leave a fossil in the suite:
``case_from_trace`` turns an observed failure into a regression case
whose budget is observed-cost x 1.5.

Two of those four classes cost money to check. One does not. The ROSTER
-- what the agent is offered at all -- is knowable the moment the agent
is built, so ``has_tools``/``lacks_tools`` are graded before a request
goes out, and a case that fails one never reaches a model. That is also
why a roster failure SHORT-CIRCUITS rather than accumulating: a trajectory
produced by an agent whose tool list is wrong is a trajectory belonging
to some other agent, and paying for it buys nothing. A case with only
roster assertions needs no ``user_message`` at all -- it is a free
assertion about configuration, which is the one thing a trajectory check
cannot see.

A DECLARED CHILD'S list is free in the same way and is a different object:
``subagent_has_tools``/``subagent_lacks_tools`` grade what a package's
``[[subagent]]`` would be offered if anybody delegated. The parent's own
roster cannot reach it -- a child sits inside the parent's registry, so
the parent's list is a ceiling over the whole package and says nothing
about the floor each child was given.

And because a trajectory is a DIE ROLL, one run is one sample.
``min_pass_rate`` is the author's honest claim about a case ("7 of 10"),
``repeat`` is the operator's decision about how much evidence to buy, and
``CaseOutcome`` holds the n runs plus the verdict the threshold turns them
into. Default 1.0 over one run is exactly the old behaviour, which is why
nothing had to change to keep it.

Pass ``spec=`` and the cases run against a whole agent PACKAGE instead
of a bare agent -- its prompt, its skills, its own tools, its admission
policy -- which is what lets a package ship the evidence that it works
(eval_suite.py). Without it the runners measure this harness; with it
they measure somebody's agent, and the difference is the whole of the
acceptance gate.
"""

from __future__ import annotations

import asyncio
import fnmatch
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar

from yantra.agent import Agent
from yantra.async_agent import AsyncAgent
from yantra.errors import ConfigError
from yantra.providers.base import Provider, ProviderSettings, collect
from yantra.spec import AgentSpec
from yantra.subagent import (DeclaredSubagent, SpawnSubagent,
                             SubagentSpawner, resolve_child_tools)
from yantra.tools.base import Tool, ToolContext, ToolRegistry
from yantra.types import Message, TextBlock


class OfflineProvider(Provider):
    """A provider for a run that will not make a request, and knows it.

    Grading a ROSTER means building the agent, and building an agent takes
    a provider -- which is how a suite of nothing but ``has_tools`` claims
    came to need an API key to reach zero tokens. Assembling the agent by
    hand instead was the obvious fix and the wrong one: ``AgentSpec.build``
    owns the order things are wired in (spec.py), and a second copy of that
    order is a second thing to keep in step.

    So the provider is the part that gets replaced, and every method on it
    raises. A roster-only run never calls one; if it ever does, the suite's
    own accounting of which cases need a model is wrong, and that is worth
    a loud error rather than a mysteriously empty answer.
    """

    name = "none"

    def __init__(self) -> None:
        super().__init__(ProviderSettings(api_key="", base_url=""))

    def _refuse(self):
        raise ConfigError(
            "this eval run resolved no provider because every selected case "
            "grades the roster, and something asked for a model anyway -- "
            "which means a case with a user_message was counted as free")

    def complete(self, **kwargs):
        self._refuse()

    def stream(self, **kwargs):
        self._refuse()

    async def acomplete(self, **kwargs):
        self._refuse()

    async def astream(self, **kwargs):
        self._refuse()
        yield  # pragma: no cover -- unreachable; keeps this an async generator


@dataclass(slots=True)
class EvalCase:
    """A golden trajectory: task + expected outcomes + scorer.

    Two of the assertion families here are free and two are not.
    ``required_tools``/``forbidden_tools`` grade the TRAJECTORY and need a
    run to grade; ``has_tools``/``lacks_tools`` grade the ROSTER and need
    only a built agent. A case that asserts nothing but the roster may
    leave ``user_message`` empty -- there is nothing for a model to do.
    """

    id: str
    description: str
    user_message: str = ""
    system: str | None = None
    required_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    #: Roster assertions: fnmatch patterns against the tools the agent is
    #: OFFERED, graded before any request. "no way to write to disk" is
    #: ``lacks_tools = ["write_file", "edit_file", "bash"]`` -- a claim
    #: about configuration, which no trajectory check can reach.
    has_tools: list[str] = field(default_factory=list)
    lacks_tools: list[str] = field(default_factory=list)
    #: The same two claims about a DECLARED sub-agent's list, keyed by the
    #: child's name (or ``"*"`` for every child this package declares).
    #: ``{"fact_checker": ["web_*"]}`` under ``subagent_lacks_tools`` is
    #: the assertion that widening a child goes through a gate -- which
    #: the parent's own roster cannot make, because a child's list is a
    #: fact about something that does not exist until somebody delegates.
    subagent_has_tools: dict[str, list[str]] = field(default_factory=dict)
    subagent_lacks_tools: dict[str, list[str]] = field(default_factory=dict)
    check_answer: Callable[[str], bool] | None = None
    max_tokens: int | None = None       # input+output ceiling for the turn
    max_iterations: int | None = None   # model round-trips ceiling
    #: The fraction of repeated runs that must pass. 1.0 -- every run --
    #: is the default and the only honest value at n=1; a lower one is a
    #: claim that needs ``repeat`` to mean anything (see CaseOutcome).
    min_pass_rate: float = 1.0
    setup: Callable[[Agent], None] | None = None  # per-case agent wiring
                                      # (e.g. spawn_setup() -> sub-agents)

    def __post_init__(self) -> None:
        # A case with neither a task nor a roster assertion is a case that
        # cannot fail, and a gate made of those reports green forever --
        # the same bug an empty cases.toml would be (eval_suite.py).
        if not self.user_message and not self.grades_a_roster:
            raise ValueError(
                f"case {self.id!r} has nothing to do: give it a user_message, "
                f"or a roster assertion (has_tools/lacks_tools, or the "
                f"subagent_ pair), which needs no model")
        if not 0 < self.min_pass_rate <= 1:
            raise ValueError(
                f"case {self.id!r}: min_pass_rate must be greater than 0 and "
                f"at most 1 (got {self.min_pass_rate})")

    @property
    def grades_a_roster(self) -> bool:
        """Whether this case asserts anything that costs no tokens -- about
        the agent's own tool list, or about a declared child's."""
        return bool(self.has_tools or self.lacks_tools
                    or self.subagent_has_tools or self.subagent_lacks_tools)

    @property
    def needs_a_model(self) -> bool:
        """Whether grading this case costs anything. False for a case whose
        only assertions are about the roster."""
        return bool(self.user_message)


@dataclass(slots=True)
class EvalResult:
    """One run of one case."""

    case_id: str
    passed: bool
    failures: list[str]
    final_answer: str
    tokens_used: int
    iterations_used: int
    tool_calls_seen: list[str]
    duration_seconds: float
    error: str | None = None            # crash description, if any
    #: False when no request was ever made -- a roster-only case, or a case
    #: whose roster assertion failed and was not paid for.
    ran_model: bool = True


@dataclass(slots=True)
class CaseOutcome:
    """n runs of one case, and the verdict a threshold turns them into.

    A trajectory is a die roll, so one run is one sample. This is the type
    that lets a suite say "7 of 10" instead of pretending a single green
    run settled the question -- ``passes`` over ``attempts`` against the
    case's own ``min_pass_rate``.

    The verdict is a COUNT comparison, not a float one: 7 >= 7 rather than
    0.7 >= 0.7, because the second is a coin flip on the machine's rounding
    and a gate that changes its mind about arithmetic is not a gate.
    """

    case_id: str
    runs: list[EvalResult]
    min_pass_rate: float = 1.0

    @classmethod
    def of(cls, case: EvalCase, runs: Sequence[EvalResult]) -> CaseOutcome:
        """The outcome of running ``case``, collapsing what should not repeat.

        A run that never reached a model is DETERMINISTIC -- the same
        roster, graded the same way, n times. Reporting it as "0 of 5 runs"
        would dress a fact up as a statistic, so only the first is kept.
        """
        kept = list(runs)
        if kept and not kept[0].ran_model:
            kept = kept[:1]
        return cls(case_id=case.id, runs=kept,
                   min_pass_rate=case.min_pass_rate)

    @property
    def attempts(self) -> int:
        return len(self.runs)

    @property
    def passes(self) -> int:
        return sum(1 for r in self.runs if r.passed)

    @property
    def required_passes(self) -> int:
        """How many of ``attempts`` must pass. Rounded UP: a threshold that
        rounded down would pass a suite the author said should fail."""
        return max(1, math.ceil(round(self.min_pass_rate * self.attempts, 6)))

    @property
    def pass_rate(self) -> float:
        return self.passes / self.attempts if self.attempts else 0.0

    @property
    def passed(self) -> bool:
        return bool(self.attempts) and self.passes >= self.required_passes

    @property
    def ran_model(self) -> bool:
        return any(r.ran_model for r in self.runs)

    @property
    def tokens_used(self) -> int:
        return sum(r.tokens_used for r in self.runs)

    @property
    def duration_seconds(self) -> float:
        return sum(r.duration_seconds for r in self.runs)

    @property
    def marks(self) -> str:
        """The runs as one glyph each, in order -- 'PASS 4/5' hides which
        ones, and a case that fails its first two reads differently from
        one that fails its last two."""
        return "".join("\u2713" if r.passed else "\u2717" for r in self.runs)

    @property
    def failures(self) -> list[str]:
        """Why it failed, once per distinct reason.

        At n=1 this is the run's own list, verbatim. Above that, each
        distinct failure carries how OFTEN it happened: "required tool not
        used: outline (2 of 5 runs)" is a flaky prompt, and the same line
        at 5 of 5 is a broken one.
        """
        if self.attempts == 1:
            return list(self.runs[0].failures)
        counts: dict[str, int] = {}
        for run in self.runs:
            for failure in run.failures:
                counts[failure] = counts.get(failure, 0) + 1
        return [f"{failure} ({count} of {self.attempts} runs)"
                for failure, count in counts.items()]

    @property
    def last_answer(self) -> str:
        return self.runs[-1].final_answer if self.runs else ""


def roster_of(agent: Agent | AsyncAgent) -> list[str]:
    """The tools the agent under test is OFFERED, sorted.

    ``specs()`` rather than ``names()`` on purpose: a disabled tool is
    registered but never reaches the model and cannot be called, so
    counting it would make ``lacks_tools`` lie in the direction that
    matters. What this cannot see is an MCP server's tools -- they arrive
    from a live session the host owns, and ``--eval`` opens none -- so a
    roster assertion about ``mcp__*`` is an assertion about an empty set.
    """
    return sorted(spec.name for spec in agent.registry.specs())


def roster_failures(case: EvalCase, roster: Sequence[str]) -> list[str]:
    """Grade the roster. Zero tokens, no model, no request.

    Patterns are fnmatch, like the admission policy's own
    (``tools.allow``), because the useful form of "it cannot write" is
    often a shape rather than a list: ``lacks_tools = ["browser_*"]``.
    """
    failures: list[str] = []
    for pattern in case.has_tools:
        if not any(fnmatch.fnmatch(name, pattern) for name in roster):
            failures.append(f"not on the roster: {pattern} "
                            f"(roster: {_listing(roster)})")
    for pattern in case.lacks_tools:
        hits = [name for name in roster if fnmatch.fnmatch(name, pattern)]
        if hits:
            found = _listing(hits)
            failures.append(f"on the roster and should not be: {found}"
                            if hits == [pattern] else
                            f"on the roster and should not be: {pattern} "
                            f"matches {found}")
    return failures


#: The one pattern a ``subagent_`` key may be: every child this package
#: declares. Not fnmatch -- see ``subagent_failures`` on why the key is a
#: NAME and not a pattern language.
EVERY_CHILD = "*"


def declared_rosters(
        agent: Agent | AsyncAgent) -> dict[str, tuple[list[str], list[str]]]:
    """Every sub-agent the package DECLARED, and what each would be offered:
    ``{name: (offered, unreachable)}``.

    Read off the REGISTRY rather than the spec, for the reason ``roster_of``
    uses ``specs()``: a declared child the package's own admission policy
    turned away is not a child, it is a line in a file, and a disabled one
    cannot be called this session. Grading either would tell an author
    their gate covers a delegation that cannot happen.
    """
    rosters: dict[str, tuple[list[str], list[str]]] = {}
    for tool in agent.registry:
        # UNWRAP FIRST. Every tool in a suite's registry is a recording
        # proxy (RecordingRegistry), so an isinstance check against the
        # raw registry finds the children and the same check inside a
        # running suite finds none -- which reads as "this package
        # declares no sub-agents" and passes a lacks_ assertion for the
        # worst available reason.
        inner = getattr(tool, "_inner", tool)
        if not isinstance(inner, DeclaredSubagent):
            continue
        if agent.registry.is_disabled(tool.name):
            continue
        rosters[tool.name] = resolve_child_tools(agent.registry,
                                                 inner.declared.tools)
    return rosters


def subagent_failures(
        case: EvalCase,
        children: Mapping[str, tuple[Sequence[str], Sequence[str]]],
) -> list[str]:
    """Grade the DECLARED children's tool lists. Free, like the roster.

    THE PARENT'S ROSTER IS ALREADY A CEILING -- a child is built from the
    parent's registry, so nothing a package excluded can reach a child by
    way of delegation, and ``lacks_tools = ["bash"]`` has always covered
    the whole package. What it cannot say is anything about the FLOOR: a
    fact-checker deliberately kept off the network sits well inside a
    ceiling that permits ``web_fetch``, and widening it moves nothing the
    old assertions could see.

    A CHILD NAMED BY AN ASSERTION MUST EXIST, in both directions. The
    tempting reading of ``subagent_lacks_tools = {"fact_checkr": [...]}``
    is that a child which is not there cannot use anything, so the claim
    holds -- and that is a green case reporting on a typo. Renaming a
    sub-agent turns its assertions red, which is precisely the alarm this
    exists to install.

    The key is a NAME, or ``*`` for every declared child. Deliberately not
    fnmatch, though the values are: a pattern key that matched no child
    would be the vacuous pass above wearing a disguise, and ``*`` is the
    one generalisation worth having -- "no child of this package may
    write to disk" covers the children added after the case was written,
    which is the strongest form of the alarm.
    """
    failures: list[str] = []
    for wanted, book in ((True, case.subagent_has_tools),
                         (False, case.subagent_lacks_tools)):
        for key, patterns in book.items():
            names = (sorted(children) if key == EVERY_CHILD
                     else [key] if key in children else [])
            if not names:
                failures.append(
                    "no sub-agents are declared, so an assertion about "
                    "every child checks nothing" if key == EVERY_CHILD else
                    f"no sub-agent called {key!r} is declared "
                    f"(declared: {_listing(sorted(children))})")
                continue
            for name in names:
                offered, unreachable = children[name]
                if unreachable:
                    # Grading the rest would be grading a fiction: this
                    # child cannot be built, so nothing it is "offered"
                    # will ever reach a model.
                    failures.append(
                        f"{name} cannot be built: it declares "
                        f"{_listing(unreachable)}, which this agent does "
                        f"not offer")
                    continue
                failures.extend(_child_failures(name, offered, patterns,
                                                wanted=wanted))
    # One broken child named by both books says so once.
    return list(dict.fromkeys(failures))


def _child_failures(name: str, offered: Sequence[str],
                    patterns: Sequence[str], *, wanted: bool) -> list[str]:
    """``has``/``lacks`` against one child's list, phrased so the failure
    names the child -- "not on the roster: grep" in a package with four
    sub-agents sends the reader to the wrong file."""
    failures: list[str] = []
    for pattern in patterns:
        hits = [tool for tool in offered if fnmatch.fnmatch(tool, pattern)]
        if wanted and not hits:
            failures.append(f"not on {name}'s roster: {pattern} "
                            f"({name}: {_listing(offered)})")
        elif not wanted and hits:
            found = _listing(hits)
            failures.append(
                f"on {name}'s roster and should not be: {found}"
                if hits == [pattern] else
                f"on {name}'s roster and should not be: {pattern} "
                f"matches {found}")
    return failures


def _listing(names: Sequence[str], limit: int = 8) -> str:
    """Tool names for an error message, truncated -- a default registry has
    two dozen and a wall of them is not diagnostics."""
    if not names:
        return "nothing"
    shown = ", ".join(names[:limit])
    return shown if len(names) <= limit else f"{shown}, +{len(names) - limit} more"


def _free_result(case: EvalCase, failures: list[str],
                 duration: float) -> EvalResult:
    """The result of a case that never reached a model: a roster-only case,
    or one whose roster assertion failed before anything was spent."""
    return EvalResult(
        case_id=case.id, passed=not failures, failures=failures,
        final_answer="", tokens_used=0, iterations_used=0,
        tool_calls_seen=[], duration_seconds=duration, ran_model=False,
    )


class _RecordingTool(Tool):
    """Delegating proxy that records every real execution of a tool.

    Instance attributes shadow the ABC's ClassVars, so the wrapped
    tool's identity (name, schema, read_only) is preserved for spec(),
    permission gates, and sub-agent catalog copies alike.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[dict[str, Any]] = {}

    def __init__(self, inner: Tool, seen: list[str]) -> None:
        self._inner = inner
        self._seen = seen
        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters
        self.read_only = inner.read_only

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return self._inner.summary(args, ctx)

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        self._seen.append(self._inner.name)  # execution, not merely lookup
        return self._inner.run(args, ctx)

    async def arun(self, args: dict[str, Any], ctx: ToolContext) -> str:
        """Forwarded, not inherited. The base ``arun`` pushes ``run`` onto
        a thread, which is right for a tool that only has a blocking body
        and WRONG for one that overrode ``arun`` with a real async
        implementation -- that tool would silently run its synchronous
        path under the async runner, which is the one place the difference
        is invisible until something behaves differently in a suite than
        it does in production."""
        self._seen.append(self._inner.name)
        return await self._inner.arun(args, ctx)


class RecordingRegistry(ToolRegistry):
    """A registry that records every tool it holds -- including late arrivals.

    Wrapping a registry once was enough while a case's agent was built by
    hand. It stopped being enough the moment cases run against an
    ``AgentSpec``: a package's own tools, ``load_skill`` and MCP tools all
    register AFTER the registry is handed over, and a proxy applied
    up-front would never see them. So the recording lives in ``register``
    rather than in a one-time sweep -- the same move ``admit_only`` made,
    for the same reason.

    IDEMPOTENT: a tool that is already a proxy is unwrapped first, so an
    execution is recorded exactly once no matter how many layers it sits
    under.
    """

    def __init__(self, seen: list[str]) -> None:
        super().__init__()
        self._seen = seen

    def register(self, tool: Tool) -> None:
        inner = getattr(tool, "_inner", None)
        super().register(_RecordingTool(inner if inner is not None else tool,
                                        self._seen))


def recording_registry(base: ToolRegistry, seen: list[str]) -> RecordingRegistry:
    """A recording copy of ``base``. Iterates the registry rather than
    asking for each name, so a tool the operator disabled copies across as
    disabled instead of raising on the way out."""
    reg = RecordingRegistry(seen)
    for tool in base:
        reg.register(tool)
    for name in base.disabled_names():
        reg.disable(name)
    return reg


def _agent_for(case: EvalCase, seen: list[str], *, agent_cls,
               spec: AgentSpec | None, provider: Provider, model: str,
               provider_name: str | None, base_tools: ToolRegistry,
               permissions: Callable[[Any], bool], max_iterations: int,
               context_window: int | None, cwd: Any, extra: dict | None = None):
    """One fresh agent for one case, built from the spec when there is one.

    THE SPEC IS THE POINT when a package is under test. A hand-built
    ``Agent(provider, model=..., tools=...)`` has none of the package's
    prompt, skills, own tools or admission policy, so a suite that graded
    one would be reporting a verdict on a different agent than the one it
    named. ``AgentSpec.build`` owns that assembly (spec.py) and is the only
    thing allowed to perform it.

    The runner's own ceilings are FALLBACKS behind the package's: an agent
    whose manifest says twenty iterations is under test at twenty, and the
    runner's default only fills a blank.
    """
    registry = recording_registry(base_tools, seen)
    extra = extra or {}
    if spec is None:
        kwargs: dict[str, Any] = {}
        if context_window is not None:
            kwargs["context_window"] = context_window
        if cwd is not None:
            kwargs["cwd"] = cwd
        return agent_cls(
            provider, model=model, system=case.system, tools=registry,
            max_iterations=max_iterations, permissions=permissions,
            **kwargs, **extra,
        )
    if spec.max_iterations is None:
        spec = replace(spec, max_iterations=max_iterations)
    if spec.context_window is None and context_window is not None:
        spec = replace(spec, context_window=context_window)
    if case.system:
        # Library callers may still override; the package FORMAT refuses
        # the key outright (eval_suite.py) because a suite that swaps the
        # prompt is grading something other than the package it shipped in.
        spec = replace(spec, system=case.system)
    build = spec.build if agent_cls is Agent else spec.build_async
    return build(permissions=permissions, cwd=Path(cwd) if cwd else None,
                 registry=registry, provider=provider,
                 provider_name=provider_name, model=model, **extra)


class EvalRunner:
    """Runs cases sequentially against a live provider.

    Sequential is deliberate for 20-50 cases: deterministic ordering,
    easy rate-limiting, readable logs. Fresh Agent per case -- golden
    trajectories are independent, no history bleed.
    """

    def __init__(self, provider: Provider, model: str, *, tools: ToolRegistry,
                 permissions: Callable[[Any], bool], max_iterations: int = 25,
                 context_window: int | None = None,
                 cwd: Any = None, spec: AgentSpec | None = None,
                 provider_name: str | None = None) -> None:
        self.provider = provider
        self.model = model
        self.base_tools = tools
        self.permissions = permissions
        self.max_iterations = max_iterations
        self.context_window = context_window
        self.cwd = cwd  # sandbox root for the cases' tool calls
        #: The agent under test, when the cases belong to a package. Without
        #: it the runner builds a bare agent -- fine for measuring the
        #: harness, wrong for measuring somebody's agent (see _agent_for).
        self.spec = spec
        self.provider_name = provider_name
        self.seen: list[str] = []  # shared with the proxies; cleared per case

    def _agent(self, case: EvalCase, seen: list[str], **extra):
        return _agent_for(
            case, seen, agent_cls=Agent, spec=self.spec,
            provider=self.provider, model=self.model,
            provider_name=self.provider_name, base_tools=self.base_tools,
            permissions=self.permissions, max_iterations=self.max_iterations,
            context_window=self.context_window, cwd=self.cwd, extra=extra,
        )

    def run_case(self, case: EvalCase) -> EvalResult:
        self.seen.clear()
        agent = self._agent(case, self.seen)
        if case.setup is not None:
            # per-case wiring seam: the runner builds a fresh Agent, the
            # case decides what EXTRA machinery it gets. Re-wrap after so
            # setup-registered tools are recorded too (idempotent for the
            # originals -- no double-counting).
            case.setup(agent)
            agent.registry = recording_registry(agent.registry, self.seen)
        start = time.monotonic()
        # THE FREE CHECK GOES FIRST. The roster is knowable now, so a case
        # that asserts one either gets its verdict for nothing or stops
        # here: a trajectory from an agent with the wrong tool list belongs
        # to a different agent, and buying it teaches nothing.
        free = (roster_failures(case, roster_of(agent))
                + subagent_failures(case, declared_rosters(agent)))
        if free or not case.needs_a_model:
            return _free_result(case, free, time.monotonic() - start)
        try:
            response = agent.run(case.user_message)
        except Exception as exc:
            # completion failure is a RESULT, not a raise: one broken
            # case must not abort the suite (ProviderError, iteration
            # cap RuntimeError, anything unexpected)
            return EvalResult(
                case_id=case.id, passed=False,
                failures=[f"crashed: {type(exc).__name__}: {exc}"],
                final_answer="", tokens_used=agent.total_usage.input_tokens
                + agent.total_usage.output_tokens,
                iterations_used=self._count_iterations(agent),
                tool_calls_seen=list(self.seen),
                duration_seconds=time.monotonic() - start,
                error=f"{type(exc).__name__}: {exc}",
            )
        duration = time.monotonic() - start
        answer = response.message.text().strip()
        tokens = agent.total_usage.input_tokens + agent.total_usage.output_tokens
        return self._result(case, agent, list(self.seen), answer, tokens,
                            duration)

    def run_all(self, cases: list[EvalCase]) -> list[EvalResult]:
        """One run per case, flat. The old shape, kept: a caller that never
        asked for repeats has no use for an aggregate wrapper."""
        return [self.run_case(case) for case in cases]

    def evaluate(self, case: EvalCase, *, repeat: int = 1,
                 on_result: Callable[[EvalResult], None] | None = None
                 ) -> CaseOutcome:
        """Run one case ``repeat`` times and apply its threshold.

        ``on_result`` fires as each run lands, because n runs of a live case
        is minutes of silence otherwise and the operator who paid for the
        evidence should watch it arrive.
        """
        runs: list[EvalResult] = []
        for _ in range(max(1, repeat)):
            result = self.run_case(case)
            runs.append(result)
            if on_result is not None:
                on_result(result)
            if not result.ran_model:
                break  # nothing was rolled; rolling it again changes nothing
        return CaseOutcome.of(case, runs)

    def run_suite(self, cases: list[EvalCase], *, repeat: int = 1,
                  on_result: Callable[[EvalResult], None] | None = None,
                  on_outcome: Callable[[CaseOutcome], None] | None = None
                  ) -> list[CaseOutcome]:
        """The gate: every case, ``repeat`` runs each, thresholds applied."""
        outcomes: list[CaseOutcome] = []
        for case in cases:
            outcome = self.evaluate(case, repeat=repeat, on_result=on_result)
            outcomes.append(outcome)
            if on_outcome is not None:
                on_outcome(outcome)
        return outcomes

    def _result(self, case: EvalCase, agent: Agent, seen: list[str],
                answer: str, tokens: int, duration: float) -> EvalResult:
        """Score a finished trajectory. SHARED with AsyncEvalRunner --
        identical grading rules are the whole point of the twin."""
        iterations = EvalRunner._count_iterations(agent)
        failures: list[str] = []
        if case.check_answer is not None and not case.check_answer(answer):
            failures.append("answer check failed")
        for name in case.required_tools:
            if name not in seen:
                failures.append(f"required tool not used: {name}")
        for name in case.forbidden_tools:
            if name in seen:
                failures.append(f"forbidden tool used: {name}")
        if case.max_tokens is not None and tokens > case.max_tokens:
            failures.append(f"over token budget: {tokens} > {case.max_tokens}")
        if case.max_iterations is not None and iterations > case.max_iterations:
            failures.append(
                f"over iteration budget: {iterations} > {case.max_iterations}")
        return EvalResult(
            case_id=case.id, passed=not failures, failures=failures,
            final_answer=answer, tokens_used=tokens,
            iterations_used=iterations, tool_calls_seen=seen,
            duration_seconds=duration,
        )

    @staticmethod
    def _count_iterations(agent: Agent) -> int:
        return sum(1 for m in agent.history if m.role == "assistant")


class AsyncEvalRunner:
    """The async twin of EvalRunner: same cases, same scoring, one event
    loop driving several trajectories at once.

    Grading is SHARED (``EvalRunner._result``) -- identical rules is the
    whole point; only the driving differs. Two deliberate differences:

    * each case gets its OWN seen-list. The sync runner shares one and
      clears it per case (fine sequentially); concurrent cases would
      scribble on each other.
    * ``run_all`` and ``run_suite`` bound in-flight work with a semaphore
      -- golden trajectories are independent but the provider behind them
      is not; N-at-once multiplies request rate. Under ``repeat`` the unit
      bounded is a RUN rather than a case: five repeats of three cases is
      fifteen independent trajectories, and bounding by case would leave
      the semaphore half empty while one slow case finished alone.

    The delegate case runs here unchanged: SubagentSpawner reads only
    attributes AsyncAgent mirrors (registry/provider/model/permissions/...
    ), and an AWAITED spawn under an async parent builds an async child,
    so a suite grades the same child shape production runs
    ([notes/40](notes/40-a-package-that-delegates.md)).
    """

    def __init__(self, provider: Provider, model: str, *, tools: ToolRegistry,
                 permissions: Callable[[Any], bool], max_iterations: int = 25,
                 context_window: int | None = None, cwd: Any = None,
                 concurrency: int = 4, spec: AgentSpec | None = None,
                 provider_name: str | None = None) -> None:
        self.provider = provider
        self.model = model
        self.base_tools = tools
        self.permissions = permissions
        self.max_iterations = max_iterations
        self.context_window = context_window
        self.cwd = cwd
        self.concurrency = concurrency
        self.spec = spec
        self.provider_name = provider_name

    async def run_case(self, case: EvalCase) -> EvalResult:
        seen: list[str] = []  # per case: concurrent cases must not share
        agent = _agent_for(
            case, seen, agent_cls=AsyncAgent, spec=self.spec,
            provider=self.provider, model=self.model,
            provider_name=self.provider_name, base_tools=self.base_tools,
            permissions=self.permissions, max_iterations=self.max_iterations,
            context_window=self.context_window, cwd=self.cwd,
        )
        if case.setup is not None:
            case.setup(agent)
            agent.registry = recording_registry(agent.registry, seen)
        start = time.monotonic()
        # the sync rule, verbatim
        free = (roster_failures(case, roster_of(agent))
                + subagent_failures(case, declared_rosters(agent)))
        if free or not case.needs_a_model:
            return _free_result(case, free, time.monotonic() - start)
        try:
            response = await agent.run(case.user_message)
        except Exception as exc:
            return EvalResult(
                case_id=case.id, passed=False,
                failures=[f"crashed: {type(exc).__name__}: {exc}"],
                final_answer="",
                tokens_used=agent.total_usage.input_tokens
                + agent.total_usage.output_tokens,
                iterations_used=EvalRunner._count_iterations(agent),
                tool_calls_seen=list(seen),
                duration_seconds=time.monotonic() - start,
                error=f"{type(exc).__name__}: {exc}",
            )
        answer = response.message.text().strip()
        tokens = agent.total_usage.input_tokens + agent.total_usage.output_tokens
        # grading rules are the SYNC runner's, verbatim
        return EvalRunner._result(
            self, case, agent, list(seen), answer,
            tokens, time.monotonic() - start)

    async def run_all(self, cases: list[EvalCase]) -> list[EvalResult]:
        gate = asyncio.Semaphore(self.concurrency)

        async def _bounded(case: EvalCase) -> EvalResult:
            async with gate:
                return await self.run_case(case)

        # gather preserves SUBMISSION order: results align with `cases`
        # no matter who finishes first (same contract as tool batches).
        return await asyncio.gather(*(_bounded(c) for c in cases))

    async def evaluate(self, case: EvalCase, *, repeat: int = 1) -> CaseOutcome:
        outcome, = await self.run_suite([case], repeat=repeat)
        return outcome

    async def run_suite(self, cases: list[EvalCase], *, repeat: int = 1,
                        on_outcome: Callable[[CaseOutcome], None] | None = None
                        ) -> list[CaseOutcome]:
        """The gate, concurrently: every case, ``repeat`` runs each.

        THE UNIT OF CONCURRENCY IS A RUN, not a case. Five repeats of three
        cases is fifteen independent trajectories, and bounding them by case
        would leave the semaphore half empty while one slow case finished
        its fifth run alone.

        ``on_outcome`` fires in COMPLETION order as each case's runs all
        land, so a long suite reads as it arrives; the returned list is in
        submission order regardless, because a report you diff between
        models must not reorder itself when the network is slow.
        """
        gate = asyncio.Semaphore(self.concurrency)

        async def _attempt(case: EvalCase) -> EvalResult:
            async with gate:
                return await self.run_case(case)

        async def _one(case: EvalCase) -> CaseOutcome:
            # A roster-only case is never launched n times: it reaches no
            # model, so the repeats would be n copies of one fact.
            attempts = max(1, repeat) if case.needs_a_model else 1
            runs = await asyncio.gather(*(_attempt(case)
                                          for _ in range(attempts)))
            outcome = CaseOutcome.of(case, runs)
            if on_outcome is not None:
                on_outcome(outcome)
            return outcome

        return await asyncio.gather(*(_one(c) for c in cases))


def judge(provider: Provider, model: str, *, question: str, answer: str,
          reference: str | None = None,
          criteria: str | None = None) -> tuple[bool, str]:
    """LLM-as-judge for genuinely SUBJECTIVE criteria.

    Deliberately boring: the judge replies 'PASS' or 'FAIL' plus one
    sentence; anything not starting with PASS counts as FAIL (a confused
    judge must fail closed). Two known limits (book ch19): judge and
    candidate sharing a brain correlates their errors -- prefer a
    different provider/model for judging -- and a judge can't grade
    past its own capability ceiling. A deterministic check_answer beats
    this whenever a function would do; wire it in as
    ``check_answer=lambda a: judge(...)[0]``.
    """
    system = ("You are an impartial grader of AI answers. Reply with "
              "'PASS' or 'FAIL' followed by a one-sentence reason. "
              "Nothing else.")
    parts = [f"QUESTION:\n{question}", f"CANDIDATE ANSWER:\n{answer}"]
    if reference:
        parts.append(f"REFERENCE ANSWER:\n{reference}")
    parts.append("CRITERIA:\n" + (criteria or
                "accuracy, completeness, and relevance to the question"))
    response = collect(provider.stream(
        messages=[Message(role="user",
                          content=[TextBlock(text="\n\n".join(parts))])],
        system=system, model=model,
        tools=[],  # the grader runs with no tools -- by contract, not omission
    ))
    text = response.message.text().strip()
    return text.upper().startswith("PASS"), text


def case_from_trace(trace_id: str, failure_reason: str, user_message: str, *,
                    tokens_used: int, required_tools: list[str] | None = None,
                    **case_kwargs: Any) -> EvalCase:
    """Turn a real failure into a regression case (the fossil rule).

    Budget heuristic: whatever the failing run SPENT becomes the ceiling
    (x1.5 headroom) -- the fix must not cost more than the bug.
    """
    return EvalCase(
        id=f"trace-{trace_id[:8]}",
        description=failure_reason,
        user_message=user_message,
        max_tokens=int(tokens_used * 1.5) or None,
        required_tools=required_tools or [],
        **case_kwargs,
    )


def spawn_setup(*, max_per_session: int = 3,
                default_max_iterations: int = 10) -> Callable[[Agent], None]:
    """Case.setup factory: opt ONE case's agent into sub-agents.

    The runner builds a fresh Agent per case, and the spawn budget lives
    on the spawner -- so every spawn-capable case needs its own wiring;
    this attaches spawner + tool to that case's agent only. Eval-tight
    child budgets (3 spawns / 10 iterations) keep a runaway delegator
    from torching the suite.

    With this in place, ``required_tools=["spawn_subagent"]`` asserts
    delegation actually HAPPENED (the narrate-instead-of-delegate failure
    mode shows up as "required tool not used"), and work the CHILD does
    through shared tool instances lands in the same seen-list --
    documented conflation: delegation is the parent's doing.
    """
    def setup(agent: Agent) -> None:
        spawner = SubagentSpawner(agent, max_per_session=max_per_session,
                                  default_max_iterations=default_max_iterations)
        agent.registry.register(SpawnSubagent(spawner))
    return setup


def summarize(results: Sequence[EvalResult | CaseOutcome]) -> str:
    """Human-readable report: one line per case + the aggregate.

    Takes either shape -- a flat ``EvalResult`` per case, or a
    ``CaseOutcome`` holding n runs -- because the caller who bought repeats
    should not also have to write a second reporter for them.
    """
    lines = []
    for r in results:
        mark = "✓" if r.passed else "✗"
        # the aggregate carries the totals; the LAST run carries the shape
        # of one trajectory (its tools, its round-trips), which is what a
        # reader is looking at when a case failed four times out of five
        last = r.runs[-1] if isinstance(r, CaseOutcome) else r
        runs = (f"{r.passes}/{r.attempts} runs · "
                if isinstance(r, CaseOutcome) and r.attempts > 1 else "")
        tools = ",".join(last.tool_calls_seen) or "-"
        line = (f"{mark} {r.case_id}: {runs}{r.tokens_used}tok · "
                f"{last.iterations_used}it · {r.duration_seconds:.1f}s · "
                f"[{tools}]")
        if r.failures:
            line += f" -- {'; '.join(r.failures)}"
        lines.append(line)
    passed = sum(1 for r in results if r.passed)
    lines.append(f"{passed}/{len(results)} passed")
    return "\n".join(lines)
