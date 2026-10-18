"""Every conversation, kept by itself, to read again later.

``/save`` keeps a conversation when you remember to ask, under a name you
have to remember too, and ``/load`` gives it back by putting it into the
agent -- the right tool for carrying a session on, and the wrong one for
"what did it tell me about that router last week?". That question wants
to READ, not to resume, and it wants to find the conversation by what
was said in it, not by a name chosen before anyone knew it mattered.

This is the log that question reads. Every conversation through Yantra
(the terminal and the page) is written here after each of its turns:
when it started, its first words as a title, where it happened (which
road, which folder), and the conversation itself in the checkpoint's own
shape (session.snapshot), so one can be carried on as well as read.

WRITTEN AS IT GOES, NOT AT THE END. A conversation that ends in a crash,
a closed laptop lid or a killed server is the one most likely to be
looked for later. Each turn replaces the row: one row per conversation,
never one per turn -- this is a record of what was said, not the
append-only checkpoints of session.py, whose promise is about a session
that may continue.

FOUND BY ITS WORDS. The person's words and the agent's answers go into
an SQLite full-text index (FTS5): local, instant, no model and nothing
spent. A tool's output is not indexed -- a page of search results the
agent read is not what anybody remembers the conversation by.

DELETED MEANS GONE. ``delete`` removes the row and its words from the
index, with ``secure_delete`` on so the freed pages are overwritten and
the index merged so no old segment still holds the words. A history you
can only hide is a history you cannot trust with anything. What it does
NOT reach is a ``/save`` you made by hand: that was a separate, deliberate
copy, and ``/load``'s store still has it.

    YANTRA_HISTORY=off       nothing is kept (also --no-history)
    YANTRA_HISTORY=PATH      kept in that file instead of the default
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from yantra.session import snapshot

#: The log's file name, beside the memory store (memory/local.py).
FILE = "conversations.sqlite"
#: A title's length, at most.
TITLE = 80
#: A tool's arguments or output, as the reading view shows them.
SHOWN = 400
#: Around each match in a search's snippet.
HIT, END = "\x02", "\x03"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    n          INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    title      TEXT NOT NULL,
    road       TEXT NOT NULL,
    folder     TEXT NOT NULL,
    model      TEXT NOT NULL,
    turns      INTEGER NOT NULL,
    payload    TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS conversation_words
    USING fts5(title, body);
"""


def default_path() -> Path | None:
    """The log's file, or None when YANTRA_HISTORY=off.

    ONE FILE PER PERSON'S MACHINE, NOT PER PROJECT, for the memory store's
    reason: a conversation is found wherever it happened. It sits beside
    ``memory.sqlite`` under ``~/.local/state/yantra`` ($XDG_STATE_HOME),
    and each row says which folder it was in.
    """
    chosen = os.environ.get("YANTRA_HISTORY", "").strip()
    if chosen.lower() in ("off", "0", "no", "false"):
        return None
    if chosen:
        return Path(chosen).expanduser()
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "yantra" / FILE


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class Kept:
    """One conversation as the list shows it."""
    id: str
    title: str
    road: str
    folder: str
    model: str
    turns: int
    started_at: str
    updated_at: str
    #: The matching words around a match, when the list was a search,
    #: each match between HIT and END (control characters, so nothing a
    #: conversation says can be mistaken for a mark).
    snippet: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


# ---- what a payload says, in words ----------------------------------------------


def _texts(message: dict) -> str:
    return " ".join(b.get("text", "") for b in message.get("content") or []
                    if isinstance(b, dict) and b.get("kind") == "text")


def title_of(payload: dict) -> str:
    """Its first words: what the person first asked."""
    for message in payload.get("history") or []:
        if message.get("role") == "user":
            words = " ".join(_texts(message).split())
            if words:
                return words if len(words) <= TITLE else words[:TITLE - 1] + "…"
    return ""


def turns_of(payload: dict) -> int:
    """How many times the person wrote in it (not tool results)."""
    return sum(1 for m in payload.get("history") or []
               if m.get("role") == "user" and _texts(m).strip())


def body_of(payload: dict) -> str:
    """What the index searches: the person's words and the agent's."""
    return "\n".join(t for m in payload.get("history") or []
                     if m.get("role") in ("user", "assistant")
                     and (t := _texts(m).strip()))


def _short(text: str) -> str:
    return text if len(text) <= SHOWN else text[:SHOWN - 1] + "…"


def entries(payload: dict) -> list[dict]:
    """The conversation the way it looked: what the reading view draws.

    ``who`` is ``you``, ``agent`` or ``tool``. A tool row carries the
    call's name and arguments and, once its result is found further on,
    what came back (shortened) and whether it failed. Thinking is left
    out: it was never shown in the conversation either.
    """
    out: list[dict] = []
    calls: dict[str, dict] = {}
    for message in payload.get("history") or []:
        role = message.get("role")
        for block in message.get("content") or []:
            kind = block.get("kind")
            if kind == "text" and block.get("text", "").strip():
                out.append({"who": "you" if role == "user" else "agent",
                            "text": block["text"]})
            elif kind == "image" and role == "user":
                out.append({"who": "you", "text": "(a picture)"})
            elif kind == "tool_call":
                row = {"who": "tool", "name": block.get("name", ""),
                       "args": _short(json.dumps(block.get("arguments") or {},
                                                 ensure_ascii=False)),
                       "result": "", "error": False}
                calls[block.get("id", "")] = row
                out.append(row)
            elif kind == "tool_result":
                row = calls.get(block.get("tool_call_id", ""))
                if row is not None:
                    row["result"] = _short(str(block.get("content", "")))
                    row["error"] = bool(block.get("is_error"))
    return out


