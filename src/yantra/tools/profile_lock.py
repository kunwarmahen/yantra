"""One browser per profile, across every Yantra on this machine.

Chromium already refuses a second browser on a profile it has open --
that refusal is what keeps two agents from fighting over one signed-in
identity. What it does not give is a good answer. The second launch
fails with a stack of Playwright text about a ``SingletonLock``, and it
fails at once, so a run that would have found the profile free thirty
seconds later never gets the chance.

That stopped being rare once something other than a person could start
Yantra. A scheduled run at 08:00 lands on the same profile a
``yantra --web`` has had open since breakfast (``YANTRA_BROWSER_CLOSE=
model`` keeps it open on purpose). ``leases.py`` cannot help: it guards
one process's tools, and these are two processes.

So every Yantra takes a LOCK FILE in the profile before it launches a
browser on it, and gives it back when that browser closes:

* WAIT, THEN SAY WHO. A busy profile is waited on for a while (a short
  wait for a person, longer for an unattended run, which has nobody to
  annoy), then refused in a sentence that names the holder -- its pid,
  since when, and whether a person or a schedule is behind it.
* THE KERNEL RELEASES IT. ``flock`` on an open file: a process that
  crashes, is killed, or is ``kill -9``'d lets go when its descriptor
  closes. There is no stale lock to clean up and no expiry to guess.
* A BUSY PROFILE IS NOT A FAILURE OF THE RUN. Unattended, the refusal is
  also written down (unattended.py) so a scheduler can tell "the browser
  was busy, try at the next time" from "this went wrong".

On a platform without ``fcntl`` the lock is a no-op and Chromium's own
refusal is all there is, as before.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from yantra import unattended
from yantra.errors import ToolError

try:
    import fcntl
except ImportError:  # pragma: no cover -- not POSIX
    fcntl = None

#: The file, inside the profile. Chromium ignores names it does not own.
LOCK_NAME = "yantra.lock"

#: How long a launch waits for a busy profile ($YANTRA_BROWSER_WAIT
#: overrides both). A person is watching a spinner; a schedule is not.
ATTENDED_WAIT = 10.0
UNATTENDED_WAIT = 120.0

_POLL = 0.5


def lock_wait() -> float:
    raw = os.environ.get("YANTRA_BROWSER_WAIT", "").strip()
    if raw:
        try:
            seconds = float(raw)
        except ValueError:
            seconds = -1.0
        if seconds < 0:
            raise ToolError(
                f"YANTRA_BROWSER_WAIT must be a number of seconds, got {raw!r}")
        return seconds
    return UNATTENDED_WAIT if unattended.is_unattended() else ATTENDED_WAIT


class ProfileLock:
    """The lock on one profile, held by this process or not."""

    def __init__(self, profile: Path) -> None:
        self.profile = Path(profile)
        self.path = self.profile / LOCK_NAME
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, wait: float | None = None) -> None:
        """Take the lock, waiting up to ``wait`` seconds; ToolError if
        somebody else still has it. Taking a lock this object holds is a
        no-op, so a browser relaunched mid-turn keeps its place."""
        if self._fd is not None or fcntl is None:
            return
        wait = lock_wait() if wait is None else wait
        self.profile.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    holder = _read_holder(fd)
                    os.close(fd)
                    raise ToolError(self._busy(holder, wait)) from None
                time.sleep(_POLL)
        who = "a schedule" if unattended.is_unattended() else "a person"
        os.ftruncate(fd, 0)
        os.pwrite(fd, (f"{os.getpid()}\n{datetime.now():%H:%M}\n{who}\n")
                  .encode(), 0)
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            os.ftruncate(fd, 0)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _busy(self, holder: tuple[str, str, str] | None, wait: float) -> str:
        if holder:
            pid, since, who = holder
            whose = f"another Yantra (pid {pid}, since {since}, for {who})"
        else:
            whose = "another Yantra"
        unattended.note_busy(f"browser profile {self.profile} in use by {whose}")
        return (
            f"the browser profile {self.profile} is in use by {whose}; "
            f"waited {wait:.0f}s. Only one browser can use a profile at a "
            "time. If a `yantra --web` keeps its browser open "
            "(YANTRA_BROWSER_CLOSE=model), runs on a schedule will keep "
            "finding it busy: give them a profile of their own and sign in "
            "there once with `yantra --browse-login URL`. Do not retry now; "
            "say in your answer that the browser was busy.")


def _read_holder(fd: int) -> tuple[str, str, str] | None:
    try:
        lines = os.pread(fd, 200, 0).decode(errors="replace").splitlines()
    except OSError:
        return None
    return (lines[0], lines[1], lines[2]) if len(lines) >= 3 else None
