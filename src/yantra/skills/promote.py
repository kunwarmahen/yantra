"""A learned skill's script, promoted to a tool of its own.

A recipe that has worked five times in a row is still two or three calls
each time: load_skill, read the steps, write the bash line that runs the
script. Promoted, it is ONE structured call -- ``fan_control(fan="office",
percentage=50)`` -- which is the cheapest shape there is, and the one
small local models get right most often
([notes/98](../../notes/98-one-call.md)).

    suggest   5 successes in a row, a script, no tool yet  (loader.Skill.suggest_tool)
    propose   one fresh model call reads the recipe + script, writes a ToolDef
    test      one run THROUGH the new tool, through the permission gate
    ask       the person sees the definition and the result; only a yes writes it

WHAT A PROMOTED TOOL IS. ``tool.json`` beside the script -- a name, a
description, JSON parameters, and an argv template that maps each
parameter onto the script's command line -- plus the ``tool:`` line in
SKILL.md. The skill stays a skill: its roster line gains ``[tool: name]``,
its counters keep counting (a call to the tool is a use), and when it
goes stale the tool is set aside with it.

NO SHELL, EVER. The script runs as an argv list -- ``python3 <script>
<arg> <arg>`` -- so nothing the model passes can become a second
command. A string argument may not start with ``-``: that is the one
injection an argv list still allows (an option the script never meant
to be reachable).

IT ASKS, LIKE BASH. The person approved the script twice (at the save,
at the promotion), but a script can do whatever bash can, so each call
goes through the normal permission gate -- ``--yolo`` and a confining
``--sandbox`` cover it exactly as they cover bash. A skill whose
``needs`` talk about money always asks.

THE SANDBOX IS BASH'S. Whatever contains the session's bash contains the
script, from the working folder; under bubblewrap the skill's own folder
is mounted read-only so the script can be found.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from yantra.errors import ToolError
from yantra.sandbox import CommandTimedOut, SubprocessSandbox
from yantra.skills.loader import TOOL_NAME_RE, Skill, SkillError
from yantra.tools.base import Tool, ToolContext

#: The definition's file, beside SKILL.md.
TOOL_FILE = "tool.json"

#: How a script is started, by extension. Anything else is not promoted:
#: a learned script is python3 standard library or bash (learn.RULES).
INTERPRETERS = {".py": "python3", ".sh": "bash"}

#: Seconds one call may run -- the same ceiling the save's test has.
TOOL_TIMEOUT = 60
MAX_OUTPUT_CHARS = 10_000

#: JSON-schema types a parameter may have. Every one of them becomes ONE
#: command-line word; lists and objects have no honest spelling there.
PARAM_TYPES = ("string", "integer", "number", "boolean")

#: ``needs`` that mention money make every call ask, even under --yolo.
SPENDS = re.compile(r"\b(buy|buys|pay|pays|payment|purchase|order|spend|"
                    r"checkout|charge|refund|money|card)\b", re.I)

_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

#: Parameter names that would put a credential in the model's hands. A
#: tool argument is written by the model and kept in the history; a
#: secret belongs in the file or variable the script already reads (or
#: the Setu vault), never in a call. Found live: a script that accepts
#: ``--token`` was proposed as a tool with an ``ha_token`` parameter.
SECRET_NAMES = re.compile(r"(token|secret|passw|api_?key|private_?key|"
                          r"credential|auth)", re.I)


@dataclass(slots=True, frozen=True)
class ToolDef:
    """``tool.json``, validated. ``argv`` items are words or groups: a
    word is always passed; a GROUP (a list of words) is passed only when
    every parameter it names was given, and a boolean in a group passes
    the group when true and drops it when false -- which is how optional
    flags (``["--speed", "{speed}"]``, ``["--dry-run", "{dry_run}"]``) are
    spelled."""

    name: str
    description: str
    parameters: dict[str, Any]
    argv: tuple[str | tuple[str, ...], ...]

    def to_json(self) -> str:
        return json.dumps({
            "name": self.name, "description": self.description,
            "parameters": self.parameters,
            "argv": [list(a) if isinstance(a, tuple) else a for a in self.argv],
        }, indent=2) + "\n"

    @property
    def properties(self) -> dict[str, dict[str, Any]]:
        return self.parameters.get("properties", {})

    @property
    def required(self) -> list[str]:
        return list(self.parameters.get("required", []))

    # ---- one call's command line --------------------------------------------

    def check_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Model-supplied args against the parameters, as readable errors."""
        unknown = sorted(set(args) - set(self.properties))
        if unknown:
            raise ToolError(f"unknown argument(s) {', '.join(unknown)}; this "
                            f"tool takes {', '.join(self.properties) or 'none'}")
        missing = [k for k in self.required if args.get(k) is None]
        if missing:
            raise ToolError(f"missing required argument(s) {', '.join(missing)}")
        for key, value in args.items():
            spec = self.properties[key]
            kind = spec.get("type")
            if value is None:
                continue
            ok = {"string": isinstance(value, str),
                  "integer": isinstance(value, int) and not isinstance(value, bool),
                  "number": (isinstance(value, (int, float))
                             and not isinstance(value, bool)),
                  "boolean": isinstance(value, bool)}.get(kind, False)
            if not ok:
                raise ToolError(f"argument {key!r} must be a {kind}, got "
                                f"{type(value).__name__}")
            if "enum" in spec and value not in spec["enum"]:
                raise ToolError(f"argument {key!r} must be one of "
                                f"{', '.join(map(str, spec['enum']))}")
            if kind == "string" and value.startswith("-"):
                # An argv list stops a second command, not an option the
                # script parses: "--help" as a fan name is an injection.
                raise ToolError(f"argument {key!r} may not start with '-'")
        return args

    def command(self, args: dict[str, Any]) -> list[str]:
        """The words after the script's path, for these args."""
        words: list[str] = []
        for item in self.argv:
            if isinstance(item, str):
                words.append(_fill(item, args))
                continue
            names = [n for word in item for n in _PLACEHOLDER.findall(word)]
            if any(args.get(n) is None or args.get(n) is False for n in names):
                continue
            for word in item:
                # a boolean's own word is only the switch: "{dry_run}" alone
                # vanishes, the literal flag beside it stays
                if (m := _PLACEHOLDER.fullmatch(word)) and isinstance(
                        args.get(m.group(1)), bool):
                    continue
                words.append(_fill(word, args))
        return words


