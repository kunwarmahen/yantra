"""A run with nobody in front of it -- and what it needed from somebody.

A clock that wakes an agent at three in the morning is not a person
typing a prompt, and three things Yantra does assume the second:

* a permission prompt waits for a keystroke;
* ``ask_user`` waits for an answer;
* ``browser_handoff`` opens a window and waits ten minutes for someone
  to sign in.

Unattended, each of those is a turn that hangs or guesses. So a run can
say up front that nobody is there (``--unattended``, or
``YANTRA_UNATTENDED=1`` for anything that starts Yantra as a program),
and the three places that would wait answer at once instead: writes are
refused the way a headless run already refuses them, a question fails
the turn, and a handoff tells the model to stop and say what it needs.

WHAT IT NEEDED IS KEPT, NOT ONLY SAID. The model will usually explain an
expired login in its answer, but a caller that scheduled this run -- and
will run it again in an hour -- must not have to read prose to learn
that retrying is pointless until somebody signs in. ``note`` writes the
need down in plain words; ``--json`` reports the list as
``needs_person``, and an empty list means the run did not get stuck on a
person. Two other lists are kept apart from it, because neither one
means a person must act: ``busy`` (a browser profile another Yantra
holds -- the next run may find it free) and ``refused`` (a tool the run
reached for that was not allowed ahead of time -- the model was told no
and may well have answered without it).

ONE RUN, ONE RECORD. For a one-shot ``yantra --prompt`` the run IS the
process, so the record is the process's. A long-lived host that serves
many unattended turns at once -- a service running scheduled turns for
several people -- opens ``scope()`` around each turn instead, and each
turn gets a record of its own.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from yantra.errors import UserUnavailable


@dataclass
class Record:
    """What one unattended run found: needed, busy, refused."""

    needs: list[str] = field(default_factory=list)
    busy: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, where: list[str], item: str) -> None:
        with self._lock:
            if item not in where:
                where.append(item)


#: The process's own record: a one-shot run, which IS the process.
_process = Record()

#: A turn's own record, for a host that runs many unattended turns in
#: one process (a service). Set by ``scope``; read by everything below.
_current: ContextVar[Record | None] = ContextVar("yantra_unattended",
                                                 default=None)


def _record() -> Record:
    return _current.get() or _process


@contextmanager
def scope() -> Iterator[Record]:
    """One unattended TURN in a process that serves many.

    Inside it, ``is_unattended()`` is true and every need, busy profile
    and refusal lands on the Record it yields, not on the process's --
    so two scheduled turns running at once in one service never report
    each other's sign-in walls. A context variable, so it follows the
    turn into the tasks and ``to_thread`` workers it starts; a tool that
    keeps a worker thread of its own carries it across itself (the
    browser does).
    """
    record = Record()
    token = _current.set(record)
    try:
        yield record
    finally:
        _current.reset(token)


def is_unattended() -> bool:
    """True inside ``scope()``, or when ``$YANTRA_UNATTENDED`` says
    nobody is in front of this run."""
    if _current.get() is not None:
        return True
    return os.environ.get("YANTRA_UNATTENDED", "").strip().lower() in (
        "1", "true", "yes", "on")


def note(need: str) -> None:
    """Write down one thing this run needed a person for."""
    need = " ".join(need.split())
    if need:
        record = _record()
        record.add(record.needs, need)


def needs() -> list[str]:
    """What this run needed a person for, in the order it found out."""
    return list(_record().needs)


def note_busy(what: str) -> None:
    """Write down something this run found in use by somebody else (a
    browser profile). Not a need: nobody has to do anything, and the next
    run may well find it free."""
    record = _record()
    record.add(record.busy, what)


def busy() -> list[str]:
    return list(_record().busy)


def note_refused(tool: str) -> None:
    """A tool this run wanted and was not allowed ahead of time. Not a
    need either: the model is told no and usually finds another way, and
    the answer may be complete without it. Kept so whoever set the run
    up can see what it reached for, and allow it next time if they
    want."""
    record = _record()
    record.add(record.refused, tool)


def refused() -> list[str]:
    return list(_record().refused)


def clear() -> None:
    """Empty the process's record (tests; a host reusing a process)."""
    global _process
    _process = Record()


#: Where a run nobody is watching may still put a question: a Dvara's
#: ``POST /ask``, its token, the person, and seconds of waiting in all.
ASK_URL, ASK_TOKEN, ASK_ACTOR, ASK_WAIT = (
    "YANTRA_ASK_URL", "YANTRA_ASK_TOKEN", "YANTRA_ASK_ACTOR", "YANTRA_ASK_WAIT")
