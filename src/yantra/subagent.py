"""Sub-agents: agent-as-tool (book ch15).

The parent Agent gets ONE new tool, ``spawn_subagent``. Calling it spins
up a FRESH child Agent -- empty history, its own system prompt, a
filtered tool catalog -- runs it to completion, and returns only the
child's final answer as the tool result string. The parent's context
inflates by what the child CONCLUDED, never by the transcript it took
to conclude it (a 40-iteration research run comes back as one result
block, not 40 turns of tool noise).

Three constraints from the book, all enforced in code rather than left
to prompts:

1. ONE LEVEL DEEP. Children can't spawn children -- "spawn_subagent"
   is rejected in tools_allowed. Nested delegation compounds failure
   rates (three 85%-reliable agents in series ≈ 61% end-to-end).
2. BOUNDED. A per-session spawn budget plus a mandatory justification
   string per call. Spawning feels like doing work; the friction makes
   over-delegation visible.
3. COMPACT RESULTS. The child returns summary + cost metadata --
   never its transcript.

Two rules worth internalizing:

* Fresh context is the FEATURE: independent windows are most of the
  multi-agent value (Anthropic's research finding). Nothing from the
  parent conversation leaks into the child except the objective string.
* Scope restriction is enforced at the TOOL-CATALOG level, not trusted
  to the child's system prompt: the child physically has no other
  tools registered.

Don't use sub-agents for sequential chains where B consumes A's output
-- errors compound; do it inline. Use them for parallelizable,
self-contained subtasks (research branches, per-file surveys).

## Two ways to get a child, and they are not the same thing

Everything above describes ``spawn_subagent``: the FREEFORM route, where
the model writes the objective, picks the tool list and justifies itself
at call time. It is powerful and it is opt-in (``--subagents``) because
the model choosing its own child's permissions is exactly as sharp as it
sounds.

A package may instead DECLARE a sub-agent (``[[subagent]]`` in
``agent.toml``, notes/40), and a declared one is the opposite object in
every respect that matters. Its name, its instructions, its tool list and
its iteration cap were written by the package author and reviewed in a
diff; the model supplies one string, the task. It needs no justification
argument because nobody is being talked into anything, and it needs no
opt-in flag because it is part of what the package IS -- the same status
as the package's own tools and skills.

A DECLARED SUB-AGENT'S TOOL LIST IS THE AUTHOR'S, NOT THE MODEL'S. That
one sentence is the whole difference, and every other difference follows
from it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from yantra.agent import Agent
from yantra.async_agent import AsyncAgent
from yantra.errors import ProviderError, ToolError
from yantra.tools.base import Tool, ToolContext, ToolRegistry

SPAWN_TOOL_NAME = "spawn_subagent"

EXECUTE_CLAUSE = (
    "Before answering you MUST call at least one of your allowed tools; "
    "describing a call in prose without invoking it is a failure."
)

CHILD_SYSTEM_TEMPLATE = """You are a focused sub-agent spawned for one \
objective. Complete it, report, stop.

OBJECTIVE
{objective}

OUTPUT FORMAT (your final message must follow this)
{output_format}

TOOLS
You may use ONLY these tools: {tool_names}.
{execute_clause}

RULES
- Stop as soon as the objective is answered; no gold-plating.
- If the objective cannot be completed, SAY SO explicitly instead of \
inventing results.
- Budget: at most {max_iterations} model iterations.
"""


#: A declared sub-agent's prompt. The AUTHOR's instructions come first,
#: because they say who this child is and the harness's rules only say
#: how a child behaves; inverting that buries a package's voice under
#: boilerplate. The rules still follow, in the same words the freeform
#: template uses, because they are the ones that stop a child wandering.
DECLARED_SYSTEM_TEMPLATE = """{instructions}

TASK
{objective}
{output_format}
TOOLS
You may use ONLY these tools: {tool_names}.
{execute_clause}

