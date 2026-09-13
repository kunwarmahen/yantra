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
from pathlib import Path
from typing import Any

from yantra.errors import ConfigError
from yantra.evals import EvalCase
from yantra.tools.discover import load_module_file

#: The conventional suite directory inside a package.
SUITE_DIR = "evals"

#: The file that holds the cases.
CASES = "cases.toml"

#: Every key a ``[[case]]`` may hold. Exhaustive on purpose.
CASE_KEYS = frozenset({
    "id", "description", "user_message", "required_tools",
    "forbidden_tools", "has_tools", "lacks_tools", "max_tokens",
    "max_iterations", "min_pass_rate", "check",
})

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
        message = _str(entry, "user_message", path, where_label)
        if not message and not (has_tools or lacks_tools):
            # A roster assertion is the ONE thing a case can do without a
            # task, because it grades the agent rather than a trajectory.
            # Anything else with no message asserts nothing at all.
            _fail(path, f"{where_label} has no user_message -- only a case "
                        f"whose assertions are all about the roster "
                        f"(has_tools/lacks_tools) may leave it out, because "
                        f"that one needs no model")
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
                            f"keep it to has_tools/lacks_tools")
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
            max_tokens=_int(entry, "max_tokens", path, where_label),
            max_iterations=_int(entry, "max_iterations", path, where_label),
            min_pass_rate=1.0 if rate is None else rate,
            check_answer=(None if check is None else
                          _grader(_str(entry, "check", path, where_label),
                                  suite, path, where_label)),
        ))
    return cases
