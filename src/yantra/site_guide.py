"""After a turn on a site added by hand: what the agent found, worth keeping?

A site connected by its address (``setu connect --site``, notes/112)
starts with no guide. The agent finds its own way -- the orders page,
the search address -- and next conversation finds it again. So after a
turn that used such a site's tools, the look behind the answer
(notes/113) asks the model one short question: from the pages this turn
opened, what would a guide to the site say? The answer is an OFFER in
the tray, never a write: on the person's yes, Setu saves it as the
site's guide (``setu site guide ID --set``), and the connections prompt
layer carries it from the next turn.

ONLY SITES ADDED HERE. An installed connector's guide is its author's,
and Setu refuses to write it; such sites are not looked at.

ONLY WHAT THE TURN SHOWED. The model sees each call and where it landed
-- the address and the page's title from the snapshot's header -- not
the pages' text: a guide says where things are, and a page's words are
the site's, not advice to follow. Addresses, because the refs a call
names (``e4``) mean nothing on the next visit. A turn of fewer than two
calls on the site taught nothing worth a model call.

THE WHOLE GUIDE, NOT A LINE. The old guide goes in and the new one
comes out, so a place found again is not written twice, and a place
that turned out wrong can be dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yantra.types import Message, TextBlock, ToolCall, ToolResult

#: Fewer calls than this on a site taught nothing worth a model call.
MIN_CALLS = 2
#: Calls shown to the model, at most (the last ones count most).
CALLS_SHOWN = 30
GUIDE_MAX_CHARS = 1500
GUIDE_MAX_LINES = 10
MAX_TOKENS = 600

PROMPT = """You just used the website {name} through browser tools for a person.
Below are the calls you made this turn: each address opened (or link
followed, search typed) and the first line of what came back.

The person asked: {task}

The site's guide so far (empty if none):
---
{guide}
---

The calls:
{calls}

Write the site's guide again: short lines a future you would want before
using {name} -- where things are (paths or address patterns, like
"Your orders: /account/orders" or "Search: /search?q=WORDS") and what
worked to get something done. Write addresses, not refs: e1, e4 and
the like are numbers for one page's links and change every time. Keep
what the old guide said unless this turn showed it wrong. Only what
these calls showed: no guesses, nothing about the person, nothing from
the pages' own text. At most {lines} lines.

If this turn added nothing to the guide, reply with exactly: NOTHING"""


@dataclass(slots=True)
class GuideOffer:
    """A new guide for a site, waiting for the person's yes."""

    site: str           # the connector id: what `setu site guide` takes
    name: str
    old: str
    new: str
    calls: int


def _turn(history: list[Message]) -> list[Message]:
    """The last turn: everything after the person's last typed message."""
    for i in range(len(history) - 1, -1, -1):
        message = history[i]
        if message.role == "user" and any(isinstance(b, TextBlock) for b in message.content):
            return history[i:]
    return list(history)


def site_calls(history: list[Message], prefix: str) -> tuple[str, list[str]]:
    """(what the person asked, one line per call on the site's tools) for
    the last turn."""
    turn = _turn(history)
    task = turn[0].text().strip() if turn and turn[0].role == "user" else ""
    results = {b.tool_call_id: b for m in turn for b in m.content
               if isinstance(b, ToolResult)}
    lines = []
    for message in turn:
        for call in (b for b in message.content if isinstance(b, ToolCall)):
            if not call.name.startswith(prefix + "_"):
                continue
            verb = call.name[len(prefix) + 1:]
            if verb == "handoff":
                continue
            args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
            lines.append(f"{verb}({args}) -> {_landed(results.get(call.id))}")
    return task[:400], lines


def _landed(result: ToolResult | None) -> str:
    """Where a call landed -- the address and the page's title from the
    snapshot's header -- or the first line of an error. Never page text."""
    if result is None:
        return "(no answer)"
    lines = [line.strip() for line in result.content.splitlines() if line.strip()]
    if result.is_error:
        return "ERROR " + (lines[0][:160] if lines else "")
    title = next((ln[6:].strip() for ln in lines[:4] if ln.startswith("title:")), "")
    source = next((ln[7:].strip() for ln in lines[:4] if ln.startswith("source:")), "")
    if source:
        return f"landed on {source[:200]}" + (f" ({title[:80]})" if title else "")
    return (lines[0][:160] if lines else "")


def build_prompt(name: str, guide: str, task: str, calls: list[str]) -> str:
    return PROMPT.format(name=name, task=task or "(not said)", guide=guide or "",
                         calls="\n".join(calls[-CALLS_SHOWN:]), lines=GUIDE_MAX_LINES)


def parse_reply(text: str, old: str) -> str | None:
    """The new guide, or None when the reply adds nothing."""
    text = text.strip().strip("`").strip()
    if not text or text.upper().startswith("NOTHING"):
        return None
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    lines = [line for line in lines if line.strip() != "---"][:GUIDE_MAX_LINES]
    guide = "\n".join(lines)[:GUIDE_MAX_CHARS].strip()
    if not guide or " ".join(guide.split()) == " ".join(old.split()):
        return None
    return guide


def local_sites(setu: Any) -> list[tuple[str, str, str, str]]:
    """(connector id, name, prefix, current guide) for each site added on
    this computer that has tools in this session."""
    link = getattr(setu, "link", None)
    if link is None:
        return []
    cards = link.connectors
    found = []
    for ref, site in getattr(setu, "sites", {}).items():
        cid = ref.split(":", 1)[0]
        card = cards.get(cid) or {}
        if card.get("label") != "local":
            continue
        guide = (card.get("browser") or {}).get("guide") or ""
        found.append((cid, card.get("name") or cid, site.prefix, guide))
    return found


def worth_a_look(agent: Any) -> bool:
    """Whether the last turn used a site added here enough to ask about
    -- checked before anything is said, so a turn that used no site
    shows no "looking" at all. No model call."""
    return any(len(site_calls(agent.history, prefix)[1]) >= MIN_CALLS
               for _, _, prefix, _ in local_sites(getattr(agent, "setu", None)))


def look(agent: Any, stop: Any = lambda: False) -> list[GuideOffer]:
    """One model call per site added here that the last turn used enough.
    Raises what the provider raises; the host makes that a line."""
    setu = getattr(agent, "setu", None)
    offers: list[GuideOffer] = []
    for cid, name, prefix, guide in local_sites(setu):
        task, calls = site_calls(agent.history, prefix)
        if len(calls) < MIN_CALLS or stop():
            continue
        response = agent.provider.complete(
            messages=[Message("user", [TextBlock(build_prompt(name, guide, task, calls))])],
            system=None, tools=[], model=agent.model, max_tokens=MAX_TOKENS)
        _account(agent, response)
        new = parse_reply(response.message.text(), guide)
        if new is not None:
            offers.append(GuideOffer(site=cid, name=name, old=guide, new=new,
                                     calls=len(calls)))
    return offers


def _account(agent: Any, response: Any) -> None:
    """Spent on the person's behalf: it goes on the meter, as a look back does."""
    from yantra.memory.reflect import _account as account
    account(agent, response)
