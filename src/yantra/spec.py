"""AgentSpec -- one description of an agent, and the build that wires it.

``Agent.__init__`` takes seventeen keyword arguments, and knowing all
seventeen is still not enough, because the assembly has an ORDER:

* the operator's system prompt must be captured as the base layer before
  env_context appends a fact sheet to it;
* skills register before the tool catalog is built, or a pinned tool does
  not exist yet to be pinned;
* the tool admission policy goes on before anything is registered, or a
  tool the package excluded is briefly present and reachable.

That order lived in exactly one place -- ``cli/main.py`` -- which was
fine right up until something other than the CLI wanted an agent.

So the assembly moves here. An ``AgentSpec`` is a DESCRIPTION: provider,
model, prompt, which tools, which skills, which MCP servers. ``build()``
turns one into a wired ``Agent`` in the order that works. The primitive
stays dumb and a composer owns assembly -- the same move ``SystemPrompt``
made for the system string, for the same reason.

UNSET IS ``None``, never a default value. That is what makes ``merge()``
honest: an agent package produces one spec, the command line produces
another, and the second overrides only where it actually said something.
The resolution order every surface agrees on is

    explicit argument  >  agent.toml  >  environment  >  built-in default

and only ``build()`` consults the last two. A spec therefore records what
was ASKED FOR rather than what one machine happened to have -- which is
what lets the same package run against a cloud model on your laptop and
a local one on somebody else's, with no edit to the package.

WHAT BUILD DELIBERATELY DOES NOT DO. Three things a host owns instead,
because each one needs state a spec cannot carry:

* **MCP connections.** ``spec.mcp`` carries the server configs; opening
  them needs a live ``MCPManager`` whose sessions outlive the build and
  get closed on every exit path.
* **Tool selection.** ``spec.tools_per_turn`` carries the width, but the
  catalog must be built after EVERY tool is registered -- including MCP
  tools, which arrive after build -- so the host decides when.
* **The permission gate.** Passed IN, because what asks a human depends
  entirely on which human: a terminal prompt, a browser modal, a policy
  file with nobody home. ``permissions_mode`` says only which mode the
  host's gate should start in.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, fields, replace
from pathlib import Path

from yantra.agent import Agent
from yantra.async_agent import AsyncAgent
from yantra.budget import Budget
from yantra.config import (
    default_context_window,
    default_model,
    guess_provider,
    load_settings,
)
from yantra.env_context import EnvContext
from yantra.errors import ConfigError
from yantra.mcp import MCPServerConfig
from yantra.permissions import MODES, PermissionFn
from yantra.prompt import attach_prompt
from yantra.providers import get_provider
from yantra.skills import enable_skills
from yantra.skills.loader import prepend_skill_path
from yantra.subagent import DeclaredSubagent, SubagentSpawner, SubagentSpec
from yantra.tools import default_registry
from yantra.tools.base import ToolRegistry
from yantra.tools.discover import (register_tool_dirs,
                                   register_tool_packs)


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """Everything needed to build one agent, with unset left as ``None``.

    Frozen because a spec is passed around and merged: two callers holding
    the same object must not be able to surprise each other. ``merge`` and
    ``replace`` return new specs instead.
    """

    # ---- identity ----------------------------------------------------------
    name: str | None = None
    description: str | None = None
    version: str | None = None

    # ---- model -------------------------------------------------------------
    provider: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    max_iterations: int | None = None
    context_window: int | None = None
    cache: bool | None = None

    # ---- prompt ------------------------------------------------------------
    #: The agent's own prompt -> the ``agent`` layer. Who this agent IS.
    prompt: str | None = None
    #: The operator's --system -> the ``base`` layer, rendered after it.
    system: str | None = None

    # ---- tools -------------------------------------------------------------
    #: fnmatch patterns. None means "everything not denied"; a list is a
    #: COMPLETE whitelist and must name MCP tools ('mcp__*') if it wants any.
    tool_allow: tuple[str, ...] | None = None
    tool_deny: tuple[str, ...] = ()
    tools_per_turn: int | None = None
    #: Directories of the package's OWN Tool subclasses. Loading one runs
    #: its Python -- see tools/discover.py on where a package path may
    #: legitimately come from.
    tool_dirs: tuple[Path, ...] = ()
    #: Installed distributions publishing ``yantra.tools`` entry points --
    #: the way a tool reaches somebody who is not sharing a directory with
    #: you. Named one by one: an agent whose tool list depended on what
    #: happened to be installed would be a different agent on every
    #: machine (tools/discover.py).
    tool_packs: tuple[str, ...] = ()

    # ---- skills ------------------------------------------------------------
    skills: bool | None = None
    skill_dirs: tuple[Path, ...] = ()
    skills_disabled: tuple[str, ...] = ()

    # ---- servers -----------------------------------------------------------
    mcp: tuple[MCPServerConfig, ...] = ()

    # ---- delegation --------------------------------------------------------
    #: Sub-agents the package DECLARED -- name, instructions, tool list and
    #: iteration cap all written by the author. Each becomes one tool the
    #: model can call with a single string. Nothing like ``spawn_subagent``,
    #: which is the model choosing its own child's permissions and stays
    #: behind an operator flag (subagent.py).
    subagents: tuple[SubagentSpec, ...] = ()

    # ---- money -------------------------------------------------------------
    #: A per-turn dollar ceiling the loop stops at. The author's estimate of
    #: what one task should cost -- and therefore something the operator may
    #: RAISE, unlike tool_deny, which is a boundary rather than a guess.
    max_usd_per_turn: float | None = None

    # ---- runtime -----------------------------------------------------------
    permissions_mode: str | None = None
    #: Awareness level. None means DO NOT ATTACH -- deliberately different
    #: from "off", because attaching at "full" does a one-time network geo
    #: lookup, and a library call that builds an agent should not reach the
    #: network because it forgot to say no. The CLI passes a level always.
    env_context: str | None = None

    #: Sandbox root for file tools and bash's working directory.
    cwd: Path | None = None
    #: The package directory, when this spec came from one. Relative paths
    #: inside the package resolve against it; ``None`` for a hand-built spec.
    root: Path | None = None

    # ---- composition -------------------------------------------------------

    def merge(self, other: AgentSpec) -> AgentSpec:
        """A new spec where ``other`` wins wherever it said something.

        "Said something" means "differs from the field's default", which is
        why every optional field defaults to ``None`` or an empty tuple. The
        consequence worth knowing: an override cannot CLEAR a value back to
        empty -- ``tool_deny = []`` on the command line does not undo a
        package's deny list. Removing a restriction takes an explicit flag,
        not an empty one, and that asymmetry is on purpose.
        """
        updates = {}
        for spec_field in fields(self):
            theirs = getattr(other, spec_field.name)
            if theirs != spec_field.default:
                updates[spec_field.name] = theirs
        return replace(self, **updates)

    def validate(self) -> None:
        """Complain about anything decidable without touching the network.

        Called by ``load_package`` so a typo in ``agent.toml`` is reported
        against the file, not as a confusing failure six steps later.
        """
        if self.permissions_mode is not None and self.permissions_mode not in MODES:
            raise ConfigError(
                f"permissions mode {self.permissions_mode!r} is not one of "
                f"{'|'.join(MODES)}"
            )
        if self.env_context is not None:
            from yantra.env_context import MODES_ENV
            if self.env_context not in MODES_ENV:
                raise ConfigError(
                    f"env context {self.env_context!r} is not one of "
                    f"{'|'.join(MODES_ENV)}"
                )
        for name, value in (("max_tokens", self.max_tokens),
                            ("max_iterations", self.max_iterations),
                            ("context_window", self.context_window),
                            ("tools_per_turn", self.tools_per_turn)):
            if value is not None and value < 0:
                raise ConfigError(f"{name} cannot be negative (got {value})")
        if self.max_usd_per_turn is not None and self.max_usd_per_turn <= 0:
            raise ConfigError(
                f"budget.max_usd_per_turn must be greater than zero (got "
                f"{self.max_usd_per_turn}); a ceiling of nothing is a "
                f"refusal to run, not a budget"
            )
        if self.tool_allow is not None and not self.tool_allow:
            raise ConfigError(
                "tools.allow is present but empty, which would leave the "
                "agent no tools at all; omit the key to allow everything"
            )

    # ---- the build ---------------------------------------------------------

    def build(
        self,
        *,
        permissions: PermissionFn | None = None,
        sandbox=None,
        cwd: Path | None = None,
        registry: ToolRegistry | None = None,
        provider=None,
        provider_name: str | None = None,
        model: str | None = None,
    ) -> Agent:
        """A wired Agent, assembled in the one order that works.

        ``permissions`` is the host's gate (terminal, browser, policy);
        omitted, the Agent's own default stands and only read-only tools
        run. ``sandbox`` swaps bash's executor. ``registry`` is for tests
        and for hosts that built their own tool set.

        ``provider``/``provider_name``/``model`` let a host that ALREADY
        resolved those hand them over instead of having them resolved a
        second time here. The CLI needs the name early (to dispatch build
        mode and to print a banner), so without this it would read the
        environment twice and could, with a concurrent edit to .env,
        disagree with itself about which model it just announced.
        """
        return self._assemble(
            Agent, permissions=permissions, sandbox=sandbox, cwd=cwd,
            registry=registry, provider=provider,
            provider_name=provider_name, model=model,
        )

    def build_async(
        self,
        *,
        permissions: PermissionFn | None = None,
        sandbox=None,
        cwd: Path | None = None,
        registry: ToolRegistry | None = None,
        provider=None,
        provider_name: str | None = None,
        model: str | None = None,
        max_parallel_tools: int | None = None,
    ) -> AsyncAgent:
        """The async twin, for hosts that drive several turns at once.

        Same spec, same assembly, same order -- only the loop differs, which
        is the point of the two agents sharing one description. ``AsyncAgent``
        has one knob its sync twin does not (how many tool calls may run
        concurrently); it is an argument here rather than a spec field
        because it describes the HOST's appetite for parallelism, not the
        agent's character.
        """
        extra = ({} if max_parallel_tools is None
                 else {"max_parallel_tools": max_parallel_tools})
        return self._assemble(
            AsyncAgent, permissions=permissions, sandbox=sandbox, cwd=cwd,
            registry=registry, provider=provider,
            provider_name=provider_name, model=model, **extra,
        )

    def _assemble(self, agent_cls, *, permissions, sandbox, cwd, registry,
                  provider, provider_name, model, **extra):
        """The assembly itself. ONE body, so the sync and async agents can
        never drift into being configured differently."""
        name = provider_name or self.provider or guess_provider()
        if provider is None:
            provider = get_provider(name, load_settings(name),
                                    cache_control=bool(self.cache))
        model_slug = model or self.model or default_model(name)
        root = Path(cwd) if cwd is not None else (self.cwd or Path.cwd())

        # The admission policy goes on BEFORE anything is registered, so a
        # tool the package excluded is never momentarily present -- and so
        # it also governs ask_user, load_skill and MCP tools, which arrive
        # after this function returns.
        tools = registry if registry is not None else default_registry(sandbox)
        tools.admit_only(self.tool_allow, self.tool_deny)

        # The package's own tools, registered under that same policy and
        # before the agent exists -- a tool that arrives after the catalog
        # is built is a tool the model is never told about. This is the
        # step that executes somebody else's code; load_package deliberately
        # did not, so it happens here, where a human asked for this agent.
        if self.tool_dirs:
            register_tool_dirs(tools, self.tool_dirs)
        # And the same step for code that arrived by pip rather than by
        # being in the directory. Same admission policy, same loudness
        # about collisions; the only difference is where it came from.
        if self.tool_packs:
            register_tool_packs(tools, self.tool_packs)

        # The ceiling is resolved HERE, before an agent exists, because the
        # one failure worth catching early is a ceiling that can never fire:
        # an operator who asked for $0.50 and got no complaint is entitled
        # to assume they are covered (budget.py).
        budget = (None if self.max_usd_per_turn is None
                  else Budget.for_model(self.max_usd_per_turn,
                                        provider_name=name, model=model_slug))
        # A sub-agent on its own model spends the SAME meter, so the same
        # refusal has to cover it: a ceiling that can be priced for the
        # parent and not for the child is a ceiling that stops the turn
        # the first time anybody delegates, and the operator who set it
        # would learn that at the worst possible moment.
        if budget is not None:
            for sub in self.subagents:
                if sub.model:
                    try:
                        Budget.for_model(self.max_usd_per_turn,
                                         provider_name=name, model=sub.model)
                    except ConfigError as exc:
                        raise ConfigError(
                            f"sub-agent {sub.name!r}: {exc}") from None

        optional = {
            "max_tokens": self.max_tokens,
            "max_iterations": self.max_iterations,
        }
        agent = agent_cls(
            provider,
            model=model_slug,
            system=self.system,
            tools=tools,
            cwd=root,
            permissions=permissions,
            budget=budget,
            context_window=(self.context_window
                            if self.context_window is not None
                            else default_context_window(name)),
            **{k: v for k, v in optional.items() if v is not None},
            **extra,
        )

        # Prompt layers, in order. attach_prompt freezes ``system`` as the
        # base layer on this first call; the package's own prompt goes into
        # ``agent``, which renders ahead of it.
        prompt = attach_prompt(agent)
        if self.prompt:
            prompt.set("agent", self.prompt)
            prompt.apply()

        # Skills need load_skill to be reachable at all, so an admission
        # policy that refuses it also refuses the roster: composing a list
        # of skills into the prompt while the tool to load them is gone
        # tells the model about capabilities it does not have. A package
        # that declares skills AND excludes load_skill is rejected by
        # load_package; this branch covers the host-enabled-by-default case.
        if self.skills and tools.admits("load_skill"):
            if self.skill_dirs:
                prepend_skill_path([str(d) for d in self.skill_dirs])
            registered = enable_skills(agent, root)
            if self.skills_disabled:
                present = registered.names()
                doomed = sorted({n for pat in self.skills_disabled
                                 for n in present if fnmatch.fnmatch(n, pat)})
                for skill_name in doomed:
                    registered.disable(skill_name)
                if doomed:
                    registered.reapply()

        # Declared sub-agents, and they can only go on HERE: a spawner
        # needs its parent, and the parent is what we have just finished
        # building. One spawner for the whole session, published on the
        # agent, so a later --subagents (or a delegated skill) shares this
        # spawn budget rather than opening a second one beside it.
        if self.subagents:
            spawner = SubagentSpawner(agent)
            agent.subagents = spawner
            for sub in self.subagents:
                try:
                    tools.register(DeclaredSubagent(sub, spawner))
                except ValueError as exc:
                    # The registry is loud about duplicates; say which file
                    # is responsible, because "duplicate tool name: 'grep'"
                    # on its own sends the reader to the wrong place.
                    where = self.root or self.name or "this package"
                    raise ConfigError(
                        f"{where}: sub-agent {sub.name!r} would shadow a "
                        f"tool of the same name ({exc}); rename the "
                        f"sub-agent") from None

        # Last, because it appends to a prompt the layers above must already
        # own, and because at "full" it costs one network call.
        if self.env_context is not None:
            EnvContext(self.env_context, root).attach(agent)

        return agent
