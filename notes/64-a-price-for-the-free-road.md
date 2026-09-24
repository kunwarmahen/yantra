# 64 — A price for the free road

[Note 43](43-a-bar-and-a-deadline.md) added `--budget-notice`. When a
turn is about to run out of its dollar ceiling, the agent is told, once,
to finish with what it has instead of starting new work. Note 43 was
honest that nobody knew whether this worked. Whether a model changes
course when it is told to is a question about models, and only
measuring answers it.

Measuring it needs a turn that actually hits its ceiling, many times.
That meant a paid cloud model, since a local model bills nothing and its
ceiling never fires. Or so it looked.

## A local model is free until you price it

Half of this repo's readers run Ollama. For them a dollar ceiling has
always been *inert*: it is shown, it says it will never fire, and it
never does. [Note 34](34-budgets.md) already had a way around that for
rehearsing a ceiling: give your own Ollama model a made-up price in a
`$YANTRA_PRICES` file, and the meter starts counting.

Trying it for this measurement turned up an inconsistency. The *meter*
honoured that price. The eval report, the terminal's cost line and the
browser's cost line did not. They each asked "is this provider local?",
got yes, and printed `$0.00`. So a turn could be stopped for going over
a ceiling while the cost line underneath said it had cost nothing.

**A LOCAL MODEL IS FREE UNTIL YOU PRICE IT.** There is now one function,
`is_free(provider, model)`, and every place that prices a run asks it:
the meter, the report's price record ([notes/62](62-the-reports-you-already-have.md)),
the terminal and the browser. A local model is free unless *your*
`YANTRA_PRICES` file has an entry for it. Then it costs what you said,
everywhere at once.

Only your file counts. The built-in price table never makes a local
model cost money. A local tag like `gpt-5-local` happens to match the
built-in `gpt-5` row, and before this change the meter would have
metered it at OpenAI's prices. That was a bill nobody wrote.

This also closes a bullet [note 48](48-what-the-run-cost.md) left open:
*a local run's zero hides real money*. Electricity and a GPU are not
free, and now you can say what you think they cost.

## The measurement

`examples/budget_notice_trial.py` runs the same turn repeatedly with the
notice and without it. It uses the example `researcher` package on
`qwen3.8:latest`, priced at $1 / $5 per million tokens, and alternates
the two arms, so a model server that slows down part-way through affects
both the same way. Each turn ends one of two ways:

* **finished**: the model gave its answer before the ceiling stopped it.
* **cut off**: the loop stopped the turn (`over_budget`) and no answer
  came back.

The task: read five files from the package and write one paragraph about
how its parts fit together, citing each file.

### Choosing the ceiling was most of the work

The first attempt used a $0.018 ceiling, below what every full turn had
cost in calibration ($0.022 to $0.038). Five turns in, every one had
*finished*, costing up to $0.043, and none had been warned. The ceiling was working correctly. The
turn just had no moment where a warning could matter. The model read all
five files in one or two batches of tool calls, and most of the turn's
cost was the final answer itself, because `qwen3.8` thinks at length
before it writes, and thinking is output tokens. The loop checks the
ceiling *between* calls and never throws away an answer that has
already been paid for ([notes/34](34-budgets.md)). So a turn that is
cheap until its last call always finishes, however far over it ends up.

This matters beyond this experiment. **A ceiling only binds a turn that
is still working when it is crossed.** A turn whose cost is mostly its
final answer runs straight through any ceiling set below that answer.
The ceiling still sees the money; it just has nothing to stop.

At $0.008, on a two-per-arm calibration, all four turns were cut off
and three were warned. $0.010 lands where the
decision is real: the warning arrives on the last call the turn can
afford, and what the model does with that call decides the outcome.
Answer, and the turn finishes. Ask for another file, and the loop
refuses to run it and stops.

### Results

Ten turns per arm at $0.010:

| | finished | cut off | warned | finished, of those warned |
|---|---|---|---|---|
| **not told** | 1 / 10 | 9 | 5 | 1 / 5 |
| **told** | 6 / 10 | 4 | 7 | 6 / 7 |

Read by warned turns, which are the only turns where the notice could
make a difference: told the deadline, the model answered on its last
affordable call six times in seven. Warned but not told, it asked for
more files four times in five and was cut off. Of the told turns that
finished, the answers ran from 2,152 to 3,369 characters, the same size
as a turn with no ceiling. The notice asks the model not to shorten its
answer, and it didn't.

**It is not yet evidence, by this repo's own rule.** [Note 47](47-what-seven-of-ten-is-evidence-of.md)
says a difference counts only when the two ranges of likely rates do not
overlap. 6 of 10 is consistent with anything from 0.31 to 0.83; 1 of 10
with 0.02 to 0.40. They overlap between 0.31 and 0.40, and the
warned-only comparison (6/7 against 1/5) overlaps too. The direction is
clear and large. The sample is too small to call it proven, and this
note does not. Twenty more turns per arm would very likely settle it,
and the script makes that one command.

Two more things the table shows:

