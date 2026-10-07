"""Memory about the PERSON -- what outlives one conversation.

``write_note`` / ``recall_notes`` (tools/memory.py) remember things about
a PROJECT: a file layout, a dead end, a decision. This remembers things
about the person who is talking: they live near RDU, they run Ollama,
their manager is Priya. Different owner (the person, across every
project, not one working tree), different shape (short statements, not
topics to upsert), and so a different store. The two share no interface
on purpose.

WORTH REMEMBERING means passing three tests at once:

1. about the person, not the task ("I live near RDU", not "the flight is
   at 9");
2. stable for months ("flying to SFO Tuesday" is wrong by Wednesday);
3. able to change an answer in a DIFFERENT, later conversation -- "find
   me flights to Denver" should start from RDU without being told.

THE THIRD TEST DECIDES THE DESIGN. "Find me flights to Denver" never
mentions an origin, so the model has no reason to go looking for one. A
recall tool alone would sit unused, and on a local model -- which rarely
calls a tool it was not pushed towards -- it would sit unused every
time. So what is remembered is PUT IN THE PROMPT, as its own layer
(prompt.py), and ``recall_memory`` is only for when the model knows it
needs more. The layer is filled ONCE, from the first message of a
conversation: per-turn text would break the cached prefix, and a slow
store would tax every turn instead of one.

THE LIVE MESSAGE WINS. Memory enters the prompt framed as what the
person said before, which may be out of date. "Flying out of Boston for
the conference" beats a remembered RDU, and when an answer leans on a
memory the model says so in a few words ("from RDU, as before -- say if
not"). That one sentence is also the defence against a wrong memory:
the person sees it and corrects it, and ``/memory forget`` removes it.

THE AGENT DECIDES WHAT TO KEEP. The store stores and searches; it never
reads a transcript and never judges. Statements reach it finished, from
the ``remember`` tool (which asks first, like any tool that writes), and
from a look back over the conversation when it ends (memory/reflect.py),
which catches what was said in passing and the model never wrote down.

NO IDENTITY, NO MEMORY. Whose memories these are is PASSED IN -- $YANTRA_USER,
else the login name, for the person's own terminal and page; a service
passes each end user's. Without one nothing is read or written, so one
person's RDU can never reach somebody else's session by accident.

FAILS OPEN, OUT LOUD. A store that is down or broken costs the memory
layer, never the turn: the turn goes on without it and ``on_notice``
says why.

STORES PLUG IN; NONE IS NAMED HERE. ``MemoryStore`` is four verbs. The
built-in ``local`` store (memory/local.py) is sqlite and keyword search,
there so the feature works with nothing installed. Any other store runs
an MCP server and is named by it: ``--memory NAME`` or ``[memory] via =
"NAME"`` reaches it through a verb map (memory/mcp.py).
"""

from __future__ import annotations

import getpass
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from yantra.prompt import attach_prompt

ENV_MODE = "YANTRA_MEMORY"
ENV_USER = "YANTRA_USER"
MODES = ("local", "off")

#: How many memories the prompt layer carries, and how many characters
#: at most. Enough for a person's standing facts; a store holding more
#: than this is what ``recall_memory`` is for.
PROMPT_LIMIT = 20
#: How many of those lines the store's search may fill before the most
#: recent top up the rest. All of them: eight lines with five searched
#: thinned the dilution notes/103 found, and cost more than it saved --
#: a store that searches by meaning ranks some facts eighteenth, and a
#: narrow layer cannot reach them (notes/109).
PROMPT_SEARCHED = PROMPT_LIMIT
PROMPT_CHARS = 2_000
#: One statement's ceiling. A memory is a sentence, not a document.
MAX_STATEMENT_CHARS = 300


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """One remembered statement, as a store hands it back."""

    id: str
    statement: str
    created: str = ""                 # ISO time, when the store keeps one
    package: str | None = None        # which agent wrote it, if any
    kind: str | None = None           # "fact", "preference", "correction"


