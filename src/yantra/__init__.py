"""Yantra -- a from-scratch LLM agent harness, written to learn.

Layers, outside in:

* ``yantra.cli``     -- terminal UI (REPL, rendering, permission prompts)
* ``yantra.package`` -- an agent as a DIRECTORY: agent.toml, prompt, skills
* ``yantra.eval_suite`` -- that same directory's acceptance gate: cases in
                        TOML, graded against the package they ship with
* ``yantra.spec``    -- AgentSpec: one description of an agent, and the
                        build that wires it in the order that works
* ``yantra.agent``   -- THE LOOP: model -> tool calls -> results -> repeat
* ``yantra.providers`` -- wire-format adapters behind one streaming interface
* ``yantra.tools``   -- what the model may do (fs, shell, grep) + sandboxing
* ``yantra.skills``  -- procedural knowledge on disk: SKILL.md folders the
                         model loads on demand (notes/30)
* ``yantra.prompt``  -- the system prompt as ordered layers, so awareness
                         and the skill roster can coexist in one string
* ``types``/``errors`` -- shared vocabulary; the ONLY representation anywhere

See README.md for the map and notes/ for per-topic write-ups.
"""

from yantra.agent import (
    Agent,
    AgentEvent,
    BudgetWarning,
    ToolExecuted,
    TurnEnd,
)
from yantra.async_agent import AsyncAgent
from yantra.budget import Budget
from yantra.confidence import perfect_runs_needed, wilson_bounds
from yantra.config import default_model, load_settings
from yantra.eval_suite import find_suite, load_cases, render_case
from yantra.evals import (
    AsyncEvalRunner,
    CaseOutcome,
    EvalCase,
    EvalResult,
    EvalRunner,
    case_from_trace,
    case_from_trajectory,
    judge,
    summarize,
)
from yantra.images import load_image_block
from yantra.mcp import MCPServerConfig, MCPSession, connect_mcp, register_mcp
from yantra.package import MANIFEST, find_manifest, load_package
from yantra.permissions import (
    DENIED,
    REFUSED_OUT_OF_TIME,
    REFUSED_POLICY,
    REFUSED_TIMEOUT,
    REFUSED_UNATTENDED,
    REFUSED_UNSPECIFIED,
    REFUSED_USER,
    PermissionFn,
    PermissionRequest,
    adecide,
    allow_read_only,
    decide,
    denial_code,
    denial_text,
    deny_all,
    refuse,
    with_deadline,
    with_wait_budget,
    yolo,
)
from yantra.pricing import (ModelPrice, bills_nothing, cost_of, price_for,
                            session_cost)
from yantra.prompt import SystemPrompt, attach_prompt, recompose
from yantra.providers import get_provider
from yantra.providers.base import Provider, ProviderSettings, collect
from yantra.session import SessionStore, apply_payload
from yantra.skills import Skill, SkillRegistry, enable_skills
from yantra.spec import AgentSpec
from yantra.tools import default_registry, discover_tools
from yantra.tools.base import Tool, ToolContext, ToolOutput, ToolRegistry
from yantra.trace import Trajectory, TrajectoryLog, watch
from yantra.types import (
    Block,
    EndEvent,
    ImageBlock,
    Message,
    ModelResponse,
    RedactedThinkingBlock,
    StartEvent,
    StopReason,
    StreamEvent,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallStart,
    ToolResult,
    ToolSpec,
    Usage,
)

__all__ = [
    "Agent",
    "AgentEvent",
    "AgentSpec",
    "apply_payload",
    "AsyncAgent",
    "Budget",
    "BudgetWarning",
    "adecide",
    "allow_read_only",
    "attach_prompt",
    "Block",
    "collect",
    "default_model",
    "default_registry",
    "decide",
    "denial_code",
    "denial_text",
    "deny_all",
    "DENIED",
    "refuse",
    "with_deadline",
    "with_wait_budget",
    "REFUSED_OUT_OF_TIME",
    "REFUSED_POLICY",
    "REFUSED_TIMEOUT",
    "REFUSED_UNATTENDED",
    "REFUSED_UNSPECIFIED",
    "REFUSED_USER",
    "discover_tools",
    "EndEvent",
    "EvalCase",
    "EvalResult",
    "EvalRunner",
    "AsyncEvalRunner",
    "CaseOutcome",
    "case_from_trace",
    "case_from_trajectory",
    "find_suite",
    "judge",
    "load_cases",
    "render_case",
    "summarize",
    "get_provider",
    "ImageBlock",
    "find_manifest",
    "load_image_block",
    "load_package",
    "load_settings",
    "perfect_runs_needed",
    "wilson_bounds",
    "MANIFEST",
    "Trajectory",
    "TrajectoryLog",
    "watch",
    "Message",
    "ModelResponse",
    "MCPServerConfig",
    "MCPSession",
    "ModelPrice",
    "connect_mcp",
    "cost_of",
    "register_mcp",
    "PermissionFn",
    "PermissionRequest",
    "Provider",
    "recompose",
    "bills_nothing",
    "price_for",
    "session_cost",
    "ProviderSettings",
    "RedactedThinkingBlock",
    "StartEvent",
    "SessionStore",
    "Skill",
    "SkillRegistry",
    "enable_skills",
    "StopReason",
    "SystemPrompt",
    "StreamEvent",
    "TextBlock",
    "TextDelta",
    "ThinkingBlock",
    "ThinkingDelta",
    "Tool",
    "ToolCall",
    "ToolCallDelta",
    "ToolCallStart",
    "ToolExecuted",
    "ToolContext",
    "ToolOutput",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "TurnEnd",
    "Usage",
    "yolo",
]