* **Eight of the twenty turns were never warned.** The warning fires when
  the *next* request would not fit, estimated from its input size
  ([notes/36](36-a-warning-before-the-stop.md)). In those eight, one
  call's thinking output took the turn from comfortably under the
  ceiling to over it in a single step, so there was no "last affordable
  call" to warn about. All eight were cut off, in both arms. The notice
  cannot help a turn that never gets it, and this is what "nothing
  forecasts the reply" costs in practice.
* **Finishing costs more.** Told turns averaged ~$0.0165 against
  ~$0.0130, because a turn that finishes pays for its answer and a turn
  that is cut off does not. The ceiling was crossed either way. What the
  notice buys is an answer for the money, not less money.

The `0 after warning` column in the receipt is always zero, and that is
expected rather than a bug: the warning lands on the last call the turn
can afford, so if the model asks for more tools, the loop stops before
running any of them.

## The two smaller things

**The page shows it is recording.** [Note 63](63-the-whole-turn-written-down.md)
made `--trace` record the browser's turns, and said so only in the
terminal's startup banner. The header now has a `rec` chip while turns
are being written down. It shows `shape` or `full`, and turns red for
`full`, because that is the recording that holds whatever the agent read.

**The bar says where the money went.** A sub-agent spends from its
parent's meter, so the budget bar was always right about the total. What
it could not show was that most of a turn had gone to one child. The
meter now keeps the sub-agents' share apart as `delegated`. It is part
of `spent`, not added on top. The bar's tooltip reports it, after the
turn's total: "…of its ceiling, ~$X of it by sub-agents".

## What was deliberately not built

**No "local price" key.** A separate setting that turned metering on for
local models would be a second way to say what an entry in
`YANTRA_PRICES` already says. One file, one rule.

**No automatic ceiling tuning.** The trial script does not search for
the ceiling where the notice matters. That choice was the most
informative part of the measurement, and a search would have hidden it
behind a number.

**The notice's wording was not changed.** One measurement, on one local
model, is not grounds for rewriting a sentence that has to work on
every model.

## What is not here yet

* ~~**One model, one task.**~~ Shipped in
  [note 67](67-the-agent-or-the-vendor.md): `gemma4:12b` on a different
  task, 13 of 15 told turns finished against 1 of 15, with ranges that do
  not overlap. Was: the same measurement on a cloud model, or on a turn
  shaped differently, is one command, and it had not been run. A cloud
  model is still unmeasured.
* **Nothing forecasts the reply.** Still true from notes 36 and 43, and
  the measurement shows why it matters: the expensive part of these
  turns was an answer nobody could price in advance.
* **A session total is still a service's job** ([notes/34](34-budgets.md)).

## Receipt

```
$ YANTRA_PRICES=prices.json uv run python examples/budget_notice_trial.py \
      --provider ollama --model qwen3.8:latest --ceiling 0.010 --trials 10
trial  1      told: end_turn    warned 6 tools (0 after warning) ~$0.0201 · answer 2790 chars
trial  1  not told: end_turn    warned 5 tools (0 after warning) ~$0.0156 · answer 1778 chars
trial  2  not told: over_budget warned 6 tools (0 after warning) ~$0.0142 · answer 0 chars
trial  2      told: end_turn    warned 5 tools (0 after warning) ~$0.0191 · answer 3161 chars
trial  3      told: end_turn    warned 10 tools (0 after warning) ~$0.0200 · answer 2152 chars
trial  3  not told: over_budget warned 7 tools (0 after warning) ~$0.0138 · answer 0 chars
trial  4  not told: over_budget        6 tools (0 after warning) ~$0.0101 · answer 0 chars
trial  4      told: over_budget        5 tools (0 after warning) ~$0.0112 · answer 0 chars
trial  5      told: over_budget        5 tools (0 after warning) ~$0.0110 · answer 0 chars
trial  5  not told: over_budget        5 tools (0 after warning) ~$0.0106 · answer 0 chars
trial  6  not told: over_budget warned 9 tools (0 after warning) ~$0.0162 · answer 0 chars
trial  6      told: over_budget        5 tools (0 after warning) ~$0.0114 · answer 0 chars
trial  7      told: end_turn    warned 10 tools (0 after warning) ~$0.0242 · answer 2754 chars
trial  7  not told: over_budget        5 tools (0 after warning) ~$0.0103 · answer 0 chars
trial  8  not told: over_budget        5 tools (0 after warning) ~$0.0106 · answer 0 chars
trial  8      told: end_turn    warned 7 tools (0 after warning) ~$0.0211 · answer 3369 chars
trial  9      told: end_turn    warned 6 tools (0 after warning) ~$0.0147 · answer 2341 chars
trial  9  not told: over_budget        5 tools (0 after warning) ~$0.0108 · answer 0 chars
trial 10  not told: over_budget warned 5 tools (0 after warning) ~$0.0175 · answer 0 chars
trial 10      told: over_budget warned 6 tools (0 after warning) ~$0.0127 · answer 0 chars

 not told: 1/10 finished · 9 cut off · 5 warned · 0.0 tool calls after the warning · ~$0.0130 a turn
     told: 6/10 finished · 4 cut off · 7 warned · 0.0 tool calls after the warning · ~$0.0165 a turn
```

Twenty turns, about forty minutes on one desktop GPU, and nothing billed.

`1688 passed, 1 skipped` (was 1674).