@runtime_checkable
class MemoryStore(Protocol):
    """What a store must do. Four verbs: no ``ingest``, because a store is
    never handed a transcript, and no ``context_for``, because building
    the prompt block is this side's job."""

    name: str

    def remember(self, user: str, statement: str, meta: dict[str, Any]) -> str: ...
    def recall(self, user: str, query: str, limit: int) -> list[MemoryItem]: ...
    def forget(self, user: str, memory_id: str) -> bool: ...
    def list(self, user: str, limit: int) -> list[MemoryItem]: ...


class MemoryStoreError(Exception):
    """A statement the store must not take, or a store that failed."""


def resolve_user(env: dict[str, str] | None = None) -> str | None:
    """Whose memories: $YANTRA_USER, else the login name, else nobody."""
    env = os.environ if env is None else env
    named = (env.get(ENV_USER) or "").strip()
    if named:
        return named
    try:
        return getpass.getuser() or None
    except (OSError, KeyError):
        return None


def check_mode(value: str, where: str = "memory") -> str:
    """``local``, ``off``, or the name of an MCP server to reach a store
    through. Raises ValueError, naming ``where``, on anything else."""
    from yantra.memory.mcp import SERVER_NAME

    mode = value.strip()
    if mode.lower() in MODES:
        return mode.lower()
    if not SERVER_NAME.match(mode):
        raise ValueError(f"{where}={value!r}: expected {', '.join(MODES)}, "
                         f"or the name of an MCP server")
    return mode


def supports(store: MemoryStore, verb: str) -> bool:
    """Whether a store can do ``verb``. Stores that do not say can do all
    four; one reached over MCP may lack a tool for some (memory/mcp.py)."""
    check = getattr(store, "supports", None)
    return check(verb) if callable(check) else True


def memory_mode(flag: str | None = None,
                env: dict[str, str] | None = None) -> str:
    """``--memory`` beats $YANTRA_MEMORY beats ``local``.

    Only for a session of your OWN. A package decides for itself in
    ``[memory] via`` and gets ``off`` when it says nothing (package.py),
    so an agent somebody else wrote does not read your memories unless it
    asks to -- and the flag, which wins any merge, can still say either.
    """
    env = os.environ if env is None else env
    where, raw = ("--memory", flag) if flag is not None else \
        (ENV_MODE, env.get(ENV_MODE) or "local")
    return check_mode(raw, where)


#: An id longer than this is shown by its start (``/memory forget`` takes
#: the start back): a store's UUID is 36 characters nobody reads.
SHORT_ID = 8


def short_id(memory_id: str) -> str:
    """How an id is shown to a person: whole when short (the built-in
    file's ``3``), its first ``SHORT_ID`` characters when long."""
    return memory_id if len(memory_id) <= 12 else memory_id[:SHORT_ID]


def _clean(statement: str) -> str:
    text = " ".join((statement or "").split())
    if not text:
        raise MemoryStoreError("nothing to remember: the statement is empty")
    if len(text) > MAX_STATEMENT_CHARS:
        raise MemoryStoreError(f"too long to be one memory ({len(text)} chars, "
                          f"max {MAX_STATEMENT_CHARS}); say it in a sentence")
    return text


