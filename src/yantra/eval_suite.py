"""A package's own acceptance gate: ``evals/cases.toml`` -> ``EvalCase``.

An agent package is a directory you can hand to someone (notes/31), and
it may ship its own Python (notes/32). Both of those move trust toward
the author, and so far the only thing answering "does this agent
actually work?" has been the author saying so. That is worth exactly
what a model's claim that the tests passed is worth. VERIFY, DON'T
TRUST -- builder mode's rule, aimed at the agent instead of the project.

So a package may carry the evidence:

    researcher/
    |-- agent.toml
    |-- prompt.md
    `-- evals/
        |-- cases.toml    the cases, declaratively
        `-- graders.py    (optional) plain functions: (str) -> bool

``EvalCase`` has two callable fields and TOML cannot hold a function, so
the format is the same shape as the manifest's: declarative keys, plus
one narrow escape hatch to Python. ``check = "graders:cites_a_source"``
is ``module:function``, resolved against this suite directory with the
same by-path loader package tools use.

THE PROMPT IS UNDER TEST, so a case may not replace it. ``system`` is a
field on ``EvalCase`` and deliberately not a key here: a suite that
swapped in its own system prompt would be grading some other agent and
reporting the verdict as this one's. What a case may vary is the task
and the ceilings.

TWO KINDS OF ASSERTION, and the cheap one is not the trajectory.
``required_tools``/``forbidden_tools`` grade what RAN, which takes a run;
``has_tools``/``lacks_tools`` grade the ROSTER -- what the agent is
offered at all -- which takes nothing. "This agent has no way to write to
disk" is the assertion an author usually means when they forbid
``write_file``, and it is the one a trajectory can never make: a tool that
was available and went unused looks exactly like a tool that was absent.
A case whose only assertions are roster ones may omit ``user_message``
entirely; it costs zero tokens and needs no model.

``subagent_has_tools``/``subagent_lacks_tools`` are the same claim about a
DECLARED CHILD, keyed by its name. They exist because the parent's roster
is a ceiling and not a floor: a child is built from the parent's registry,
so ``lacks_tools = ["bash"]`` has always covered the whole package, while
a fact-checker deliberately kept off the network sits well inside a
ceiling that permits ``web_fetch`` -- and widening it moved nothing any
assertion could see.

``subagent_prompt_contains``/``subagent_prompt_lacks``, ``subagent_model``
and ``subagent_iterations_at_most`` grade the REST of what a manifest
decided about that child -- the prompt file it was handed, the model it
runs on, and how long it may go on. All four keys live in the same
``[[subagent]]`` table and change in the same one-line diff; the tool
list only came first because it is the one that changes what a package
can reach. Prose is matched as a case-insensitive SUBSTRING and never as
a pattern: a prompt is written for a model to read, and a key inviting
``*quote*line*`` would have authors debugging a regex against an
instruction file.

``min_pass_rate`` is the other half of being honest about a die roll. A
case declares the rate it claims to hold at ("7 of 10" is 0.7), and the
OPERATOR decides how many runs to buy -- ``repeat`` is deliberately not a
key here, because how much a gate costs to run is the money of whoever is
running it.

GRADERS RESOLVE AT LOAD TIME, never mid-run. A typo'd function name
discovered on case seven of nine has already spent six cases' worth of
real tokens to tell you something that was knowable before the first
request. Every ``check`` is imported, found and signature-checked while
the file is being read.

UNKNOWN KEYS ARE ERRORS, for the manifest's reason (package.py): a
misspelled ``forbidden_tools`` that is quietly ignored turns a gate into
a decoration, and the failure mode is a suite that passes because it
checked nothing.

THE SUITE RUNS SOMEBODY'S PYTHON -- ``graders.py`` is imported as you,
like ``tools/``. The security paragraph in tools/discover.py governs
this module too, unchanged: a package path comes from a human's hands,
never from a payload.
"""

