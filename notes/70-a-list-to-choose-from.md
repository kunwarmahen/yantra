# 70 — A list to choose from

[Note 57](57-a-turn-written-down.md) made `--trace` write every turn to a
file, and `--fossil ID` turn one of them into a regression case. It was
firm about one thing: **nothing in Yantra decides which turns were
failures.** A turn can end cleanly, with every tool working, and still
give a wrong answer. Only a person can tell. So `--fossil` takes an id
that a person chose, not a rule.

That is still right, and this note does not change it. What it changes
is how a person *finds* the id. Until now the only way was to open the
JSONL file and read it. With a suite recording ten lines a case
([notes/65](65-the-turn-behind-the-red-line.md)), that is a file of
hundreds of lines of JSON.

## `--turns`

```
yantra --turns --trace runs/today.jsonl           # every turn
yantra --turns failed --trace runs/today.jsonl    # only the flagged ones
```

One line per turn: the id to hand to `--fossil`, when it ran, how many
tools it called, the start of the task, and the case it was a run of.
A flagged turn gets a `✗` and a second line saying why.

**IT SHOWS SIGNALS, IT DOES NOT JUDGE.** A turn is flagged for either
of two reasons, and the flag lists every one that applies:

* **The cheap filter** (`Trajectory.failed`, from note 57): the turn did
  not end normally (`ended max_iterations`), a tool failed or was refused
  (`write_file (refused: policy)`), or a sub-agent failed
  (`sub-agent #1 fact_checker: max_iterations`).
* **The case's own grader said no.** See below.

The footer says what the list is *not*: "an unflagged turn can still be
wrong". That sentence is there on purpose. Without it, a list with a
flag column reads like a verdict on the unflagged rows.

## The one verdict a trace can carry honestly

Building this turned up the gap the cheap filter cannot see. In
[note 65](65-the-turn-behind-the-red-line.md)'s receipt, the case
`outlines-a-one-word-lookup` failed twice: the agent answered without
calling `outline`, which the case required. Both turns ended cleanly and
no tool failed, so the cheap filter passed them both. Listing that file,
the two red turns were unflagged, the very turns anyone would want to
fossil.

But those turns *were* judged. The suite graded them before it recorded
them. And the judge was not Yantra: it was a case somebody wrote.

**A SUITE'S LINE NOW CARRIES ITS GRADER'S VERDICT.** `--eval --trace`
writes `"passed": false` (or `true`) into each turn it records, next to
the case id. `--turns` flags a `false` as `red in its case -- the grader
said no`. That is not Yantra deciding what a failure is. It is Yantra
remembering what the person's own case decided.

**A BOOLEAN, NOT THE GRADER'S WORDS.** A grader's failure message can
quote the agent's answer, and a SHAPE recording
([notes/57](57-a-turn-written-down.md)) never keeps content. So the line
keeps whether the case passed, and the report keeps the reasons, as it
always has.

A turn typed at the prompt was never graded, so it carries no verdict.
For those, the cheap filter is still all there is, and the person
decides.

## Both roads

Nothing here touches a model. `--turns` reads a file, and the verdict is
written by the same code on every provider. The receipt is a local run.

## What was deliberately not built

**No "failed" rule beyond these two.** A turn that took three times the
usual tokens, or used an unusual tool, might be worth a look. Each of
those is a guess about what "wrong" means, and note 57's argument
against guessing still holds.

**No paging and no limit.** The list prints every turn it is asked for.
A long file is one `| less` away, and `--trace-prune`
([notes/67](67-the-agent-or-the-vendor.md)) keeps it from growing
forever.

**The verdict is not written for old lines.** A line recorded before
this has no `passed` key, and it is not guessed from the report after the
fact.

## What is not here yet

* **Nothing decides whether a typed turn was a failure.** Still true
  from note 57, on purpose. A person does.

## Receipt

The researcher suite on `qwen3.8:latest`, recorded, then note 65's strict
scratch case run twice into the same file. The suite was green; the
strict case was red both times.

```
$ yantra --agent researcher --eval --case outlines-a-one-word-lookup --repeat 2 --trace runs/turns.jsonl
        turns: f8d557f7 28a0f12c

SUBSET RED · 0/1 passed · 2 runs · 6 case(s) not run · 16043 tokens

$ yantra --turns failed --trace runs/turns.jsonl
✗ f8d557f7  2026-09-24T15:04:55Z   2 tool(s)  Which file in sources/ mentions 'token bucket'? Answer wi... case outlines-a-one-word-lookup
             red in its case -- the grader said no; read_file (failed)
✗ 28a0f12c  2026-09-24T15:05:03Z   2 tool(s)  Which file in sources/ mentions 'token bucket'? Answer wi... case outlines-a-one-word-lookup
             red in its case -- the grader said no

10 turn(s), 2 flagged -- by the cheap filter or by a case's own grader; an unflagged turn can still be wrong. A case from one: --fossil ID --trace runs/turns.jsonl
```

The cheap filter alone would have caught one of the two: `f8d557f7`
had a `read_file` that failed. The other, `28a0f12c`, ended cleanly with
every tool working, and only the grader's verdict flags it. The first
version of this listing printed only the first reason it found, which
hid the failed read behind the grader's no; it prints every reason
now, because the two are different leads.

`1770 passed, 1 skipped` (was 1757).
