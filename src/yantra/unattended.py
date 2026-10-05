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

ONE PROCESS, ONE RUN. The list is a module global because the run that
reads it is the process: a one-shot ``yantra --prompt`` that prints its
answer and exits. A long-lived host that serves many unattended turns
passes ``unattended`` per turn instead and clears between them.
"""

from __future__ import annotations

import os
import threading

from yantra.errors import UserUnavailable

_needs: list[str] = []
_busy: list[str] = []
_refused: list[str] = []
_lock = threading.Lock()


def is_unattended() -> bool:
    """True when ``$YANTRA_UNATTENDED`` says nobody is in front of this run."""
    return os.environ.get("YANTRA_UNATTENDED", "").strip().lower() in (
        "1", "true", "yes", "on")


def note(need: str) -> None:
    """Write down one thing this run needed a person for."""
    need = " ".join(need.split())
    if not need:
        return
    with _lock:
        if need not in _needs:
            _needs.append(need)


def needs() -> list[str]:
    """What this run needed a person for, in the order it found out."""
    with _lock:
        return list(_needs)


def note_busy(what: str) -> None:
    """Write down something this run found in use by somebody else (a
    browser profile). Not a need: nobody has to do anything, and the next
    run may well find it free."""
    with _lock:
        if what not in _busy:
            _busy.append(what)


def busy() -> list[str]:
    with _lock:
        return list(_busy)


def note_refused(tool: str) -> None:
    """A tool this run wanted and was not allowed ahead of time. Not a
    need either: the model is told no and usually finds another way, and
    the answer may be complete without it. Kept so whoever set the run
    up can see what it reached for, and allow it next time if they
    want."""
    with _lock:
        if tool not in _refused:
            _refused.append(tool)


def refused() -> list[str]:
    with _lock:
        return list(_refused)


def clear() -> None:
    with _lock:
        _needs.clear()
        _busy.clear()
        _refused.clear()


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
