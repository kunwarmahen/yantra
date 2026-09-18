# 42 · Two runs of the same suite

A gate answers one question: do the cases pass? That is what CI needs and
it is about half of what a person needs, because the question a person
asks second is never "did it pass".

It is **did that get better or worse**.

[Note 35](35-roster-and-pass-rates.md) made the first question richer —
a case can now say "7 of 10" instead of pretending one green run settled
anything — and in doing so made the second one unavoidable. A pass rate
is a number, numbers invite comparison, and nothing in this repo wrote
one down anywhere a later run could find it. Its closing list said so:

> **Comparing two runs of the same suite.** A pass rate invites the next
> question — did this model do better than that one? — and nothing here
> writes a report anywhere a later run could read.

This note is that store, which turns out to be one JSON file and four
rules about how not to lie with it.

```bash
yantra --agent . --eval --report runs/qwen.json
yantra --agent . --eval --model gemma4:12b --report runs/gemma.json \
                        --against runs/qwen.json
```

## A report is a record, not a baseline

The first rule, and the one with the most pull in the other direction:

**COMPARING CHANGES NO VERDICT AND NO EXIT CODE.** This run's cases
passed or they did not. A run that got worse than yesterday's and is
still green exits 0. A run that got better and is red exits 1.

The alternative is the thing every metrics dashboard eventually becomes:
a gate that goes red because a number moved. It sounds like rigour for
about a day — until the first honest improvement that happens to cost two
hundred tokens more than last time has to be argued with a YAML file. The
comparison is *reporting*. The operator knows whether "ten thousand fewer
tokens and one case broken" is good news for them; a program that decided
that on their behalf would be wrong before the week was out.

So the comparison prints under the verdict, in no colour, with no summary
adjective.

## Counts, never percentages alone

A case is `7/10`. Another is `1/1`. Those are not 70% and 100%: one is
ten samples and the other is a single die roll, which is the entire point
[note 35](35-roster-and-pass-rates.md) was making when it introduced
rates in the first place.

So the report stores `passes` and `attempts`, never a float, and a
comparison prints both sides' counts:

```
  broke  cites-what-it-read  1/1 → 0/1
```

When the two runs used different `--repeat` values, the header says so
before any line is read:

```
different sample sizes: 1 run(s) per case then, 10 now -- the counts
below are not rates
```

There is exactly one place a fraction is the right unit, and it is the
`rate up` / `rate down` word on a case whose verdict did not change —
`2/3` against `7/10` is a genuine comparison of one claim at two sample
sizes, and the header has already warned what it is.

### The line that would otherwise be lost

A case going from `9/10` to `6/10` with `min_pass_rate = 0.5` **passed
both times**. A comparison that printed only verdict changes would say
nothing about it, and it is the most useful line on the page: something
got worse and the gate cannot see it yet. So a pass count that moved
while the verdict held is reported in its own right.

## Never intersect the two runs

The easiest way to write this feature is to walk the case ids both
reports share. It is also the one operation guaranteed to hide the worst
news available:

```
  gone  cites-what-it-read
```

A case that exists in one report and not the other means somebody deleted
a case, or renamed it, or a `--case` filter cut it out
([note 41](41-a-gate-you-can-point.md)). Each of those is more
interesting than any verdict that changed, and an intersection makes all
three invisible. So cases only one side has survive as `added` and
`gone`, and when there are any, the header says the two runs did not
grade the same set:

```
the two runs did not grade the same cases; added/gone below are about the
SELECTION, not about the package
```

That sentence exists because the first useful thing anybody does with
`--against` is compare a `--case`-filtered run to a full one, and a page
of `gone` lines looks like a catastrophe until you remember you asked
for it.

## Two models is the point, not an error

The most valuable thing this can do is **the same suite, a cheaper
model**. So a report carries the provider and model it ran against, the
comparison names both sides, and nothing refuses to compare across them:

```
against researcher 0.1.0 on ollama/qwen3.8:latest, 2026-09-17T02:33:29Z
different model: ollama/qwen3.8:latest → ollama/gemma4:12b
```

A stricter design would have refused — "these runs are not comparable" —
and would have refused precisely the comparison people want. What
deserves a warning is not a different model; it is a different set of
*cases*, which is the one difference that makes the lines below mean
something other than what they say.

## The file

JSON, one object, with a format tag in it, because a report is written by
one version of this program and read by another — possibly months later,
by a CI job nobody has looked at since. An unreadable file has to say so
rather than be half-understood:

```
error: runs/old.json is format 'yantra.eval.v9', and this build reads
'yantra.eval.v1'; write a fresh report rather than comparing against one
this version may misread
```

And the baseline is read **before the suite runs**. A comparison the
operator asked for and cannot have is discovered now, not after a suite's
worth of real tokens has been spent producing the other half of it — the
same rule graders get ([note 33](33-evals-as-a-gate.md)), and a test
pins it against an empty provider script so a model call would raise.

A missing baseline is an error rather than a shrug:

```
error: no eval report at runs/old.json; write one first with:
       --eval --report runs/old.json
```

That is deliberate and it does cost something: the first run in a fresh
CI job fails until somebody writes a baseline. The alternative is a flag
that silently does nothing on exactly the runs where nobody is watching,
which is the failure mode this whole format exists to prevent. A shell
`if` is a fine price.

## Receipts

The researcher package, run twice against two local Ollama models — no
key and no cloud spend:

```
$ yantra --agent . --eval --provider ollama --model qwen3.8:latest \
         --report run-a.json
SUITE GREEN · 6/6 passed · 34991 tokens · 2 case(s) cost nothing
report: run-a.json

$ yantra --agent . --eval --provider ollama --model gemma4:12b \
         --against run-a.json
SUITE GREEN · 6/6 passed · 24862 tokens · 2 case(s) cost nothing

against researcher 0.1.0 on ollama/qwen3.8:latest, 2026-09-17T02:33:29Z (6/6 passed)
different model: ollama/qwen3.8:latest → ollama/gemma4:12b
  no case changed verdict or pass count
tokens: 34991 → 24862 (-10129)
```

Same package, same six cases, both green, ten thousand fewer tokens. That
last line is the whole feature: it is the answer to a question that could
not previously be asked without keeping two terminals open and doing
arithmetic.

And the other direction, on a much smaller model, which is the honest
half of the same story:

```
$ yantra --agent . --eval --provider ollama --model gemma4:e4b \
         --against run-a.json

  FAIL  cites-what-it-read  5.9s · 2787 tok · 1 it · no tools
        answer check failed
        required tool not used: read_file

SUITE RED · 5/6 passed · 14203 tokens · 2 case(s) cost nothing

against researcher 0.1.0 on ollama/qwen3.8:latest, 2026-09-17T02:33:29Z (6/6 passed)
different model: ollama/qwen3.8:latest → ollama/gemma4:e4b
  broke  cites-what-it-read  1/1 → 0/1
tokens: 34991 → 14203 (-20788)
```

Less than half the tokens, and it stopped reading the file before
answering. The exit code is 1 because of the case, not because of the
comparison — and the comparison is what tells you *which* of the cheaper
model's economies you just bought.

## The tradeoff

**One run is still one sample, and the comparison cannot tell you which
kind of difference it is looking at.** `1/1 → 0/1` above might be a
smaller model that genuinely cannot do the task, or the same model having
a bad roll. The report faithfully records what happened and has no
opinion about which; `--repeat` buys the evidence and `--case` aims it
([note 41](41-a-gate-you-can-point.md)), and both cost money the
comparison cannot spend for you.

This is the same limit note 35 named — a threshold is not a confidence
interval — arriving in a new place. Writing results down makes it easier
to see and no easier to fix.

## What was deliberately not built

**A pass/fail on the comparison.** Covered above; the first rule of the
note.

**Appending to a log of runs.** One file per run, named by the operator.
A rolling history wants a schema, a retention policy and a directory
convention, and everybody who has needed one so far has a CI artifact
store that already does it better than a text file would.

**A stored trajectory.** `case_from_trace`
([note 33](33-evals-as-a-gate.md)) has been pointing at a missing store
from the other side since it was written, and this is not that store: it
holds RESULTS — verdicts, counts, failure lines, tokens — and not the
transcripts that produced them. Keeping transcripts is a different
feature with a privacy question attached, because a trajectory contains
whatever the agent read.

**`--against` implying `--report`.** Comparing and recording are separate
decisions and somebody will want exactly one of them; guessing here would
scatter JSON files into directories nobody asked to have them in.

**A diff of failure TEXT.** The comparison reports which cases changed
verdict, not how the wording of their failures changed. Failure lines
already carry counts at n>1, and a diff of prose is a thing nobody reads
twice.

## What is not here yet

* **Nothing reads a report except a human.** There is no
  `yantra --eval-report-summary`, no HTML, no chart. The file is JSON so
  that whatever somebody wants can be written in ten lines elsewhere.
* **No way to compare more than two runs.** Three models side by side is
  the obvious next ask, and it is a table rather than a pair, which is a
  different rendering problem than this one.
* **Cost is measured in tokens, not dollars.** The report stores token
  counts; the ceiling machinery ([note 34](34-budgets.md)) knows how to
  price them per model, and a comparison across two models would want
  dollars rather than tokens to mean anything financial. Nobody has asked
  yet, and doing it wrong — pricing both sides with today's table — would
  quietly rewrite history every time a vendor changes a price.
* ~~**The store still cannot answer "which cases failed last time".**~~
  Shipped in [note 46](46-the-cases-that-were-red.md) as `--failed FILE`
  — the same file, read at the other end of a run. It is a named file
  rather than the `--case failed` this bullet guessed at: a suite may
  hold a case *called* `failed`, and one string with two meanings picks
  the wrong one silently. "Which report is last time" stayed the
  operator's decision; with no `FILE` it is the one `--against` names.
