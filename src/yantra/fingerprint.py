"""What a package WAS when a suite ran it -- not what its version says.

A pool of reports adds runs together only when they are samples of the
same thing (notes/62), and "the same thing" has meant the same package
name and version. That is only as good as the author's habit of bumping
the version. Edit ``prompt.md``, forget the bump, and a week of runs of
the old prompt pools with a week of the new one as if nothing changed.
``disagree`` catches that only when the change is so large that two
runs' ranges miss each other entirely.

So a report now carries a FINGERPRINT: a hash of the files the agent is
built from, taken when the suite ran.

WHAT IS HASHED is everything under the package directory, plus any
declared ``tools.dirs``, ``skills.dirs`` or ``agent.prompt`` that lives
outside it, plus the installed version of every named tool pack. That
includes files the agent only READS, like a ``sources/`` folder: if the
material a case asks about changed, runs before and after are not
samples of one rate either.

WHAT IS NOT: ``evals/``, which is the grading side -- editing one case
must not split the history of every other case -- and anything hidden
(``.yantra/`` holds a session database that changes on every run),
``__pycache__`` and compiled ``.pyc`` files, which are the machine's
copies of files already hashed.

Short on purpose: twelve hex characters is a label a person can read in
a terminal and compare by eye, and a collision between two edits of one
package is not a risk worth a longer line.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path
from typing import Any

#: Directories under the package root that are not the agent.
NOT_THE_AGENT = frozenset({"evals"})


def fingerprint(spec: Any) -> str | None:
    """The package's fingerprint, or None for an agent with no package.

    A spec built from flags alone has no directory to hash, and a report
    of it says "unknown" rather than claiming a fingerprint of nothing.
    """
    root = getattr(spec, "root", None)
    if root is None:
        return None
    root = Path(root).resolve()
    digest = hashlib.sha256()
    extra = [Path(d) for d in (*spec.tool_dirs, *spec.skill_dirs)]
    for path in sorted(set(_files(root, top=True)).union(
            *(_files(d.resolve()) for d in extra if not _inside(d, root)))):
        digest.update(os.path.relpath(path, root).encode() + b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    # The prompt as loaded, because agent.prompt may point outside the
    # directory; inside it, this repeats a file already hashed, harmlessly.
    digest.update(b"prompt:" + (spec.prompt or "").encode() + b"\0")
    for pack in sorted(spec.tool_packs):
        try:
            version = metadata.version(pack)
        except metadata.PackageNotFoundError:
            version = "<not installed>"
        digest.update(f"pack:{pack}=={version}\0".encode())
    return digest.hexdigest()[:12]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _files(directory: Path, *, top: bool = False) -> Iterator[Path]:
    """Every file the agent could be built from, under ``directory``."""
    if not directory.is_dir():
        return
    for here, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs
                   if not d.startswith(".") and d != "__pycache__"
                   and not (top and Path(here) == directory
                            and d in NOT_THE_AGENT)]
        for name in files:
            if name.startswith(".") or name.endswith(".pyc"):
                continue
            yield Path(here) / name