from __future__ import annotations

import inspect
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from yantra.errors import ConfigError
from yantra.evals import EVERY_CHILD, EvalCase
from yantra.fingerprint import case_fingerprint
from yantra.tools.discover import load_module_file

#: The conventional suite directory inside a package.
SUITE_DIR = "evals"

#: The file that holds the cases.
CASES = "cases.toml"

#: Every key a ``[[case]]`` may hold. Exhaustive on purpose.
CASE_KEYS = frozenset({
    "id", "description", "user_message", "required_tools",
    "forbidden_tools", "has_tools", "lacks_tools", "subagent_has_tools",
    "subagent_lacks_tools", "subagent_prompt_contains",
    "subagent_prompt_lacks", "subagent_model",
    "subagent_iterations_at_most", "max_tokens", "max_iterations",
    "min_pass_rate", "check",
})

#: The roster keys: everything gradeable with no model and no request.
#: Named as a set because three places ask the same question -- may this
#: case omit ``user_message``, does it assert anything at all, and which
#: keys does the "no task" error tell the author to keep.
ROSTER_KEYS = ("has_tools", "lacks_tools", "subagent_has_tools",
               "subagent_lacks_tools", "subagent_prompt_contains",
               "subagent_prompt_lacks", "subagent_model",
               "subagent_iterations_at_most")

#: Keys that only mean something once a model has RUN. A case with no
#: ``user_message`` never reaches one, so any of these on such a case is a
#: check that would never be performed.
TRAJECTORY_KEYS = frozenset({
    "required_tools", "forbidden_tools", "max_tokens", "max_iterations",
    "min_pass_rate", "check",
})

#: fnmatch metacharacters. Allowed in the roster keys (a roster is a SET,
#: and "nothing that writes" is a shape); refused in the trajectory keys,
#: where a pattern would silently match nothing and turn an assertion into
#: a decoration -- the exact failure this format exists to prevent.
GLOB_CHARS = "*?["

#: Keys that exist on ``EvalCase`` and are refused here, with the reason
#: the error will give. Naming them beats "unknown key": the author did
#: not typo, they asked for something the format declines to offer.
REFUSED_KEYS: dict[str, str] = {
    "system": "the package's own prompt is what this suite is testing; a "
              "case that replaced it would grade some other agent",
    "setup": "per-case Python wiring is a library feature (EvalCase.setup), "
             "not a package one -- a manifest that could install arbitrary "
             "machinery into the agent under test is no longer describing it",
    "check_answer": "spell it 'check' and point at a function: "
                    'check = "graders:your_function"',
    "repeat": "how many times a case runs is the operator's money, not the "
              "author's: declare min_pass_rate here and let whoever runs "
              "the gate pass --repeat N",
}


def _fail(path: Path, message: str) -> None:
    raise ConfigError(f"{path}: {message}")


def _str(entry: dict[str, Any], key: str, path: Path, where: str,
         *, required: bool = False) -> str | None:
    value = entry.get(key)
    if value is None:
        if required:
            _fail(path, f"{where} has no {key}")
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(path, f"{where}.{key} must be a non-empty string")
    return value


def _int(entry: dict[str, Any], key: str, path: Path,
         where: str) -> int | None:
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, f"{where}.{key} must be an integer")
    if value <= 0:
        _fail(path, f"{where}.{key} must be positive (got {value})")
    return value


def _rate(entry: dict[str, Any], key: str, path: Path,
          where: str) -> float | None:
    """A pass rate: a number in (0, 1]. ``1`` and ``1.0`` both mean every run."""
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, f"{where}.{key} must be a number between 0 and 1 "
                    f"(0.7 means 'seven runs in ten')")
    if not 0 < value <= 1:
        _fail(path, f"{where}.{key} must be greater than 0 and at most 1 "
                    f"(got {value}); it is a FRACTION of runs, so nine "
                    f"cases in ten is 0.9, not 9")
    return float(value)