RULES
- Stop as soon as the task is answered; no gold-plating.
- If the task cannot be completed, SAY SO explicitly instead of inventing results.
- Budget: at most {max_iterations} model iterations.
"""


@dataclass(frozen=True, slots=True)
class SubagentSpec:
    """One sub-agent a package DECLARES, as a pure description.

    Frozen and code-free, exactly like ``AgentSpec``: reading a manifest
    must never run anything, and a listing, a registry or a UI has to be
    able to show what a package would delegate to without building it.

    ``model`` is a slug on the PARENT'S PROVIDER, not a provider of its
    own. Letting a package name a second provider would mean a manifest
    could open a connection to a service the operator never configured
    and does not know is being paid for; naming a cheaper model on the
    provider they already chose is the thing people actually want here.
    """

    name: str
    description: str
    instructions: str
    tools: tuple[str, ...]
    model: str | None = None
    max_iterations: int = 20
    output_format: str | None = None


@dataclass(slots=True)
class SubagentResult:
    """What a spawn returns to the parent -- conclusions, not transcripts."""

    summary: str
    iterations_used: int
    tool_calls_made: int
    input_tokens: int
    output_tokens: int
    error: str | None = None


class SubagentSpawner:
    """Builds and runs child Agents on behalf of the spawn tool.

    One spawner per session: the budget counter lives here.
    """

    def __init__(self, parent: Agent | AsyncAgent, *, max_per_session: int = 5,
                 default_max_iterations: int = 20,
                 on_child_event: Callable[[int, Any], None] | None = None) -> None:
        self.parent = parent
        self.max_per_session = max_per_session
        self.default_max_iterations = default_max_iterations
        self.spawned = 0
        self.results: list[SubagentResult] = []  # observability / evals
        # optional live view of child internals: called as
        # on_child_event(spawn_number, StreamEvent). Deliberately NOT part
        # of the StreamEvent union -- child events are a UI concern with a
        # different provenance, not new loop vocabulary.
        self.on_child_event: Callable[[int, Any], None] | None = on_child_event

    # ---- validation --------------------------------------------------------

    def _validate(self, args: dict[str, Any]) -> tuple[str, str, list[str], int]:
        objective = args.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ToolError("missing required argument 'objective'")

        output_format = args.get("output_format")
        if not isinstance(output_format, str) or not output_format.strip():
            raise ToolError(
                "missing required argument 'output_format' -- vague "
                "requests produce rambling; name the exact shape of the answer"
            )

        justification = args.get("justification")
        if not isinstance(justification, str) or not justification.strip():
            raise ToolError(
                "a non-empty 'justification' is required -- why can't this "
                "be done inline? (anti-over-delegation friction)"
            )

        allowed = args.get("tools_allowed")
        if not isinstance(allowed, list) or not allowed \
                or not all(isinstance(t, str) for t in allowed):
            raise ToolError(
                "'tools_allowed' must be a NON-EMPTY list of tool names -- "
                "narrower is better; scope restriction is enforced here, "
                "not asked for nicely"
            )
        known = set(self.parent.registry.names())
        unknown = [t for t in allowed if t not in known]
        if unknown:
            raise ToolError(
                f"unknown tool(s) {unknown}; available: {sorted(known)}"
            )
        if SPAWN_TOOL_NAME in allowed:
            # one level deep -- nested delegation compounds failure rates
            raise ToolError("sub-agents cannot spawn sub-agents")

        max_iterations = args.get("max_iterations", self.default_max_iterations)
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) \
                or not (1 <= max_iterations <= 50):
            raise ToolError("'max_iterations' must be an integer in [1, 50]")

        return objective, output_format, list(dict.fromkeys(allowed)), max_iterations

    # ---- execution ---------------------------------------------------------

    def _charge_one(self) -> None:
        """Take one spawn off the session budget, or say it is gone.

        Counted when ATTEMPTED, successful or not: a child that crashed
        still consumed a model call, and a budget that only counted
        successes would be a budget a failing loop could ignore.
        """
        if self.spawned >= self.max_per_session:
            raise ToolError(
                f"sub-agent budget exhausted ({self.spawned}/"
                f"{self.max_per_session} used this session); do the work "
                "inline or ask the user to raise the budget"
            )
        self.spawned += 1

    def _child_registry(self, allowed: list[str]) -> ToolRegistry:
        """The child's WHOLE tool set -- scope restriction as a fact about
        the registry, not a sentence in a prompt."""
        registry = ToolRegistry()
        for name in allowed:
            registry.register(self.parent.registry.get(name))
        return registry

    def _build(self, *, system: str, allowed: list[str], max_iterations: int,
               model: str | None = None, want_async: bool = False):
        """One child, of whichever KIND ITS CALLER can drive.

        Keyed to the CALL PATH, not to the parent: a synchronous spawn
        must get a synchronous child even from an async parent, or the
        coroutine it returns is never awaited and the child never runs.
        An awaited spawn under an async parent gets an async child, which
        matters twice -- the parent's permission gate may be one that
        SUSPENDS (notes/37), and a synchronous child handed an awaitable
        gate refuses every dangerous call it makes; and the child stops
        occupying a worker thread for HTTP it could have awaited.
        """
        cls = (AsyncAgent if want_async and isinstance(self.parent, AsyncAgent)
               else Agent)
        child = cls(
            self.parent.provider,
            model=model or self.parent.model,
            system=system,
            tools=self._child_registry(allowed),
            cwd=self.parent.ctx.cwd,
            max_tokens=self.parent.max_tokens,
            max_iterations=max_iterations,
            permissions=self.parent.permissions,  # cannot escalate by being a sub-agent
            context_window=self.parent.context_window,
            # THE SAME meter, not a copy: a child that got its own fresh
            # ceiling would turn "spend at most $0.50" into "spend $0.50
            # per spawn", and delegating would be the cheapest way around
            # the one limit that costs real money (budget.py).
            budget=self.parent.budget,
        )
        if self.on_child_event is not None:
            # Stream tee: the child's raw StreamEvents are PUSHED to the
            # observer tagged with this spawn's 1-based number, so a UI can
            # watch child progress live without the parent's context ever
            # seeing any of it. Still deliberately NOT part of the
            # StreamEvent union -- this is a UI seam with different
            # provenance, not new loop vocabulary (hence the loose Any).
            spawn_no = self.spawned  # already incremented: this child's number
            child.on_stream_event = (
                lambda event, n=spawn_no: self.on_child_event(n, event)
            )
        return child

    def _freeform_system(self, objective: str, output_format: str,
                         allowed: list[str], max_iterations: int) -> str:
        return CHILD_SYSTEM_TEMPLATE.format(
            objective=objective,
            output_format=output_format,
            tool_names=", ".join(allowed),
            execute_clause=EXECUTE_CLAUSE if allowed else "",
            max_iterations=max_iterations,
        )

    @staticmethod
    def _declared_system(spec: SubagentSpec, objective: str) -> str:
        shape = ("" if not spec.output_format else
                 f"\nOUTPUT FORMAT (your final message must follow this)\n"
                 f"{spec.output_format}\n")
        return DECLARED_SYSTEM_TEMPLATE.format(
            instructions=spec.instructions.strip(),
            objective=objective,
            output_format=shape,
            tool_names=", ".join(spec.tools),
            execute_clause=EXECUTE_CLAUSE if spec.tools else "",
            max_iterations=spec.max_iterations,
        )

    def _declared_objective(self, spec: SubagentSpec,
                            args: dict[str, Any]) -> str:
        """A declared sub-agent takes ONE argument, and this checks it.

        No tools_allowed, no justification: both exist on the freeform
        tool because the MODEL is choosing, and here the author already
        chose. Asking a model to justify a call to a sub-agent its own
        package ships would be friction against the wrong decision.
        """
        objective = args.get("task")
        if not isinstance(objective, str) or not objective.strip():
            raise ToolError(
                f"missing required argument 'task' -- say what you want "
                f"{spec.name} to do, in one self-contained instruction "
                f"(it sees nothing else from this conversation)")
        missing = [t for t in spec.tools
                   if t not in set(self.parent.registry.names())]
        if missing:
            # The author's list, checked at the only moment it CAN be
            # checked: MCP tools and package tools register after the
            # manifest is read, so a load-time check would reject lists
            # that turn out to be fine (notes/31 made the same call for
            # tools.allow).
            raise ToolError(
                f"sub-agent {spec.name!r} needs tool(s) {missing}, which "
                f"this agent does not have; available: "
                f"{sorted(self.parent.registry.names())}")
        return objective

    # ---- the two spawns, sync and async ------------------------------------

    def spawn(self, args: dict[str, Any]) -> SubagentResult:
        """The FREEFORM route: the model named everything. Validate, build,
        run to completion."""
        objective, output_format, allowed, max_iterations = self._validate(args)
        self._charge_one()
        child = self._build(
            system=self._freeform_system(objective, output_format, allowed,
                                         max_iterations),
            allowed=allowed, max_iterations=max_iterations)
        return self._run(child, objective)

    async def aspawn(self, args: dict[str, Any]) -> SubagentResult:
        """The freeform route from a coroutine. Same validation, same child
        construction, awaited rather than blocked on."""
        objective, output_format, allowed, max_iterations = self._validate(args)
        self._charge_one()
        child = self._build(
            system=self._freeform_system(objective, output_format, allowed,
                                         max_iterations),
            allowed=allowed, max_iterations=max_iterations, want_async=True)
        return await self._arun(child, objective)

    def spawn_declared(self, spec: SubagentSpec,
                       args: dict[str, Any]) -> SubagentResult:
        """A sub-agent the PACKAGE declared. The model supplies one string."""
        objective = self._declared_objective(spec, args)
        self._charge_one()
        child = self._build(system=self._declared_system(spec, objective),
                            allowed=list(spec.tools),
                            max_iterations=spec.max_iterations,
                            model=spec.model)
        return self._run(child, objective)

    async def aspawn_declared(self, spec: SubagentSpec,
                              args: dict[str, Any]) -> SubagentResult:
        objective = self._declared_objective(spec, args)
        self._charge_one()
        child = self._build(system=self._declared_system(spec, objective),
                            allowed=list(spec.tools),
                            max_iterations=spec.max_iterations,
                            model=spec.model, want_async=True)
        return await self._arun(child, objective)

    # ---- running one child, and what its failures mean ----------------------

    def _run(self, child, objective: str) -> SubagentResult:
        try:
            child.run(objective)
        except ProviderError as exc:
            return self._record(self._provider_failed(child, exc))
        except RuntimeError as exc:
            return self._record(self._capped(child, exc))
        return self._record(self._finished(child))

    async def _arun(self, child, objective: str) -> SubagentResult:
        try:
            await child.run(objective)
        except ProviderError as exc:
            return self._record(self._provider_failed(child, exc))
        except RuntimeError as exc:
            return self._record(self._capped(child, exc))
        return self._record(self._finished(child))

    @staticmethod
    def _provider_failed(child, exc: Exception) -> SubagentResult:
        """A terminal provider failure INSIDE the child: data to the parent
        (which may decide to retry later), not a crashed turn."""
        return SubagentResult(
            "", 0, 0, child.total_usage.input_tokens,
            child.total_usage.output_tokens,
            error=f"provider error inside sub-agent: {exc}",
        )

    def _capped(self, child, exc: Exception) -> SubagentResult:
        """child.run() raises on max_iterations without end_turn; salvage
        the best-effort last assistant text."""
        partial = ""
        for message in reversed(child.history):
            if message.role == "assistant" and message.text().strip():
                partial = message.text().strip()
                break
        return SubagentResult(
            partial,
            sum(1 for m in child.history if m.role == "assistant"),
            self._count_calls(child),
            child.total_usage.input_tokens,
            child.total_usage.output_tokens,
            error=f"sub-agent hit its iteration cap: {exc}",
        )

    def _finished(self, child) -> SubagentResult:
        return SubagentResult(
            self._final_text(child),
            sum(1 for m in child.history if m.role == "assistant"),
            self._count_calls(child),
            child.total_usage.input_tokens,
            child.total_usage.output_tokens,
        )

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _count_calls(child: Agent | AsyncAgent) -> int:
        return sum(len(m.tool_calls()) for m in child.history)

    @staticmethod
    def _final_text(child: Agent | AsyncAgent) -> str:
        for message in reversed(child.history):
            if message.role == "assistant":
                text = message.text().strip()
                if text:
                    return text
        return ""

    def _record(self, result: SubagentResult) -> SubagentResult:
        self.results.append(result)
        return result


class SpawnSubagent(Tool):
    """The tool object the parent model sees; thin skin over the spawner."""

    name: ClassVar[str] = SPAWN_TOOL_NAME
    description: ClassVar[str] = (
        "Spawn a focused sub-agent with a fresh context window to complete "
        "one self-contained objective. It sees NOTHING from this "
        "conversation except what you pass here, uses only 'tools_allowed', "
        "and returns ONLY its final answer. Use for self-contained "
        "subtasks (research branches, per-file surveys); do NOT use for "
        "simple lookups you can do inline or sequential dependent steps."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "objective": {"type": "string",
                          "description": "Operationally specific task. This is "
                                         "the ONLY context the sub-agent gets."},
            "output_format": {"type": "string",
                              "description": "Required structure of the final "
                                             "answer, precisely named (e.g. "
                                             "'JSON {found: bool, path: string}')."},
            "tools_allowed": {"type": "array", "items": {"type": "string"},
                              "description": "Exact tool names the sub-agent may "
                                             "use. Narrower is better."},
            "justification": {"type": "string",
                              "description": "Why this needs a sub-agent instead "
                                             "of being done inline."},
            "max_iterations": {"type": "integer",
                               "description": "Model-iteration cap for the "
                                              "child (default 20)."},
        },
        "required": ["objective", "output_format", "tools_allowed", "justification"],
        "additionalProperties": False,
    }
    # conservative marking: children can do whatever their tools can do
    read_only: ClassVar[bool] = False

    def __init__(self, spawner: SubagentSpawner) -> None:
        self.spawner = spawner

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        objective = str(args.get("objective", ""))[:100]
        remaining = self.spawner.max_per_session - self.spawner.spawned
        return (f"spawn sub-agent (budget: {remaining} left): "
                f"{objective!r} · tools={args.get('tools_allowed')}")

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return _render(self.spawner.spawn(args))

    async def arun(self, args: dict[str, Any], ctx: ToolContext) -> str:
        """Overridden rather than inherited, because the default skin
        (``to_thread``) would run a synchronous child -- which cannot use
        a permission gate that suspends, and which sits on a worker thread
        for every HTTP round trip the child makes."""
        return _render(await self.spawner.aspawn(args))


def _render(result: SubagentResult) -> str:
    """One child's outcome as the string the PARENT reads.

    Conclusions plus a cost line, never a transcript -- the parent's
    context inflates by what the child concluded, not by the work.
    """
    meta = (f"\n[sub-agent · {result.iterations_used} iteration(s) · "
            f"{result.tool_calls_made} tool call(s) · "
            f"{result.input_tokens}in/{result.output_tokens}out tokens]")
    if result.error:
        if result.summary:
            return f"{result.summary}\n[INCOMPLETE -- {result.error}]{meta}"
        raise ToolError(result.error)
    return result.summary + meta


class DeclaredSubagent(Tool):
    """One tool per sub-agent a package declared.

    A tool per sub-agent, rather than one ``spawn(name=...)`` dispatcher,
    because the model's tool list IS the menu: a package that ships a
    fact-checker wants the model to see a fact-checker, with the author's
    own description, in the same list as everything else. A dispatcher
    would bury the roster inside one tool's description, where tool
    selection (notes/17) cannot see it and cannot rank it.

    The schema is one required string. Everything a sub-agent usually
    argues about at call time -- which tools, how many iterations, what
    shape the answer takes -- was decided by the author, in a file, in a
    diff somebody could read.
    """

    # read_only is a PROPERTY here, uniquely among the tools in this repo.
    # See below: it is the one delegation whose blast radius is knowable
    # in advance, and it is only knowable at call time.

    def __init__(self, declared: SubagentSpec,
                 spawner: SubagentSpawner) -> None:
        # ``declared``, never ``spec``: Tool.spec() is a METHOD, and an
        # attribute of that name shadows it -- which surfaces as the
        # registry failing to describe this tool to the model at all.
        self.declared = declared
        self.spawner = spawner
        spec = declared
        self.name = spec.name
        self.description = (
            f"{spec.description} Delegates to a sub-agent with a fresh "
            f"context window: it sees NOTHING from this conversation except "
            f"the task you write here, may use only "
            f"{', '.join(spec.tools)}, and returns only its final answer."
        )
        self.parameters = {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "One self-contained instruction. This is "
                                   "the ONLY context the sub-agent gets.",
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        }

    @property
    def read_only(self) -> bool:
        """True only if every tool this child may use is itself read-only.

        The freeform ``spawn_subagent`` is marked unconditionally unsafe
        because the MODEL writes the child's tool list at call time, and
        by the time you could inspect it the human is already being asked
        about a call whose blast radius is whatever the model just typed.
        A declared child's list was written by the author, so the question
        has an answer -- and a fact-checker that can only read files has
        nothing to confirm. Making a person approve one is friction that
        teaches them to approve without reading, which is the opposite of
        what a permission prompt is for.

        Computed at CALL time, not construction: MCP tools register after
        the agent is built, so a list naming one would be judged against a
        registry that had not met it yet. A name the registry does not
        know counts as unsafe -- the same pessimism MCP's own
        ``readOnlyHint`` gets, and for the same reason.
        """
        registry = self.spawner.parent.registry
        known = set(registry.names())
        return all(name in known and registry.get(name).read_only
                   for name in self.declared.tools)

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        remaining = self.spawner.max_per_session - self.spawner.spawned
        on = f" on {self.declared.model}" if self.declared.model else ""
        return (f"delegate to {self.declared.name}{on} (budget: {remaining} "
                f"left): {str(args.get('task', ''))[:100]!r}")

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return _render(self.spawner.spawn_declared(self.declared, args))

    async def arun(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return _render(await self.spawner.aspawn_declared(self.declared, args))
