# 48 · What the run cost, written down

A report ([notes/42](42-two-runs-of-the-same-suite.md)) records what a
suite run did: which cases passed, how many attempts each took, how many
tokens it burned. The last of those is the one people squint at, and it
is measured in the wrong unit.

```
tokens: 47236 → 8423 (-38813)
```

Tokens are a fine unit for one model. Across two, they are not a unit at
all — which is a problem, because the comparison this feature exists for
is *the same suite, a cheaper model*. A run that burns 20% more tokens on
a model that costs a tenth as much is an enormous win rendered as a
regression.

[notes/21](21-cost-accounting.md) has priced tokens since long before
any of this, and [notes/34](34-budgets.md) uses those prices to stop a
runaway turn. Nothing had joined the two to the report.

## Why joining them is not a one-liner

Note 42 named the trap when it named the gap:

> doing it wrong — pricing both sides with today's table — would quietly
> rewrite history every time a vendor changes a price.

A report is a file somebody keeps. Its whole value is that it does not
change. Store the token counts and price them at *read* time and you get
a file whose answer to "what did that run cost" drifts silently every
time a vendor posts a new price page — including downward, so last
month's expensive experiment quietly becomes cheap and the decision it
justified becomes unreadable.

**DOLLARS ARE WRITTEN DOWN, NEVER RECOMPUTED.** The figure is priced
when the run happens, stored beside the tokens, and read back verbatim. A
run costs what it cost on the day it ran.

It is also the only place the arithmetic can be done correctly. A report
keeps one token total per case; the price of a trajectory depends on the
split between input, output, cache reads and cache writes, which are four
different rates ([notes/21](21-cost-accounting.md)). That breakdown exists
in `agent.total_usage` while the case is running and nowhere afterwards,
so pricing at write time is not just more honest — it is the only version
that is right.

## Zero and unknown are different numbers

`pricing.cost_now()` returns one of three things, and the middle one is
the reason it exists rather than being a call to `cost_of`:

* **`0.0`** — a provider that bills nothing. A local model on your own
  GPU cost you nothing, and that is a fact rather than a gap.
* **`None`** — a hosted model with no entry in the price table. Unknown.
  The caller omits the figure.
* **a float** — the weighted sum.

Collapsing the first two into `$0.00` is the failure
[notes/21](21-cost-accounting.md) refused when it wrote "a wrong price
silently shown is worse than no price", and it fails in the direction
that matters: an unpriced hosted model would look free, on the exact
screen somebody uses to decide what to run all week.

So a run against a local model prints no dollar figure at all:

```
$ yantra --agent examples/agents/researcher --eval --provider ollama --case "outlines*"
  PASS  outlines-before-reading  13.9s · 5645 tok · 2 it · outline

SUBSET GREEN · 1/1 passed · 5 case(s) not run · 5645 tokens
```

…and the report it wrote records `"usd": 0.0` for that case. Nothing on
screen, because `$0.0000` beside a two-minute suite reads as a broken
meter rather than as the free road working — and a real zero in the file,
because a later comparison against a metered run needs to know that this
side was free rather than unmeasured.

Those same 5,645 tokens, priced against this repo's table for
`claude-sonnet-5` at list, come to **between $0.02 and $0.04** depending
on how they split between input and output — which is arithmetic on the
counts above rather than a run anybody paid for, and is also a small
demonstration of why the split has to be priced while it still exists.
The argument is in the range: six cents a suite is nothing to think about,
and nine repeats of a flaky case
([notes/47](47-what-seven-of-ten-is-evidence-of.md)) on a frontier model
is a different conversation.

## What a comparison says now

```
tokens: 47236 → 8423 (-38813)
cost: $1.2500 → $0.0421 (-1.2079)
```

Each side priced on the day it ran. When the two runs used different
models, this is the only line on the page that means anything financial —
and it means it *without* the two sides having to agree about today's
prices, because neither of them is consulting today's prices.

A report written before this key existed has no figure, and says so
rather than claiming a free run:

```
cost: that run recorded no dollar figure (unpriced model, or written before costs were kept)
```

The format tag did not move for this. `usd` is a key an older reader
ignores and a newer one finds missing, which is exactly the case
[notes/42](42-two-runs-of-the-same-suite.md)'s versioning rule set aside
as not worth a bump: *adding a key that readers may ignore does not bump
it; changing what a key means does.*

## What is not here yet

* **Nothing records WHICH price table was used.** The figure is stored;
  the prices behind it are not. Two runs priced a year apart are
  comparable as money spent, and neither one can tell you whether the
  gap was the agent getting cheaper or the vendor getting dearer. The
  fix is to store the four rates beside the figure, which is four more
  numbers per case and an argument about per-case versus per-run.
* **A local run's zero hides real money.** Electricity and a GPU are not
  free; they are just not billed per token. `bills_nothing` means "this
  provider does not invoice you", which is the honest scope for a harness
  and is not the same sentence as "this was free".
* **Budgets and reports still do not meet.** `max_usd_per_turn`
  ([notes/34](34-budgets.md)) stops a turn; the report records what turns
  cost. A suite could now say "these 6 cases cost $0.38, and your package
  ceiling is $0.50 per turn" — nothing does.
* **No cost per case in the rendering.** The figure is stored per case
  and only the total is printed. The expensive case is usually the
  interesting one, and a column would say which it was.
