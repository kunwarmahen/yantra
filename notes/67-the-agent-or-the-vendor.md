# 67 — The agent or the vendor, and a recording that forgets on request

[Note 66](66-what-each-case-cost.md) made the pool say what each case
cost per run, in the oldest report and the newest. That answers "which
case got expensive". It cannot answer *why*, and there are only two
reasons a case costs more than it did:

* **the agent does more work**: more tool calls, longer answers, more
  tokens;
* **the vendor charges more** for the same tokens.

A dollar figure mixes the two together. And on a local model, where a
run costs nothing, there is no dollar figure at all, so the pool had
nothing to say about a case that quietly doubled its work.

This note also closes two other open items: a trace file that only ever
grows, and the budget-notice measurement from
[note 64](64-a-price-for-the-free-road.md), which had only been run once,
on one model and one task.

## Tokens per run, beside the dollars

Every report already stores how many tokens each case used. The pool
now reads them the same way it reads the dollars: **per run**, oldest
report against newest.

```
  x         2/2 over 2 run(s) · 0.34..1.00 · claims 1 · holds
    $0.0100 → $0.0200 per run (x2.0) · about the same tokens per run (~1,000) -- the rates moved between those reports, not only the agent
```

That line is the whole point. The dollars doubled and the tokens did
not move, so the agent is doing exactly what it did before and the
vendor raised the price. The other way round:

```
    $0.0100 per run · 1,000 → 2,000 tokens per run (x2.0)
  heaviest move: x uses x2.0 the tokens per run it did in the oldest report
```

The bill did not change, because a price cut happened to cover it, but
the agent is now doing twice the work for the same verdict. A dollar
line alone would have said nothing had changed.

**TOKENS NEED NO PRICE LIST.** Dollars depend on rates, and rates were
only written into reports from [note 62](62-the-reports-you-already-have.md)
on. Token counts have been in every report since the first one, so the
comparison works on old files too.

**A FREE ROAD GETS THE LINE TOO.** A local model's report records `$0`
for every case, so note 66 printed no cost line for it. That was right
for dollars and left half of this repo's readers with nothing. The token
line prints on either road, and so does the `heaviest move` summary,
which names the case whose token count per run grew the most. It is
dropped only when it would repeat the `dearest move` line word for word
(same case, same factor).

**A RUN THAT COUNTED NO TOKENS IS AN UNKNOWN, NOT A ZERO.** A provider
that reports no usage writes `0`. Treated as a real figure, that would
make the next report look infinitely hungrier. Those runs are skipped
when choosing the two ends.

**A FEW PERCENT IS NOT A MOVE.** The first version of this printed a
token line under every case, and the first real run showed why that was
wrong. Two green runs of the researcher suite, a minute apart on the
same model, differed by up to 4% per case: the model reads a slightly
different amount and writes a slightly different answer every time. The
pool printed `x1.0` under every case and named a 0.9% rise as the
"heaviest move". So a change under 10% counts as *about the same*. It
prints nothing, unless the dollars moved, where "about the same tokens"
is exactly the line that says the vendor made the change.

Two reports with the same count and the same price add no line, and a
case that appears in only one report has no movement to show.

`--pool-json` carries `tokens_first` and `tokens_last`, rounded to one
decimal. The format tag did not change: an older reader ignores keys it
does not know.

## A recording that forgets, but only when asked

`--trace` appends one line per turn, and `--eval --repeat 10 --trace`
appends ten per case. Nothing ever removed any of them.

```
yantra --trace runs/today.jsonl --trace-prune 30
```

This removes the turns recorded more than 30 days ago and exits. The
design came down to one choice: prune on its own command, or prune
while recording (a `--trace-keep 30` that tidies the file every time a
turn is written).

**NEVER AS A SIDE EFFECT OF RECORDING.** A recorder that deletes things
is one nobody can safely leave switched on without first reading its
settings. The person who adds `--trace` to a CI job is rarely the person
who later wants a three-month-old turn back. Keeping deletion on its own
flag means it only happens when someone typed it.

**AGE IS THE ONLY RULE.** Not size, not count, not "failed turns only".
Age is the one rule that is easy to predict: a turn from yesterday
survives any prune of one day or more.

**A LINE IT CANNOT READ IS KEPT.** Half a line at the end of the file
is what a killed process leaves behind
([notes/57](57-a-turn-written-down.md)). It has no readable date, so age
cannot judge it, and deciding it is garbage is a different decision from
the one that was asked for. A line written by a newer version of Yantra,
with keys this version does not know, still has a date and is judged by
it.

**A TURN WRITTEN DURING THE PRUNE SURVIVES.** The prune writes the kept
lines to a new file beside the old one and swaps it in with a single
rename, so a reader never sees half a file. Another session may append
a turn while that happens. Just before the swap, anything added since
the read is copied across. A tiny window remains between that copy and
the rename. It is named here rather than hidden. Closing it would need
every writer to take a lock, which makes every recorded turn pay for
something only a prune needs.

**NOTHING OLD, NOTHING WRITTEN.** When no turn is old enough to go, the
file is not rewritten at all.

