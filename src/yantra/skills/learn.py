"""Learned skills: a task solved once, written down, and followed after.

The first "turn the bedroom fan to 60%" takes eight steps of discovery:
where Home Assistant is, which entity is the fan, which payload the
service wants. The second time should take two. When a turn solves
something worth keeping, this module turns the WORKING path into a
skill (the ordinary SKILL.md format, in a ``learned/`` folder), tests
it, and hands the host an offer to put in front of the person. Next
session the roster lists it, and the model follows a recipe that is
known to work instead of rediscovering one -- which is what small local
models do well, and rediscovery is what they do badly
([notes/96](../../notes/96-solve-it-once.md)).

    notice   the turn finished, took real effort, followed no skill    (counted)
    distil   one model call, fresh and small: decide, then write       (1 call)
    test     run the script once, where the solve ran; one retry       (<= 2 runs)
    ask      the person sees all of it and says yes, edit, or no       (the host)

FRESH AND SMALL, BECAUSE THE OBVIOUS WAY COSTS TEN TIMES THE TASK.
Asking the agent "save that as a skill" inside the session re-sends the
whole conversation on every step of the write-up, and a model left to
test its own script tests it until it is bored. Measured on
``qwen3.8:latest``: the save cost 263,811 tokens against a 27,385-token
solve. So the distil call sees the task, each step's call and a clipped
result, and nothing else, and the test runs at most twice.

ONE CALL DECIDES AND WRITES. "Did it work" and "will it come again" are
questions for a model; "did it take effort" is a count, and a count is
not asked. The two model questions ride in the same call that writes
the skill, so a turn that does not qualify costs one short answer, not a
judgement call plus a write-up.

ONLY WHAT WAS OBSERVED. Left to itself the write-up invents "pitfalls
learned the hard way" -- a 501 that was really a 400, error codes the
server never sent. The prompt forbids anything the steps do not show,
and the person reads every line before it is saved (the host's job).

A SKILL NEVER HOLDS A SECRET OR A PERSONAL FACT. What the model sees is
scrubbed first (tokens, ``KEY=value`` lines, Authorization headers),
the prompt says this person's URLs and device names are inputs, and a
draft that still carries a scrubbed value is not offered at all.

THE TEST RUNS WHERE THE SOLVE RAN: the session's own ``bash`` tool,
through the session's own permission gate, in the session's sandbox. A
script the agent just wrote is code nobody has read yet, and nothing
here gets to run it on a looser rule than the turn that produced it.

Counters (``worked``/``failed``) are Yantra's, never the model's: a
learned skill loaded in a turn WORKED when the turn finished and none of
the RECIPE'S OWN calls failed -- its script or its tool (own_steps) --
and FAILED otherwise. A look-around ``ls`` that exits 1 is not the
recipe failing. A recipe with no script is judged on every call after
the load. Crude, and honest about being crude -- it is what the harness
saw, not a grade.

THE SAME CALL FINDS THE FACTS. Keeping the person's values out of a
recipe means someone has to remember them: the bedroom fan's entity id
was discovered once, and a recipe that asks for it every time has only
moved the discovery. So the write-up also lists them, as the look back
at a conversation's end would (memory/reflect.py), and they go to memory
under the look back's own ask/auto/off -- offered when the turn ends,
whatever the person says to the skill. One call learns both kinds of
thing; the recipe names the input, memory holds this person's value.

THE FAILED TURN IS THE REPAIR ([notes/97](../../notes/97-when-the-recipe-breaks.md)).
A recipe that failed while the task was still finished another way has
its fix sitting in the history already. The same one-call write-up is
pointed at it, with the saved skill beside the steps, and the answer is
offered as an update -- a diff, same name, same folder, the record kept
and the streak cleared. Three failures in a row with nothing to repair
from set a recipe aside (loader.STALE_AFTER); the next fresh solve is
told its name, and replaces it.

BEHIND THE PERSON'S BACK, OR NOT AT ALL. A page looks at the turn after
it has handed the input back, not before: the write-up is a whole model
call, and on a local model it can take longer than the answer did. Run
that way (``consider(stop=...)``) the look gives way the moment the
person sends something -- the model call is closed mid-stream, so a
local model is not left finishing a write-up nobody waits for -- and it
never asks a question. A test that would need a yes is not run; the
offer waits with ``waiting`` set, and the person runs it from where
the offer waits ([notes/113](../../notes/113-after-the-answer.md)).
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import shutil
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from yantra.skills.loader import (
    LEARNED_SOURCES,
    MIN_DESCRIPTION,
    NAME_RE,
    SKILL_FILE,
    LearnedRecord,
    Skill,
    SkillError,
    learned_root,
    load_skill,
    render_skill_md,
    validate_text,
)
from yantra.context import is_summary
from yantra.permissions import REFUSED_UNATTENDED
from yantra.providers.base import collect
from yantra.setu_link import connectors_hint, resolve_needs
from yantra.skills.registry import write_atomic
from yantra.trace import REDACT_PRESETS
from yantra.types import Message, TextBlock, ToolCall, ToolResult, Usage

#: What happens after a turn that looks worth keeping. ``ask`` is the
#: default and the only mode that needs a person; ``auto`` saves a draft
#: that passed its test without asking, for unattended runs only;
#: ``off`` never looks.
MODES = ("ask", "auto", "off")
ENV_MODE = "YANTRA_LEARN"

#: Fewer tool calls than this and there was nothing to discover: the
#: model already knew the way, and a recipe would only restate it
#: (weather in Raleigh took two steps cold).
MIN_STEPS = 4

#: How much of each result the distil call sees. Enough to show what
#: came back and where the answer was; not the 7KB state dump.
MAX_RESULT_CHARS = 1200
MAX_FAILED_CHARS = 240
MAX_ANSWER_CHARS = 1500

#: Room for the write-up. A thinking model spends some of it thinking.
DISTIL_MAX_TOKENS = 8192

#: One run, plus one retry after a repair. Then stop.
MAX_TEST_RUNS = 2
TEST_TIMEOUT = 60

#: What a test's output starts with when it was not run because the look
#: ran in the background and the test needed a yes nobody was asked for.
WAITING = "[waiting]"

#: Where drafts are staged for their test, under the workspace so a
#: sandboxed bash can reach them. Gitignored with the rest of .yantra/.
STAGING = (".yantra", "learning")

#: The save question's words for each scope.
SCOPES = {"user": "you, in every project",
          "project": "this project only (private, never committed)"}

REDACTED = "[redacted]"

#: Secrets that show up in the steps of a solve. Each pattern's LAST
#: group is the value; everything before it is kept so the model still
#: sees that a token existed and where it came from.
_SECRET_PATTERNS = (
    # HA_TOKEN=..., export API_KEY=... -- env-file and shell shape only:
    # upper case, no spaces round the '=', so ``token = args.token`` in
    # a script is code, not a secret
    re.compile(r"(?m)((?:^|(?<=[\s;]))(?:export\s+)?[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD"
               r"|PASSWD|API_?KEY|PRIVATE_KEY)[A-Z0-9_]*=)"
               r"""["']?([^\s"'$]{4,})"""),
    # Authorization: Bearer ..., "Authorization": "Token ..."
    re.compile(r"""(?i)(authorization["']?\s*[:=]\s*["']?(?:bearer|basic"""
               r"""|token)?\s*)([A-Za-z0-9._~+/=-]{8,})"""),
    # "access_token": "...", "password": "..."
    re.compile(r"""(?i)("(?:access_|refresh_|api_)?(?:token|secret"""
               r"""|password|api_?key)"\s*:\s*")([^"]{8,})"""),
    # the named token shapes the trace scrubber already knows
    re.compile(f"()({REDACT_PRESETS['token']})"),
)