def _str_list(entry: dict[str, Any], key: str, path: Path, where: str,
              *, patterns: bool = False) -> list[str]:
    value = entry.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        _fail(path, f"{where}.{key} must be a list of strings")
    if not patterns:
        # A pattern here would match nothing and report nothing: the
        # trajectory is a list of names that EXECUTED, and 'write_*' is not
        # one of them. Refusing it beats a green case that checked nothing.
        globbed = [v for v in value if any(c in v for c in GLOB_CHARS)]
        if globbed:
            _fail(path, f"{where}.{key} takes exact tool names, not patterns "
                        f"({', '.join(globbed)}): this key grades what RAN, "
                        f"and a pattern would match nothing. For a claim "
                        f"about the tool LIST, use has_tools/lacks_tools, "
                        f"which do take patterns")
    return list(value)


def _child_key(child: str, key: str, path: Path, where: str) -> None:
    """The KEY of every subagent_ table: a name, or ``*``. Never a pattern.

    A pattern key that matched no child would be a case that checked
    nothing while reading as caution (notes/44), which is the failure the
    whole family exists to prevent.
    """
    if child != EVERY_CHILD and any(c in child for c in GLOB_CHARS):
        _fail(path, f"{where}.{key} takes a sub-agent NAME, not a pattern "
                    f"({child}): a key matching no child would assert "
                    f'nothing. Use "{EVERY_CHILD}" for every declared '
                    f"sub-agent, or name them one at a time")


def _child_str_table(entry: dict[str, Any], key: str, path: Path,
                     where: str) -> dict[str, str]:
    """``{ fact_checker = "gemma4:12b" }`` -> the same, checked.

    One string per child rather than a list: a child runs on one model,
    and "" is the claim that it names none of its own (notes/50).
    """
    value = entry.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        _fail(path, f"{where}.{key} must be a table keyed by sub-agent name: "
                    f'{key} = {{ fact_checker = "gemma4:12b" }}')
    table: dict[str, str] = {}
    for child, slug in value.items():
        _child_key(child, key, path, where)
        if not isinstance(slug, str):
            _fail(path, f"{where}.{key}.{child} must be a model slug, or "
                        f'"" for "no model of its own"')
        table[child] = slug
    return table


def _child_int_table(entry: dict[str, Any], key: str, path: Path,
                     where: str) -> dict[str, int]:
    """``{ fact_checker = 12 }`` -> the same, checked. A CEILING."""
    value = entry.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        _fail(path, f"{where}.{key} must be a table keyed by sub-agent name: "
                    f"{key} = {{ fact_checker = 12 }}")
    table: dict[str, int] = {}
    for child, cap in value.items():
        _child_key(child, key, path, where)
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:
            _fail(path, f"{where}.{key}.{child} must be a whole number of "
                        f"iterations, at least 1")
        table[child] = cap
    return table


def _child_table(entry: dict[str, Any], key: str, path: Path,
                 where: str, *, what: str = "tool names or patterns"
                 ) -> dict[str, list[str]]:
    """``{ fact_checker = ["web_*"] }`` -> the same, checked.

    A TABLE rather than a flat list of ``child:tool`` strings, because the
    two rosters are two objects and one namespace holding both would make
    ``lacks_tools`` ambiguous about which it meant -- the question this
    key exists to answer.

    The KEY is a child's name or ``*``; patterns are refused there for the
    reason ``subagent_failures`` gives (a key matching no child is a case
    that checked nothing). The VALUES are tool patterns, exactly as in
    ``has_tools``, because "nothing that writes" is a shape.
    """
    value = entry.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        _fail(path, f"{where}.{key} must be a table keyed by sub-agent name: "
                    f'{key} = {{ fact_checker = ["web_*"] }}')
    table: dict[str, list[str]] = {}
    for child, patterns in value.items():
        _child_key(child, key, path, where)
        if not isinstance(patterns, list) \
                or any(not isinstance(v, str) for v in patterns):
            _fail(path, f"{where}.{key}.{child} must be a list of {what}")
        if not patterns:
            # An empty list is a child named and then asked nothing. It
            # reads as caution and grades as decoration.
            _fail(path, f"{where}.{key}.{child} is empty: naming a sub-agent "
                        f"and asserting nothing about it is a check that "
                        f"cannot fail")
        table[child] = list(patterns)
    return table