def _fill(word: str, args: dict[str, Any]) -> str:
    def value(match: re.Match[str]) -> str:
        got = args.get(match.group(1))
        if isinstance(got, bool):
            return "true" if got else "false"
        return "" if got is None else str(got)
    return _PLACEHOLDER.sub(value, word)


def parse_tool_def(data: Any, where: str = TOOL_FILE) -> ToolDef:
    """A dict (from tool.json or a model's reply) -> ToolDef, or SkillError."""
    if not isinstance(data, dict):
        raise SkillError(f"{where}: expected a JSON object")
    name = str(data.get("name", "")).strip()
    if not TOOL_NAME_RE.match(name):
        raise SkillError(f"{where}: tool name {name!r} must be snake_case "
                         f"(fan_control), 2-48 characters")
    description = " ".join(str(data.get("description", "")).split())
    if len(description) < 12:
        raise SkillError(f"{where}: the description is too thin to pick the "
                         f"tool by")
    params = data.get("parameters")
    if not isinstance(params, dict) or params.get("type", "object") != "object":
        raise SkillError(f"{where}: parameters must be a JSON-schema object")
    props = params.get("properties", {})
    if not isinstance(props, dict):
        raise SkillError(f"{where}: parameters.properties must be an object")
    for key, spec in props.items():
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", key):
            raise SkillError(f"{where}: parameter {key!r} must be snake_case")
        if SECRET_NAMES.search(key):
            raise SkillError(f"{where}: parameter {key!r} would pass a secret "
                             f"through the model -- leave it to the file or "
                             f"environment variable the script reads")
        if not isinstance(spec, dict) or spec.get("type") not in PARAM_TYPES:
            raise SkillError(f"{where}: parameter {key!r} needs a type, one of "
                             f"{', '.join(PARAM_TYPES)}")
    required = params.get("required", [])
    if not isinstance(required, list) or any(r not in props for r in required):
        raise SkillError(f"{where}: parameters.required names a parameter "
                         f"that is not defined")

    raw = data.get("argv")
    if not isinstance(raw, list):
        raise SkillError(f"{where}: argv must be a list of words and groups")
    argv: list[str | tuple[str, ...]] = []
    placed: dict[str, bool] = {}          # name -> placed at top level
    for item in raw:
        if isinstance(item, str):
            argv.append(item)
            for n in _PLACEHOLDER.findall(item):
                placed[n] = True
        elif (isinstance(item, list) and item
              and all(isinstance(w, str) for w in item)):
            argv.append(tuple(item))
            for n in (n for w in item for n in _PLACEHOLDER.findall(w)):
                placed.setdefault(n, False)
        else:
            raise SkillError(f"{where}: argv items are strings or non-empty "
                             f"lists of strings, got {item!r}")
    for n in placed:
        if n not in props:
            raise SkillError(f"{where}: argv uses {{{n}}}, which is not a "
                             f"parameter")
    for key in props:
        if key not in placed:
            raise SkillError(f"{where}: parameter {key!r} never reaches the "
                             f"script -- argv does not use {{{key}}}")
        if placed[key] and key not in required:
            raise SkillError(f"{where}: optional parameter {key!r} must sit "
                             f"in a group (a list), so it can be left out")
        if props[key]["type"] == "boolean" and placed[key]:
            raise SkillError(f"{where}: boolean {key!r} must sit in a group "
                             f"with the flag it switches")
    schema = {"type": "object", "properties": props,
              "required": list(required), "additionalProperties": False}
    return ToolDef(name=name, description=description, parameters=schema,
                   argv=tuple(argv))