#: What a finished draft is checked against: the named token shapes.
#: Not the whole scrubber -- a script that reads ``Authorization`` from
#: a variable is exactly right, and must not look like a leak.
_TOKEN_SHAPES = re.compile(REDACT_PRESETS["token"])


class Stopped(Exception):
    """A look in the background gave way: the person wants the agent."""


class LearnError(RuntimeError):
    """A draft that cannot be offered, with the reason a person can read."""


# ---- reading the turn ---------------------------------------------------------


@dataclass(slots=True)
class Step:
    """One tool call of the turn, and how it came out."""

    call_id: str
    name: str
    arguments: dict[str, Any]
    result: str
    ok: bool


@dataclass(slots=True)
class Turn:
    """What the distil call is allowed to know: the ask, the steps, the answer."""

    task: str
    steps: list[Step]
    answer: str

    def skills_loaded(self, tools: dict[str, str] | None = None) -> list[str]:
        """Skills the turn followed. ``tools`` maps a promoted tool's name
        to its skill's: calling it is following the skill too."""
        tools = tools or {}
        return [str(s.arguments.get("name", "")) if s.name in (
                    "load_skill", "run_skill") else tools[s.name]
                for s in self.steps
                if s.name in ("load_skill", "run_skill") or s.name in tools]


def _worked(name: str, result: ToolResult) -> bool:
    """Did this call do what it was asked? A tool error says no -- and so
    does a shell command that exited non-zero, which the bash tool
    reports as data rather than as an error (the model reads the code),
    but which is a failed step all the same."""
    if result.is_error:
        return False
    if name == "bash":
        code = _exit_code(result.content)
        return code is None or code == 0
    return True


def used_skill(step: Step, skills: Any) -> Skill | None:
    """The learned skill a call USED: a load_skill that worked, or a call
    to a promoted skill's own tool (loader.Skill.tool_name)."""
    if step.name == "load_skill":
        return skills.get(str(step.arguments.get("name", ""))) if step.ok else None
    return next((s for s in skills if s.tool_name and s.tool_name == step.name),
                None)


def own_steps(skill: Skill, steps: list[Step]) -> list[Step] | None:
    """The calls that were the recipe itself: its promoted tool, or a
    command that runs something from its folder.

    A use is judged on these, not on every call after the load. Small
    models look around while following a recipe -- an ``ls`` for a file
    that is not there, a ``grep`` over folders it cannot read -- and those
    exit non-zero without the recipe having failed at anything. Counted,
    they set working recipes aside (notes/106).

    None when the recipe has no script, or the turn never ran it: then
    nothing marks the recipe's own steps, and every call after the load
    is judged, as before.
    """
    scripts = skill.directory / "scripts"
    if not skill.tool_name and not (scripts.is_dir() and any(scripts.iterdir())):
        return None
    folder = str(skill.directory)
    own = [s for s in steps
           if (skill.tool_name and s.name == skill.tool_name)
           or (s.name == "bash" and folder in str(s.arguments.get("command", "")))]
    return own or None


def _is_prompt(message: Message) -> bool:
    """A user message a PERSON wrote -- not a batch of tool results, and
    not the summary compaction put in place of older messages."""
    return (message.role == "user"
            and any(isinstance(b, TextBlock) for b in message.content)
            and not any(isinstance(b, ToolResult) for b in message.content)
            and not is_summary(message))


def _last_prompt(history: list[Message]) -> int | None:
    return next((i for i in range(len(history) - 1, -1, -1)
                 if _is_prompt(history[i])), None)


def read_turn(history: list[Message]) -> Turn | None:
    """The last turn in ``history``, from its prompt to its final answer.

    Walks back to the most recent message a person typed, so a turn that
    was held for approval and resumed reads as the one task it was. Only
    what ``history`` still holds: after compaction folded part of the
    turn away, ``current_turn`` is the reader that knows the rest.
    """
    start = _last_prompt(history)
    if start is None:
        return None
    return _turn_from(history, start, history[start].text().strip())


def _turn_from(history: list[Message], start: int, task: str) -> Turn:
    calls: dict[str, ToolCall] = {}
    steps: list[Step] = []
    answer = ""
    for message in history[start + 1:]:
        if message.role == "assistant":
            for call in message.tool_calls():
                calls[call.id] = call
            if text := message.text().strip():
                answer = text
            continue
        for block in message.content:
            if isinstance(block, ToolResult) and block.tool_call_id in calls:
                call = calls[block.tool_call_id]
                steps.append(Step(call.id, call.name, dict(call.arguments),
                                  block.content, _worked(call.name, block)))
    return Turn(task=task, steps=steps, answer=answer)


# ---- a turn compaction cut in two ---------------------------------------------
#
# On a small context window compaction can run INSIDE a turn: the older
# messages, the person's question and the first steps among them, become
# one summary. Read afterwards, the turn would start at the summary --
# a five-step solve read as three, with the summary as its "task" -- so
# nothing would be learned from exactly the long turns worth learning
# from. So just before compaction the turn so far is kept on the agent
# (``carry_turn``), and ``current_turn`` joins it to what came after.


def carry_turn(agent: Any) -> None:
    """Keep the turn so far, before compaction folds part of it away."""
    history = getattr(agent, "history", [])
    turn = current_turn(agent)
    if turn is None:
        agent.turn_carried = None
        return
    carried = getattr(agent, "turn_carried", None)
    prompt = carried[0] if carried and _same_turn(history, carried[0]) is not None \
        else history[_last_prompt(history)]
    agent.turn_carried = (prompt, turn)


def _same_turn(history: list[Message], prompt: Message) -> int | None:
    """Where the carried turn continues in ``history`` -- its prompt, or
    the summary that replaced it -- or None when a newer turn began."""
    at = next((i for i, m in enumerate(history) if m is prompt), None)
    last = _last_prompt(history)
    if at is not None:
        return at if last == at else None
    summary = next((i for i in range(len(history) - 1, -1, -1)
                    if is_summary(history[i])), None)
    if summary is None or (last is not None and last > summary):
        return None
    return summary


def current_turn(agent: Any) -> Turn | None:
    """The last turn, whole: what compaction kept of it, joined to what
    was carried from before (steps by call id, so none counts twice)."""
    history = getattr(agent, "history", [])
    carried = getattr(agent, "turn_carried", None)
    if not carried:
        return read_turn(history)
    prompt, before = carried
    start = _same_turn(history, prompt)
    if start is None:
        return read_turn(history)       # a newer turn: the carried one is done
    after = _turn_from(history, start, before.task)
    seen = {step.call_id for step in before.steps}
    return Turn(task=before.task,
                steps=before.steps + [s for s in after.steps if s.call_id not in seen],
                answer=after.answer or before.answer)


def why_not(turn: Turn | None, reason: str,
            *, min_steps: int = MIN_STEPS,
            repairing: bool = False,
            tools: dict[str, str] | None = None) -> str | None:
    """The counted half of noticing: None when the turn is worth a look,
    otherwise why not, in words the host can show on ``/learn``."""
    if turn is None:
        return "there is no finished turn to learn from yet"
    if reason != "end_turn":
        return f"the last turn did not finish ({reason})"
    if not repairing and (loaded := list(dict.fromkeys(
            n for n in turn.skills_loaded(tools) if n))):
        return f"the last turn already followed a skill ({', '.join(loaded)})"
    if len(turn.steps) < min_steps:
        return (f"the last turn took {len(turn.steps)} tool call(s); fewer "
                f"than {min_steps} means there was nothing to discover")
    if not any(step.ok for step in turn.steps):
        return "none of the last turn's tool calls worked"
    return None


