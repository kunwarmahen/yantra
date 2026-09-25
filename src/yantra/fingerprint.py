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

TWO MORE THINGS CHANGE A RATE UNDER AN UNCHANGED NAME (notes/72), and
each gets a fingerprint of its own rather than joining this one:

* A CASE's definition -- its table as written, minus the prose
  ``description``, plus the grader module when it names one. Per case,
  because editing one case must split that case's history and no other.
* The MODEL's weights behind a local tag. ``ollama pull`` can put new
  weights behind ``qwen3.8:latest`` without the tag changing, and Ollama
  reports the digest; a cloud provider does not, so there it is unknown.

Short on purpose: twelve hex characters is a label a person can read in
a terminal and compare by eye, and a collision between two edits of one
package is not a risk worth a longer line.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

from yantra.tools.discover import parse_pack

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
    for text in sorted(spec.tool_packs):
        # By NAME: "tide-pack==0.2.1" is the line, "tide-pack" the
        # distribution, and the installed release is what gets hashed.
        pack = parse_pack(text)[0]
        try:
            version = metadata.version(pack)
        except metadata.PackageNotFoundError:
            version = "<not installed>"
        digest.update(f"pack:{pack}=={version}\0".encode())
    return digest.hexdigest()[:12]


def case_fingerprint(entry: dict, graders: Path | None) -> str:
    """One ``[[case]]`` table as written, plus its grader file (notes/72).

    ``description`` is left out: it is prose for the reader, and a typo
    fixed in it grades nothing differently. The whole grader module
    (``graders.py`` for ``check = "graders:..."``) is hashed rather than
    the one function, because a grader leans on the module around it (a
    constant, a helper) and a function's source alone would miss the edit
    that mattered.
    """
    digest = hashlib.sha256()
    graded = {k: v for k, v in entry.items() if k != "description"}
    digest.update(json.dumps(graded, sort_keys=True, default=str).encode())
    if graders is not None and "check" in entry:
        try:
            digest.update(b"\0graders:" + graders.read_bytes())
        except OSError:
            digest.update(b"\0graders:<unreadable>")
    return digest.hexdigest()[:12]


def weights(provider_name: str, base_url: str | None, model: str,
            *, timeout: float = 3.0) -> str | None:
    """The digest of the weights behind a LOCAL model tag, or None.

    Asked of Ollama's own ``/api/tags`` -- the one road where the tag and
    the weights can drift apart on the operator's own machine. Anything
    that goes wrong (another provider, the server down, a tag it does not
    list) is None: unknown, which a pool treats as unknown, never as a
    match or a difference. A report is not worth failing over this.
    """
    if provider_name != "ollama" or not base_url:
        return None
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        response = httpx.get(f"{root}/api/tags", timeout=timeout)
        models = response.json().get("models", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    wanted = {model, f"{model}:latest"}
    for entry in models if isinstance(models, list) else []:
        if isinstance(entry, dict) and entry.get("name") in wanted:
            digest = str(entry.get("digest") or "")
            return digest.removeprefix("sha256:")[:12] or None
    return None


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
