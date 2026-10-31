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
from yantra.memory import TOPICS, Memory, MemoryStoreError, slot
from yantra.tools.base import Tool, ToolContext, require_str

KINDS = ("fact", "preference", "correction")


def kept_line(said: str, replaced: list[Any]) -> str:
    """What was kept, and what it was kept instead of."""
    if not replaced:
        return said
    return f"{said} (instead of: {'; '.join(item.statement for item in replaced)})"


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
        "says what the thing IS and what it plainly implies: 'Uses Fish as "
        "their command-line shell', not 'Uses Fish'; 'Lives in Lisbon, "
        "Portugal (Western European Time)', not 'Lives in Lisbon'. Not the "
        "task at hand, not plans for next week. Give it a topic; for "
        "someone else's fact say who in `about`; when it names one thing "
        "that can change (an email, a brokerage, a city) give that a `key`, "
        "so a newer one replaces it. Account numbers are fine; passwords, "
        "tokens, card or ID numbers never."
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
            "topic": {"type": "string", "enum": list(TOPICS),
                      "description": "What it is about; sets how long it "
                                     "is kept unused."},
            "about": {"type": "string",
                      "description": "Who it is about, when not the person "
                                     "talking: 'Naina', 'their daughter'."},
            "key": {"type": "string",
                    "description": "The one thing it names, e.g. 'city', "
                                   "'email', 'brokerage': a later memory "
                                   "with the same topic, about and key "
                                   "replaces it. Reuse the key of a "
                                   "remembered fact about the same thing."},
        },
        "required": ["statement"],
        "additionalProperties": False,
    }
    read_only = False  # it writes to the person's memory: ask first

    def __init__(self, memory: Memory) -> None:
        self.memory = memory

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        """What is asked about: the words to be kept, and what they would
        replace -- forgetting is part of the write, so it is in the ask."""
        said = f"remember: {require_str(args, 'statement')}"
        try:
            before = self.memory.in_slot(slot(args.get("topic"), args.get("about"),
                                              args.get("key")))
        except Exception:      # the ask still shows the words
            before = []
        if before:
            said += f" (instead of: {'; '.join(i.statement for i in before)})"
        return said

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        statement = require_str(args, "statement")
        kind = args.get("kind") or "fact"
        if kind not in KINDS:
            raise ToolError(f"kind {kind!r} is not one of {', '.join(KINDS)}")
        topic = args.get("topic") or None
        try:
            memory_id, replaced = self.memory.remember_replacing(
                statement, kind=kind, topic=topic, about=args.get("about"),
                key=args.get("key"))
        except MemoryStoreError as exc:
            raise ToolError(str(exc)) from None
        except Exception as exc:
            raise ToolError(f"memory unavailable ({self.memory.store.name}: "
                            f"{exc}); nothing was remembered") from None
        said = kept_line(" ".join(statement.split()), replaced)
        self.memory.kept.append((str(memory_id), said))
        return f"remembered (#{memory_id}): {said}"


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
        # shown to the model now: an answer that draws on one reports it
        self.memory.recalled.update((item.id, item) for item in found)
        return "\n".join(f"- {item.statement}" for item in found)