# ---- keeping secrets out ------------------------------------------------------


def scrub(text: str, found: set[str] | None = None) -> str:
    """Replace every secret-looking value with [redacted].

    ``found`` collects the literal values removed, so the draft can be
    checked for them afterwards -- and so a value caught once (in an env
    file) is also caught where it appears bare (in a curl command).
    """
    seen: set[str] = found if found is not None else set()

    def keep_label(match: re.Match[str]) -> str:
        value = match.group(match.re.groups)
        seen.add(value)
        return match.group(0)[:match.start(match.re.groups) - match.start()] + REDACTED

    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(keep_label, text)
    for value in sorted(seen, key=len, reverse=True):
        if len(value) >= 8:
            text = text.replace(value, REDACTED)
    return text


def _clip(text: str, cap: int) -> str:
    text = text.strip()
    if len(text) <= cap:
        return text
    keep = cap // 2
    return f"{text[:keep]}\n[... {len(text) - cap} chars omitted ...]\n{text[-keep:]}"


def digest(turn: Turn, found: set[str] | None = None) -> str:
    """The distil call's whole world: the task, the steps, the answer.

    Successful results are clipped; failed ones get one short line each
    -- enough to know a call failed and how, not enough to spin a
    "pitfalls" section out of.
    """
    lines = [f"TASK: {turn.task}", "", "STEPS:"]
    for n, step in enumerate(turn.steps, start=1):
        args = json.dumps(step.arguments, ensure_ascii=False)
        lines.append(f"{n}. {step.name} {args}")
        if step.ok:
            lines.append("   -> ok: " + _clip(step.result, MAX_RESULT_CHARS)
                         .replace("\n", "\n      "))
        else:
            first = step.result.strip().splitlines()[:1] or [""]
            lines.append(f"   -> FAILED: {_clip(first[0], MAX_FAILED_CHARS)}")
    lines += ["", "FINAL ANSWER TO THE PERSON:", _clip(turn.answer, MAX_ANSWER_CHARS)]
    return scrub("\n".join(lines), found)


# ---- the one model call -------------------------------------------------------

#: What every write-up -- a new skill or an updated one -- must obey.
RULES = """\
Rules for the skill:
- Write ONLY what the steps below show. No tips, pitfalls, error codes, \
retries or fallbacks that did not happen here.
- No secrets, ever. Tokens, passwords and keys are never written down; \
say where they come from (a file the person named, an environment \
variable). Values shown as [redacted] are secrets.
- No personal facts. This person's server addresses, device names and \
ids, places and account names are INPUTS the skill asks for or looks \
up, never written into it. List them under FACTS instead.
- Keep the path that worked; drop the dead ends.
- If a script turns the task into one command, write it: python3 \
standard library only (or bash), taking the inputs as command-line \
arguments, printing what it did, and exiting non-zero when it failed. \
Otherwise write no script."""

#: The reply's shape. ``{verdict}`` and ``{name}`` differ between a new
#: skill and an update; everything else is one format, one parser.
SHAPE = """\
Reply in EXACTLY this shape, with nothing before it:

VERDICT: {verdict}
NAME: {name}
DESCRIPTION: What it does, then "Use when ..." -- one or two sentences.
SCOPE: user   (or project, only when the recipe is about the code in \
this folder)
INPUTS: each input, and where it comes from
NEEDS: the access it needs, in words (or none){connectors}
SCRIPT: scripts/<file>   (or none)
TEST: one shell command, run from the working folder, that repeats \
THIS task with THIS task's inputs; write the script's path as \
"$SKILL_DIR/scripts/<file>"   (or none)
=== INSTRUCTIONS ===
# A short title
Numbered steps the next run follows. If there is a script, one step runs \
it from the working folder, exactly as TEST does -- the same program \
(python3 for a Python script, bash for a shell one), the same arguments: \
python3 "$SKILL_DIR/scripts/<file>" ... -- never cd into the skill's \
folder, or paths the person gives stop working.
=== SCRIPT ===
the whole script (leave this section out when SCRIPT is none)
=== FACTS ===
The person's own values the steps show and the skill takes as INPUTS \
(their devices and ids, server addresses, places, account names, the \
file they said holds their credentials), one line each, as a sentence \
that says what the value IS:
fact: Their bedroom fan is fan.master_bedroom_ceiling in Home Assistant.
fact: Their Home Assistant URL and token are in the file ha.env.
Never a secret. Leave this section out when there are none.
=== END ===

When it is not worth {skipping}, reply with exactly two lines:

VERDICT: skip
REASON: one short sentence"""

DISTIL_PROMPT = """\
You are writing down a recipe, so that the next time someone asks for \
this kind of task it takes two steps instead of ten.

Below is ONE task a person asked for, every tool call that was made, and \
what each returned. Failed calls are shown in one line each.

{decide}
{set_aside}
""" + RULES + "\n\n" + SHAPE.format(
    verdict="save", name="short-lowercase-hyphenated-name",
    skipping="saving", connectors="{connectors}") + "\n\n{digest}\n"

#: A learned skill was followed, a step failed, and the task was finished
#: another way. The fresh solve IS the repair; this asks for it written
#: over the old recipe -- same one-call, small-context shape as a new one.
UPDATE_PROMPT = """\
A saved skill, {name}, was followed for the task below, and a step \
failed. The task was then finished another way. Update the skill so the \
next run follows the way that worked.

First decide. Reply "VERDICT: skip" if the steps show the failure was \
not the skill's fault (a server that was down, a mistake in what the \
person asked for), or that the task was not really finished.

THE SAVED SKILL, AS IT IS NOW:
{saved}

""" + RULES + "\n\n" + SHAPE.format(
    verdict="update", name="{name}   (keep this name)",
    skipping="updating", connectors="{connectors}") + "\n\n{digest}\n"

#: Added to a new write-up when learned skills have been set aside: a
#: fresh solve of what one of them did should take its name and replace
#: it, not sit beside it.
SET_ASIDE = """\
These saved skills were set aside because they kept failing. If this \
task is what one of them was for, use its NAME, so the new recipe \
replaces it:
{names}
"""

DECIDE_ASKED = """\
First decide. Reply "VERDICT: skip" if either of these is false:
  1. It worked: the steps show the task was actually done, not just tried.
  2. It will come again with different inputs (another device, another \
city, another invoice). A one-off question, or a fact about this code, \
is not a recipe."""

DECIDE_FORCED = """\
The person asked for this to be saved. Reply "VERDICT: skip" only if the \
steps show the task did not work."""

FIX_SCRIPT_PROMPT = """\
This script was just written to repeat a task, and its test failed.

TEST COMMAND:
{test}

OUTPUT (exit code last):
{output}

THE SCRIPT ({script_name}):
{script}

Fix the script so the test passes. Change only what the output shows is \
wrong. Reply with the whole corrected script and nothing else, between \
these two lines:
=== SCRIPT ===
=== END ===
"""


@dataclass(slots=True)
class Draft:
    """A skill as the distil call wrote it, before anyone said yes."""

    name: str
    description: str
    scope: str
    inputs: str
    needs: str
    body: str
    script_name: str = ""
    script: str = ""
    test: str = ""
    #: The FACTS section as written: this person's values, kept out of
    #: the skill and offered to memory instead (settle_facts).
    facts: str = ""

    def skill_md(self, learned: LearnedRecord | None = None) -> str:
        return render_skill_md(self.name, self.description, self.body,
                               origin="learned", needs=self.needs,
                               inputs=self.inputs, learned=learned)