#: Extra seconds the HTTP call gets beyond the question's own wait.
ASK_SLACK = 30.0


@dataclass
class Reach:
    """Somebody a run nobody is WATCHING can still ASK.

    Unattended means nobody is at this run; it does not have to mean the
    person is out of reach. "Turn off the stairs light at four" made at
    the computer ran on Samay's direct road, met a switch nobody had
    allowed ahead of time, and was refused -- while its person sat in
    front of Telegram and the page that would have taken a yes. When
    whoever started this run knows a Dvara and who the run is for, the
    question goes there instead (``POST /ask``): on the person's chat,
    with two buttons, waiting at most ``seconds`` IN ALL for the run.

    THE WAIT IS THE RUN'S, NOT EACH QUESTION'S. A run with five held
    calls does not wait five times thirty minutes; once its wait is
    spent, what is left is refused without asking, the way Dvara's own
    scheduled turns lapse. A question nobody answered is a refusal, never
    a yes, and every refusal is written down (``note_refused``) exactly
    as the plain unattended gate writes them.

    Dvara decides what a person may be asked: its owner's deny rules
    still refuse, and a person who is never asked is never "answered".
    This only carries the question; it grants nothing.
    """

    url: str
    token: str
    actor: str
    seconds: float
    spent: float = 0.0

    @classmethod
    def from_env(cls) -> Reach | None:
        url = os.environ.get(ASK_URL, "").strip().rstrip("/")
        actor = os.environ.get(ASK_ACTOR, "").strip()
        if not url or not actor:
            return None
        try:
            seconds = float(os.environ.get(ASK_WAIT, "") or 1800)
        except ValueError:
            seconds = 1800.0
        return cls(url=url, token=os.environ.get(ASK_TOKEN, "").strip(),
                   actor=actor, seconds=max(0.0, seconds))

    def gate(self, request: Any) -> bool:
        """A ``PermissionFn``: read-only runs, anything else is asked."""
        from yantra.permissions import (REFUSED_OUT_OF_TIME,
                                        REFUSED_UNATTENDED, refuse)

        if request.read_only:
            return True
        left = self.seconds - self.spent
        if left <= 0:
            note_refused(request.tool_name)
            return refuse(
                request,
                f"{request.tool_name} was denied: this run's time for asking "
                f"its person is spent, so it was not asked. Say what you would "
                f"have done.", code=REFUSED_OUT_OF_TIME)
        started = time.monotonic()
        try:
            answer = self._post({
                "actor": self.actor, "tool": request.tool_name,
                "summary": request.summary or request.tool_name,
                "arguments": _plain(request.arguments), "timeout": left})
        except (OSError, ValueError) as exc:
            note_refused(request.tool_name)
            return refuse(
                request,
                f"{request.tool_name} was denied: this run is unattended and "
                f"its person could not be reached to ask ({exc}). Say what "
                f"you would have done.", code=REFUSED_UNATTENDED)
        finally:
            self.spent += time.monotonic() - started
        if answer.get("via"):
            request.answered_by = f"{self.actor} on {answer['via']}"
        if answer.get("approved") is True:
            return True
        note_refused(request.tool_name)
        return refuse(request, str(answer.get("reason") or
                                   f"{request.tool_name} was not approved."),
                      code=str(answer.get("code") or REFUSED_UNATTENDED))

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.url}/ask", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(
                    request, timeout=body["timeout"] + ASK_SLACK) as response:
                answer = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                said = json.loads(exc.read() or b"{}").get("detail")
            except (ValueError, AttributeError):
                said = None
            raise OSError(f"dvara said {exc.code}: {said or exc.reason}") from None
        if not isinstance(answer, dict):
            raise ValueError("dvara answered something that is not an object")
        return answer


def _plain(arguments: Any) -> Any:
    """The call's arguments as JSON can carry them (Dvara's rules read
    them); anything odd becomes its text."""
    return json.loads(json.dumps(arguments or {}, default=str))


class NobodyChannel:
    """``ask_user``'s channel when nobody is there: the question is kept
    as a need, and the turn fails the way a headless one always has --
    a guessed answer would be worse than none."""

    def ask(self, question: str, choices: list[str],
            context: str = "") -> str:
        note(f"a question: {question}")
        raise UserUnavailable(
            f"ask_user ran in an unattended run, so nobody can answer: "
            f"{question}")
