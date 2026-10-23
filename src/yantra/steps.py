"""What a turn did, step by step -- to look at, and to send with a bug report.

A long phone or site job that went wrong leaves a question nobody can
answer from the chat: WHICH step? The answer said "I couldn't find the
Return button", and the person wants to see what the agent saw on step
four, what it tapped, whether anybody was asked, and how long each part
took. The X sign-in hunt took a day of reading logs for exactly this.

THE CONVERSATION ALREADY HAS MOST OF IT. Every host keeps the whole
conversation: the terminal and the page in History, Dvara per person, a
scheduled run in its ``--job`` file. Each tool call is there with its
arguments, each result in full, and the pictures a tool returned. What a
conversation could not say is WHEN, HOW LONG, and WHO SAID YES. So the
loop writes a small note per step and per turn beside the history, and
``snapshot`` carries them in the same payload (``steps``, ``turns``):

    steps[call_id] = {"turn": KEY, "at": ISO, "waited": s, "ran": s,
                      "answered_by": "you, on the page", "pictures": 1,
                      "refused": CODE}
    turns[KEY]     = {"started": ISO, "ended": ISO, "outcome": "end_turn",
                      "kind": "message" | "resumed" | "carried",
                      "message": "...", "answer": "..."}

``KEY`` is the host's tag for the turn when it has one (Dvara's run id,
so a run in its ledger finds its own steps) and the turn's own id
otherwise. ``waited`` is the time at the permission gate: a person's yes
can take a minute, and that minute is not the tool's.

NOTHING NEW IS KEPT. Arguments and results stay where they already are,
in the history, under the conversation's own rules (History's delete
deletes them). A note is times and names.

ONE READING, MANY PAGES. ``timeline`` turns a payload into one turn's
steps (format ``yantra.steps.v1``), the shape Yantra's page, Dvara's
page and Samay's page all draw, and ``yantra --steps FILE`` prints for a
program that imports nothing of Yantra's. A conversation from before the
notes still reads: its steps, without the times. ``markdown`` is the
same turn as a file to attach to a bug report -- and it says, first
line, that it holds what the tools were given and read.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

FORMAT = "yantra.steps.v1"
#: How much of one result a timeline carries; the history has the rest.
RESULT_CHARS = 4000
SHORT_CHARS = 160
ANSWER_CHARS = 2000
#: How the carry-on note to the model starts (carry.note): not a
#: person's message, so not a new turn in the timeline.
CARRY_NOTE = "[The work on this stopped here"


def _iso(moment: float | None = None) -> str:
    return datetime.fromtimestamp(moment if moment is not None else time.time(),
                                  UTC).isoformat(timespec="seconds")


# ---- the notes, as the loop takes them -----------------------------------------


def turn_began(agent: Any, kind: str = "message") -> None:
    key = getattr(agent, "turn_tag", "") or agent._turn_id
    agent._turn_key = key
    message = ""
    for m in reversed(agent.history):
        text = m.text().strip() if m.role == "user" else ""
        if text and not text.startswith(CARRY_NOTE):
            message = text
            break
    agent.turn_notes[key] = {"started": _iso(), "kind": kind,
                             "message": message[:ANSWER_CHARS]}


def turn_ended(agent: Any, event: Any) -> None:
    note = agent.turn_notes.get(getattr(agent, "_turn_key", ""))
    if note is None:
        return
    note["ended"] = _iso()
    note["outcome"] = event.reason
    if event.response is not None:
        note["answer"] = event.response.message.text().strip()[:ANSWER_CHARS]


def gated(agent: Any, call: Any, began: float, request: Any) -> None:
    note = agent.step_notes.setdefault(call.id, {})
    note.update(turn=getattr(agent, "_turn_key", ""), at=_iso(began),
                waited=round(time.time() - began, 3))
    by = getattr(request, "answered_by", None) if request is not None else None
    if by:
        note["answered_by"] = by
    code = agent._refusals.get(call.id)
    if code:
        note["refused"] = code


def ran(agent: Any, call: Any, began: float, result: Any) -> None:
    note = agent.step_notes.setdefault(call.id, {"at": _iso(began)})
    note["ran"] = round(time.time() - began, 3)
    if getattr(result, "images", None):
        note["pictures"] = len(result.images)


def kept(agent: Any, history: list[dict]) -> tuple[dict, dict]:
    """The notes a payload carries: only those of this history's steps and
    turns, so a cleared conversation's do not ride into the next."""
    ids, said = set(), set()
    for m in history:
        for b in m.get("content") or []:
            if b.get("kind") == "tool_call":
                ids.add(b.get("id"))
            elif b.get("kind") == "text" and m.get("role") == "user":
                said.add((b.get("text") or "").strip()[:ANSWER_CHARS])
    steps = {cid: dict(n) for cid, n in agent.step_notes.items() if cid in ids}
    used = {n.get("turn") for n in steps.values()}
    turns = {k: dict(n) for k, n in agent.turn_notes.items()
             if k in used or n.get("message") in said}
    return steps, turns