def _section(text: str, start: str, *ends: str) -> str:
    """The text between ``=== START ===`` and the first of ``ends``."""
    match = re.search(rf"^=+\s*{start}\s*=+\s*$", text, re.M | re.I)
    if match is None:
        return ""
    rest = text[match.end():]
    stops = [m.start() for end in ends
             if (m := re.search(rf"^=+\s*{end}\s*=+\s*$", rest, re.M | re.I))]
    return rest[:min(stops)] if stops else rest


def _unfence(text: str) -> str:
    """Drop a ``` fence a model wrapped around a script anyway."""
    text = text.strip("\n")
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
    return "\n".join(lines).strip("\n") + "\n" if lines else ""


def _none(value: str) -> str:
    """'none', 'n/a', '-' -> ''. Models write absence many ways."""
    return "" if value.strip().lower().strip(".()") in ("none", "n/a", "-", "") else value.strip()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)[:64].strip("-")


#: The header lines the distil reply may carry, in the prompt's order.
HEADER_KEYS = ("verdict", "reason", "name", "description", "scope", "inputs",
               "needs", "script", "test")


def parse_reply(text: str) -> Draft | str:
    """The distil reply -> a Draft, or the skip reason as a string.

    Raises LearnError for a reply that is neither -- a malformed draft is
    never repaired into something the model did not write.
    """
    text = re.sub(r"(?s)<think>.*?</think>", "", text)
    header = re.split(r"^=+\s*INSTRUCTIONS\s*=+\s*$", text, maxsplit=1,
                      flags=re.M | re.I)[0]
    fields: dict[str, str] = {}
    last = ""
    for line in header.splitlines():
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if sep and key in HEADER_KEYS and key not in fields:
            fields[key] = value.strip()
            last = key
        elif last and line.strip():
            # "INPUTS:" followed by a list on the lines below it: one value.
            fields[last] = f"{fields[last]} {line.strip()}".strip()
    verdict = fields.get("verdict", "").lower()
    if verdict.startswith("skip"):
        return fields.get("reason") or "the model judged it not worth keeping"
    if not verdict.startswith(("save", "update")):
        raise LearnError("the write-up did not follow the format "
                         "(no 'VERDICT: save', 'update' or 'skip')")

    name = _slug(fields.get("name", ""))
    if not NAME_RE.match(name or "-") or name == "learned":
        raise LearnError(f"the write-up named the skill {fields.get('name')!r}, "
                         f"which is not a usable name")
    description = " ".join(fields.get("description", "").split())
    if len(description) < MIN_DESCRIPTION:
        raise LearnError("the write-up's description is too thin to ever be "
                         "picked")
    body = _section(text, "INSTRUCTIONS", "SCRIPT", "FACTS", "END").strip()
    if not body:
        raise LearnError("the write-up has no instructions")
    scope = fields.get("scope", "user").split()[0:1] or ["user"]
    scope = scope[0].lower().strip(".,")
    script_name = _none(fields.get("script", "")).split()[0:1]
    script_name = script_name[0] if script_name else ""
    script = (_unfence(_section(text, "SCRIPT", "FACTS", "END"))
              if script_name else "")
    if script_name:
        # One file, directly under scripts/ -- the draft may not reach
        # anywhere else in the skill's folder, or out of it.
        base = Path(script_name).name
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", base) or base.startswith("."):
            raise LearnError(f"the write-up's script name {script_name!r} is "
                             f"not a plain file name")
        script_name = f"scripts/{base}"
        if not script.strip():
            raise LearnError(f"the write-up names {script_name} but has no script")
    return Draft(name=name, description=description,
                 scope=scope if scope in SCOPES else "user",
                 inputs=_none(fields.get("inputs", "")),
                 needs=_none(fields.get("needs", "")),
                 body=body, script_name=script_name, script=script,
                 test=_none(fields.get("test", "")) if script_name else "",
                 facts=_section(text, "FACTS", "END").strip())


# ---- the offer ------------------------------------------------------------------


@dataclass(slots=True)
class Offer:
    """Everything the save question shows, and what saving needs."""

    draft: Draft
    staging: Path
    #: True passed, False failed, None nothing to run (no script).
    tested: bool | None
    test_output: str = ""
    test_runs: int = 0
    #: Tokens the learning itself cost: the distil call and any repair.
    spent: Usage = field(default_factory=Usage)
    #: An existing learned skill of this name, which saving replaces.
    replaces: Path | None = None
    #: The draft's name was taken by a hand-written skill and changed.
    renamed_from: str = ""
    #: The learned skill this offer would update, when the turn followed
    #: it and a step failed -- then the question is "update it?".
    repairs: Skill | None = None
    #: What failed this time, one line, scrubbed.
    failure: str = ""
    #: A repair of a promoted skill: its tool's name, and whether the new
    #: test still fits the tool's arguments -- kept if so, dropped if not
    #: (promote.fits).
    tool: str = ""
    keeps_tool: bool = False
    #: Each ``setu:<id>`` the draft needs, against what Setu reports now
    #: (setu_link.Need) -- the save question says which are connected.
    connections: list[Any] = field(default_factory=list)
    #: The test needs a yes and the look ran with nobody asked: not run
    #: yet, and not saveable until it is (Learner.test_waiting).
    waiting: bool = False

    def diff(self) -> str:
        """The update as a person reads one: saved version against proposed.
        The counter line is left out of both sides -- it is Yantra's, and
        a diff full of it would bury the change."""
        if self.repairs is None:
            return ""
        chunks = []
        old_dir, draft = self.repairs.directory, self.draft
        pairs = [(SKILL_FILE, _without_counters(_read(old_dir / SKILL_FILE)),
                  _without_counters(self._staged(SKILL_FILE, draft.skill_md())))]
        names = {draft.script_name} | {
            f"scripts/{p.name}" for p in (old_dir / "scripts").glob("*")
            if p.is_file()} - {""}
        for name in sorted(names):
            pairs.append((name, _read(old_dir / name),
                          self._staged(name, "") if name == draft.script_name else ""))
        for name, old, new in pairs:
            chunks.extend(difflib.unified_diff(
                old.splitlines(keepends=True), new.splitlines(keepends=True),
                fromfile=f"{name} (saved)", tofile=f"{name} (proposed)"))
        return "".join(chunks)

    def view(self, cwd: Path, home: Path | None = None) -> dict[str, Any]:
        """The save question's data, for a page or a test."""
        draft = self.draft
        return {
            "name": draft.name,
            "description": draft.description,
            "scope": draft.scope,
            "scopes": {k: {"label": v,
                           "path": str(learned_root(k, cwd, home=home) / draft.name)}
                       for k, v in SCOPES.items()},
            "inputs": draft.inputs,
            "needs": draft.needs,
            "connections": [n.describe() for n in self.connections],
            "skill_md": self._staged(SKILL_FILE, draft.skill_md()),
            "script_name": draft.script_name,
            "script": (self._staged(draft.script_name, draft.script)
                       if draft.script_name else ""),
            "test": {"command": draft.test, "passed": self.tested,
                     "runs": self.test_runs, "waiting": self.waiting,
                     "output": _clip(self.test_output, 1500)},
            "spent": {"input": self.spent.input_tokens,
                      "output": self.spent.output_tokens},
            "replaces": str(self.replaces) if self.replaces else None,
            "renamed_from": self.renamed_from,
            "repairs": self.repairs.name if self.repairs else None,
            "failure": self.failure,
            "diff": self.diff(),
            "tool": ({"name": self.tool, "kept": self.keeps_tool}
                     if self.tool else None),
        }

    def _staged(self, relative: str, fallback: str) -> str:
        """A staged file's text: the offer as it stands, edits included."""
        try:
            return (self.staging / relative).read_text(encoding="utf-8")
        except OSError:
            return fallback

    def keep_edits(self, skill_md: str | None, script: str | None) -> None:
        """Write the person's edits into the staged files, so an offer asked
        again (after a save that failed validation) shows what they wrote."""
        if skill_md is not None:
            (self.staging / SKILL_FILE).write_text(skill_md, encoding="utf-8")
        if script is not None and self.draft.script_name:
            (self.staging / self.draft.script_name).write_text(
                script, encoding="utf-8")


