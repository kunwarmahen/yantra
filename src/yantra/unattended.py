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

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

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
