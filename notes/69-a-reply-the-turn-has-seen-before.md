# 69 — A reply the turn has seen before

[Note 36](36-a-warning-before-the-stop.md) built the warning that comes
before a turn is stopped for money. It prices **the call about to go
out**: the context it carries is known the moment the request is put
together, so its cost can be worked out before anything is billed. The
one thing it could not price was the **reply**, because nobody knows how
long a reply will be until it arrives. Every note since has listed that
as open: "nothing forecasts the reply".

[Note 64](64-a-price-for-the-free-road.md) measured what that costs.
Eight of twenty turns on `qwen3.8:latest` were never warned at all. In
each of them, one call's reply (mostly the model *thinking* before it
answered) took the turn from comfortably under its ceiling to over it in
a single step. The forecast had said "this next call fits", and it did
fit. The money went out in the reply.

## A turn is not a stranger to itself

Nobody can know the next reply's length. But a turn has already had
replies, and they are a good guide. A model that thinks for three
thousand tokens before every tool call does it on the next call too.

**THE FORECAST ADDS THE LARGEST REPLY THIS TURN HAS HAD.** The call
about to go out is now priced as its context *plus* a reply as long as
the longest one so far. On the first call of a turn there are no replies
to go on, so that call is still forecast on its input alone. Nothing is
invented.

**THE LARGEST, NOT THE AVERAGE OR THE LAST.** Guessing too low is the
mistake that loses warnings: the turn crosses the ceiling without being
told. Guessing too high costs a warning one call early, which is a
sentence. So the forecast takes the pessimistic figure.

**ONLY THE TURN'S OWN AGENT.** A sub-agent spends from its parent's
meter ([notes/34](34-budgets.md)), but its replies are a different
agent's habit, often on a different model. The call being forecast is
the parent's, so only the parent's replies count.

The warning says what it counted. A real one, from `qwen3.8:latest` on
the third call of the trial's task (here the context alone would have
crossed too; the sentence is the same shape either way):

```
the next call carries ~7,075 tokens of context, and replies this turn have run to ~418 tokens -- about $0.0092 for both, and ~$0.0014 is left of the $0.01 ceiling for this turn
```

`WARN_AT` (warn at 80% of the ceiling) stays on as a floor. A reply
longer than any the turn has had before still gets past the forecast,
and a turn that creeps up slowly still needs the fraction.

## What this cost

The warning now comes earlier on turns with long replies, and that is
the tradeoff. When the agent is told (`--budget-notice`), it may start
wrapping up a call or two sooner than it strictly had to. The receipt
shows how much: told turns averaged 6.1 tool calls against 6.5 in note
64, and cost about the same (~$0.0163 against ~$0.0165). The
not-told arm shows it from the other side: one turn there went on for
five tool calls after its warning and still finished, because the
warning had come well before the edge.

## The measurement

Note 64's setup exactly: `qwen3.8:latest` priced at $1 / $5 per
million tokens, a $0.010 ceiling, ten trials per arm, the same task.

| | warned | told, finished | not told, finished |
|---|---|---|---|
| note 64, input only | 12 of 20 (0.39..0.78) | 6 of 10 (0.31..0.83) | 1 of 10 (0.02..0.40) |
| this note, with the reply | 19 of 20 (0.76..0.99) | 9 of 10 (0.60..0.98) | 2 of 10 (0.06..0.51) |

**Fewer turns stopped unannounced: from eight in twenty to one.** By this
repo's own rule ([notes/47](47-what-seven-of-ten-is-evidence-of.md)) the
two "warned" ranges touch at 0.76..0.78, so this is *nearly* evidence and
not quite. It is also against a baseline from a different day, not run
alongside it. The direction is large and the mechanism is the one
the note was about. It is not called proven here.

**The notice itself now separates the arms on qwen.** Told 9 of 10
against not told 2 of 10, and those ranges do not overlap, where note
64's 6 of 10 against 1 of 10 did. A warning that arrives is a warning
the model can act on, and more of them arrive now.

## Both roads