def load_tool_def(skill: Skill) -> ToolDef:
    """A promoted skill's tool.json, checked against its ``tool:`` line."""
    path = skill.directory / TOOL_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise SkillError(f"tool: names {skill.tool_name!r} but "
                         f"{TOOL_FILE} is missing") from None
    except json.JSONDecodeError as exc:
        raise SkillError(f"{TOOL_FILE} is not valid JSON: {exc}") from None
    tool = parse_tool_def(data, str(path))
    if tool.name != skill.tool_name:
        raise SkillError(f"{TOOL_FILE} names {tool.name!r}, SKILL.md says "
                         f"{skill.tool_name!r}")
    script = skill.directory / skill.tool_script
    if not script.is_file():
        raise SkillError(f"tool: runs {skill.tool_script}, which is missing")
    if script.suffix not in INTERPRETERS:
        raise SkillError(f"tool: {script.name} is neither python (.py) nor "
                         f"bash (.sh)")
    return tool


def interpreter_line(directory: Path, script: str) -> list[str]:
    """``python3 /abs/skill/scripts/fan.py`` -- the words before the args."""
    path = directory / script
    return [INTERPRETERS[path.suffix], str(path)]


def fits(tool: ToolDef, test: str, script: str) -> dict[str, Any] | None:
    """Do the words of a recipe's TEST command still fit the tool?

    The repair check (plan: "re-check, keep if it passes"): a fixed script
    whose test is ``python3 "$SKILL_DIR/scripts/fan.py" ha.env office 50``
    keeps ``fan_control`` when those words can be read back through its
    argv template into arguments the parameters accept. Returns those
    arguments, or None when the shape changed (a new argument, one gone,
    a different script) -- and then the tool is dropped, not guessed at.
    """
    try:
        words = shlex.split(test)
    except ValueError:
        return None
    target = f"$SKILL_DIR/{script}"
    at = next((i for i, w in enumerate(words) if w == target), None)
    if at is None or at == 0 or words[at - 1] != INTERPRETERS.get(
            Path(script).suffix):
        return None
    got = _match(list(tool.argv), words[at + 1:], {}, tool)
    if got is None:
        return None
    try:
        return tool.check_args(got)
    except ToolError:
        return None