def _grader(reference: str, suite: Path, path: Path, where: str):
    """``module:function`` -> the callable, or an error naming the file.

    Resolved HERE rather than at first use: see the module docstring on
    why a broken reference must cost zero tokens to discover.
    """
    module_name, sep, function_name = reference.partition(":")
    if not sep or not module_name or not function_name:
        _fail(path, f"{where}.check must be 'module:function' (for example "
                    f'"graders:cites_a_source"), not {reference!r}')
    if "/" in module_name or module_name.endswith(".py"):
        _fail(path, f"{where}.check names a MODULE, not a file: write "
                    f'"{Path(module_name).stem}:{function_name}"')

    source = suite / f"{module_name}.py"
    if not source.is_file():
        _fail(path, f"{where}.check points at {source}, which does not exist")

    module = load_module_file(source)   # ConfigError names the file if it raises
    function = getattr(module, function_name, None)
    if function is None:
        defined = sorted(
            name for name, value in vars(module).items()
            if callable(value) and not name.startswith("_")
            and getattr(value, "__module__", None) == module.__name__
        )
        _fail(path, f"{where}.check: {source.name} defines no "
                    f"{function_name!r}" + (f" (it defines: "
                    f"{', '.join(defined)})" if defined else ""))
    if not callable(function):
        _fail(path, f"{where}.check: {module_name}.{function_name} is not "
                    f"callable")
    try:
        inspect.signature(function).bind("")
    except TypeError:
        _fail(path, f"{where}.check: {module_name}.{function_name} must take "
                    f"the answer as its one argument -- (str) -> bool")
    return function


def find_suite(where: Path) -> Path | None:
    """The suite directory at or inside ``where``, or None if there is none.

    Accepts a package root, the ``evals/`` directory, or ``cases.toml``
    itself, so every way an operator might name it lands in the same place.
    """
    where = Path(where).expanduser()
    if where.is_file():
        return where.parent if where.name == CASES else None
    for candidate in (where / SUITE_DIR, where):
        if (candidate / CASES).is_file():
            return candidate
    return None


