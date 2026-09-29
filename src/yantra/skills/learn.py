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
learned skill loaded in a turn WORKED when the turn finished and no call
after the load failed, and FAILED otherwise. Crude, and honest about
being crude -- it is what the harness saw, not a grade.
"""

from __future__ import annotations

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

    def skills_loaded(self) -> list[str]:
        return [str(s.arguments.get("name", "")) for s in self.steps
                if s.name in ("load_skill", "run_skill")]


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


def _is_prompt(message: Message) -> bool:
    """A user message a PERSON wrote -- not a batch of tool results."""
    return (message.role == "user"
            and any(isinstance(b, TextBlock) for b in message.content)
            and not any(isinstance(b, ToolResult) for b in message.content))


def read_turn(history: list[Message]) -> Turn | None:
    """The last turn in ``history``, from its prompt to its final answer.

    Walks back to the most recent message a person typed, so a turn that
    was held for approval and resumed reads as the one task it was.
    """
    start = next((i for i in range(len(history) - 1, -1, -1)
                  if _is_prompt(history[i])), None)
    if start is None:
        return None
    task = history[start].text().strip()
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


def why_not(turn: Turn | None, reason: str,
            *, min_steps: int = MIN_STEPS) -> str | None:
    """The counted half of noticing: None when the turn is worth a look,
    otherwise why not, in words the host can show on ``/learn``."""
    if turn is None:
        return "there is no finished turn to learn from yet"
    if reason != "end_turn":
        return f"the last turn did not finish ({reason})"
    if loaded := [n for n in turn.skills_loaded() if n]:
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

DISTIL_PROMPT = """\
You are writing down a recipe, so that the next time someone asks for \
this kind of task it takes two steps instead of ten.

Below is ONE task a person asked for, every tool call that was made, and \
what each returned. Failed calls are shown in one line each.

{decide}

Rules for the skill:
- Write ONLY what the steps below show. No tips, pitfalls, error codes, \
retries or fallbacks that did not happen here.
- No secrets, ever. Tokens, passwords and keys are never written down; \
say where they come from (a file the person named, an environment \
variable). Values shown as [redacted] are secrets.
- No personal facts. This person's server addresses, device names and \
ids, places and account names are INPUTS the skill asks for or looks \
up, never written into it.
- Keep the path that worked; drop the dead ends.
- If a script turns the task into one command, write it: python3 \
standard library only (or bash), taking the inputs as command-line \
arguments, printing what it did, and exiting non-zero when it failed. \
Otherwise write no script.

Reply in EXACTLY this shape, with nothing before it:

VERDICT: save
NAME: short-lowercase-hyphenated-name
DESCRIPTION: What it does, then "Use when ..." -- one or two sentences.
SCOPE: user   (or project, only when the recipe is about the code in \
this folder)
INPUTS: each input, and where it comes from
NEEDS: the access it needs, in words (or none)
SCRIPT: scripts/<file>   (or none)
TEST: one shell command, run from the working folder, that repeats \
THIS task with THIS task's inputs; write the script's path as \
"$SKILL_DIR/scripts/<file>"   (or none)
=== INSTRUCTIONS ===
# A short title
Numbered steps the next run follows. If there is a script, one step runs \
it from the working folder, exactly as TEST does: \
python3 "$SKILL_DIR/scripts/<file>" ... -- never cd into the skill's \
folder, or paths the person gives stop working.
=== SCRIPT ===
the whole script (leave this section out when SCRIPT is none)
=== END ===

When it is not worth saving, reply with exactly two lines:

VERDICT: skip
REASON: one short sentence

{digest}
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