# ---- the search box's words -> an FTS5 query ------------------------------------

_WORD = re.compile(r"\w+", re.UNICODE)


def match_of(words: str) -> str:
    """Every word must appear, each as a prefix ("rout" finds "router").

    Built from word characters only, each one quoted, so nothing a person
    types can be read as FTS5 syntax (a stray quote or ``NEAR`` is a
    word, never an error).
    """
    return " AND ".join(f'"{w}"*' for w in _WORD.findall(words))


class ConversationLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Same reasoning as SessionStore: built on one thread, used from
        # the page's endpoints on another; one lock serializes it.
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA secure_delete = ON")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- writing -------------------------------------------------------------

    def record(self, conversation_id: str, agent, *, provider_name: str,
               road: str, folder: str = "") -> bool:
        """``agent``'s conversation, as it is now, under ``conversation_id``.
        False (and nothing written) while the person has not said anything."""
        return self.keep(conversation_id, snapshot(agent, provider_name),
                         road=road, folder=folder)

    def keep(self, conversation_id: str, payload: dict, *, road: str,
             folder: str = "", at: str | None = None) -> bool:
        """A payload in hand (a host that has one already), same rules.
        ``at`` is when it last moved, for a conversation brought in from
        an older record; now otherwise."""
        turns = turns_of(payload)
        if not turns:
            return False
        # To the microsecond: two turns in one second (a page, a fast
        # model) must still come out newest first.
        now = at or datetime.now(UTC).isoformat(timespec="microseconds")
        title = title_of(payload)
        with self._lock:
            row = self._db.execute(
                "SELECT n FROM conversations WHERE id = ?",
                (conversation_id,)).fetchone()
            if row is None:
                cur = self._db.execute(
                    "INSERT INTO conversations (id, started_at, updated_at, "
                    "title, road, folder, model, turns, payload) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (conversation_id, now, now, title, road, folder,
                     payload.get("model") or "", turns, json.dumps(payload)))
                n = cur.lastrowid
            else:
                n = row[0]
                # The road is where it is going on NOW: a conversation
                # carried on from the page to the terminal says terminal.
                self._db.execute(
                    "UPDATE conversations SET updated_at = ?, title = ?, "
                    "road = ?, folder = ?, model = ?, turns = ?, payload = ? "
                    "WHERE n = ?",
                    (now, title, road, folder, payload.get("model") or "",
                     turns, json.dumps(payload), n))
                self._db.execute("DELETE FROM conversation_words WHERE rowid = ?",
                                 (n,))
            self._db.execute(
                "INSERT INTO conversation_words (rowid, title, body) "
                "VALUES (?,?,?)", (n, title, body_of(payload)))
            self._db.commit()
        return True

    def delete(self, conversation_id: str) -> bool:
        """Gone for good: the row, its words, and the pages they were on."""
        with self._lock:
            row = self._db.execute(
                "SELECT n FROM conversations WHERE id = ?",
                (conversation_id,)).fetchone()
            if row is None:
                return False
            self._db.execute("DELETE FROM conversation_words WHERE rowid = ?",
                             (row[0],))
            self._db.execute("DELETE FROM conversations WHERE n = ?", (row[0],))
            # A deleted FTS5 row leaves its words in older index segments
            # until they merge; merging now leaves none behind.
            self._db.execute("INSERT INTO conversation_words "
                             "(conversation_words) VALUES ('optimize')")
            self._db.commit()
        return True

    # ---- reading -------------------------------------------------------------

    _COLUMNS = ("c.id, c.title, c.road, c.folder, c.model, c.turns, "
                "c.started_at, c.updated_at")

    def listed(self, words: str = "", *, folder: str | None = None,
               limit: int = 200) -> list[Kept]:
        """Newest first; with ``words``, only the ones that say all of them;
        with ``folder``, only the ones held there (a host that keeps one
        log per person and names each agent as the folder: Dvara)."""
        query = match_of(words)
        where, args = ("AND c.folder = ? ", [folder]) if folder is not None else ("", [])
        with self._lock:
            if not query:
                rows = self._db.execute(
                    f"SELECT {self._COLUMNS}, '' FROM conversations c "
                    f"WHERE 1 {where}"
                    "ORDER BY c.updated_at DESC, c.n DESC LIMIT ?",
                    (*args, limit)).fetchall()
            else:
                rows = self._db.execute(
                    f"SELECT {self._COLUMNS}, snippet(conversation_words, 1, "
                    "char(2), char(3), '…', 12) FROM conversation_words w "
                    "JOIN conversations c ON c.n = w.rowid "
                    f"WHERE conversation_words MATCH ? {where}"
                    "ORDER BY c.updated_at DESC, c.n DESC LIMIT ?",
                    (query, *args, limit)).fetchall()
        return [Kept(*r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM conversations").fetchone()[0]

    def payload(self, conversation_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT payload FROM conversations WHERE id = ?",
                (conversation_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def read(self, conversation_id: str) -> dict | None:
        """One conversation for the reading view: its row and its entries."""
        with self._lock:
            row = self._db.execute(
                f"SELECT {self._COLUMNS}, '', c.payload FROM conversations c "
                "WHERE c.id = ?", (conversation_id,)).fetchone()
        if row is None:
            return None
        return {**Kept(*row[:-1]).as_dict(), "entries": entries(json.loads(row[-1]))}


def open_log(path: Path | None = None) -> ConversationLog | None:
    """The log at ``path`` (default: ``default_path()``), or None when off.

    A file that cannot be opened is said by the caller and costs the
    session nothing: no history is a lesser session, not a broken one.
    """
    chosen = path if path is not None else default_path()
    return ConversationLog(chosen) if chosen is not None else None