def load_cases(where: Path) -> list[EvalCase]:
    """Read a suite and return its cases, with every grader already resolved.

    Touches no network and builds no agent: a suite that cannot run should
    say so before a model is reached, and before the first case's tokens
    are spent on the seventh case's typo.
    """
    suite = find_suite(where)
    if suite is None:
        target = Path(where).expanduser()
        raise ConfigError(
            f"no {SUITE_DIR}/{CASES} in {target}" if target.is_dir()
            else f"not an eval suite: {target}"
        )
    path = (suite / CASES).resolve()

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from None
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read: {exc}") from None

    unknown_tables = sorted(set(data) - {"case"})
    if unknown_tables:
        _fail(path, f"unknown table(s): {', '.join(unknown_tables)} "
                    f"(this file holds [[case]] entries and nothing else)")

    raw = data.get("case", [])
    if isinstance(raw, dict):        # a single [case] table instead of [[case]]
        raw = [raw]
    if not isinstance(raw, list):
        _fail(path, "[[case]] must be a list of tables")
    if not raw:
        _fail(path, "no [[case]] entries -- an empty suite is a gate that "
                    "passes everything")

    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            _fail(path, f"[[case]] entry {index} must be a table")
        where_label = f"case[{index}]"
        case_id = _str(entry, "id", path, where_label, required=True)
        where_label = f"case '{case_id}'"

        for key, reason in REFUSED_KEYS.items():
            if key in entry:
                _fail(path, f"{where_label}.{key} is not a key here: {reason}")
        unknown = sorted(set(entry) - CASE_KEYS)
        if unknown:
            _fail(path, f"unknown key(s) in {where_label}: "
                        f"{', '.join(unknown)} "
                        f"(known: {', '.join(sorted(CASE_KEYS))})")

        if case_id in seen_ids:
            # Results are read and compared BY id -- two cases answering to
            # one name makes a report that cannot be acted on.
            _fail(path, f"duplicate case id {case_id!r}")
        seen_ids.add(case_id)

        has_tools = _str_list(entry, "has_tools", path, where_label,
                              patterns=True)
        lacks_tools = _str_list(entry, "lacks_tools", path, where_label,
                                patterns=True)
        subagent_has = _child_table(entry, "subagent_has_tools", path,
                                    where_label)
        subagent_lacks = _child_table(entry, "subagent_lacks_tools", path,
                                      where_label)
        prompt_has = _child_table(entry, "subagent_prompt_contains", path,
                                  where_label, what="phrases")
        prompt_lacks = _child_table(entry, "subagent_prompt_lacks", path,
                                    where_label, what="phrases")
        child_model = _child_str_table(entry, "subagent_model", path,
                                       where_label)
        child_caps = _child_int_table(entry, "subagent_iterations_at_most",
                                      path, where_label)
        message = _str(entry, "user_message", path, where_label)
        if not message and not (has_tools or lacks_tools
                                or subagent_has or subagent_lacks
                                or prompt_has or prompt_lacks
                                or child_model or child_caps):
            # A roster assertion is the ONE thing a case can do without a
            # task, because it grades the agent rather than a trajectory.
            # Anything else with no message asserts nothing at all.
            _fail(path, f"{where_label} has no user_message -- only a case "
                        f"whose assertions are all about the roster "
                        f"({', '.join(ROSTER_KEYS)}) may leave it out, "
                        f"because that one needs no model")
        if not message:
            # Every other key grades a TRAJECTORY, and this case has none.
            # Silently ignoring one would be a suite that checks less than
            # its author believes -- the failure this whole format is built
            # against -- so say which key has nothing to grade.
            stray = sorted(set(entry) & TRAJECTORY_KEYS)
            if stray:
                _fail(path, f"{where_label} has no user_message, so "
                            f"{', '.join(stray)} would grade a trajectory "
                            f"that never happens: give the case a task, or "
                            f"keep it to {', '.join(ROSTER_KEYS)}")
        rate = _rate(entry, "min_pass_rate", path, where_label)
        check = entry.get("check")
        cases.append(EvalCase(
            id=case_id,
            description=_str(entry, "description", path, where_label) or "",
            user_message=message or "",
            required_tools=_str_list(entry, "required_tools", path, where_label),
            forbidden_tools=_str_list(entry, "forbidden_tools", path,
                                      where_label),
            has_tools=has_tools,
            lacks_tools=lacks_tools,
            subagent_has_tools=subagent_has,
            subagent_lacks_tools=subagent_lacks,
            subagent_prompt_contains=prompt_has,
            subagent_prompt_lacks=prompt_lacks,
            subagent_model=child_model,
            subagent_iterations_at_most=child_caps,
            max_tokens=_int(entry, "max_tokens", path, where_label),
            max_iterations=_int(entry, "max_iterations", path, where_label),
            min_pass_rate=1.0 if rate is None else rate,
            check_answer=(None if check is None else
                          _grader(_str(entry, "check", path, where_label),
                                  suite, path, where_label)),
            fingerprint=case_fingerprint(
                entry, (suite / f"{check.partition(':')[0]}.py"
                        if isinstance(check, str) else None)),
        ))
    return cases


# ---- the other direction: a case somebody can paste into a suite -----------


