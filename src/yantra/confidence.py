"""What "7 of 10" is actually evidence of.

A trajectory is a die roll, so a suite that runs a case ten times and
counts the greens is doing statistics whether it says so or not
(notes/35). Up to here it said `7/10` and stopped, which is a THRESHOLD
-- did the count clear the bar the author declared -- and a threshold
answers a different question from the one a person asks when they look
at the number:

    Would it come out that way again?

Ten runs cannot answer that precisely, and three runs can barely answer
it at all. The honest thing is not to hide the fraction and not to dress
it up either: print the fraction, and beside it the range of true rates
that fraction is consistent with.

**The Wilson score interval**, which is the standard interval for a
proportion at small n. The obvious one -- p ± z·sqrt(p(1-p)/n) -- is
wrong exactly where an eval suite lives: at 3/3 it computes a width of
zero and claims certainty from three coin flips, and at 0/5 it does the
same in the other direction. Wilson does not collapse at the ends, needs
no table, and is four lines of arithmetic:

    3/3   ->  0.44 .. 1.00     a perfect record, consistent with 44%
    7/10  ->  0.40 .. 0.89
    9/10  ->  0.60 .. 0.98
    97/100 ->  0.92 .. 0.99

The first line is the one worth staring at. A case that declares
``min_pass_rate = 0.7``, run three times, passing all three, is graded
PASS -- and the evidence does not distinguish it from a case that holds
44% of the time. The gate was never lying; nobody had written down what
the number meant.

AN INTERVAL IS INFORMATION, NEVER A VERDICT. Nothing here changes an
exit code, and no case goes red because its interval is wide. That rule
comes from the same place as notes/42's "a record, not a baseline": a
gate that reddens on a statistic is a gate that has to be argued with
every time an honest run lands two runs unlucky. What the interval does
is let the OPERATOR see that a suite of three-run samples is not the
evidence they thought they had bought, and act on it with --repeat,
which is theirs to spend.

95% AND NOT A FLAG. One confidence level, fixed, because a knob here
buys nothing: at these sample sizes the interval is wide at any level
anybody would pick, and a suite whose verdict reads differently at 90%
than at 99% is a suite that needs more runs rather than a different
constant. The z it needs is a literal; nothing imports a stats library.
"""

from __future__ import annotations

import math

#: Two-sided 95%. See the module docstring for why this is not a flag.
Z_95 = 1.959963984540054


def wilson_bounds(passes: int, attempts: int, z: float = Z_95
                  ) -> tuple[float, float]:
    """The Wilson score interval for ``passes`` of ``attempts``.

    Returns ``(0.0, 1.0)`` for no attempts at all -- no samples means no
    information, which is exactly what the whole range says.
    """
    if attempts <= 0:
        return (0.0, 1.0)
    p = passes / attempts
    z2 = z * z
    denom = 1 + z2 / attempts
    centre = p + z2 / (2 * attempts)
    spread = z * math.sqrt(p * (1 - p) / attempts + z2 / (4 * attempts ** 2))
    lo = (centre - spread) / denom
    hi = (centre + spread) / denom
    return (max(0.0, lo), min(1.0, hi))


def perfect_runs_needed(min_pass_rate: float, z: float = Z_95) -> int:
    """How many runs, ALL of them green, before the lower bound reaches
    ``min_pass_rate``.

    The friendliest possible answer to "how many should I buy?": it
    assumes a perfect record, so it is a floor. A case that fails one run
    needs more than this, and no number here can promise it will ever get
    there -- a case whose true rate is under its claim never will.

    With a perfect record the algebra collapses to ``n / (n + z²)``, so
    this is a closed form rather than a search: 9 runs for a claim of 0.7,
    22 for 0.85, 73 for 0.95. Those numbers are the argument for why a
    declared rate is a claim about a case and not a number to tune.
    """
    if min_pass_rate >= 1:
        # A claim of 1.0 has no n: n/(n+z²) approaches 1 and never gets
        # there. Saying "infinite" is more useful than a large integer
        # that looks like advice, so the caller gets 0 and says so.
        return 0
    if min_pass_rate <= 0:
        return 1
    z2 = z * z
    return max(1, math.ceil(min_pass_rate * z2 / (1 - min_pass_rate)))


def overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Whether two intervals share any value.

    The question behind a comparison: 9/10 then 6/10 LOOKS like a
    regression and is not evidence of one, because both counts are
    consistent with the same underlying rate. Two runs whose intervals
    overlap have not shown that anything changed (notes/42 prints the
    movement anyway -- this only says what it is worth).
    """
    return a[0] <= b[1] and b[0] <= a[1]


def describe(passes: int, attempts: int, z: float = Z_95) -> str:
    """"0.40-0.89 at 95%" -- the interval as it appears beside a tally."""
    lo, hi = wilson_bounds(passes, attempts, z)
    return f"{lo:.2f}-{hi:.2f} at 95%"