def turn_number(history: list[Any]) -> int:
    """How many turns a live history has: the messages a person wrote
    (a carry-on's note to the model is not one). The page's steps link."""
    return sum(1 for m in history if m.role == "user"
               and (text := m.text().strip()) and not text.startswith(CARRY_NOTE))


# ---- reading a payload ------------------------------------------------------------


def _texts(message: dict) -> str:
    return "".join(b.get("text", "") for b in message.get("content") or []
                   if b.get("kind") == "text").strip()


def _segments(payload: dict) -> list[dict]:
    """The conversation cut at each message a person wrote."""
    segments: list[dict] = []
    current: dict | None = None
    for message in payload.get("history") or []:
        role, blocks = message.get("role"), message.get("content") or []
        said = _texts(message) if role == "user" else ""
        if said and not said.startswith(CARRY_NOTE):
            current = {"message": said, "calls": [], "results": {},
                       "images": {}, "answer": ""}
            segments.append(current)
            continue
        if current is None:
            continue
        if role == "assistant":
            current["calls"] += [b for b in blocks if b.get("kind") == "tool_call"]
            if _texts(message):
                current["answer"] = _texts(message)
        else:
            results = [b for b in blocks if b.get("kind") == "tool_result"]
            images = [b for b in blocks if b.get("kind") == "image"]
            for r in results:
                current["results"][r.get("tool_call_id")] = r
            _share_images(current, results, images, payload.get("steps") or {})
    return segments


def _share_images(segment: dict, results: list[dict], images: list[dict],
                  notes: dict) -> None:
    """A batch's pictures follow its results in order; the notes say how
    many each step gave. Without notes, they go to the batch's first."""
    if not images or not results:
        return
    at = 0
    for r in results:
        cid = r.get("tool_call_id")
        count = (notes.get(cid) or {}).get("pictures")
        if count is None:
            count = len(images) if r is results[0] else 0
        segment["images"][cid] = images[at:at + count]
        at += count


def _short(text: str) -> str:
    line = " ".join(str(text).split())
    return line if len(line) <= SHORT_CHARS else line[:SHORT_CHARS - 1] + "…"


def _seconds(start: str | None, end: str | None) -> float | None:
    try:
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start))
                     .total_seconds(), 1)
    except (TypeError, ValueError):
        return None


def _keys(segment: dict, notes: dict) -> list[str]:
    seen: list[str] = []
    for call in segment["calls"]:
        key = (notes.get(call.get("id")) or {}).get("turn")
        if key and key not in seen:
            seen.append(key)
    return seen


def turns(payload: dict) -> list[dict]:
    """Each turn of a conversation, for a list to pick one from."""
    notes, marks = payload.get("steps") or {}, payload.get("turns") or {}
    out = []
    for n, segment in enumerate(_segments(payload), 1):
        keys = _keys(segment, notes) or [k for k, m in marks.items()
                                         if m.get("message") == segment["message"]][-1:]
        first = marks.get(keys[0], {}) if keys else {}
        last = marks.get(keys[-1], {}) if keys else {}
        out.append({"turn": n, "keys": keys, "message": _short(segment["message"]),
                    "steps": len(segment["calls"]), "started": first.get("started"),
                    "seconds": _seconds(first.get("started"), last.get("ended")),
                    "outcome": last.get("outcome")})
    return out


