"""The built-in store: one sqlite file, keyword search, nothing to install.

It exists so memory works for everybody on day one -- on a laptop with
no server, no embeddings and no second model -- and so the plumbing
around it can be tested against something real. It will not be clever.
Search is word overlap: "flights from RDU" finds "lives near RDU";
"somewhere warm" finds nothing. That is what other stores are for, and
the prompt layer's top-up (memory/__init__.py) covers the common case
anyway, because a person's standing facts are few enough to carry whole.

ONE FILE PER PERSON'S MACHINE, NOT PER PROJECT. It lives at
``~/.local/state/yantra/memory.sqlite`` (or under $XDG_STATE_HOME), beside
the MCP token store, because what it holds is about the person and
follows them from one working tree to the next. Every row carries its
``user``; every read and delete is scoped by it, so two identities on one
machine never see each other's rows.

A MEMORY IN USE IS LISTED AS NEW. ``used`` stamps ``used_at``, and
``list`` (which tops up the prompt layer) orders by the later of that
and ``created_at``: "lives near RDU" from a year ago, drawn on last
week, stays among the twenty a conversation starts with.

SAYING IT TWICE STORES IT ONCE. The same statement (ignoring case and
spacing) for the same person returns the existing id rather than a
second row -- a model that remembers "uses uv, not pip" in three
conversations should not fill the prompt with three copies.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from yantra.memory import MemoryItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user       TEXT NOT NULL,
    statement  TEXT NOT NULL,
    norm       TEXT NOT NULL,
    kind       TEXT,
    package    TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_user ON memories (user, norm);
"""
#: Added after the first files were written: an older file gains it.
USED_COLUMN = "ALTER TABLE memories ADD COLUMN used_at TEXT"

#: Words too common to say anything about what matches.
STOPWORDS = frozenset(
    "a an and are as at be but by can do for from have how i if in is it "
    "me my of on or so that the this to was what when where who why will "
    "with you your find show get want need please".split())

_WORD = re.compile(r"[a-z0-9]+")


def default_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "yantra" / "memory.sqlite"


def _norm(statement: str) -> str:
    return " ".join(statement.lower().split())


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower())
            if len(w) > 1 and w not in STOPWORDS}


class LocalStore:
    """``MemoryStore`` over one sqlite file."""

    name = "local"

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One connection and a lock, as in session.py: the page's endpoints
        # and the agent's worker thread both reach this store.
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(memories)")}
        if "used_at" not in columns:
            self._db.execute(USED_COLUMN)
        self._db.commit()

    def remember(self, user: str, statement: str, meta: dict[str, Any]) -> str:
        norm = _norm(statement)
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM memories WHERE user = ? AND norm = ?",
                (user, norm)).fetchone()
            if row is not None:
                return str(row[0])
            cur = self._db.execute(
                "INSERT INTO memories (user, statement, norm, kind, package, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user, statement, norm, meta.get("kind"), meta.get("package"),
                 datetime.now(UTC).isoformat(timespec="seconds")))
            self._db.commit()
            return str(cur.lastrowid)

    def recall(self, user: str, query: str, limit: int) -> list[MemoryItem]:
        wanted = _words(query)
        if not wanted:
            return []
        scored = []
        for item in self._all(user):
            hits = len(wanted & _words(item.statement))
            if hits:
                scored.append((hits, item))
        # most words in common first; newest first among equals (_all's order)
        scored.sort(key=lambda pair: -pair[0])
        return [item for _, item in scored[:limit]]

    def forget(self, user: str, memory_id: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM memories WHERE user = ? AND id = ?",
                (user, str(memory_id).strip()))
            self._db.commit()
            return cur.rowcount > 0

    def list(self, user: str, limit: int) -> list[MemoryItem]:
        return self._all(user, by_use=True)[:limit]

    def used(self, user: str, memory_ids: list[str]) -> int:
        ids = [str(i).strip() for i in memory_ids if str(i).strip()]
        if not ids:
            return 0
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            cur = self._db.execute(
                f"UPDATE memories SET used_at = ? WHERE user = ? AND id IN "
                f"({','.join('?' * len(ids))})", (now, user, *ids))
            self._db.commit()
            return cur.rowcount

    def _all(self, user: str, *, by_use: bool = False) -> list[MemoryItem]:
        # ISO times in one zone sort as text; in a tie (to the second) the
        # one in use goes first, then the newest row
        order = ("MAX(created_at, COALESCE(used_at, created_at)) DESC, "
                 "used_at IS NOT NULL DESC, id DESC" if by_use else "id DESC")
        with self._lock:
            rows = self._db.execute(
                "SELECT id, statement, created_at, package, kind, used_at "
                f"FROM memories WHERE user = ? ORDER BY {order}", (user,)).fetchall()
        return [MemoryItem(str(i), s, c, p, k, u or "") for i, s, c, p, k, u in rows]