class Memory:
    """One session's handle: a store, whose memories, and the prompt layer.

    Published as ``agent.memory``; the tools, ``/memory`` and the page all
    go through it, so the identity check lives in one place.
    """

    def __init__(self, store: MemoryStore, user: str, *,
                 package: str | None = None) -> None:
        self.store = store
        self.user = user
        self.package = package
        #: Whether this conversation's layer has been filled. The agent
        #: fills it on the first message, and again after a /clear.
        self.primed = False
        #: What the layer was filled with, for /memory and the page.
        self.in_prompt: list[MemoryItem] = []
        #: The last failure, as a sentence, or None.
        self.notice: str | None = None
        #: Where a failure is said out loud. Hosts set it (terminal line,
        #: page banner); the default keeps the sentence on ``notice`` only.
        self.on_notice: Callable[[str], None] = lambda text: None
        # ---- the look back at a conversation's end (memory/reflect.py) ----
        #: ask | auto | off. Hosts set it; ``ask`` where nobody can answer
        #: is their job to turn off, as with --learn.
        self.reflect = "ask"
        #: History messages already looked back over.
        self.reviewed = 0
        #: Candidates found mid-turn (before compaction), waiting for the
        #: host to offer them when the turn ends.
        self.pending: list[Any] = []
        #: Candidates the person dropped: not offered again this session.
        self.declined: set[str] = set()
        #: The trace's redaction pattern, when the session has one.
        self.redact: Any = None

    # ---- the verbs, scoped to this person --------------------------------

    def remember(self, statement: str, *, kind: str | None = None) -> str:
        meta = {"kind": kind, "package": self.package}
        return self.store.remember(self.user, _clean(statement), meta)

    def recall(self, query: str, limit: int = 10) -> list[MemoryItem]:
        return self.store.recall(self.user, query, limit)

    def forget(self, memory_id: str) -> bool:
        return self.store.forget(self.user, self.resolve(memory_id))

    def resolve(self, memory_id: str) -> str:
        """The whole id for what a person typed: ``#2ad6b855`` for
        ``2ad6b855-0ef2-…``. A store's id may be a UUID, and nobody types
        one; a short start of it is enough when it names ONE memory. Two
        that start the same are refused by name rather than guessed
        between: forgetting the wrong fact is not undone."""
        wanted = memory_id.strip().lstrip("#")
        if not wanted or not supports(self.store, "list"):
            return wanted
        ids = [item.id for item in self.list(1000)]
        if wanted in ids:
            return wanted
        matches = [i for i in ids if i.startswith(wanted)]
        if len(matches) > 1:
            raise MemoryStoreError(f"#{wanted} is the start of {len(matches)} memories' "
                                   "ids; give more of it")
        return matches[0] if matches else wanted

    def copy_from(self, other: Any, limit: int = 1000) -> tuple[int, int]:
        """Copy this person's memories from ``other`` (a store) into this
        one, the way a person moving stores would retype them: each as a
        statement, under its kind. One already here, word for word, is
        skipped, so copying twice keeps one of each. ``other`` is only
        read. Returns (copied, already here)."""
        def norm(text: str) -> str:
            return " ".join(text.lower().split())
        here = ({norm(item.statement) for item in self.list(limit)}
                if supports(self.store, "list") else set())
        copied = skipped = 0
        for item in reversed(other.list(self.user, limit)):   # oldest first
            if norm(item.statement) in here:
                skipped += 1
                continue
            self.store.remember(self.user, _clean(item.statement),
                                {"kind": item.kind, "package": item.package})
            here.add(norm(item.statement))
            copied += 1
        return copied, skipped

    def list(self, limit: int = 100) -> list[MemoryItem]:
        return self.store.list(self.user, limit)

    # ---- the prompt layer -------------------------------------------------

    def prime(self, agent: Any, first_message: str) -> None:
        """Fill the ``memory`` layer for a new conversation. Never raises.

        What matches the first message comes first, up to
        ``PROMPT_SEARCHED``, then the most recent of the rest, up to
        ``PROMPT_LIMIT``. The top-up is the RDU case: "flights to Austin"
        shares no word with "lives near RDU", and on a store that searches
        by words alone the fact has to be there anyway.
        """
        self.primed = True
        try:
            chosen: list[MemoryItem] = []
            seen: set[str] = set()
            found = []
            if supports(self.store, "recall"):
                found += self.recall(first_message, PROMPT_SEARCHED)
            if supports(self.store, "list"):
                found += self.list(PROMPT_LIMIT)
            for item in found:
                if item.id not in seen:
                    seen.add(item.id)
                    chosen.append(item)
        except Exception as exc:  # any store, any failure: fail open
            self.in_prompt = []
            self._set_layer(agent, None)
            self._fail(f"memory unavailable ({self.store.name}: {exc}); "
                       f"continuing without it")
            return
        self.notice = None
        self.in_prompt = _fit(chosen)
        self._set_layer(agent, self.layer_text(agent))

    def layer_text(self, agent: Any) -> str | None:
        can_write = "remember" in getattr(agent, "registry", ())
        lines = ["# What you remember about this person"]
        if self.in_prompt:
            lines.append(
                "They told you these in earlier conversations. Any of them "
                "may be out of date: when the current message says "
                "otherwise, the current message wins. When an answer leans "
                "on one, say so in a few words (\"from RDU, as before -- "
                "say if not\") so a wrong one gets corrected.")
            lines.append("")
            lines.extend(f"- {item.statement}" for item in self.in_prompt)
        elif not can_write:
            return None
        else:
            lines.append("Nothing yet.")
        if can_write:
            lines.append("")
            lines.append(
                "When they tell you something about THEMSELVES that will "
                "still be true in months and would change an answer in a "
                "later conversation -- where they live, what they use, "
                "what they prefer, a correction -- call `remember` with it "
                "as one short sentence. Not the task at hand, not plans "
                "for next week.")
        return "\n".join(lines)

    def _set_layer(self, agent: Any, text: str | None) -> None:
        prompt = attach_prompt(agent)
        prompt.set("memory", text)
        prompt.apply()

    def _fail(self, text: str) -> None:
        self.notice = text
        self.on_notice(text)

    def describe(self) -> dict[str, Any]:
        """For the page's chip and panel."""
        return {"store": self.store.name, "user": self.user,
                "in_prompt": len(self.in_prompt), "notice": self.notice,
                "reflect": self.reflect, "cannot": self.cannot()}

    def cannot(self) -> list[str]:
        """The verbs this store has no way to do, e.g. ``["forget"]``."""
        return [verb for verb in ("remember", "recall", "forget", "list")
                if not supports(self.store, verb)]


