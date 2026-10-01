"""Reflection: a look back over a conversation when it ends.

Nobody says "remember that I live near RDU". They say "flights from RDU
to Denver", and whether that is kept depends on the model happening to
call ``remember`` in the middle of answering. Some do; nothing makes
them. So when a conversation ENDS -- /quit, /clear, switching to another
one, and just before compaction drops old turns -- the agent's own model
is asked once, fresh and small: what did you learn about this person
that passes the three tests (memory/__init__.py)? It answers with short
candidate sentences, and the person keeps or drops each.

    notice   the conversation ended, and has turns not yet looked at   (free)
    scrub    secrets, and the trace's own redaction, out of the text   (free)
    ask      one plain completion: the three tests, what is known      (1 call)
    offer    the person keeps or drops each candidate                  (the host)

ONE CALL PER CONVERSATION, NOT PER TURN. A look back after every turn
would pay a model call for "thanks" and "try again". The end of a
conversation is where everything it will say has been said. The cost is
that a fact from this conversation reaches the prompt from the next
one -- which is when the layer is filled anyway.

THE AGENT'S OWN MODEL, NOTHING NEW. The call goes to the provider and
model the session already has. On a local model that means no second
model and nothing leaving the machine; on a cloud model the transcript
goes where every turn of it already went. The store still never sees a
transcript: it is handed the sentences the person kept.

NOTHING IS WRITTEN WITHOUT A YES. ``ask`` (the default where somebody is
at the terminal or page) shows each candidate and keeps none unless
told. ``auto`` keeps them all, for unattended runs that asked for it.
``off`` never looks. A candidate dropped once is not offered again in
the same session.

SCRUBBED BEFORE IT IS READ. The transcript goes through the same secret
patterns the skill learner uses (tokens, ``KEY=value``, Authorization
headers) and through the trace's own ``--trace-redact`` patterns and
word lists when the session has them. A candidate that still carries a
``[redacted]`` is dropped rather than offered: a memory with a hole in
it is either useless or the shape of a secret.

A FACT SAYS WHAT IT IS ABOUT. "Uses Neovim." is true and nearly
unfindable: a store that searches by meaning ranked it below twenty
other facts for "format-on-save for Python", while "Uses Neovim as their
text editor" was found every time (notes/105). The prompt asks for the
kind of thing as well as the value -- with examples the recall trial
does not use, so the trial still measures the rule and not a copied
answer.

WHAT WAS SAID, NOT WHAT WAS THOUGHT. The model's reasoning and the
arguments of its tool calls are left out of the transcript. Both are the
assistant's, and both read the system prompt: a model thinking "the user
is mahen, in /home/mahen", or calling bash with ``cd /home/mahen/...``,
hands the look back a fact the person never said, and the look back kept
it. The assistant's replies stay, because an answer ("Neovim") only
means something next to its question ("which editor?"); for those, the
prompt's own rule is the guard.

LOOKED AT ONCE. ``Memory.reviewed`` counts the history messages already
looked back over, so /remember followed by /quit asks about the new
turns only, and a conversation compacted twice is not read twice.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from yantra.context import render_segment
from yantra.types import (Message, RedactedThinkingBlock, TextBlock,
                          ThinkingBlock, ToolCall, Usage)

ENV_MODE = "YANTRA_REFLECT"
MODES = ("ask", "auto", "off")

#: At most this many candidates from one look back. A conversation that
#: taught five standing facts about a person is already a rare one.
MAX_CANDIDATES = 5
#: The transcript's ceiling, in characters, clipped from the middle: the
#: opening says who the person is and what they came for, the end is
#: where corrections land.
TRANSCRIPT_CHARS = 16_000
#: Room for a local model's reasoning before its answer.
REFLECT_MAX_TOKENS = 4096
REDACTED = "[redacted]"
KINDS = ("fact", "preference", "correction")

PROMPT = """\
Below is a conversation between a person and an assistant. Pick out what \
is worth remembering about the PERSON for later, different conversations.

Keep a fact only if all three hold:
1. It is about the person themselves -- where they live, what they use, \
what they prefer, who they work with, a correction of something wrong -- \
not about the task.
2. It will still be true in months. Trips, deadlines and this week's \
plans fail.
3. It would change an answer in a later conversation that does not \
mention it. ("Flights from RDU" said in passing means they probably live \
near RDU, and a later "find me flights to Austin" should start from \
there.)

Only what the person said or plainly showed. Not what the assistant \
suggested, guessed or looked up. Nothing already remembered (listed \
below). No passwords, tokens, keys or account numbers.