def _exit_code(output: str) -> int | None:
    match = re.search(r"exit code: (-?\d+)\s*$", output.strip())
    return int(match.group(1)) if match else None


class Learner:
    """One session's learning: notice, distil, test, and save on a yes.

    Lives on the agent as ``agent.learner``. Hosts call ``consider`` after
    a turn, show the Offer, and call ``save`` -- the asking itself is the
    host's, because a terminal and a page ask differently.
    """

    def __init__(self, agent: Any, mode: str = "ask",
                 *, home: Path | None = None) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown learn mode {mode!r} "
                             f"(want one of {', '.join(MODES)})")
        self.agent = agent
        self.mode = mode
        # The registry's home when not given: learned skills must land in
        # the same user root the registry scans, or a save would vanish
        # from the roster it was meant to join.
        skills = getattr(agent, "skills", None)
        self.home = home if home is not None else getattr(skills, "home", None)
        #: Why the last ``consider`` offered nothing, for ``/learn``.
        self.last_skip: str | None = None
        #: What ``after_turn`` counted last: (name, worked) pairs.
        self.last_counted: list[tuple[str, bool]] = []
        #: How the last turn ended, set by ``after_turn``. A host that makes
        #: a Learner on demand (``/learn`` with learning off) sets it itself.
        self.last_reason = ""
        #: How many facts the last write-up handed to memory.
        self.facts_found = 0
        #: Set while a look runs in the background: says the person wants
        #: the agent back. None when the person is waiting on the look.
        self._stop: Callable[[], bool] | None = None

    @property
    def cwd(self) -> Path:
        return self.agent.ctx.cwd

    # ---- counters -----------------------------------------------------------

    def after_turn(self, end: Any) -> list[tuple[str, bool]]:
        """Count this turn against every learned skill it followed.

        Call once per TurnEnd, whatever the reason. Returns what was
        counted, as (name, worked) pairs.
        """
        self.last_reason = getattr(end, "reason", "")
        skills = getattr(self.agent, "skills", None)
        turn = current_turn(self.agent)
        if skills is None or turn is None:
            return []
        refused = set(getattr(self.agent, "turn_refusals", {}) or {})
        counted: list[tuple[str, bool]] = []
        for index, step in enumerate(turn.steps):
            skill = used_skill(step, skills)
            if skill is None or not skill.is_learned:
                continue
            if skill.name in (name for name, _ in counted):
                continue
            if step.call_id in refused:
                continue        # a refused tool call ran nothing
            # A refusal is the person's call, not the recipe's failure. A
            # promoted tool's own call is part of the use; a load is not.
            start = index + (1 if step.name == "load_skill" else 0)
            after = [s for s in turn.steps[start:] if s.call_id not in refused]
            judged = own_steps(skill, after) or after
            worked = self.last_reason == "end_turn" and all(s.ok for s in judged)
            try:
                record_use(skill, worked)
            except (OSError, SkillError):
                continue   # a counter must never cost the answer it follows
            counted.append((skill.name, worked))
        if counted:
            skills.reload()
            self._report_uses(counted)
        self.last_counted = counted
        return counted

    def _report_uses(self, counted: list[tuple[str, bool]]) -> None:
        """A recipe that came from the Setu catalog: tell the catalog whether
        this use worked -- through Setu, anonymously, under its
        share-installs switch -- on a thread of its own, never in the way."""
        import shutil
        import threading

        from yantra.setu_link import run_setu
        from yantra.skills.share import catalog_origin

        link = self._setu_link()
        program = ((link.data.get("command") if link is not None else None)
                   or shutil.which("setu"))
        if not program:
            return
        for name, worked in counted:
            origin = catalog_origin(name, self.home)
            if origin is None or not origin.get("version"):
                continue
            threading.Thread(target=run_setu, daemon=True, name="yantra-catalog-use", args=(
                program, "catalog", "worked", origin["item"], "--version",
                origin["version"], "--outcome", "ok" if worked else "failed")).start()

    # ---- notice, distil, test ------------------------------------------------

    def consider(self, *, forced: bool = False,
                 progress: Callable[[str], None] | None = None,
                 stop: Callable[[], bool] | None = None) -> Offer | None:
        """Look at the last turn; return an Offer, or None with ``last_skip``
        saying why not. ``forced`` is the person asking (``/learn``): the
        effort count and the will-it-recur question are theirs to waive.

        ``stop`` runs the look in the background: polled through the
        write-up's stream and between steps, and true means give way --
        None comes back, nothing staged is left. Nothing is asked: a test
        that needs a yes comes back as an offer with ``waiting`` set.
        """
        self._stop = stop
        try:
            return self._consider(forced, progress)
        except Stopped:
            self.last_skip = "stopped: a new message came first"
            return None
        finally:
            self._stop = None

    def _stopped(self) -> None:
        if self._stop is not None and self._stop():
            raise Stopped

    def _consider(self, forced: bool,
                  progress: Callable[[str], None] | None) -> Offer | None:
        say = progress or (lambda text: None)
        self.last_skip = None
        turn = current_turn(self.agent)
        repairs, failure = self._repair_target(turn)
        if repairs is None and failure:
            self.last_skip = failure
            return None
        # Asked for by name, two steps are still a recipe: the person has
        # answered "was it worth it" already. A repair is a load, a failure
        # and a recovery -- the effort question does not apply.
        skip = why_not(turn, self.last_reason,
                       min_steps=1 if forced or repairs else MIN_STEPS,
                       repairing=repairs is not None,
                       tools=self._tool_names())
        if skip:
            self.last_skip = skip
            return None

        found: set[str] = set()
        if repairs is not None:
            prompt = UPDATE_PROMPT.format(
                name=repairs.name, saved=scrub(_saved_text(repairs), found),
                connectors=self._connectors_hint(), digest=digest(turn, found))
            say(f"{repairs.name} failed this time; seeing whether it needs "
                f"updating")
        else:
            prompt = DISTIL_PROMPT.format(
                decide=DECIDE_FORCED if forced else DECIDE_ASKED,
                set_aside=self._set_aside(), connectors=self._connectors_hint(),
                digest=digest(turn, found))
            say("looking at what worked, to see if it is worth keeping")
        spent = Usage()
        reply = self._complete(prompt, spent)
        try:
            parsed = parse_reply(reply)
        except LearnError as exc:
            self.last_skip = str(exc)
            return None
        if isinstance(parsed, str):
            self.last_skip = (f"{repairs.name} left as it is: {parsed}"
                              if repairs else f"not worth keeping: {parsed}")
            return None
        draft = parsed
        if repairs is not None:
            # An update keeps its name and its home, whatever the reply said.
            draft.name = repairs.name
            draft.scope = next((k for k, v in LEARNED_SOURCES.items()
                                if v == repairs.source), draft.scope)

        written = f"{draft.skill_md()}\n{draft.script}\n{draft.test}"
        if any(v in written for v in found) or _TOKEN_SHAPES.search(written):
            self.last_skip = ("the draft contained what looks like a secret, "
                              "so it was not offered")
            return None

        self.facts_found = self.settle_facts(draft.facts, found)
        offer = Offer(draft=draft, staging=self._stage(draft), tested=None,
                      spent=spent, repairs=repairs,
                      failure=scrub(failure_line(turn, repairs), found)
                      if repairs else "")
        self._settle_name(offer)
        offer.connections = resolve_needs(draft.needs, self._setu_link())
        if repairs is not None and repairs.tool_name:
            offer.tool = repairs.tool_name
        if draft.script:
            try:
                self._test(offer, say)
            except Stopped:
                self.discard(offer)
                raise
            if offer.test_output.startswith(WAITING):
                offer.tested, offer.waiting = None, True
            elif not offer.tested and offer.test_output.startswith("[not run]"):
                self.last_skip = ("its test did not run, so it was not "
                                  "offered: " + offer.test_output[10:])
                self.discard(offer)
                return None
            elif not offer.tested:
                self.discard(offer)
                self.last_skip = (f"its script failed its test "
                                  f"{offer.test_runs} time(s), so it was not "
                                  f"offered:\n{_clip(offer.test_output, 600)}")
                return None
        if offer.tool:
            offer.keeps_tool = self._tool_still_fits(repairs, draft)
        return offer

    def settle_facts(self, text: str, found: set[str]) -> int:
        """The write-up's FACTS, handed to memory the way a look back's are.

        One call, two kinds of thing learned: the recipe keeps the way, and
        the values it takes as inputs -- which fan, which server -- are
        facts about this person, so they go where facts go. Under the
        look back's own mode: ``ask`` leaves them on ``memory.pending``
        for the host to offer when the turn ends, ``auto`` keeps them,
        ``off`` drops them. The same dedup applies (what is known, what
        was dropped), and a fact that carries a value scrubbed from the
        steps is not offered: that value was a secret.

        Returns how many were kept or left to offer. Never raises -- a fact
        must not cost the skill it came with.
        """
        from yantra.memory.reflect import keep, parse_reply as parse_facts

        memory = getattr(self.agent, "memory", None)
        if not text or memory is None or memory.reflect == "off":
            return 0
        try:
            remembered = [item.statement for item in memory.list(100)]
            facts = [c for c in parse_facts(text, remembered, memory.declined)
                     if not any(v in c.statement for v in found)
                     and c not in memory.pending]
            if memory.reflect == "auto":
                return len(keep(memory, facts))
            memory.pending.extend(facts)
            return len(facts)
        except Exception as exc:
            memory._fail(f"facts from the write-up not kept ({exc})")
            return 0

    def _tool_still_fits(self, skill: Skill, draft: Draft) -> bool:
        """The repair check: same script, and the passing test's words read
        back through the tool's argv template (promote.fits)."""
        from yantra.skills.promote import fits, load_tool_def

        if not draft.script or draft.script_name != skill.tool_script:
            return False
        try:
            tool = load_tool_def(skill)
        except SkillError:
            return False
        return fits(tool, draft.test, draft.script_name) is not None

    def _repair_target(self, turn: Turn | None) -> tuple[Skill | None, str]:
        """The learned skill this turn followed and saw fail, when the task
        was then finished another way -- (skill, "").

        (None, reason) when a followed skill failed and there is nothing
        to repair FROM: no step after the failure worked. (None, "") when
        no learned skill failed this turn at all.
        """
        failed = [name for name, worked in self.last_counted if not worked]
        skills = getattr(self.agent, "skills", None)
        if not failed or turn is None or skills is None:
            return None, ""
        skill = skills.get(failed[0])
        if skill is None:
            return None, ""
        uses = [i for i, s in enumerate(turn.steps)
                if (s.name == "load_skill" and s.arguments.get("name") == skill.name)
                or (skill.tool_name and s.name == skill.tool_name)]
        # a load is not part of the use; a promoted tool's own call is
        after = (turn.steps[uses[0] + (turn.steps[uses[0]].name == "load_skill"):]
                 if uses else [])
        # the recipe's own first failure, then anything at all that worked
        bad = next((s for s in own_steps(skill, after) or after if not s.ok), None)
        first_bad = after.index(bad) if bad is not None else None
        if (self.last_reason != "end_turn" or first_bad is None
                or not any(s.ok for s in after[first_bad + 1:])):
            return None, (f"the last turn followed {skill.name}, which failed, "
                          f"and the task was not finished another way -- "
                          f"there is nothing to update it from")
        return skill, ""

    def _tool_names(self) -> dict[str, str]:
        """Promoted tool name -> its skill's name, for this session."""
        skills = getattr(self.agent, "skills", None)
        return ({s.tool_name: s.name for s in skills if s.tool_name}
                if skills is not None else {})

    def _setu_link(self) -> Any:
        setu = getattr(self.agent, "setu", None)
        return getattr(setu, "link", None)

    def _connectors_hint(self) -> str:
        hint = connectors_hint(self._setu_link())
        return f"\n  {hint.rstrip()}" if hint else ""

    def _set_aside(self) -> str:
        skills = getattr(self.agent, "skills", None)
        stale = [s for s in skills if s.is_stale] if skills is not None else []
        if not stale:
            return ""
        return SET_ASIDE.format(names="\n".join(
            f"- {s.name}: {s.description}" for s in stale))

    def _complete(self, prompt: str, spent: Usage) -> str:
        """One plain completion, fresh context, no tools -- and its cost on
        the session's meter, because it was spent on the person's behalf."""
        agent = self.agent
        request = dict(messages=[Message("user", [TextBlock(prompt)])],
                       system=None, tools=[], model=agent.model,
                       max_tokens=DISTIL_MAX_TOKENS)
        if self._stop is None:
            response = agent.provider.complete(**request)
        else:
            # Streamed so it can be closed mid-answer: a local model stops
            # generating when the connection goes, and the person's next
            # turn does not queue behind a write-up nobody will read.
            events = agent.provider.stream(**request)
            try:
                response = collect(self._until_stopped(events))
            finally:
                close = getattr(events, "close", None)
                if close is not None:
                    close()
        spent.add(response.usage)
        agent.total_usage.add(response.usage)
        agent.usage_by_model.setdefault(
            response.model or agent.model, Usage()).add(response.usage)
        return response.message.text()

    def _until_stopped(self, events: Any) -> Any:
        for event in events:
            self._stopped()
            yield event

    def _stage(self, draft: Draft) -> Path:
        """Write the draft where a sandboxed bash can run it."""
        staging = self.cwd.joinpath(*STAGING, draft.name)
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        (staging / SKILL_FILE).write_text(draft.skill_md(), encoding="utf-8")
        if draft.script:
            script = staging / draft.script_name
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(draft.script, encoding="utf-8")
            script.chmod(0o755)
        return staging

    def _settle_name(self, offer: Offer) -> None:
        """A hand-written skill keeps its name; a learned one is replaced."""
        if offer.repairs is not None:
            offer.replaces = offer.repairs.directory
            return
        skills = getattr(self.agent, "skills", None)
        existing = skills.get(offer.draft.name) if skills is not None else None
        if existing is None:
            return
        if existing.is_learned:
            offer.replaces = existing.directory
            return
        offer.renamed_from = offer.draft.name
        offer.draft.name = f"{offer.draft.name}-learned"[:64]
        fresh = self._stage(offer.draft)
        shutil.rmtree(offer.staging, ignore_errors=True)
        offer.staging = fresh

    def _test(self, offer: Offer, say: Callable[[str], None]) -> None:
        """Run the draft's test at most MAX_TEST_RUNS times; repair between."""
        draft = offer.draft
        if not draft.test:
            offer.tested, offer.test_output = False, "the write-up gave no test command"
            return
        for attempt in range(1, MAX_TEST_RUNS + 1):
            self._stopped()
            say(f"testing {draft.script_name} ({attempt} of {MAX_TEST_RUNS})")
            offer.test_runs = attempt
            ok, output = self.run_test(draft, offer.staging)
            offer.tested, offer.test_output = ok, output
            if (ok or attempt == MAX_TEST_RUNS
                    or output.startswith(("[not run]", WAITING))):
                return
            say("the test failed; asking for one fix")
            reply = self._complete(FIX_SCRIPT_PROMPT.format(
                test=draft.test, output=_clip(output, 2000),
                script_name=draft.script_name, script=draft.script),
                offer.spent)
            fixed = _unfence(_section(reply, "SCRIPT", "END")) or _unfence(reply)
            if not fixed.strip() or fixed == draft.script:
                return
            draft.script = fixed
            (offer.staging / draft.script_name).write_text(fixed, encoding="utf-8")

    def run_test(self, draft: Draft, staging: Path) -> tuple[bool, str]:
        """One run through the session's bash tool and permission gate.

        ``$SKILL_DIR`` points at the staged draft; the command runs from the
        working folder, as the solve did. The gate may not HOLD here -- the
        turn is over, so a "not yet" is a refusal.
        """
        agent = self.agent
        if "bash" not in agent.registry or agent.registry.is_disabled("bash"):
            return False, "[not run] this session has no bash tool to test with"
        command = (f"export SKILL_DIR={shlex.quote(str(staging))}\n"
                   f"{draft.test}")
        call = ToolCall(id=f"learn-{uuid.uuid4().hex[:12]}", name="bash",
                        arguments={"command": command, "timeout": TEST_TIMEOUT})
        could_hold, agent.can_hold = agent.can_hold, False
        try:
            result = agent._execute(call)
        finally:
            agent.can_hold = could_hold
            # Refusal codes are drained onto a turn's events; this call
            # belongs to no turn, so nothing would ever collect its code.
            agent._refusals.pop(call.id, None)
        if call.id in agent.turn_refusals:
            # Refused at the gate: the script was never run, so there is
            # nothing for a repair to fix.
            code = agent.turn_refusals.pop(call.id)
            if code == REFUSED_UNATTENDED and self._stop is not None:
                # The background look asked nobody: it waits for a yes.
                return False, f"{WAITING} {result.content}"
            return False, f"[not run] {result.content}"
        if result.is_error:
            return False, result.content
        return _exit_code(result.content) == 0, result.content

    def test_waiting(self, offer: Offer, *,
                     progress: Callable[[str], None] | None = None) -> bool:
        """Run the test a background look left waiting, now that the
        person is here to say yes to it. True when it passed; a refusal
        at the gate leaves it waiting, a failure leaves it failed."""
        self._test(offer, progress or (lambda text: None))
        offer.waiting = offer.test_output.startswith(("[not run]", WAITING))
        offer.tested = None if offer.waiting else offer.tested
        return bool(offer.tested)

    # ---- promotion to a tool (notes/98) -----------------------------------------

    def propose_tool(self, name: str, *,
                     progress: Callable[[str], None] | None = None) -> Any:
        """Write a tool for a learned skill's script and test it once.

        Returns a promote.ToolOffer, or None with ``last_skip`` saying why.
        The suggestion (Skill.suggest_tool) is not required here: the
        person may promote earlier by asking -- it is their call either way.
        """
        from yantra.skills.promote import (
            FIX_PROPOSAL_PROMPT,
            MAX_RUNS_SHOWN,
            PROMOTE_PROMPT,
            ToolOffer,
            _only_script,
            parse_proposal,
        )

        say = progress or (lambda text: None)
        self.last_skip = None
        skills = getattr(self.agent, "skills", None)
        skill = skills.get(name) if skills is not None else None
        why, script_name = None, ""
        if skill is None or not skill.is_learned:
            why = f"there is no learned skill called {name!r}"
        elif skill.tool:
            why = f"{name} is already the tool {skill.tool_name}"
        elif skill.is_stale:
            why = f"{name} is set aside after failing; repair it first"
        else:
            try:
                script_name = _only_script(skill)
            except SkillError as exc:
                why = str(exc)
        if why is not None:
            self.last_skip = why
            return None

        taken = sorted(n for n in self.agent.registry.names())
        runs = [s.arguments.get("command", "") for s in self._script_runs(
            skill, script_name)][-MAX_RUNS_SHOWN:]
        found: set[str] = set()
        prompt = PROMOTE_PROMPT.format(
            worked=skill.learned.in_a_row if skill.learned else 0,
            skill_md=scrub(_without_counters(_read(skill.path)).strip(), found),
            script_name=script_name,
            script=scrub(_read(skill.directory / script_name).strip(), found),
            runs=("\nHOW IT WAS RUN IN THIS SESSION:\n" + "\n".join(
                f"$ {scrub(r, found)}" for r in runs) + "\n") if runs else "",
            taken=", ".join(taken))
        say(f"writing {name}'s script up as a tool")
        spent = Usage()
        reply = self._complete(prompt, spent)
        try:
            parsed = parse_proposal(reply)
        except SkillError as exc:
            say("the definition broke a rule; asking for one fix")
            reply = self._complete(FIX_PROPOSAL_PROMPT.format(
                error=exc, reply=_clip(reply, 4000)), spent)
            try:
                parsed = parse_proposal(reply)
            except SkillError as again:
                self.last_skip = str(again)
                return None
        if isinstance(parsed, str):
            self.last_skip = f"not made a tool: {parsed}"
            return None
        tool, test = parsed
        if tool.name in self.agent.registry:
            self.last_skip = (f"the proposed name {tool.name!r} is already a "
                              f"tool in this session")
            return None
        if any(v in json.dumps(test) for v in found):
            self.last_skip = ("the proposed test carries what looks like a "
                              "secret, so it was not run")
            return None
        offer = ToolOffer(skill=skill, tool=tool, test_args=test,
                          spent_in=spent.input_tokens,
                          spent_out=spent.output_tokens)
        say(f"testing {tool.name} once")
        offer.passed, offer.test_output = self._run_tool(offer, script_name)
        if offer.test_output.startswith("[not run]"):
            self.last_skip = "its test did not run: " + offer.test_output[10:]
            return None
        return offer

    def _script_runs(self, skill: Skill, script_name: str) -> list[Step]:
        """This session's bash calls that ran the skill's script -- real
        arguments the proposal can pick its test from."""
        needle = f"{skill.directory}/{script_name}"
        steps: list[Step] = []
        for message in self.agent.history:
            for block in message.content:
                if (isinstance(block, ToolCall) and block.name == "bash"
                        and needle in str(block.arguments.get("command", ""))):
                    steps.append(Step(block.id, "bash", block.arguments, "", True))
        return steps

    def _run_tool(self, offer: Any, script_name: str) -> tuple[bool, str]:
        """One call of the proposed tool, through the session's permission
        gate -- registered for that one call, then taken away again."""
        from yantra.skills.promote import ScriptTool

        agent = self.agent
        skill = offer.skill
        probe = ScriptTool(agent.skills, skill, offer.tool)
        probe.script = script_name          # not promoted yet: no tool: line
        probe.run_checks = False
        agent.registry.register(probe)
        call = ToolCall(id=f"promote-{uuid.uuid4().hex[:12]}",
                        name=offer.tool.name, arguments=dict(offer.test_args))
        could_hold, agent.can_hold = agent.can_hold, False
        try:
            result = agent._execute(call)
        finally:
            agent.can_hold = could_hold
            agent.registry.unregister(offer.tool.name)
            agent._refusals.pop(call.id, None)
        if call.id in agent.turn_refusals:
            del agent.turn_refusals[call.id]
            return False, f"[not run] {result.content}"
        return not result.is_error, result.content

    def save_tool(self, offer: Any) -> Skill:
        """The person said yes: write tool.json and the ``tool:`` line."""
        from yantra.skills.promote import TOOL_FILE, _only_script

        skill = offer.skill
        script_name = _only_script(skill)
        text = set_tool_line(_read(skill.path), f"{offer.tool.name} {script_name}")
        validate_text(text, skill.path, source=skill.source)
        write_atomic(skill.directory / TOOL_FILE, offer.tool.to_json())
        write_atomic(skill.path, text)
        skills = self.agent.skills
        skills.reload()
        return skills.get(skill.name) or load_skill(skill.path, source=skill.source)

    # ---- saving ----------------------------------------------------------------

    def save(self, offer: Offer, *, scope: str | None = None,
             skill_md: str | None = None, script: str | None = None) -> Skill:
        """Write the approved skill into its learned/ folder and rescan.

        ``skill_md`` and ``script`` are the person's edits, when they made
        any; the edited SKILL.md must still pass every rule a hand-written
        one does, and may not rename the skill. The counter line is
        Yantra's and is set here, whatever the edit said.
        """
        draft = offer.draft
        scope = scope or draft.scope
        root = learned_root(scope, self.cwd, home=self.home)
        target = root / draft.name
        text = skill_md if skill_md is not None else draft.skill_md()
        old = offer.repairs.learned if offer.repairs is not None else None
        # A repair keeps the record and clears both streaks: the fixed
        # version has proved nothing yet -- which is also what makes a
        # dropped tool wait for PROMOTE_AFTER fresh successes.
        record = (LearnedRecord(since=old.since, worked=old.worked,
                                failed=old.failed, last_ok=old.last_ok,
                                streak=0)
                  if old is not None
                  else LearnedRecord(since=date.today().isoformat()))
        text = set_learned_line(text, record)
        keep = offer.repairs if offer.keeps_tool else None
        text = set_tool_line(text, keep.tool if keep is not None else "")
        validate_text(text, target / SKILL_FILE, source=LEARNED_SOURCES[scope])

        root.mkdir(parents=True, exist_ok=True)
        building = Path(tempfile.mkdtemp(dir=root, prefix=f".{draft.name}-"))
        try:
            (building / SKILL_FILE).write_text(text, encoding="utf-8")
            body = script if script is not None else draft.script
            if draft.script_name and body.strip():
                path = building / draft.script_name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")
                path.chmod(0o755)
            if keep is not None:
                from yantra.skills.promote import TOOL_FILE
                shutil.copy2(keep.directory / TOOL_FILE, building / TOOL_FILE)
            if offer.replaces is not None and offer.replaces.exists():
                shutil.rmtree(offer.replaces)
            if target.exists():
                shutil.rmtree(target)
            os.replace(building, target)
        finally:
            if building.exists():
                shutil.rmtree(building, ignore_errors=True)
        shutil.rmtree(offer.staging, ignore_errors=True)

        skills = getattr(self.agent, "skills", None)
        if skills is not None:
            skills.reload()
            saved = skills.get(draft.name)
            if saved is not None:
                return saved
        return load_skill(target / SKILL_FILE, source=LEARNED_SOURCES[scope])

    def discard(self, offer: Offer) -> None:
        """The person said no: the staged draft goes, nothing is kept."""
        shutil.rmtree(offer.staging, ignore_errors=True)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _without_counters(text: str) -> str:
    """SKILL.md minus the lines only Yantra writes: the counters, and the
    tool line (a repair's offer says in words whether the tool stays)."""
    return "".join(line for line in text.splitlines(keepends=True)
                   if not re.match(r"^(learned|tool)\s*:", line))


