# 49 · Three runs, side by side

`--against` answers one question: *did that get better or worse?* It
takes a report written earlier, compares it with the run that just
finished, and prints what moved.

There is a second question with the same ingredients and a different
shape:

> Which of these three models should this package run on?

Nothing about that question has a "before" in it. Three reports exist,
all equally current, and the comparison wanted is a *table* — six cases
down the side, three runs across the top, read in whichever direction
answers your question.

Two runs are a **difference**. Three are a **table**. Rendering the
second as a stack of the first makes the reader do the join in their
head, from three separate blocks of text, each of which names a
different pair.

## The flag did not have to grow

`--against` became repeatable. One report keeps exactly the rendering it
had; two or more line up as columns, with the run that just happened as
the last column:

```
$ yantra --agent . --eval --provider ollama \
      --against runs/qwen.json --against runs/gemma.json --against runs/red.json

SUITE GREEN · 6/6 passed · 28626 tokens · 2 case(s) cost nothing

across 4 runs researcher 0.1.0
  qwen: ollama/qwen3.8:27b · 2026-09-18T14:30:27Z · 6/6 passed · 42141 tok
  gemma: ollama/gemma4:12b · 2026-09-18T14:55:36Z · 6/6 passed · 33762 tok
  red: ollama/qwen3.8:27b · 2026-09-18T14:32:09Z · 4/6 passed · 47236 tok
  this run: ollama/qwen3.8:latest · 2026-09-18T14:57:01Z · 6/6 passed · 28626 tok
  case                                   qwen  gemma  red  this run
  outlines-before-reading                   ✓      ✓    ✓         ✓
  cites-what-it-read                        ✓      ✓    ✓         ✓
  cannot-write-even-when-asked              ✓      ✓    ✓         ✓
  has-no-way-to-write                       ✓      ✓    ✓         ✓
  delegation-works-end-to-end               ✓      ✓    ✗         ✓
  the-checker-is-actually-on-the-roster     ✓      ✓    ✗         ✓
```

That is a real run of `examples/agents/researcher` against three local
models on one desktop GPU. Read a column and you have one model's
verdict; read a row and you have a case's stability across all of them.
The `red` column is the same suite with one sub-agent's tool list
widened by a word ([notes/44](44-a-ceiling-and-a-floor.md)) — two cases
down, which is what a *real* difference looks like next to three
interchangeable green columns.

Column labels are the file names the operator typed, not the models.
Two runs of the same model against different builds of a package is a
comparison somebody will want, and labelling both columns
`ollama/qwen3.8:27b` would make it unreadable. The model is on the
header line above, where it is said once.

## What a table may not do, which is everything a pair may not do

**The verdict is untouched.** This prints under a run whose exit code
was decided before any of these files were opened — the rule
[notes/42](42-two-runs-of-the-same-suite.md) set and the reason it set
it: a gate that reddens because a number moved has to be argued with the
first time an honest improvement costs two tokens more than last time.

**Nothing is intersected away.** A case that one run graded and another
did not leaves a visible hole:

```
not every run graded every case; a blank cell is a case that run did not have
  case                                   qwen  gemma  red  this run
  cannot-write-even-when-asked              ✓      ✓    ✓         ✓
  outlines-before-reading                   ✓      ✓    ✓        --
```

A `--` is "that run did not have this case", which is different from a
failure and different from a pass, and a table that quietly dropped the
row would hide the most informative thing on the page. Row order is the
last run's, with cases only the earlier runs had appended — the same
rule `compare` follows, for the same reason.

**Every file is read before a single token is spent.** One unreadable
report among four is exit 2 at the doorstep, not after a suite's worth
of real tokens has produced the other half of a comparison that cannot
happen.

## The one flag that had to learn to say no

`--failed` with no argument means "the report `--against` names"
([notes/46](46-the-cases-that-were-red.md)). With three reports named,
there is no such report:

```
error: --failed with no FILE means the report --against names, and there are
3 of them; pass --failed FILE
```

Picking one — the first, the newest, the reddest — would be a selection
nobody asked for, which is exactly the failure note 46 exists to avoid.

## What is not here yet

* **No summary row.** Passed counts, tokens and cost per run are on the
  header lines above the table rather than as a footer under the columns
  they belong to. A footer row would need the same padding arithmetic as
  the cells and is worth doing the day somebody has ten columns.
* **No sort.** Rows come out in the last run's case order. "Show me the
  cases that disagree between runs" is the obvious next ask and is a
  filter, not a sort — and it is the one query where a table beats
  reading the file with `jq`.
* **A wide table just wraps.** Six columns of nine characters fits; ten
  models do not, and the terminal wraps them into nonsense. A real fix
  means paging columns, which is a rendering project rather than a
  comparison one.
* ~~**Nothing reads a report without running the suite.**~~ Shipped in
  [note 62](62-the-reports-you-already-have.md) as `--reports FILE ...`,
  which prints the same difference or table and exits 0. Was: Four reports on
  disk and no way to line them up except by running a fifth. That is
  still deliberate ([notes/42](42-two-runs-of-the-same-suite.md): the
  file is JSON so that anything else can be written in ten lines
  elsewhere), and it is the bullet most likely to fall.
