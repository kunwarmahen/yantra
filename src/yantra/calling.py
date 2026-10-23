"""One tool, run once, and no model: the rule ``yantra --call`` keeps.

A program that checks something on a timer -- "is the price under $50
yet?" -- needs the tool's answer, not a model's account of it. Asking a
model every ten minutes to call one tool and repeat what it said is a
bill for nothing, and a small model may not repeat it faithfully. So the
agent is built exactly as for a turn -- the package, Setu's accounts,
MCP servers, sites -- and then one tool is run straight from the
registry with the arguments given.

ONLY A TOOL THAT READS, OR ONE CALL SAID YES TO WORD FOR WORD. A call
has nobody to approve it and no model deciding it is wanted, so a tool
not marked read-only is refused -- unless ``allow_tools`` holds a grant
for exactly this tool (no glob) that fixes EVERY argument passed
(``web_fetch(url=https://...)``). Fetching a page is not read-only here
because an address can carry data out; a person who accepted "fetch
this address every three hours" approved that address, and nothing
else. A bare name or a glob never covers a call, and a tool that asks
every time (a purchase) is never run.

ONE RULE, TWO DOORS. The terminal's ``--call`` (cli/main.py) and Dvara's
``POST /call`` -- a look made as one person of the house, with their own
accounts -- both come here, so a look through Dvara cannot be let do
what a look at the terminal is refused.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

#: The JSON shape both doors print.
CALL_FORMAT = "yantra.call.v1"


@dataclass(frozen=True, slots=True)
class Called:
    """What one call said: the tool's text, or why it did not run."""

    ok: bool
    tool: str
    text: str = ""
    detail: str = ""
    #: Pictures the tool made (a screenshot is saved where the tool saves
    #: it; its pixels are not handed back).
    images: int = 0


def call_once(agent: Any, name: str, arguments: Any,
              allow_tools: list[str] | tuple[str, ...] = ()) -> Called:
    """Run ``name`` once with ``arguments``, by the rule above. Never
    raises for anything a caller could have got wrong: a refusal, an
    unknown tool and a tool's own failure are all answers."""
    from yantra.tools.base import ToolOutput, coerce_arguments

    if not isinstance(arguments, dict):
        return Called(False, name, detail="the arguments are one JSON object, like "
                                          "{\"url\": \"...\"}")
    if name not in agent.registry or agent.registry.is_disabled(name):
        close = difflib.get_close_matches(name, agent.registry.names(), n=3)
        return Called(False, name, detail=f"no tool {name!r} here"
                      + (f"; did you mean {', '.join(close)}?" if close else ""))
    tool = agent.registry.get(name)
    arguments = coerce_arguments(arguments, tool.parameters)
    if not tool.read_only and not said_yes_to(list(allow_tools), tool, arguments):
        return Called(False, name, detail=(
            f"{name} is not read-only; --call runs only tools that read, or one call "
            f"granted word for word: --allow-tools '{name}(key=value, ...)' fixing "
            "every argument"))
    try:
        out = tool.run(arguments, agent.ctx)
    except Exception as exc:          # a tool's failure is the answer, said as one
        return Called(False, name, detail=str(exc) or type(exc).__name__)
    finally:
        try:
            tool.turn_ended()         # a browser it opened closes with the call
        except Exception:
            pass
    if isinstance(out, ToolOutput):
        return Called(True, name, out.text, images=len(out.images))
    return Called(True, name, str(out))


def said_yes_to(patterns: list[str], tool: Any, arguments: dict) -> bool:
    """A grant names this exact tool and fixes every argument passed."""
    from yantra.permissions import parse_grant

    if getattr(tool, "always_ask", False):
        return False
    for pattern in patterns:
        try:
            grant = parse_grant(pattern)
        except ValueError:
            continue
        fixed = {key for key, _ in grant.args}
        if (grant.tool == tool.name and fixed and set(arguments) <= fixed
                and grant.allows(tool.name, arguments)):
            return True
    return False


def as_json(called: Called, *, needs: list[str] | None = None,
            busy: list[str] | None = None) -> dict:
    """``yantra.call.v1``: what was said, and what it needed a person for."""
    return {"format": CALL_FORMAT, "ok": called.ok, "tool": called.tool,
            "text": called.text, "detail": called.detail, "images": called.images,
            "needs_person": list(needs or []), "busy": list(busy or [])}
