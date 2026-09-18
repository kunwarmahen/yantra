# 47 · What "seven of ten" is evidence of

Since [notes/35](35-roster-and-pass-rates.md), a case can be run more
than once and graded on how many of those runs passed. The gate prints
the count:

```
PASS  delegation-works-end-to-end  ✓✓✓ 3/3 runs (needs 3) · 95.0s · 29930 tok
```

Three green runs, threshold cleared, PASS. That line has been true and
slightly dishonest for as long as it has existed, and note 35 said so at
the time:

> "7 of 10" is a threshold, not a confidence interval. Three runs of a
> case tell you very little and the gate will happily print `✓✓✓` as
> though they told you a lot.

This note is about closing that gap without turning a gate into a
statistics package.

## The gap, in one number

A case that declares `min_pass_rate = 0.7` is making a claim: *this
behaviour holds about seven times in ten.* Run it three times, watch all
three pass, and the gate agrees.

Now ask what those three runs actually rule out. The answer is: almost
nothing. Three successes are consistent with a case whose true rate is
**44%** — worse than a coin, and less than two thirds of what the author
claimed. The run did not lie about anything. It just never said how
little it had bought.

The same arithmetic is worse one line up. A case run *once*, passing, is
consistent with a true rate of **21%**. Every single-run green line in
this repo's history has meant that, and none of them said so.

## The interval, and why Wilson

The fix is the standard one for a proportion: print the range of true
rates the observed count is consistent with.

The textbook interval — `p ± z·sqrt(p(1-p)/n)` — is wrong precisely
where an eval suite lives. At 3/3 it computes `p = 1`, `p(1-p) = 0`, and
a width of **zero**: perfect certainty, from three coin flips. At 0/5 it
does the same thing in the other direction. An interval that collapses
exactly at the ends is worse than no interval, because the ends are
where suites spend most of their time.

The **Wilson score interval** does not collapse. It has no table, no
dependency, and is four lines of arithmetic in
[`src/yantra/confidence.py`](../src/yantra/confidence.py):

| count | consistent with |
|---|---|
| 1/1 | 0.21 – 1.00 |
| 3/3 | 0.44 – 1.00 |
| 7/10 | 0.40 – 0.89 |
| 9/10 | 0.60 – 0.98 |
| 97/100 | 0.92 – 0.99 |

So the line becomes:

```
PASS  delegation-works-end-to-end  ✓✓✓ 3/3 runs (needs 3) · 0.44-1.00 at 95% · 95.0s
```

Same verdict, same count, one more fact.

## An interval is information, never a verdict

**Nothing here changes an exit code.** No case goes red because its
interval is wide, no suite fails because a claim is unsupported, and
`passed` is still the count comparison it has always been — 3 ≥ 3, not
0.44 ≥ 0.7.

This is the same rule [notes/42](42-two-runs-of-the-same-suite.md) holds
for comparisons, for the same reason. A gate that reddens on a statistic
has to be argued with every time an honest run lands two samples unlucky,
and the argument is always won by the person with the deadline. What a
statistic can do without that cost is *tell the operator what they
bought*, and leave the spending to them.

So the suite says it out loud at the end, and stops:

```
SUBSET GREEN · 1/1 passed · 3 runs · 29930 tokens
1 case(s) passed on evidence that does not reach the rate they claim
  delegation-works-end-to-end  3/3 · true rate could be as low as 0.44 ·
  claims 0.7 · --repeat 9 would settle it, all green
```

That is a real run of `examples/agents/researcher` against
`qwen3.8:27b`, with the delegation case temporarily declaring 0.7. Green
line, honest footnote, exit 0.

## Nine runs, and where that number comes from

`--repeat 9` is not a guess. With a perfect record the Wilson lower bound
simplifies to `n / (n + z²)`, so the smallest n whose lower bound reaches
a claimed rate is a closed form rather than a search:

| claim | perfect runs needed |
|---|---|
| 0.50 | 4 |
| 0.70 | 9 |
| 0.85 | 22 |
| 0.90 | 35 |
| 0.95 | 73 |

Two things fall out of that table, and both are arguments rather than
features.

The first: **a declared rate is a claim about a case, not a number to
tune.** An author who writes `min_pass_rate = 0.95` because it sounds
rigorous has committed to 73 green runs before anyone can say the case
holds at 0.95 — which, at 30k tokens a run, is most of a million tokens
to grade one behaviour. 0.7 costs nine. The cheaper claim is usually the
more honest one.

The second: **the number is a floor.** It assumes every run is green. A
case that fails one needs more, and a case whose true rate is genuinely
below its claim never gets there at all — which is the correct outcome
and takes a while to establish, exactly as it should.

The pre-run note now says this before any tokens are spent, at `--repeat 1`:

```
note: 1 case(s) declare a min_pass_rate below 1.0; one run each can only grade
them all-or-nothing -- --repeat 9 would hold the lowest claim among them at 95%,
all green
```

## The same arithmetic, pointed at a comparison

[notes/42](42-two-runs-of-the-same-suite.md) prints what moved between
two runs, and its most useful line is the one where the verdict did not
change but the count did:

```
rate down  flaky-case  9/10 → 6/10
```

That line has always been ambiguous, and now says which it is. 9/10 and
6/10 have overlapping intervals — both are consistent with the same
underlying rate — so the movement is not evidence that anything changed:

```
rate down  flaky-case  9/10 → 6/10  (intervals overlap: not evidence of a change)
```

The line still prints. The movement really did happen, and hiding it
would be the same mistake as intersecting away a missing case. What the
caveat prevents is the other failure: three people spending an afternoon
on a regression that was two unlucky die rolls.

One exception, and it matters: a case that reached **no model** has no
die to blame. A roster assertion is deterministic — same list, same
grading, every time — so it gets no interval at all, and a roster case
that changed verdict is always evidence that something in the *package*
changed. Dressing a fact up as a statistic is the failure mode
[notes/35](35-roster-and-pass-rates.md) already refused when it stopped
reporting a roster case as "0 of 5 runs".

## 95%, and why it is not a flag

One confidence level, fixed as a literal.

A flag here buys nothing anybody wants. At these sample sizes the
interval is wide at every level a person would pick, so a suite whose
reading changes between 90% and 99% is a suite that needs more runs, not
a different constant. And a configurable level is a number that ends up
in a package manifest, where it becomes something an author can lower
until the warning goes away — which is precisely the wrong direction for
a tool whose entire job is to say *you have less evidence than you
think*.

## What is not here yet

* **Nothing compares a case against its own history.** The interval is
  computed from one run's counts; twenty runs of the same case across
  twenty days are twenty separate intervals, and pooling them would be
  the genuinely interesting number. That needs a store keyed by case
  rather than by run ([notes/42](42-two-runs-of-the-same-suite.md)'s
  reports are keyed by run), and a decision about how far back to look.
* **`--repeat` still cannot stop early.** A case that has already failed
  four of its first five runs cannot clear 0.7 at n=9, and the suite runs
  the other four anyway. Sequential stopping is a real technique and a
  real way to bias a result if done carelessly.
* **The suite does not size itself.** Nothing reads `min_pass_rate`,
  works out that nine runs would hold it, and offers to buy them. A run
  count is the operator's money ([notes/41](41-a-gate-you-can-point.md)),
  and a gate that quietly multiplies the bill by nine would be spending
  it for them.
* **Nothing measures a case's cost against its claim.** "73 runs at 30k
  tokens" is computable from the numbers already on screen, and would
  make the argument above concrete at the moment an author writes 0.95.