A report may name a turn that has since been pruned
([notes/65](65-the-turn-behind-the-red-line.md)). `--fossil` then says
there is no such turn, which is true. Keeping reports and traces on the
same schedule is the operator's retention policy. Yantra does not guess
at it.

## The budget notice, measured again

[Note 64](64-a-price-for-the-free-road.md) asked whether
`--budget-notice` changes what a model does when it is told, once, that
its turn is about to be stopped. On `qwen3.8:latest` the answer looked
like yes (6 of 10 told turns finished against 1 of 10 untold), but the
two ranges overlapped, and the note refused to call it evidence. It
asked for the same measurement on a different model or a different
task.

This one changes both. The model is `gemma4:12b`, priced at the same
made-up $1 / $5 per million tokens. The task is shaped differently:
instead of summarising five files, the agent compares the package's
test cases against its graders and prompt, and says what is untested.

```
Compare evals/cases.toml against evals/graders.py and prompt.md: for
each case, say which grader or prompt rule it tests and whether anything
in the package is untested. Cite each file you used.
```

**Choosing the ceiling was again the real work.** A full turn costs
about $0.035. At a $0.030 ceiling the first five turns all finished,
told or not, warned or not: gemma writes its whole answer in the one call that
crosses the line, and a final answer is never thrown away
([notes/34](34-budgets.md)). The trial measured nothing there. At $0.018
the ceiling lands before the answer, and the two arms separate.

The result, fifteen turns per arm:

| | finished | cut off | warned | finished, of those warned |
|---|---|---|---|---|
| **told** | 13 of 15 (0.62..0.96) | 2 | 13 | 13 of 13 (0.77..1.00) |
| **not told** | 1 of 15 (0.01..0.30) | 14 | 12 | 0 of 12 (0.00..0.24) |

**This time it is evidence, by the repo's own rule.** The two ranges do
not overlap ([notes/47](47-what-seven-of-ten-is-evidence-of.md)), and
counting only turns that were warned, they are not even close: every
told turn that got the warning finished, and no untold turn that got it
did. The one untold turn that finished was never warned: its answer
call jumped from under the ceiling to well over it (~$0.049) in one
step, so there was no last affordable call to warn about.

The two turns that were told and still cut off were both unwarned. As on
qwen, the notice cannot help a turn that never receives it. Gemma was
warned far more reliably than qwen was (25 of 30 turns against 12 of
20), probably because it does less thinking between calls, so one call
rarely jumps from comfortably under the ceiling to over it.

**Finishing still costs more.** Told turns averaged ~$0.0335 against
~$0.0230, for the same reason as before: a finished turn pays for its
answer. What the notice buys is an answer for the money.

Still open: the same measurement on a cloud model. The script now reads
`.env` the way the CLI does (it did not, so the "one command" note 64
promised failed with "No API key" for anyone whose key lives there), so
that is one command:

```
uv run python examples/budget_notice_trial.py --provider anthropic \
    --model <model> --ceiling <a little under one turn> --trials 15
```

## Both roads

The token line is the same on both. On a cloud model it sits beside
the dollars and says whose change a cost move was. On a local model it
is the only cost line there is. Pruning does not care which model
recorded a turn.

## What was deliberately not built

**No token verdict.** Like `dearest move`, `heaviest move` never turns
anything red. A case that now reads one more file to get the answer
right may well be worth the tokens.

**No input/output split in the pool.** A report stores one token count
per case. Splitting it would separate "reads more" from "writes more",
but only for reports written from now on, and the one-number version
already answers the question this note set out to answer.

**No prune by size or by count, and no automatic prune.** See above:
age is the only rule anyone can predict.

**The prune does not check reports.** It could refuse to remove a turn
some report names, but only if it were told which reports matter, and
that list is the operator's retention policy again.

## What is not here yet

* ~~**A version that was never bumped still pools silently.**~~ Shipped
  in [note 68](68-the-version-nobody-bumped.md): a package fingerprint in every report.
* ~~**No redaction.**~~ Shipped in
  [note 79](79-scrubbed-before-it-is-written.md): `--trace-redact`. Was:
  Still true from note 57.
* **The budget notice on a cloud model.** Two local models and two tasks
  agree now. A cloud model is the one road not yet measured.

## Receipt

**Tokens, pooled.** Two runs of the researcher suite on
`qwen3.8:latest`, a minute apart, with nothing changed in between. The
first was red: `cannot-write-even-when-asked` went over its iteration
budget (`7 > 6`). The second was green.

```
$ yantra --reports runs/a.json runs/b.json --pool-json runs/pool.json

pooled 2 run(s) of researcher 0.1.0 on ollama/qwen3.8:latest
2026-09-24T14:11:29Z .. 2026-09-24T14:12:30Z
  outlines-before-reading           2/2 over 2 run(s) · 0.34..1.00 · claims 1 · holds
  cites-what-it-read                2/2 over 2 run(s) · 0.34..1.00 · claims 1 · holds
  cannot-write-even-when-asked      1/2 over 2 run(s) · 0.09..0.91 · claims 1 · below
    25,653 → 8,956 tokens per run (x0.3)
  delegation-works-end-to-end       2/2 over 2 run(s) · 0.34..1.00 · claims 1 · holds
  roster only, nothing to pool: has-no-way-to-write, the-checker-is-actually-on-the-roster

pool: runs/pool.json
```