REPAIR_PROMPT = """\
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
    if not verdict.startswith("save"):
        raise LearnError("the write-up did not follow the format "
                         "(no 'VERDICT: save' or 'VERDICT: skip')")

    name = _slug(fields.get("name", ""))
    if not NAME_RE.match(name or "-") or name == "learned":
        raise LearnError(f"the write-up named the skill {fields.get('name')!r}, "
                         f"which is not a usable name")
    description = " ".join(fields.get("description", "").split())
    if len(description) < MIN_DESCRIPTION:
        raise LearnError("the write-up's description is too thin to ever be "
                         "picked")
    body = _section(text, "INSTRUCTIONS", "SCRIPT", "END").strip()
    if not body:
        raise LearnError("the write-up has no instructions")
    scope = fields.get("scope", "user").split()[0:1] or ["user"]
    scope = scope[0].lower().strip(".,")
    script_name = _none(fields.get("script", "")).split()[0:1]
    script_name = script_name[0] if script_name else ""
    script = _unfence(_section(text, "SCRIPT", "END")) if script_name else ""
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
                 test=_none(fields.get("test", "")) if script_name else "")


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
            "skill_md": self._staged(SKILL_FILE, draft.skill_md()),
            "script_name": draft.script_name,
            "script": (self._staged(draft.script_name, draft.script)
                       if draft.script_name else ""),
            "test": {"command": draft.test, "passed": self.tested,
                     "runs": self.test_runs,
                     "output": _clip(self.test_output, 1500)},
            "spent": {"input": self.spent.input_tokens,
                      "output": self.spent.output_tokens},
            "replaces": str(self.replaces) if self.replaces else None,
            "renamed_from": self.renamed_from,
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
        #: How the last turn ended, set by ``after_turn``. A host that makes
        #: a Learner on demand (``/learn`` with learning off) sets it itself.
        self.last_reason = ""

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
        turn = read_turn(self.agent.history)
        if skills is None or turn is None:
            return []
        refused = set(getattr(self.agent, "turn_refusals", {}) or {})
        counted: list[tuple[str, bool]] = []
        for index, step in enumerate(turn.steps):
            if step.name != "load_skill" or not step.ok:
                continue
            skill = skills.get(str(step.arguments.get("name", "")))
            if skill is None or not skill.is_learned:
                continue
            if skill.name in (name for name, _ in counted):
                continue
            # A refusal is the person's call, not the recipe's failure.
            after = [s for s in turn.steps[index + 1:] if s.call_id not in refused]
            worked = self.last_reason == "end_turn" and all(s.ok for s in after)
            try:
                record_use(skill, worked)
            except (OSError, SkillError):
                continue   # a counter must never cost the answer it follows
            counted.append((skill.name, worked))
        if counted:
            skills.reload()
        return counted

    # ---- notice, distil, test ------------------------------------------------

    def consider(self, *, forced: bool = False,
                 progress: Callable[[str], None] | None = None) -> Offer | None:
        """Look at the last turn; return an Offer, or None with ``last_skip``
        saying why not. ``forced`` is the person asking (``/learn``): the
        effort count and the will-it-recur question are theirs to waive.
        """
        say = progress or (lambda text: None)
        self.last_skip = None
        turn = read_turn(self.agent.history)
        # Asked for by name, two steps are still a recipe: the person has
        # answered "was it worth it" already.
        skip = why_not(turn, self.last_reason,
                       min_steps=1 if forced else MIN_STEPS)
        if skip:
            self.last_skip = skip
            return None

        found: set[str] = set()
        prompt = DISTIL_PROMPT.format(
            decide=DECIDE_FORCED if forced else DECIDE_ASKED,
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
            self.last_skip = f"not worth keeping: {parsed}"
            return None
        draft = parsed

        written = f"{draft.skill_md()}\n{draft.script}\n{draft.test}"
        if any(v in written for v in found) or _TOKEN_SHAPES.search(written):
            self.last_skip = ("the draft contained what looks like a secret, "
                              "so it was not offered")
            return None

        offer = Offer(draft=draft, staging=self._stage(draft), tested=None,
                      spent=spent)
        self._settle_name(offer)
        if draft.script:
            self._test(offer, say)
            if not offer.tested and offer.test_output.startswith("[not run]"):
                self.last_skip = ("its test did not run, so it was not "
                                  "offered: " + offer.test_output[10:])
                self.discard(offer)
                return None
            if not offer.tested:
                self.discard(offer)
                self.last_skip = (f"its script failed its test "
                                  f"{offer.test_runs} time(s), so it was not "
                                  f"offered:\n{_clip(offer.test_output, 600)}")
                return None
        return offer

    def _complete(self, prompt: str, spent: Usage) -> str:
        """One plain completion, fresh context, no tools -- and its cost on
        the session's meter, because it was spent on the person's behalf."""
        agent = self.agent
        response = agent.provider.complete(
            messages=[Message("user", [TextBlock(prompt)])],
            system=None, tools=[], model=agent.model,
            max_tokens=DISTIL_MAX_TOKENS)
        spent.add(response.usage)
        agent.total_usage.add(response.usage)
        agent.usage_by_model.setdefault(
            response.model or agent.model, Usage()).add(response.usage)
        return response.message.text()

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
            say(f"testing {draft.script_name} ({attempt} of {MAX_TEST_RUNS})")
            offer.test_runs = attempt
            ok, output = self.run_test(draft, offer.staging)
            offer.tested, offer.test_output = ok, output
            if ok or attempt == MAX_TEST_RUNS or output.startswith("[not run]"):
                return
            say("the test failed; asking for one fix")
            reply = self._complete(REPAIR_PROMPT.format(
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
            del agent.turn_refusals[call.id]
            return False, f"[not run] {result.content}"
        if result.is_error:
            return False, result.content
        return _exit_code(result.content) == 0, result.content

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
        text = set_learned_line(text, LearnedRecord(since=date.today().isoformat()))
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


def record_use(skill: Skill, worked: bool, today: str | None = None) -> LearnedRecord:
    """Add one use to a learned skill's counters, on disk."""
    today = today or date.today().isoformat()
    old = skill.learned or LearnedRecord(since=today)
    record = LearnedRecord(
        since=old.since,
        worked=old.worked + (1 if worked else 0),
        failed=old.failed + (0 if worked else 1),
        last_ok=today if worked else old.last_ok)
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
    when the mode is off or the session has no skills to add to."""
    if mode == "off" or getattr(agent, "skills", None) is None:
        agent.learner = None
        return None
    agent.learner = Learner(agent, mode, home=home)
    return agent.learner
