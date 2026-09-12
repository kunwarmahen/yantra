"""Graders for the researcher's cases: plain functions, (str) -> bool.

The escape hatch, and deliberately a narrow one. TOML cannot hold a
function, so a case that needs to look at the ANSWER points at a name in
here with ``check = "graders:cites_a_file"``.

Keep them boring. A grader is code that decides whether other code is
allowed to ship, so it should be the kind of thing you can be sure of by
reading it once. Anything subjective enough to need a model to judge it
belongs in ``judge()`` (yantra/evals.py), wired in from here -- and
before reaching for that, check whether a substring would have done.

Both of these grade the SHAPE of an answer rather than its content: that
a source is named, that a line is given. What the agent actually found is
the model's business, and pinning exact prose here would make the suite
fail every time a heading is renamed.
"""

from __future__ import annotations

import re

#: Files this package ships. Naming one is what "cite your source" means
#: when the source is on disk.
SHIPPED = ("agent.toml", "prompt.md", "SKILL.md", "outline.py", "cases.toml")


def cites_a_file(answer: str) -> bool:
    """The answer names a file it could have read."""
    return any(name.lower() in answer.lower() for name in SHIPPED)


def names_a_line_number(answer: str) -> bool:
    """The answer points at a line, the way `outline` reports one.

    'line 42', 'line: 42', 'L42' and '(42)' all count; a bare number in
    prose does not, because 'three sentences' would pass and should not.
    """
    return bool(re.search(r"(?:\bline\b\s*:?\s*|\bL|\()\d{1,4}\b",
                          answer, re.IGNORECASE))