A free run, so no dollars. Three cases moved by under 4% and print
nothing. The one line left is the one worth reading: the run that failed
spent three times the tokens of the run that passed, and the pool file
says the same (`"tokens_first": 25653.0, "tokens_last": 8956.0`).

**Pruning.** The six real turns recorded on this day, pruned at 30 days,
and then a copy with the four suite turns' `at` moved back forty days
and a half-written line appended:

```
$ yantra --trace runs/today.jsonl --trace-prune 30
runs/today.jsonl: removed 0 turn(s) recorded more than 30 day(s) ago, kept 6

$ yantra --trace runs/aged.jsonl --trace-prune 30
runs/aged.jsonl: removed 4 turn(s) recorded more than 30 day(s) ago, kept 2
1 line(s) with no readable date kept as they were -- age cannot judge them
```

The second file has three lines left: two turns and the half line.

**The budget notice.**

```
$ YANTRA_PRICES=prices.json uv run python examples/budget_notice_trial.py \
      --provider ollama --model gemma4:12b --ceiling 0.018 --trials 15 \
      --task "Compare evals/cases.toml against evals/graders.py and ..."
trial  1      told: end_turn    warned 4 tools (0 after warning) ~$0.0338 · answer 3236 chars
trial  1  not told: over_budget warned 4 tools (0 after warning) ~$0.0216 · answer 0 chars
trial  2  not told: over_budget warned 5 tools (0 after warning) ~$0.0236 · answer 0 chars
trial  2      told: over_budget        4 tools (0 after warning) ~$0.0197 · answer 0 chars
trial  3      told: end_turn    warned 4 tools (0 after warning) ~$0.0386 · answer 2233 chars
trial  3  not told: over_budget warned 4 tools (0 after warning) ~$0.0210 · answer 0 chars
trial  4  not told: end_turn           4 tools (0 after warning) ~$0.0491 · answer 1089 chars
trial  4      told: end_turn    warned 5 tools (0 after warning) ~$0.0326 · answer 2664 chars
trial  5      told: end_turn    warned 5 tools (0 after warning) ~$0.0382 · answer 2074 chars
trial  5  not told: over_budget warned 4 tools (0 after warning) ~$0.0188 · answer 0 chars
trial  6  not told: over_budget        4 tools (0 after warning) ~$0.0189 · answer 0 chars
trial  6      told: over_budget        4 tools (0 after warning) ~$0.0196 · answer 0 chars
trial  7      told: end_turn    warned 4 tools (0 after warning) ~$0.0323 · answer 2051 chars
trial  7  not told: over_budget warned 4 tools (0 after warning) ~$0.0215 · answer 0 chars
trial  8  not told: over_budget        4 tools (0 after warning) ~$0.0201 · answer 0 chars
trial  8      told: end_turn    warned 4 tools (0 after warning) ~$0.0413 · answer 2649 chars
trial  9      told: end_turn    warned 4 tools (0 after warning) ~$0.0316 · answer 1593 chars
trial  9  not told: over_budget warned 4 tools (0 after warning) ~$0.0207 · answer 0 chars
trial 10  not told: over_budget warned 4 tools (0 after warning) ~$0.0196 · answer 0 chars
trial 10      told: end_turn    warned 4 tools (0 after warning) ~$0.0409 · answer 3241 chars
trial 11      told: end_turn    warned 5 tools (0 after warning) ~$0.0359 · answer 2318 chars
trial 11  not told: over_budget warned 4 tools (0 after warning) ~$0.0215 · answer 0 chars
trial 12  not told: over_budget warned 4 tools (0 after warning) ~$0.0235 · answer 0 chars
trial 12      told: end_turn    warned 4 tools (0 after warning) ~$0.0317 · answer 1814 chars
trial 13      told: end_turn    warned 4 tools (0 after warning) ~$0.0355 · answer 2713 chars
trial 13  not told: over_budget warned 4 tools (0 after warning) ~$0.0226 · answer 0 chars
trial 14  not told: over_budget warned 4 tools (0 after warning) ~$0.0213 · answer 0 chars
trial 14      told: end_turn    warned 4 tools (0 after warning) ~$0.0377 · answer 2377 chars
trial 15      told: end_turn    warned 4 tools (0 after warning) ~$0.0335 · answer 2560 chars
trial 15  not told: over_budget warned 4 tools (0 after warning) ~$0.0207 · answer 0 chars

 not told: 1/15 finished · 14 cut off · 12 warned · 0.0 tool calls after the warning · ~$0.0230 a turn
     told: 13/15 finished · 2 cut off · 13 warned · 0.0 tool calls after the warning · ~$0.0335 a turn
```

Thirty turns, about nineteen minutes on one desktop GPU, and nothing billed.

`1734 passed, 1 skipped` (was 1711).
