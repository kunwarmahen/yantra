"""Yantra -- a from-scratch LLM agent harness, written to learn.

Layers, outside in:

* ``yantra.cli``     -- terminal UI (REPL, rendering, permission prompts)
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

from yantra.agent import Agent, AgentEvent, ToolExecuted, TurnEnd
from yantra.config import default_model, load_settings
from yantra.images import load_image_block
from yantra.mcp import MCPServerConfig, MCPSession, connect_mcp, register_mcp
from yantra.permissions import (
    PermissionFn,
    PermissionRequest,
    allow_read_only,
    deny_all,
    yolo,
)
from yantra.pricing import ModelPrice, cost_of, price_for, session_cost
from yantra.prompt import SystemPrompt, attach_prompt, recompose
from yantra.providers import get_provider
from yantra.providers.base import Provider, ProviderSettings, collect
from yantra.skills import Skill, SkillRegistry, enable_skills
from yantra.tools import default_registry
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
    "allow_read_only",
    "attach_prompt",
    "Block",
    "collect",
    "default_model",
    "default_registry",
    "deny_all",
    "EndEvent",
    "get_provider",
    "ImageBlock",
    "load_image_block",
    "load_settings",
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
    "price_for",
    "session_cost",
    "ProviderSettings",
    "RedactedThinkingBlock",
    "StartEvent",
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
    "ToolCall",
    "ToolCallDelta",
    "ToolCallStart",
    "ToolExecuted",
    "ToolResult",
    "ToolSpec",
    "TurnEnd",
    "Usage",
    "yolo",
]