def timeline(payload: dict, which: int | str | None = None) -> dict:
    """One turn's steps (``yantra.steps.v1``): ``which`` is its number
    (1 is the first), a turn key (Dvara's run id), or None for the last.
    LookupError when there is no such turn."""
    notes, marks = payload.get("steps") or {}, payload.get("turns") or {}
    segments = _segments(payload)
    rows = turns(payload)
    if not segments:
        raise LookupError("this conversation has no turns")
    if which is None:
        index = len(segments) - 1
    elif isinstance(which, int) or str(which).isdigit():
        index = int(which) - 1
        if not 0 <= index < len(segments):
            raise LookupError(f"there is no turn {which} (1 to {len(segments)})")
    else:
        found = [i for i, row in enumerate(rows) if which in row["keys"]]
        if not found:
            raise LookupError(f"no turn {which} in this conversation")
        index = found[0]
    segment, row = segments[index], rows[index]
    steps = []
    for n, call in enumerate(segment["calls"], 1):
        cid = call.get("id")
        result = segment["results"].get(cid) or {}
        note = notes.get(cid) or {}
        content = str(result.get("content", "")) if result else ""
        steps.append({
            "n": n, "tool": call.get("name"), "arguments": call.get("arguments") or {},
            "result": content[:RESULT_CHARS], "short": _short(content),
            "error": bool(result.get("is_error")), "missing": not result,
            "at": note.get("at"), "waited": note.get("waited"), "ran": note.get("ran"),
            "answered_by": note.get("answered_by"), "refused": note.get("refused"),
            "pictures": [{"media_type": i.get("media_type"), "data": i.get("data")}
                         for i in segment["images"].get(cid, [])],
        })
    last = marks.get(row["keys"][-1], {}) if row["keys"] else {}
    return {"format": FORMAT, "turn": index + 1, "of": len(segments),
            "keys": row["keys"], "message": segment["message"],
            "started": row["started"], "ended": last.get("ended"),
            "seconds": row["seconds"], "outcome": row["outcome"],
            "kinds": [marks.get(k, {}).get("kind") for k in row["keys"]],
            "answer": (last.get("answer") or segment["answer"])[:ANSWER_CHARS],
            "steps": steps}


# ---- in words, for a person and for a bug report -----------------------------------


def _clock(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return ""


def took(seconds: float | None) -> str:
    """``1.4s``, ``340ms``: a fast step is not "0s"."""
    if seconds is None:
        return ""
    if seconds < 0.001:
        return "<1ms"
    return f"{seconds * 1000:.0f}ms" if seconds < 1 else f"{seconds:.1f}s"


def step_line(step: dict) -> str:
    """``3. phone_tap  10:04:12 · asked you on the page, 12s · ran 1.4s``"""
    bits = [b for b in (
        _clock(step.get("at")),
        (f"asked {step['answered_by']}, {took(step['waited'])}"
         if step.get("answered_by") and step.get("waited") is not None else ""),
        f"ran {took(step['ran'])}" if step.get("ran") is not None else "",
        f"refused ({step['refused']})" if step.get("refused") else "",
        "failed" if step.get("error") and not step.get("refused") else "",
        f"{len(step['pictures'])} picture(s)" if step.get("pictures") else "",
    ) if b]
    return f"{step['n']}. {step['tool']}" + (f"  {' · '.join(bits)}" if bits else "")


def text(view: dict) -> str:
    """The terminal's ``/steps``."""
    head = (f"turn {view['turn']} of {view['of']}: {_short(view['message'])}"
            + (f" -- {view['seconds']:g}s" if view.get("seconds") is not None else "")
            + (f", {view['outcome']}" if view.get("outcome") else ""))
    lines = [head]
    for step in view["steps"]:
        lines.append("  " + step_line(step))
        lines.append(f"     {json.dumps(step['arguments'], ensure_ascii=False)[:200]}")
        lines.append(f"     -> {step['short'] or '(no result)'}")
    if not view["steps"]:
        lines.append("  (no tools: it answered from what it knew)")
    return "\n".join(lines)


def markdown(view: dict) -> str:
    """The turn as a file for a bug report."""
    out = ["> This file holds what the agent's tools were given and what they read "
           "(pages, mails, a phone's screen). Read it before you send it anywhere.",
           "", f"# What the agent did: {_short(view['message'])}", ""]
    facts = [f"turn {view['turn']} of {view['of']}"]
    if view.get("started"):
        facts.append(f"started {view['started']}")
    if view.get("seconds") is not None:
        facts.append(f"took {view['seconds']:g}s")
    if view.get("outcome"):
        facts.append(f"ended: {view['outcome']}")
    out += [" · ".join(facts), "", "**Asked:**", "", view["message"], ""]
    for step in view["steps"]:
        out += [f"## {step_line(step)}", "", "```json",
                json.dumps(step["arguments"], indent=2, ensure_ascii=False), "```", "",
                "```", step["result"] or "(no result)", "```", ""]
        if step["pictures"]:
            out += [f"*({len(step['pictures'])} picture(s) not included in this file)*", ""]
    out += ["## Answer", "", view.get("answer") or "(none)", ""]
    return "\n".join(out)


__all__ = ["FORMAT", "markdown", "text", "timeline", "turns"]