def _saved_text(skill: Skill) -> str:
    """A learned skill as the update call sees it: SKILL.md, then each
    script, minus the counter line (the model never writes that)."""
    parts = [_without_counters(_read(skill.path)).strip()]
    for script in sorted((skill.directory / "scripts").glob("*")):
        if script.is_file():
            parts.append(f"=== scripts/{script.name} ===\n{_read(script).strip()}")
    return "\n\n".join(parts)


def failure_line(turn: Turn, skill: Skill) -> str:
    """The first of the recipe's own calls that failed after ``skill`` was
    loaded (own_steps), in one line."""
    loaded, after = False, []
    for step in turn.steps:
        if step.name == "load_skill" and step.arguments.get("name") == skill.name:
            loaded = True
            continue
        loaded = loaded or bool(skill.tool_name and step.name == skill.tool_name)
        if loaded:
            after.append(step)
    for step in own_steps(skill, after) or after:
        if not step.ok:
            first = (step.result.strip().splitlines() or [""])
            detail = next((ln for ln in first if ln.strip()
                           and not ln.startswith("exit code")), first[0])
            return f"{step.name}: {_clip(detail, 200)}"
    return ""


# ---- the counter line -----------------------------------------------------------


def set_learned_line(text: str, record: LearnedRecord) -> str:
    """Put ``origin: learned`` and ``learned: ...`` in a SKILL.md's
    frontmatter, replacing whatever an edit left there: both are
    Yantra's to write."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return text
    head = [line for line in lines[1:end]
            if not re.match(r"^(learned|origin)\s*:", line, re.I)]
    new = ["---", *head, "origin: learned", f"learned: {record.render()}",
           *lines[end:]]
    return "\n".join(new) + ("\n" if text.endswith("\n") else "")


def set_tool_line(text: str, tool: str) -> str:
    """Put ``tool: <name> scripts/<file>`` in a SKILL.md's frontmatter, or
    take it out when ``tool`` is empty. Yantra's to write, like the
    counters: a person promotes through the question, not by typing it."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return text
    head = [line for line in lines[1:end] if not re.match(r"^tool\s*:", line, re.I)]
    new = ["---", *head, *([f"tool: {tool}"] if tool else []), *lines[end:]]
    return "\n".join(new) + ("\n" if text.endswith("\n") else "")