def render_case(case: EvalCase) -> str:
    """One ``EvalCase`` as a ``[[case]]`` block, ready to append to a suite.

    THE WRITER LIVES BESIDE THE READER. A host that produced this text
    itself -- a service turning a failed run into a regression case, say --
    would be a second implementation of a format whose parser is up there,
    and the two would drift on the first thing that needed quoting. So the
    round trip is one module's problem and one module's test.

    Only what a case actually SAYS is written. A default is not an
    assertion, and a block full of ``min_pass_rate = 1.0`` reads as though
    somebody chose it.

    ``check_answer`` and ``setup`` hold PYTHON, which is why the manifest
    format spells the first as ``check = "module:function"`` and refuses
    the second outright. A resolved callable cannot be turned back into
    the reference it came from, so rendering a case that carries one
    RAISES rather than quietly dropping an assertion -- a suite that
    checked less than its author believed is the failure this whole format
    is built against.
    """
    if case.check_answer is not None or case.setup is not None:
        raise ConfigError(
            f"case {case.id!r} carries Python (check_answer or setup) and "
            f"cannot be written back as TOML: a resolved function is not "
            f"the 'graders:name' reference it was loaded from. Write the "
            f"block by hand, or render a case without one")

    lines = ["[[case]]", f"id = {_toml_str(case.id)}"]
    if case.description:
        lines.append(f"description = {_toml_str(case.description)}")
    if case.user_message:
        lines.append(f"user_message = {_toml_str(case.user_message)}")
    for key in ("required_tools", "forbidden_tools", "has_tools",
                "lacks_tools"):
        values = getattr(case, key)
        if values:
            lines.append(f"{key} = {_toml_list(values)}")
    for key in ("subagent_has_tools", "subagent_lacks_tools",
                "subagent_prompt_contains", "subagent_prompt_lacks"):
        table = getattr(case, key)
        if table:
            inner = ", ".join(f"{_toml_key(child)} = {_toml_list(patterns)}"
                              for child, patterns in table.items())
            lines.append(f"{key} = {{ {inner} }}")
    if case.subagent_model:
        inner = ", ".join(f"{_toml_key(child)} = {_toml_str(slug)}"
                          for child, slug in case.subagent_model.items())
        lines.append(f"subagent_model = {{ {inner} }}")
    if case.subagent_iterations_at_most:
        inner = ", ".join(f"{_toml_key(child)} = {cap}" for child, cap
                          in case.subagent_iterations_at_most.items())
        lines.append(f"subagent_iterations_at_most = {{ {inner} }}")
    if case.max_tokens is not None:
        lines.append(f"max_tokens = {case.max_tokens}")
    if case.max_iterations is not None:
        lines.append(f"max_iterations = {case.max_iterations}")
    if case.min_pass_rate != 1.0:
        lines.append(f"min_pass_rate = {case.min_pass_rate}")
    return "\n".join(lines) + "\n"


def _toml_str(value: str) -> str:
    """A TOML string, in whichever of the two forms stays readable.

    A multi-line literal for anything with a newline in it, because the
    cases people write by hand use those and a generated one that did not
    would look foreign in the same file. Everything else is a basic
    string. Both escape what they must: a description quoting an error
    message is exactly where a naive quoter produces a file that no longer
    parses.
    """
    if "\n" in value:
        body = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        # A leading newline after the opening delimiter is trimmed by the
        # format, which is what makes the block line up in the file.
        newline = "" if value.startswith("\n") else "\n"
        # A value ending in a quote would run into the delimiter.
        tail = "\\\n" if body.endswith('"') else ""
        return f'"""{newline}{body}{tail}"""'
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\t", "\\t").replace("\r", "\\r"))
    return f'"{escaped}"'


def _toml_key(name: str) -> str:
    """A bare key where TOML allows one, quoted where it does not ("*")."""
    if name and all(c.isalnum() or c in "-_" for c in name):
        return name
    return _toml_str(name)


def _toml_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"
