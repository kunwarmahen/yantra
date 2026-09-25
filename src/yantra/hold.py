"""A turn that stops to wait for an answer, and carries on when it comes.

When nobody answered an approval in time, the call used to be refused
and the turn carried on with an error result. The model did the sensible
thing -- said nobody approved it and offered to try again -- and the
person who came back an hour later had to ask again, and the model had
to plan, read and request the same write a second time.

Notes 51 and 78 said the fix was "a queue, not a budget, and it belongs
to whatever owns the channel." That is true of the QUEUE: storing held
requests, telling the person, expiring them, knowing who may answer.
It is not true of what the queue needs underneath: a turn that can stop
WITHOUT an answer and continue WITH one. Only the agent loop can do
that, because only the loop owns the history invariant -- every tool
call has a result before the next request. So this module and the two
agents build the stop and the continue, and a service builds the queue
on top.

A HELD CALL IS NOT A REFUSED CALL. A gate says so with ``permissions.
hold`` (or ``on_timeout="hold"`` on the clock wrappers), and the loop
then does three things with the batch it was in. Calls approved beside
it RUN -- the person may have said yes to three of four. Calls refused
beside it get their error results as usual. Held calls get nothing, and
the turn ends with ``reason="held"``. The history deliberately ends on
an assistant message whose calls are not all answered, and ``agent.
held`` says which ones are waiting.

``resume(answers)`` continues it: approved calls run through the same
execution path (hooks included), the rest are refused in the person's
own words if they gave any, one batch is appended, and the loop carries
on. IT IS A NEW TURN for every budget -- a fresh turn id, a fresh wait
allowance, a fresh dollar meter -- because the person is here now, and
one thing they asked for an hour ago is not the same spend as one they
asked for this minute.

A NEW MESSAGE ABANDONS A HOLD. Sending something else instead of
answering answers the waiting calls with a result saying they were set
aside, and the new turn proceeds. The invariant is restored at the next
request, which is the only moment a provider checks it.

THE ARGUMENTS APPROVED ARE THE ARGUMENTS RUN, BUT THE WORLD MAY HAVE
MOVED. A ``write_file`` approved an hour late writes over whatever is
there now. That is approve-with-edits' rule (what runs is what was
approved) stretched across time, and nothing here can re-check it.
``Held.at`` says how old the question is, for whoever shows it.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from yantra.permissions import PermissionRequest
from yantra.types import ToolCall, ToolResult

#: What ``_gate`` hands back for a held call. Compared by identity, never
#: sent anywhere: a held call has no result, and this is not one.
HOLD = ToolResult("", "", is_error=True)

#: What a held call's result says when a new message arrives instead of
#: an answer. Addressed to the model, which is about to read it.
ABANDONED = ("[held for approval, then set aside: a new message arrived "
             "before anyone answered, so this call never ran]")

#: What one answer may be: approve, refuse, refuse with the person's own
#: sentence, or approve these (edited) arguments instead.
Answer = bool | str | dict[str, Any]


@dataclass(slots=True)
class Held:
    """A turn stopped for approval: what already happened, what is waiting."""

    turn_id: str
    #: The whole batch, in the order the model asked for it -- the order
    #: its results must go back in.
    calls: list[ToolCall]
    #: Results for the calls that ran or were refused beside the held ones.
    done: dict[str, ToolResult]
    #: One request per held call, in call order, exactly as the gate saw it
    #: (a host shows ``summary`` and asks about ``call_id``).
    waiting: list[PermissionRequest]
    #: When the turn stopped (time.time()), so a host can say how old a
    #: question is before somebody approves it.
    at: float = field(default_factory=time.time)
    #: The recording's id for the held turn, when a host recorded it --
    #: kept ON the hold, and saved with it, so the resumed turn can name
    #: the one it continues even after a restart.
    recorded: str | None = None

    @property
    def ids(self) -> list[str]:
        return [request.call_id for request in self.waiting]


class TurnHeld(RuntimeError):
    """``run()`` ended on a hold: there is no reply yet, only questions.

    Raised rather than returned because ``run()`` promises a reply. The
    held calls are on ``held``; answer them with ``resume``.
    """

    def __init__(self, held: Held) -> None:
        names = ", ".join(r.tool_name for r in held.waiting)
        super().__init__(f"turn held for approval of {len(held.waiting)} "
                         f"call(s): {names}; answer them with resume()")
        self.held = held


def check_answers(held: Held | None, answers: Mapping[str, Answer],
                  history: list[Any]) -> Held:
    """Every waiting call answered, nothing else answered, and the
    conversation still where the hold left it. Raises before anything
    has changed, so a bad answer costs nothing."""
    if held is None:
        raise ValueError("nothing is held: there is no turn waiting for "
                         "approval to resume")
    if not still_current(held, history):
        raise ValueError("the conversation moved on since this turn was "
                         "held (cleared, compacted or loaded), so there is "
                         "nothing to resume it into")
    missing = [i for i in held.ids if i not in answers]
    extra = [i for i in answers if i not in held.ids]
    if missing or extra:
        raise ValueError(
            "resume() takes one answer per held call"
            + (f"; unanswered: {', '.join(missing)}" if missing else "")
            + (f"; not held: {', '.join(extra)}" if extra else ""))
    for call_id, answer in answers.items():
        if not isinstance(answer, (bool, str, dict)):
            raise ValueError(
                f"answer for {call_id} must be True, False, the person's "
                f"reason as a string, or edited arguments as a dict "
                f"(got {type(answer).__name__})")
    return held


def held_task(history: list[Any]) -> str:
    """The message a held turn was answering: the last thing the person
    typed before the question. What a resumed turn is recorded under."""
    for message in reversed(history):
        if message.role == "user" and message.text().strip():
            return message.text().strip()
    return ""


def still_current(held: Held, history: list[Any]) -> bool:
    """Whether history still ends on the message the hold stopped at."""
    if not history or history[-1].role != "assistant":
        return False
    return {c.id for c in history[-1].tool_calls()} == {c.id for c in held.calls}


def refusal_text(request: PermissionRequest, answer: Answer) -> str:
    """What the model reads for a held call the person came back and
    refused -- their own words when they gave some (notes/56)."""
    if isinstance(answer, str) and answer.strip():
        return (f"{request.tool_name} was denied. The person said: "
                f"{answer.strip()}")
    return (f"{request.tool_name} was denied: the person came back to the "
            f"approval and said no.")


def abandoned_results(held: Held) -> list[ToolResult]:
    """The batch for a hold nobody answered: what ran, and ABANDONED for
    the rest, in call order."""
    return [held.done.get(call.id)
            or ToolResult(call.id, ABANDONED, is_error=True)
            for call in held.calls]
