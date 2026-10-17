"""Every tool a package could name, said plainly, for a page that picks them.

A package's ``[tools] allow`` is a list of names, and the names are a
programmer's: ``web_fetch``, ``mcp__samay__*``, ``browser_open``. A person
choosing what their agent may use had to know them by heart, and a typo
was a tool silently missing (an allow list is complete, base.py). A host
that draws checkboxes needs the other half: what each one is, in words,
and whether using it asks first.

THE CLASSES SAY IT, NOTHING IS STARTED. Each row is read off the tool's
class -- its ``name`` and ``read_only`` -- so listing them builds no
browser, no shell and no server. The browser family is listed only when
the ``[browse]`` extra is installed, because a box for a tool that will
never register is a box that lies.

ASKS IS THE GATE'S OWN BIT. A tool that declared ``read_only`` runs
unasked under "ask"; every other one is a question (permissions.py). The
row carries that bit, not a judgement of its own, so the page and the
gate can't disagree about what a tick means.

PLAIN WORDS, NOT THE MODEL'S. A tool's ``description`` is written for the
model ("Call this BEFORE doing work the skill covers"); ``PLAIN`` is
written for the person ticking the box. A tool without a plain line falls
back to its description's first sentence, so a new built-in shows up
worded, never missing.

What a host adds on top -- its own tools (Dvara's ``send_file``) and the
families its servers bring (``mcp__samay__*``) -- is the host's to list.
"""

from __future__ import annotations

from yantra.memory.tools import RecallMemory, Remember
from yantra.skills.tools import LoadSkill
from yantra.tools.ask_user import AskUser
from yantra.tools.background import BashKill, BashPoll, BashStart
from yantra.tools.browser import (
    BrowserClick,
    BrowserClose,
    BrowserFill,
    BrowserHandoff,
    BrowserOpen,
    browser_available,
)
from yantra.tools.fs import EditFile, ListDir, ReadFile, WriteFile
from yantra.tools.glob import Glob
from yantra.tools.memory import RecallNotes, WriteNote
from yantra.tools.read_image import ReadImage
from yantra.tools.search import Grep
from yantra.tools.shell import Bash
from yantra.tools.todo import TodoRead, TodoWrite
from yantra.tools.web_fetch import WebFetch

#: For the person choosing, not for the model.
PLAIN = {
    "read_file": "Read a file in its folder",
    "list_dir": "See what's in a folder",
    "glob": "Find files by name",
    "grep": "Search inside files",
    "read_image": "Look at a picture",
    "write_file": "Write a file in its folder",
    "edit_file": "Change part of a file",
    "bash": "Run commands on this computer",
    "bash_start": "Start a long-running command",
    "bash_poll": "Check on a long-running command",
    "bash_kill": "Stop a long-running command",
    "web_fetch": "Read a web page (no sign-in)",
    "browser_open": "Open a page in its own browser (no sign-in)",
    "browser_click": "Click on a page it opened",
    "browser_fill": "Type into a page it opened",
    "browser_close": "Close its browser",
    "browser_handoff": "Hand you its browser window to finish a step",
    "write_note": "Keep a note for later",
    "recall_notes": "Read its notes",
    "todo_write": "Keep a to-do list while it works",
    "todo_read": "Read its to-do list",
    "ask_user": "Ask you a question mid-job",
    "load_skill": "Follow a skill's written steps",
    "remember": "Remember something about you",
    "recall_memory": "Recall what it remembers about you",
}

#: Group, then the classes in it, in the order a page shows them.
GROUPS = (
    ("Files", (ReadFile, ListDir, Glob, Grep, ReadImage, WriteFile, EditFile)),
    ("Web", (WebFetch,)),
    ("Its own browser", (BrowserOpen, BrowserClick, BrowserFill, BrowserClose,
                         BrowserHandoff)),
    ("Commands", (Bash, BashStart, BashPoll, BashKill)),
    ("Notes and to-dos", (WriteNote, RecallNotes, TodoWrite, TodoRead)),
    ("Talking and skills", (AskUser, LoadSkill)),
    ("Memory", (Remember, RecallMemory)),
)


def said(tool) -> str:
    """The plain line for one tool (a class or an instance)."""
    if tool.name in PLAIN:
        return PLAIN[tool.name]
    first = " ".join((tool.description or "").split()).split(". ")[0]
    return first.rstrip(".")


def builtin_catalog() -> list[dict]:
    """Yantra's own tools as rows: ``name``, ``said``, ``asks``, ``group``."""
    rows = []
    for group, classes in GROUPS:
        if group == "Its own browser" and not browser_available():
            continue
        for cls in classes:
            rows.append({"name": cls.name, "said": said(cls),
                         "asks": not cls.read_only, "group": group})
    return rows