Plainly showed includes HOW they say things: the currency they quote \
prices in, the units they measure in, the language they write in. Said \
in passing inside a task, a habit like that is still about the person.

Already remembered:
{remembered}

Word each one so someone asking about it later finds it: say what the \
thing IS. "Uses Fish as their command-line shell", not "Uses Fish"; \
"Their dog is named Biscuit", not "Biscuit".

Reply with one line per fact, at most {limit}. Start every line with its \
kind -- fact:, preference: or correction: -- then one short \
self-contained sentence in the third person, like these:
fact: Lives near RDU (Raleigh-Durham airport).
preference: Prefers short answers.
correction: Their manager is Priya, not Sam.
If nothing passes, reply with exactly: NONE

The conversation:
<<<
{transcript}
>>>"""

_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*\**(fact|preference|correction)"
                   r"\**\s*[:\-–—]\s*(.+?)\s*$", re.IGNORECASE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One sentence the look back proposes to remember."""

    statement: str
    kind: str = "fact"


def reflect_mode(flag: str | None = None,
                 env: dict[str, str] | None = None) -> str:
    """``--reflect`` beats $YANTRA_REFLECT beats ``ask``."""
    env = os.environ if env is None else env
    where, raw = ("--reflect", flag) if flag is not None else \
        (ENV_MODE, env.get(ENV_MODE) or "ask")
    mode = raw.strip().lower()
    if mode not in MODES:
        raise ValueError(f"{where}={raw!r}: expected one of {', '.join(MODES)}")
    return mode


# ---- the text the model sees -------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 1}


