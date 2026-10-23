"""A job stopped by a restart, carried on from the step it reached.

A turn that works a phone or a site can take twenty steps and ten
minutes. ``sarathi down && up``, a crash or a killed container in the
middle of it used to lose all of it: the steps already done were done
(a form filled, a message typed) and nothing remembered them, so the
person found out by asking again, and the agent started over from the
first tap.

SAVED AFTER EVERY STEP. ``agent.on_step``, when a host sets it, is
called as a turn begins (the person's message is in the history), after
every batch of tools, and once more as the turn ends (no longer open):
the moments the history has no outstanding call (session.py's
invariant), so a checkpoint written then is always loadable. The host
decides where it goes -- Dvara's session store, the file a scheduled
run was given (``yantra --job``).

OPEN UNTIL IT ENDS. ``agent.turn_open`` is set when a turn begins and
cleared when it ends for any reason: an answer, the iteration cap, a
budget, a hold, a person's Ctrl-C or Stop. A cancelled task -- a service
shutting down -- leaves it set, and so does a process that is simply
killed. The SHAPE of a history cannot tell these apart (a turn that hit
its iteration cap also ends on tool results), so it is a flag, and
``snapshot`` writes it as the ``unfinished`` block: when the turn
started, the person's message, the steps done, the tools they called,
and what outside text it had read (outside.py) so carrying on is not a
way past that guard.

CARRIED ON, NOT RUN AGAIN. ``carry_on`` continues from the last saved
step: nothing already done is done twice. First it tells the model, in
a message of its own, that the work stopped and when, that a screen or
page may have changed and the last step may or may not have happened,
and that if things no longer match it should stop and say so instead of
guessing. That is the "carry on with ...?" when the screen moved: the
agent looks before it acts, and asks when what it sees is not where it
left off. The gate still asks for whatever it would have asked for.

Whether to carry on is the host's: Dvara asks the person; Samay carries
a schedule's run on when it stopped recently, and leaves an old one
``interrupted``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from yantra.types import Message, TextBlock, ToolResult

#: The block a checkpoint of a turn still in progress carries.
KEY = "unfinished"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def step_done(agent: Any) -> None:
    """A step ended (or a turn began): the host's ``on_step``, if any.
    A checkpoint that fails is the host's to say; it never costs the turn."""
    hook = getattr(agent, "on_step", None)
    if hook is None:
        return
    try:
        hook(agent)
    except Exception as exc:  # noqa: BLE001 -- the turn goes on regardless
        agent.step_error = str(exc)


def _person_message(history: list[Message]) -> int | None:
    """Index of the person's message this turn answers: the last user
    message with words in it (tool results carry none)."""
    for i in range(len(history) - 1, -1, -1):
        message = history[i]
        if message.role == "user" and any(isinstance(b, TextBlock) and b.text.strip()
                                          for b in message.content):
            return i
    return None


def unfinished(agent: Any) -> dict | None:
    """The ``unfinished`` block for a turn still open, else None."""
    if not getattr(agent, "turn_open", False):
        return None
    history = agent.history
    start = _person_message(history)
    message = history[start].text().strip() if start is not None else ""
    tools: list[str] = []
    steps = 0
    for later in history[(start or 0) + 1:]:
        if later.role == "assistant":
            tools += [c.name for c in later.tool_calls()]
        elif any(isinstance(b, ToolResult) for b in later.content):
            steps += 1
    return {"started": getattr(agent, "turn_started", "") or now_iso(),
            "saved": now_iso(), "message": message, "steps": steps,
            "tools": tools, "read_outside": list(getattr(agent, "outside_read", []))}


def describe(info: dict, *, limit: int = 3) -> str:
    """One line for a person: what it was doing and how far it got."""
    message = " ".join((info.get("message") or "").split())
    if len(message) > 80:
        message = message[:77] + "..."
    steps = int(info.get("steps") or 0)
    tools = list(dict.fromkeys(info.get("tools") or []))
    done = (f"{steps} step{'s' if steps != 1 else ''} done"
            + (f": {', '.join(tools[-limit:])}" if tools else "")) if steps else \
        "before its first step"
    return f"“{message}” ({done})"


def note(info: dict, now: str | None = None) -> str:
    """What the model is told before it carries on."""
    stopped = info.get("saved") or info.get("started") or "earlier"
    tools = info.get("tools") or []
    last = f" The last tool called was {tools[-1]}." if tools else ""
    return (f"[The work on this stopped here at {stopped}, when the service "
            f"restarted, and is being carried on now ({now or now_iso()}).{last} "
            "Anything on a screen, a page or a phone may have changed since, and "
            "the last step may or may not have happened. Look again before you "
            "act on what you saw before. If things no longer match where you "
            "left off, don't guess: stop, say what you found, and ask whether "
            "to carry on.]")


def prepare(agent: Any) -> dict:
    """Ready ``agent`` to carry on: the note in history, the outside text
    it had read back on its list. Returns the ``unfinished`` block used.
    Raises ValueError when there is nothing to carry on."""
    info = getattr(agent, "unfinished", None)
    if not info or not agent.history or agent.history[-1].role != "user":
        raise ValueError("nothing to carry on: the last turn finished")
    agent.history.append(Message("user", [TextBlock(note(info))]))
    agent.outside_read[:] = list(info.get("read_outside") or [])
    agent.unfinished = None
    return info


__all__ = ["KEY", "describe", "note", "now_iso", "prepare", "step_done", "unfinished"]
