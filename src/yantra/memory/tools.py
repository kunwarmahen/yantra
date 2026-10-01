"""``remember`` and ``recall_memory`` -- the model's two verbs on memory.

``remember`` is the in-conversation writer: the person says something
about themselves that passes the three tests (memory/__init__.py), and
the model writes it down as one sentence. It is NOT read-only, so it
asks before writing like any other tool that changes something -- the
person sees exactly what is about to be remembered about them, in the
words it will be stored in, and can say no.

``recall_memory`` is the fallback, not the main road. What is remembered
is already in the prompt; this is for the case the prompt layer could
not carry -- a store too big to inline, or a question about something
the first message gave no hint of.

Neither tool takes a ``user``. Whose memory it is was settled when the
session started, and a model that could name somebody else's would be
a model that could read somebody else's.
"""

from __future__ import annotations

from typing import Any, ClassVar

from yantra.errors import ToolError
from yantra.memory import Memory, MemoryStoreError
from yantra.tools.base import Tool, ToolContext, require_str

KINDS = ("fact", "preference", "correction")


class Remember(Tool):
    name = "remember"
    description = (
        "Remember something the person told you about THEMSELVES that will "
        "still be true in months and would change an answer in a later "
        "conversation: where they live, what they use, what they prefer, a "
        "correction of something you had wrong -- including habits shown "
        "in passing, like the currency or units they talk in. One short "
        "sentence, in the "
        "third person ('Lives near RDU (Raleigh-Durham airport)'), that "
        "says what the thing IS: 'Uses Fish as their command-line shell', "
        "not 'Uses Fish'. Not the task at hand, not plans for next week."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "statement": {"type": "string",
                          "description": "The fact, as one self-contained "
                                         "sentence a later conversation can "
                                         "use with no other context."},
            "kind": {"type": "string", "enum": list(KINDS),
                     "description": "fact (default), preference, or "
                                    "correction."},
        },
        "required": ["statement"],
        "additionalProperties": False,
    }
    read_only = False  # it writes to the person's memory: ask first

    def __init__(self, memory: Memory) -> None:
        self.memory = memory

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return f"remember: {require_str(args, 'statement')}"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        statement = require_str(args, "statement")
        kind = args.get("kind") or "fact"
        if kind not in KINDS:
            raise ToolError(f"kind {kind!r} is not one of {', '.join(KINDS)}")
        try:
            memory_id = self.memory.remember(statement, kind=kind)
        except MemoryStoreError as exc:
            raise ToolError(str(exc)) from None
        except Exception as exc:
            raise ToolError(f"memory unavailable ({self.memory.store.name}: "
                            f"{exc}); nothing was remembered") from None
        return f"remembered (#{memory_id}): {' '.join(statement.split())}"


class RecallMemory(Tool):
    name = "recall_memory"
    description = (
        "Search what you remember about the person, from earlier "
        "conversations. What you remember is already in your instructions; "
        "use this only when you need something that is not there."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "query": {"type": "string",
                      "description": "Words to look for, e.g. 'airport' or "
                                     "'diet'."},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    read_only = True

    def __init__(self, memory: Memory) -> None:
        self.memory = memory

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return f"recall memory matching {args.get('query')!r}"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        query = require_str(args, "query")
        try:
            found = self.memory.recall(query, 10)
        except Exception as exc:
            raise ToolError(f"memory unavailable ({self.memory.store.name}: "
                            f"{exc})") from None
        if not found:
            return (f"nothing remembered matches {query!r} -- if the person "
                    f"has not said, ask them")
        return "\n".join(f"- {item.statement}" for item in found)