def _clip(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    keep = cap // 2
    return (f"{text[:keep]}\n[... {len(text) - cap} chars omitted ...]\n"
            f"{text[-keep:]}")


def scrub(text: str, redact: re.Pattern[str] | None = None) -> str:
    """Secrets out, then the session's own trace redaction, if any."""
    from yantra.skills.learn import scrub as scrub_secrets

    text = scrub_secrets(text)
    if redact is not None:
        text = redact.sub(REDACTED, text)
    return text


def unreviewed(memory: Any, history: list[Message]) -> list[Message]:
    """The messages not yet looked back over; empty when none of them is
    something the person said."""
    start = memory.reviewed if 0 <= memory.reviewed <= len(history) else 0
    tail = history[start:]
    said = any(m.role == "user" and any(isinstance(b, TextBlock) and b.text.strip()
                                        for b in m.content) for m in tail)
    return tail if said else []


def spoken(messages: list[Message]) -> list[Message]:
    """The conversation without the model's reasoning, and its tool calls
    by name only."""
    def said(block):
        if isinstance(block, ToolCall):
            return ToolCall(block.id, block.name, {})
        return block
    thought = (ThinkingBlock, RedactedThinkingBlock)
    return [Message(m.role, [said(b) for b in m.content
                             if not isinstance(b, thought)])
            for m in messages]


def build_prompt(transcript: str, remembered: list[str]) -> str:
    known = "\n".join(f"- {s}" for s in remembered) or "(nothing yet)"
    return PROMPT.format(remembered=known, limit=MAX_CANDIDATES,
                         transcript=transcript)


def parse_reply(text: str, remembered: list[str],
                declined: set[str] = frozenset()) -> list[Candidate]:
    """Candidate lines out of a reply, minus what is known, dropped, or
    scrubbed. Anything that is not a candidate line is ignored, which is
    how "NONE", a preamble, or a model thinking out loud all come to
    nothing."""
    known = [(_norm(s), _words(s)) for s in remembered]
    out: list[Candidate] = []
    seen: set[str] = set()
    for line in _THINK.sub("", text or "").splitlines():
        match = _LINE.match(line)
        if match is None:
            continue
        kind = match.group(1).lower()
        statement = " ".join(match.group(2).strip().strip('"').split())
        norm = _norm(statement)
        if not statement or REDACTED in statement or len(statement) > 300:
            continue
        if norm in seen or norm in declined:
            continue
        words = _words(statement)
        # "Lives near RDU." against a remembered "Lives near RDU
        # (Raleigh-Durham airport)": the same fact, said again.
        if any(norm == k or (words and words <= kw) for k, kw in known):
            continue
        seen.add(norm)
        out.append(Candidate(statement, kind if kind in KINDS else "fact"))
        if len(out) == MAX_CANDIDATES:
            break
    return out


# ---- the look back -------------------------------------------------------------


def _prepare(agent: Any) -> tuple[Any, str, list[str]] | None:
    memory = getattr(agent, "memory", None)
    if memory is None:
        return None
    tail = unreviewed(memory, agent.history)
    if not tail:
        memory.reviewed = len(agent.history)
        return None
    remembered = [item.statement for item in memory.list(100)]
    transcript = _clip(scrub(render_segment(spoken(tail)), memory.redact),
                       TRANSCRIPT_CHARS)
    return memory, build_prompt(transcript, remembered), remembered


def _account(agent: Any, response: Any) -> None:
    """A look back is spent on the person's behalf: it goes on the meter."""
    usage = getattr(agent, "total_usage", None)
    if usage is not None:
        usage.add(response.usage)
    by_model = getattr(agent, "usage_by_model", None)
    if by_model is not None:
        by_model.setdefault(response.model or agent.model, Usage()).add(response.usage)


def reflect(agent: Any) -> list[Candidate]:
    """Look back over what this conversation has not been looked at for.

    Raises what the store or provider raises; the hosts turn that into a
    line, because a look back must never cost the conversation it ends.
    """
    prepared = _prepare(agent)
    if prepared is None:
        return []
    memory, prompt, remembered = prepared
    end = len(agent.history)
    response = agent.provider.complete(
        messages=[Message("user", [TextBlock(prompt)])], system=None,
        tools=[], model=agent.model, max_tokens=REFLECT_MAX_TOKENS)
    _account(agent, response)
    memory.reviewed = end
    return parse_reply(response.message.text(), remembered, memory.declined)


async def areflect(agent: Any) -> list[Candidate]:
    """The async twin: the store off the loop, the call through acomplete."""
    import asyncio

    prepared = await asyncio.to_thread(_prepare, agent)
    if prepared is None:
        return []
    memory, prompt, remembered = prepared
    end = len(agent.history)
    response = await agent.provider.acomplete(
        messages=[Message("user", [TextBlock(prompt)])], system=None,
        tools=[], model=agent.model, max_tokens=REFLECT_MAX_TOKENS)
    _account(agent, response)
    memory.reviewed = end
    return parse_reply(response.message.text(), remembered, memory.declined)


def keep(memory: Any, candidates: list[Candidate]) -> list[str]:
    """Remember each; returns the ids. Scrubbed once more on the way in."""
    ids = []
    for candidate in candidates:
        statement = scrub(candidate.statement, memory.redact)
        if REDACTED in statement:
            continue
        ids.append(memory.remember(statement, kind=candidate.kind))
    return ids


def decline(memory: Any, candidates: list[Candidate]) -> None:
    """Not offered again this session."""
    memory.declined.update(_norm(c.statement) for c in candidates)


def mark_reviewed(agent: Any) -> None:
    """A conversation restored from a checkpoint counts as looked at: it
    was looked back over when it ended, and a candidate dropped then
    should not come back because the same turns were loaded again."""
    memory = getattr(agent, "memory", None)
    if memory is not None:
        memory.reviewed = len(agent.history)
        memory.pending.clear()


# ---- before compaction ------------------------------------------------------------


def _settle(memory: Any, found: list[Candidate]) -> None:
    if memory.reflect == "auto":
        keep(memory, found)
    else:
        memory.pending.extend(c for c in found if c not in memory.pending)


def before_compaction(agent: Any) -> None:
    """Look back at what compaction is about to fold away. Never raises.

    Mid-turn there is nobody to ask, so under ``ask`` the candidates wait
    on ``memory.pending`` for the host to offer when the turn ends.
    """
    memory = getattr(agent, "memory", None)
    if memory is None or memory.reflect == "off":
        return
    try:
        _settle(memory, reflect(agent))
    except Exception as exc:  # the turn goes on; the look back does not
        memory._fail(f"looking back before compaction failed ({exc}); "
                     f"continuing")


async def abefore_compaction(agent: Any) -> None:
    memory = getattr(agent, "memory", None)
    if memory is None or memory.reflect == "off":
        return
    try:
        _settle(memory, await areflect(agent))
    except Exception as exc:
        memory._fail(f"looking back before compaction failed ({exc}); "
                     f"continuing")


__all__ = [
    "Candidate",
    "ENV_MODE",
    "MODES",
    "abefore_compaction",
    "areflect",
    "before_compaction",
    "decline",
    "keep",
    "mark_reviewed",
    "parse_reply",
    "reflect",
    "reflect_mode",
]