def _match(items: list, words: list[str], got: dict[str, Any],
           tool: ToolDef) -> dict[str, Any] | None:
    """Backtracking read of ``words`` through the argv template."""
    if not items:
        return got if not words else None
    item, rest = items[0], items[1:]
    if isinstance(item, str):
        if not words:
            return None
        bound = _bind(item, words[0], got, tool)
        return None if bound is None else _match(rest, words[1:], bound, tool)
    # a group: try it present, then absent
    group, trial, ok = list(item), dict(got), True
    if len(words) >= len(group):
        for word, taken in zip(group, words[:len(group)], strict=True):
            m = _PLACEHOLDER.fullmatch(word)
            if m and tool.properties.get(m.group(1), {}).get("type") == "boolean":
                trial[m.group(1)] = True
                continue
            bound = _bind(word, taken, trial, tool)
            if bound is None:
                ok = False
                break
            trial = bound
        if ok:
            # a boolean's own "{flag}" word takes no command-line word
            used = sum(1 for w in group if not (
                (m := _PLACEHOLDER.fullmatch(w))
                and tool.properties.get(m.group(1), {}).get("type") == "boolean"))
            done = _match(rest, words[used:], trial, tool)
            if done is not None:
                return done
    return _match(rest, words, got, tool)


def _bind(template: str, word: str, got: dict[str, Any],
          tool: ToolDef) -> dict[str, Any] | None:
    """One template word against one command-line word."""
    names = _PLACEHOLDER.findall(template)
    if not names:
        return got if template == word else None
    if len(names) > 1:
        return None                     # ambiguous; never guessed
    pattern = "^" + re.escape(template).replace(
        re.escape("{" + names[0] + "}"), "(.+)") + "$"
    m = re.match(pattern, word)
    if m is None:
        return None
    kind = tool.properties.get(names[0], {}).get("type")
    raw: Any = m.group(1)
    try:
        value = (int(raw) if kind == "integer" else
                 float(raw) if kind == "number" else raw)
    except ValueError:
        return None
    return {**got, names[0]: value}


# ---- the tool ----------------------------------------------------------------


