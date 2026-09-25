"""Durable sessions: the transcript survives process death.

SQLite (book ch21's choice) with VERSIONED checkpoints: every save
appends a row, nothing is updated in place. Cheap on disk; any prior
version stays loadable for debugging, and a crashed save can never
corrupt the previous good state -- that is what "durable" means here.

What we persist is deliberately small because of one design invariant:
history is ALWAYS resumable at prompt boundaries (the
invariant), so there is no such thing as saving mid-tool-call state.
No idempotency ledger is needed -- a saved session contains zero
outstanding tool_call ids by construction.

Payload shape (JSON inside checkpoints.payload):

    {"format": 1,
     "provider": "anthropic",        # rebuilt via get_provider on load
     "model": "...", "system": ..., "max_iterations": 25,
     "total_usage": {"input_tokens": ..., ...},
     "history": [{"role": "user",
                  "content": [{"kind": "text", "text": ...}, ...]}, ...]}

A TURN HELD FOR APPROVAL IS SAVED AS THE TURN BEFORE IT ASKED (notes/88).
A held turn's history ends on a question nobody has answered, which is
the one state the invariant above rules out. So ``history`` is written
without that last assistant message, and the message, the results of its
batch-mates and the waiting requests go in a separate ``held`` block. An
older reader ignores the block and loads a valid conversation that ends
on the person's message; this one puts the question back and
``agent.held`` with it, so a service can resume tomorrow what a process
held today. No format bump: nothing an old reader does with the file is
wrong, only less.

Blocks serialize with an explicit ``kind`` discriminator and restore
via match/case dispatch -- never guesswork. ThinkingBlock signatures
are opaque strings; they round-trip byte-exact or thinking-assisted
tool loops will 400 after a /load.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, UTC
from pathlib import Path

from yantra.hold import Held, still_current
from yantra.permissions import HELD, PermissionRequest
from yantra.types import (
    ImageBlock,
    Message,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolCall,
    ToolResult,
    Usage,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    version    INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    payload    TEXT NOT NULL,
    UNIQUE (session_id, version)
);
"""