def _fit(items: list[MemoryItem]) -> list[MemoryItem]:
    kept, used = [], 0
    for item in items[:PROMPT_LIMIT]:
        used += len(item.statement) + 3
        if used > PROMPT_CHARS:
            break
        kept.append(item)
    return kept


def prime_if_new(agent: Any, user_input: str) -> None:
    """The agents' hook: fill the layer on a conversation's first message.

    "First" is an empty history (a fresh session, or one just /clear-ed)
    or a layer never filled (a resumed conversation's next message).
    """
    memory = getattr(agent, "memory", None)
    if memory is not None and not agent.history:
        memory.reviewed = 0     # a new conversation: nothing looked at yet
    if memory is not None and (not agent.history or not memory.primed):
        memory.prime(agent, user_input)
        # A promoted skill's tool is filled without load_skill, so its
        # remembered inputs ride in its description (skills/promote.py).
        skills = getattr(agent, "skills", None)
        if skills is not None and hasattr(skills, "recall_tool_inputs"):
            skills.recall_tool_inputs(user_input)


def enable_memory(agent: Any, store: MemoryStore, *, user: str | None = None,
                  package: str | None = None) -> Memory | None:
    """Attach memory as ``agent.memory`` and register its two tools.

    None, with nothing attached, when there is nobody to remember things
    about. The tools go through the registry's admission policy like any
    other, so a package's ``[tools] allow`` can leave them out.
    """
    from yantra.memory.tools import RecallMemory, Remember

    who = user if user is not None else resolve_user()
    if not who:
        agent.memory = None
        return None
    memory = Memory(store, who, package=package)
    agent.memory = memory
    for tool in (Remember(memory), RecallMemory(memory)):
        agent.registry.register(tool)
    return memory


__all__ = [
    "ENV_MODE",
    "ENV_USER",
    "MODES",
    "Memory",
    "MemoryStoreError",
    "MemoryItem",
    "MemoryStore",
    "check_mode",
    "enable_memory",
    "memory_mode",
    "short_id",
    "prime_if_new",
    "resolve_user",
    "supports",
]