def _head_tail(text: str, cap: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= cap:
        return text
    keep = cap // 2
    return f"{text[:keep]}\n[... {len(text) - cap} chars omitted ...]\n{text[-keep:]}"


class ScriptTool(Tool):
    """One promoted skill's script, as a tool. Instance attributes shadow
    the ClassVars, the way MCP tools do it."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters: ClassVar[dict] = {}
    read_only: ClassVar[bool] = False

    def __init__(self, skills: Any, skill: Skill, tool: ToolDef) -> None:
        self.skills = skills
        #: What memory holds that may fill its arguments (remember()).
        self.remembered: list[str] = []
        self.update(skill, tool)

    def update(self, skill: Skill, tool: ToolDef) -> None:
        """A rescan found the skill again (repaired, edited): take the new
        definition without re-registering the name."""
        self.skill_name = skill.name
        self.directory = skill.directory
        self.script = skill.tool_script
        self.definition = tool
        self.name = tool.name
        self.description = self._describe()
        self.parameters = tool.parameters
        self.always_ask = bool(SPENDS.search(skill.needs or ""))
        #: False only for the promotion's own test run: the skill is not
        #: a tool yet, so "is it still this tool?" has no answer.
        self.run_checks = True

    def remember(self, statements: list[str]) -> None:
        """Facts that may fill its arguments, said in its description.

        A recipe's inputs come back when load_skill delivers it, but a
        promoted tool is called without loading anything -- the model
        fills ``fan`` from the schema alone. So the facts go where it
        reads while filling it: the description, refreshed with each new
        conversation (SkillRegistry.recall_tool_inputs). The memory layer
        may carry them too, but twenty lines ranked by the first message
        bury a fact the request never names (notes/103).
        """
        self.remembered = list(statements)
        self.description = self._describe()

    def _describe(self) -> str:
        if not self.remembered:
            return self.definition.description
        return "\n".join([
            self.definition.description, "",
            "What you remember about this person that may fill its "
            "arguments (use what fits; what they asked for now wins):",
            *(f"- {statement}" for statement in self.remembered)])

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return (f"{self.name}({json.dumps(args, default=str)}) -- runs "
                f"{self.skill_name}/{self.script}")

    def sandbox(self) -> Any:
        """The session's bash sandbox: what contains bash contains this."""
        agent = getattr(self.skills, "_agent", None)
        registry = getattr(agent, "registry", None)
        bash = registry._tools.get("bash") if registry is not None else None
        return getattr(bash, "sandbox", None) or SubprocessSandbox()

    def _check_still_live(self) -> None:
        """Its skill may have changed since registration: pulled, set
        aside, repaired without its tool, deleted."""
        skill = self.skills.get(self.skill_name)
        if skill is None or skill.tool_name != self.name:
            raise ToolError(f"{self.name} is no longer a tool (its skill "
                            f"{self.skill_name!r} changed or was removed)")
        if skill.is_stale:
            raise ToolError(f"{self.name} has failed {skill.learned.failing} "
                            f"times in a row and is set aside -- do the task "
                            f"with your tools directly instead")
        if self.skills.is_disabled(self.skill_name):
            raise ToolError(f"{self.name} is off this session (its skill "
                            f"{self.skill_name!r} was pulled)")

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        if self.run_checks:
            self._check_still_live()
        self.definition.check_args(args)
        command = (interpreter_line(self.directory, self.script)
                   + self.definition.command(args))
        folder = self.directory.resolve()
        outside = () if folder.is_relative_to(ctx.cwd.resolve()) else (folder,)
        try:
            code, output = self.sandbox().execute(
                command, cwd=ctx.cwd, timeout=TOOL_TIMEOUT,
                **({"read_only": outside} if outside else {}))
        except CommandTimedOut as exc:
            detail = f" last output:\n{exc.salvaged}" if exc.salvaged else ""
            raise ToolError(f"{self.name} timed out after {TOOL_TIMEOUT}s."
                            f"{detail}") from None
        result = _head_tail(output.strip())
        if code != 0:
            raise ToolError(f"{result}\nexit code: {code}")
        return result or "(done; the script printed nothing)"


# ---- the proposal ---------------------------------------------------------------

PROMOTE_PROMPT = """\
A saved recipe has worked {worked} times in a row. Turn its script into \
ONE tool call, so the next run is a single structured call instead of \
loading the recipe and writing a shell command.

THE RECIPE (SKILL.md):
{skill_md}

THE SCRIPT ({script_name}):
{script}
{runs}
Write the tool as JSON:
- "name": snake_case, what it does (fan_control, weather_today). Not any \
of: {taken}.
- "description": what it does and when to use it, one or two sentences, \
from the recipe's description.
- "parameters": a JSON-schema object. One property per value the SCRIPT \
takes on its command line -- no more (the script must accept every one) \
and no fewer. Types: string, integer, number or boolean. Mark the ones \
the script cannot run without as required. Describe each in a few words. \
NEVER a parameter for a secret (a token, password or key): the script \
reads those itself, from its file or environment, and a tool's arguments \
pass through the model. Leave such options out of argv.
- "argv": the script's command-line words, in order, AFTER the script's \
path. A word is a literal ("--speed") or a parameter in braces \
("{{speed}}"). Put an optional parameter in a group with its flag: \
["--speed", "{{speed}}"] -- the group is left out when the parameter is \
not given. A boolean flag is a group of the flag and the boolean: \
["--dry-run", "{{dry_run}}"].
- "test": arguments for ONE run that repeats a use shown above, or the \
recipe's own example. It WILL run -- pick what the person has already \
done, never something new.

Reply with the JSON between these two lines, and nothing else:
=== TOOL ===
=== END ===

If the script cannot be one call (it needs a person mid-way, it takes a \
list, the recipe has several scripts), reply with exactly:
VERDICT: skip
REASON: one short sentence
"""

#: A proposal that broke a rule gets ONE more try, with the rule it broke
#: -- the save's test-and-fix cap, applied to the definition. Found live:
#: qwen3.8 put an optional ``--env`` outside a group on its first try.
FIX_PROPOSAL_PROMPT = """\
Your tool definition was refused:
{error}

What you wrote:
{reply}

Fix only that, and reply with the whole corrected JSON between these two \
lines, and nothing else:
=== TOOL ===
=== END ===
"""

#: At most this many earlier runs of the script shown to the proposal.
MAX_RUNS_SHOWN = 3


def parse_proposal(text: str) -> tuple[ToolDef, dict[str, Any]] | str:
    """The proposal reply -> (ToolDef, test args), or the skip reason.
    Raises SkillError for a reply that is neither."""
    text = re.sub(r"(?s)<think>.*?</think>", "", text)
    skip = re.search(r"^VERDICT:\s*skip\b", text, re.M | re.I)
    body = re.search(r"^=+\s*TOOL\s*=+\s*$(.*?)(?:^=+\s*END\s*=+\s*$|\Z)",
                     text, re.M | re.I | re.S)
    if body is None:
        if skip is not None:
            reason = re.search(r"^REASON:\s*(.+)$", text, re.M | re.I)
            return reason.group(1).strip() if reason else "the model judged it cannot be one call"
        raise SkillError("the proposal did not follow the format (no "
                         "=== TOOL === section)")
    raw = body.group(1).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkillError(f"the proposal is not valid JSON: {exc}") from None
    tool = parse_tool_def(data, "the proposal")
    test = data.get("test", {})
    if not isinstance(test, dict):
        raise SkillError("the proposal's test must be an object of arguments")
    try:
        tool.check_args(test)
    except ToolError as exc:
        raise SkillError(f"the proposal's test does not fit its own "
                         f"parameters: {exc}") from None
    return tool, test


@dataclass(slots=True)
class ToolOffer:
    """The promotion question: the definition, its test, what it cost."""

    skill: Skill
    tool: ToolDef
    test_args: dict[str, Any]
    #: True passed, False failed; ``test_output`` says what came back.
    passed: bool = False
    test_output: str = ""
    spent_in: int = 0
    spent_out: int = 0

    def view(self) -> dict[str, Any]:
        return {
            "skill": self.skill.name,
            "name": self.tool.name,
            "description": self.tool.description,
            "parameters": self.tool.parameters,
            "argv": [list(a) if isinstance(a, tuple) else a
                     for a in self.tool.argv],
            "command": " ".join(shlex.quote(w) for w in (
                interpreter_line(self.skill.directory, self.skill.tool_script
                                 or _only_script(self.skill))
                + self.tool.command(self.test_args))),
            "tool_json": self.tool.to_json(),
            "test": {"args": self.test_args, "passed": self.passed,
                     "output": _head_tail(self.test_output, 1500)},
            "spent": {"input": self.spent_in, "output": self.spent_out},
        }


def _only_script(skill: Skill) -> str:
    """The one script a recipe has, as ``scripts/<file>``; SkillError when
    it has none, or several (which one would the tool run?)."""
    folder = skill.directory / "scripts"
    found = sorted(p for p in folder.glob("*")
                   if p.is_file() and p.suffix in INTERPRETERS) if folder.is_dir() else []
    if len(found) != 1:
        raise SkillError(
            f"{skill.name} has {len(found) or 'no'} python or bash script"
            f"{'s' if len(found) != 1 else ''} -- a tool runs exactly one")
    return f"scripts/{found[0].name}"