def record_use(skill: Skill, worked: bool, today: str | None = None) -> LearnedRecord:
    """Add one use to a learned skill's counters, on disk."""
    today = today or date.today().isoformat()
    old = skill.learned or LearnedRecord(since=today)
    record = LearnedRecord(
        since=old.since,
        worked=old.worked + (1 if worked else 0),
        failed=old.failed + (0 if worked else 1),
        last_ok=today if worked else old.last_ok,
        failing=0 if worked else old.failing + 1,
        streak=old.in_a_row + 1 if worked else 0)
    text = skill.path.read_text(encoding="utf-8")
    write_atomic(skill.path, set_learned_line(text, record))
    return record


def learn_mode(flag: str | None = None) -> str:
    """--learn beats $YANTRA_LEARN beats the default, which is ``ask``."""
    mode = (flag or os.environ.get(ENV_MODE, "") or "ask").strip().lower()
    if mode not in MODES:
        raise ValueError(f"{ENV_MODE}={mode!r}: expected one of {', '.join(MODES)}")
    return mode


def enable_learning(agent: Any, mode: str = "ask",
                    *, home: Path | None = None) -> Learner | None:
    """Attach a Learner as ``agent.learner``; None (and nothing attached)
    when the session has no skills to add to.

    Off is attached too: it stops the OFFERS, never the counting. A
    learned skill used by a piped run or a scheduled one is still counted,
    so one that keeps failing there still goes stale -- counting asks
    nothing and calls no model."""
    if getattr(agent, "skills", None) is None:
        agent.learner = None
        return None
    agent.learner = Learner(agent, mode, home=home)
    return agent.learner
