"""What Yantra keeps on this computer, for a backup to take.

A backup of the house is one file holding every program's own files
(Sarathi's ``sarathi backup``). Sarathi could guess where those are --
``~/.local/state/yantra`` today -- and be wrong the day a variable moves
one, or a new file appears beside the others. So each program in the
family answers ``keeps --json`` about itself, the way it answers
``status --json``, and the program that packs the backup knows nothing
of what is inside.

THE SHAPE IS THE FAMILY'S. ``format`` names the version
(``yantra.keeps.v1``); ``keeps`` is a list of places, each with an
``id`` (stable: a restore matches places by it), the ``path`` as this
environment resolves it, ``what`` in a person's words, and
``leave_out``: names that are never worth carrying, matched against each
part of a path inside the place (a browser's caches hold no sign-in and
most of the bytes).

WHAT IS YOURS, NOT WHAT IS REBUILT. History, the built-in memory, the
yes you gave each package, MCP servers' sign-ins, the page's browser
profile and the skills you wrote or it learned: each is something only
this computer has. A package, a checkout or a model is not: they come
back by installing them, so none of them is here.

NOTHING IS READ. Only where things are is said; whether a place exists
is said too (``exists``), so "the backup took nothing of Yantra's" has
an answer before it is made.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from yantra.history import default_path as history_path
from yantra.skills.loader import USER_ROOT

FORMAT = "yantra.keeps.v1"

#: Chrome's folders and files that hold no sign-in: its caches, and the
#: locks a running browser leaves (restored, they read as "in use").
BROWSER_LEAVE_OUT = ["Cache", "Code Cache", "GPUCache", "DawnCache", "GrShaderCache",
                     "GraphiteDawnCache", "ShaderCache", "CacheStorage", "ScriptCache",
                     "Crashpad", "BrowserMetrics", "component_crx_cache",
                     "optimization_guide_model_store", "Safe Browsing", "Singleton*"]


def state_dir() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "yantra"


def report(home: Path | None = None) -> dict[str, Any]:
    home = home or Path.home()
    state = state_dir()
    keeps = [
        {"id": "state", "path": str(state),
         "what": "conversations (History), memories in the built-in file, sign-ins to "
                 "MCP servers, the yes you gave each package, the page's browser profile",
         "leave_out": BROWSER_LEAVE_OUT},
        {"id": "skills", "path": str(home.joinpath(*USER_ROOT)),
         "what": "the skills you wrote and the ones it learned", "leave_out": []},
    ]
    history = history_path()
    if history is not None and state not in history.parents:
        # YANTRA_HISTORY names a file of its own, somewhere else
        keeps.append({"id": "history", "path": str(history),
                      "what": "conversations (History)", "leave_out": []})
    for place in keeps:
        place["exists"] = Path(place["path"]).exists()
    return {"format": FORMAT, "keeps": keeps}


def lines(data: dict[str, Any]) -> list[str]:
    out = []
    for place in data["keeps"]:
        missing = "" if place["exists"] else "  (not there yet)"
        out.append(f"{place['id']:<8} {place['path']}{missing}")
        out.append(f"         {place['what']}")
    return out