The forecast is in the meter, so it works the same on every provider.
A local model's ceiling is inert unless you price it in
`$YANTRA_PRICES` ([notes/64](64-a-price-for-the-free-road.md)), and
then the forecast works exactly as it does on a cloud model. The
receipt is a local run.

## What was deliberately not built

**No reply forecast from earlier turns.** A session's earlier turns
would give the first call something to go on. But the meter resets every
turn ([notes/34](34-budgets.md)), and a forecast that reached back across
turns would be the only part of it that did. The first call is forecast
on its input, as before.

**No use of `max_tokens`.** It is an upper bound, and a useless one:
priced in full, it would warn on every call of every turn
([notes/36](36-a-warning-before-the-stop.md)).

**No change to when the loop stops.** The forecast decides when to
*warn*. The stop is still made only on money actually billed.

## What is not here yet

* **A reply longer than any before it is still a surprise.** The
  `WARN_AT` floor is all that catches it. The first call of a turn in a
  session now borrows the previous turn's largest reply
  ([note 73](73-the-turn-before.md)), and a fresh agent remembers the
  last turn on disk ([note 76](76-remembered-and-capped.md)). `--budget-cap-reply` closes the rest, at the
  price of a cut-off answer.
* **The cloud trial.** Still one command, still not run
  ([notes/67](67-the-agent-or-the-vendor.md)).

## Receipt

```
$ YANTRA_PRICES=prices.json uv run python examples/budget_notice_trial.py \
      --provider ollama --model qwen3.8:latest --ceiling 0.010 --trials 10
trial  1      told: end_turn    warned 9 tools (0 after warning) ~$0.0199 · answer 2255 chars
trial  1  not told: over_budget warned 6 tools (0 after warning) ~$0.0153 · answer 0 chars
trial  2  not told: over_budget warned 6 tools (0 after warning) ~$0.0127 · answer 0 chars
trial  2      told: end_turn    warned 6 tools (0 after warning) ~$0.0147 · answer 2374 chars
trial  3      told: end_turn    warned 7 tools (0 after warning) ~$0.0176 · answer 3958 chars
trial  3  not told: end_turn    warned 10 tools (0 after warning) ~$0.0187 · answer 1985 chars
trial  4  not told: end_turn    warned 10 tools (5 after warning) ~$0.0215 · answer 1883 chars
trial  4      told: end_turn    warned 5 tools (0 after warning) ~$0.0189 · answer 3925 chars
trial  5      told: end_turn    warned 6 tools (0 after warning) ~$0.0173 · answer 3341 chars
trial  5  not told: over_budget warned 5 tools (0 after warning) ~$0.0105 · answer 0 chars
trial  6  not told: over_budget warned 5 tools (0 after warning) ~$0.0120 · answer 0 chars
trial  6      told: end_turn    warned 6 tools (0 after warning) ~$0.0166 · answer 3269 chars
trial  7      told: end_turn    warned 5 tools (0 after warning) ~$0.0167 · answer 2235 chars
trial  7  not told: over_budget warned 6 tools (0 after warning) ~$0.0108 · answer 0 chars
trial  8  not told: over_budget warned 6 tools (0 after warning) ~$0.0137 · answer 0 chars
trial  8      told: end_turn    warned 5 tools (0 after warning) ~$0.0149 · answer 2666 chars
trial  9      told: over_budget        6 tools (0 after warning) ~$0.0109 · answer 0 chars
trial  9  not told: over_budget warned 5 tools (0 after warning) ~$0.0119 · answer 0 chars
trial 10  not told: over_budget warned 5 tools (0 after warning) ~$0.0132 · answer 0 chars
trial 10      told: end_turn    warned 6 tools (0 after warning) ~$0.0156 · answer 4080 chars

 not told: 2/10 finished · 8 cut off · 10 warned · 0.5 tool calls after the warning · ~$0.0140 a turn
     told: 9/10 finished · 1 cut off · 9 warned · 0.0 tool calls after the warning · ~$0.0163 a turn
```

`1757 passed, 1 skipped` (was 1750).