class SessionStore:
    """Append-only checkpoint store. One file holds many named sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + a lock: the store is constructed on the
        # main thread but the web UI's endpoints run on the server's event
        # loop thread -- sqlite connections are thread-affine by default,
        # which would 500 every /api/save. Writes stay serialized (one lock,
        # one connection), which is all a local single-process store needs.
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ---- public API ----------------------------------------------------------

    def save(self, agent, *, provider_name: str, session_id: str = "default") -> int:
        """Snapshot ``agent`` as the next version; returns its version number."""
        with self._lock:
            payload = {
                "format": 1,
                "provider": provider_name,
                "model": agent.model,
                "system": agent.system,
                "max_iterations": agent.max_iterations,
                "total_usage": {
                    "input_tokens": agent.total_usage.input_tokens,
                    "output_tokens": agent.total_usage.output_tokens,
                    "cache_read_tokens": agent.total_usage.cache_read_tokens,
                    "cache_write_tokens": agent.total_usage.cache_write_tokens,
                },
                "history": [_dump_message(m) for m in agent.history],
            }
            held = getattr(agent, "held", None)
            if held is not None and still_current(held, agent.history):
                payload["history"].pop()   # the question, kept below
                payload["held"] = _dump_held(held, agent.history[-1])
            latest = self._latest_version(session_id)
            self._db.execute(
                "INSERT INTO checkpoints (session_id, version, created_at, payload) "
                "VALUES (?, ?, ?, ?)",
                (session_id, latest + 1,
                 datetime.now(UTC).isoformat(timespec="seconds"),
                 json.dumps(payload)),
            )
            self._db.commit()
            return latest + 1

    def load_latest(self, session_id: str = "default") -> dict | None:
        """The newest payload for ``session_id``, or None if never saved."""
        with self._lock:
            row = self._db.execute(
                "SELECT payload FROM checkpoints WHERE session_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def latest_version(self, session_id: str = "default") -> int:
        with self._lock:
            return self._latest_version(session_id)

    def _latest_version(self, session_id: str) -> int:
        """Caller holds the lock."""
        row = self._db.execute(
            "SELECT MAX(version) FROM checkpoints WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row and row[0] is not None else 0


# ---- block-level (de)serialization: kind-discriminated, match-dispatched ----


def _dump_message(message: Message) -> dict:
    content = []
    for block in message.content:
        match block:
            case TextBlock(text=text):
                content.append({"kind": "text", "text": text})
            case ToolCall(id=cid, name=name, arguments=args):
                content.append({"kind": "tool_call", "id": cid,
                                "name": name, "arguments": args})
            case ToolResult(tool_call_id=cid, content=out, is_error=err):
                content.append({"kind": "tool_result", "tool_call_id": cid,
                                "content": out, "is_error": err})
            case ThinkingBlock(thinking=text, signature=sig):
                content.append({"kind": "thinking", "thinking": text,
                                "signature": sig})
            case RedactedThinkingBlock(data=payload):
                # Same byte-exact contract as signatures: ciphertext the
                # provider validates on the next request.
                content.append({"kind": "redacted_thinking", "data": payload})
            case ImageBlock(media_type=mime, data=b64):
                # User attachments AND tool-produced images (read_image)
                # both round-trip: base64 is the payload, so this is
                # byte-exact by construction -- no re-encode, no loss.
                content.append({"kind": "image", "media_type": mime,
                                "data": b64})
    return {"role": message.role, "content": content}


def _load_message(raw: dict) -> Message:
    blocks = []
    for item in raw.get("content", []):
        match item.get("kind"):
            case "text":
                blocks.append(TextBlock(item["text"]))
            case "tool_call":
                blocks.append(ToolCall(item["id"], item["name"],
                                       item.get("arguments") or {}))
            case "tool_result":
                blocks.append(ToolResult(item["tool_call_id"],
                                         item.get("content", ""),
                                         is_error=item.get("is_error", False)))
            case "thinking":
                blocks.append(ThinkingBlock(item.get("thinking", ""),
                                            signature=item.get("signature", "")))
            case "redacted_thinking":
                blocks.append(RedactedThinkingBlock(item.get("data", "")))
            case "image":
                blocks.append(ImageBlock(item["media_type"], item["data"]))
            case unknown:
                raise ValueError(f"cannot restore block kind {unknown!r} "
                                 f"(payload format newer than this code?)")
    return Message(raw["role"], blocks)


def apply_payload(agent, payload: dict, *, settings_loader=None,
                  provider_factory=None, history_only: bool = False) -> str:
    """Restore ``payload`` into ``agent`` IN PLACE. Returns a one-line
    summary for the UI.

    Provider/model rebuild goes through injected callables so this module
    stays decoupled from provider construction (and testable offline).

    A CHECKPOINT HOLDS TWO DIFFERENT THINGS and only one of them is the
    conversation. ``history`` and ``total_usage`` are what was SAID and
    what it cost; ``provider``, ``model``, ``system`` and
    ``max_iterations`` are who was saying it. Restoring both is right at a
    keyboard -- ``/load`` should hand you back the session you left,
    prompt and model included. It is wrong for a host that rebuilds its
    agent every turn from a package on disk: that agent's identity comes
    from the package, which may have been edited since the checkpoint was
    written, and a restore that quietly reinstates last week's system
    prompt makes editing the package look broken.

    ``history_only=True`` restores the conversation and leaves the
    identity alone. Usage rides with the history rather than the identity
    on purpose: it is the record of what this thread has spent, and a
    host that dropped it would restart every cost readout at zero.
    """
    provider_name = payload.get("provider") if history_only else payload["provider"]
    model = payload.get("model") if history_only else payload["model"]
    if history_only:
        # Deliberately BEFORE any identity write, so there is exactly one
        # place to look when asking what this flag does.
        return _apply_history(agent, payload, provider_name, model)
    if provider_factory is not None and settings_loader is not None:
        agent.provider = provider_factory(provider_name, settings_loader(provider_name))
    agent.model = model
    agent.system = payload.get("system")
    agent.max_iterations = payload.get("max_iterations", 25)
    n_msgs = _restore_conversation(agent, payload)
    u = agent.total_usage
    return (f"restored {n_msgs} message(s), "
            f"{u.input_tokens}in/{u.output_tokens}out · "
            f"{provider_name}/{model}")


def _restore_conversation(agent, payload: dict) -> int:
    """What was SAID and what it cost. The half both paths always want."""
    usage = payload.get("total_usage") or {}
    agent.total_usage = Usage(
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=usage.get("cache_read_tokens", 0),
        cache_write_tokens=usage.get("cache_write_tokens", 0),
    )
    agent.history.clear()
    agent.history.extend(_load_message(m) for m in payload.get("history", []))
    agent.held = None
    if payload.get("held"):
        message, agent.held = _load_held(payload["held"])
        agent.history.append(message)
    return len(agent.history)


def _dump_held(held: Held, message: Message) -> dict:
    """A held turn -> the ``held`` block (see the module docstring)."""
    return {
        "message": _dump_message(message),
        "turn_id": held.turn_id,
        "at": held.at,
        "recorded": held.recorded,
        "done": [{"tool_call_id": r.tool_call_id, "content": r.content,
                  "is_error": r.is_error,
                  "images": [{"media_type": i.media_type, "data": i.data}
                             for i in r.images]}
                 for r in held.done.values()],
        "waiting": [{"call_id": r.call_id, "tool_name": r.tool_name,
                     "arguments": r.arguments, "summary": r.summary,
                     "read_only": r.read_only}
                    for r in held.waiting],
    }


def _load_held(raw: dict) -> tuple[Message, Held]:
    """The ``held`` block -> the question to put back, and the hold.

    ``Held.calls`` are the message's OWN ToolCall objects, so an approval
    that edits arguments edits the call history records, exactly as a
    live resume does. ``summarize`` cannot be rebuilt without the tool,
    so an edit made after a reload is previewed as raw arguments.
    """
    message = _load_message(raw["message"])
    done = {r["tool_call_id"]: ToolResult(
        r["tool_call_id"], r.get("content", ""),
        is_error=r.get("is_error", False),
        images=[ImageBlock(i["media_type"], i["data"])
                for i in r.get("images", [])])
        for r in raw.get("done", [])}
    waiting = [PermissionRequest(
        tool_name=w["tool_name"], arguments=w.get("arguments") or {},
        summary=w.get("summary", ""), read_only=w.get("read_only", False),
        call_id=w["call_id"], turn_id=raw.get("turn_id", ""), code=HELD)
        for w in raw.get("waiting", [])]
    held = Held(turn_id=raw.get("turn_id", ""),
                calls=list(message.tool_calls()), done=done, waiting=waiting,
                at=raw.get("at", 0.0), recorded=raw.get("recorded"))
    return message, held


def _apply_history(agent, payload: dict, provider_name, model) -> str:
    """The conversation without the identity -- see ``history_only``."""
    n_msgs = _restore_conversation(agent, payload)
    u = agent.total_usage
    line = (f"restored {n_msgs} message(s), "
            f"{u.input_tokens}in/{u.output_tokens}out · history only")
    # Say it when the checkpoint disagrees with the agent it is going
    # into: silence here is how somebody spends an afternoon wondering
    # which model actually answered.
    if provider_name and model and (model != agent.model):
        line += f" (checkpoint was {provider_name}/{model})"
    return line
