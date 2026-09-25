# 83 — A table that fits

[Note 49](49-three-runs-side-by-side.md) lined up three or more eval
runs as a table: cases down the side, one column per run, a ✓ or ✗ in
each cell. It worked for the example it was built on, which was four
runs and six cases, and it left three problems for the day somebody had
more:

* **The totals were in the wrong place.** Each run's passed count and
  tokens were on a header line above the table. To compare two runs'
  totals you read two lines of prose, when the numbers could have sat
  under the columns they add up.
* **There was no order.** Rows came out in the last run's order. The
  question you actually bring to a table of models is *where do they
  disagree?*, and with forty cases the answer is scattered down the
  screen.
* **A wide table wrapped.** Ten models at nine characters each is wider
  than most terminals. The terminal wraps each row, so the second half
  of a row lands under the first half's headers. Nothing errors. It
  just reads wrong.

## Totals under their columns

The table now ends in footer rows:

```
  case                                    priced  gemma4-12b  gemma4-e4b
  cites-what-it-read                           ✓           ✓           ✗
  ...
  passed                                     6/6         6/6         5/6
  tokens                                   33937       33902       15419
  cost                                   $0.0127        free        free
```

**THE SAME ARITHMETIC AS THE CELLS.** Each total is right-aligned in its
column's width, and that width is worked out from the header, the cells
*and* the totals together. Padding them separately is how a sum ends up
under its neighbour's header.

The `cost` row appears only when some run cost money. A table of local
runs with `$0.0000` under every column reads as a broken meter
([note 48](48-what-the-run-cost.md)). Beside a priced run, a local one
says `free` and a run with no figure says `--`, because zero and unknown
are different numbers. The first version of this printed `$0.0000`
under the two gemma columns; the receipt below is where that was
caught.

The header lines above the table now carry only what is said once per
run: the model, when it ran, and the rates it was priced at.

## `--sort`

```
yantra --reports a.json b.json c.json --sort disagree
```

Three orders:

* **`disagree`** puts first the cases where the runs reached different
  verdicts. Those are the rows a table of models is for.
* **`red`** puts first the cases with the most ✗ cells, which is the
  view you want when every run is struggling.
* **`id`** is alphabetical, for finding one case among forty.

**A SORT, NOT A FILTER.** Note 49 guessed that "show me the cases that
disagree" would be a filter. It is not, because a filter throws rows
away, and the rule note 49 built the table on is that *nothing is
intersected away*. The cases everyone agreed on are still there, below
the ones they did not. A line above the table says how many rows the
sort moved to the top, so you know where the interesting part ends.

**A BLANK IS NOT A VOTE.** A run that never graded a case shows `--`.
For `disagree`, that is a hole in the table, not a disagreement: `✓ ✓ --`
counts as agreement. Otherwise every newly added case would jump to the
top of every sort.

**EACH SORT IS STABLE.** Rows that tie keep the last run's order, so
you never see a second, unexplained order among rows the sort did not
separate.

`--sort` only works where there is a table: `--eval` with `--against`
named twice or more, or `--reports` with three or more files and no
`--pool`. Anywhere else it is an error, because two reports print a
difference and a pool prints a list, and neither has rows to order. A
flag that silently did nothing would look like one that had worked.

## A wide table is split, not wrapped

When the columns do not fit the terminal, they are cut into blocks that
do, and each block repeats the case names on its left:

```
10 columns are wider than this terminal (60); shown in 4 blocks, the case names repeated on each
  columns 1-3 of 10
  case                     model-00  model-01  model-02
  a-case-with-a-long-name         ✓         ✓         ✓
  ...
  columns 4-6 of 10
  ...
```

Every block has its own footer, so each total stays under its column.
**EVERY BLOCK HOLDS AT LEAST ONE COLUMN.** A column wider than the
whole screen still has to be shown somewhere, and one wrapped column is
readable where ten wrapped ones are not.

The width is whatever the terminal says. When the output goes to a file
or a pipe, `rich` assumes 80 columns unless `COLUMNS` says otherwise.
So `COLUMNS=200 yantra --reports ... > table.txt` writes one unbroken
table.

## Both roads

Nothing here depends on the provider. Reports from a cloud model and
from Ollama line up in the same table, and the receipt below mixes a
priced local run with two unpriced ones.

## What was deliberately not built

**No cost column per cell.** A ✓ is one character and a dollar figure
is nine. Per-case cost over time is the pool's job
([note 66](66-what-each-case-cost.md)), and the live run already prints
each case's cost on its own line ([note 82](82-what-each-line-cost.md)).

**No `--only-disagree`.** Once the disagreeing rows sort to the top and
a line says how many there are, a filter would save you one glance and
cost you the rows that give the disagreements context. If that turns
out to be wrong for a hundred-case suite, `jq` over the reports is ten
lines (note 42 kept them JSON for exactly that).

**No paging of rows.** A long table scrolls and each row still reads
correctly. Only a wide table produces wrong output, so only width gets
the fix.

## Receipt

`examples/agents/researcher` run three times on one desktop GPU:
`qwen3.8:latest` priced through `$YANTRA_PRICES` (the run in
[note 82](82-what-each-line-cost.md)'s receipt), then `gemma4:12b` and
`gemma4:e4b` with no price. The small gemma answered one case without
reading the file it was asked about.

```
$ yantra --reports priced.json gemma4-12b.json gemma4-e4b.json --sort disagree

across 3 runs researcher 0.1.0
  priced: ollama/qwen3.8:latest · 2026-09-25T00:57:41Z · at $0.30 in / $1.20 out per Mtok (YANTRA_PRICES)
  gemma4-12b: ollama/gemma4:12b · 2026-09-25T01:01:54Z
  gemma4-e4b: ollama/gemma4:e4b · 2026-09-25T01:02:27Z
sorted: 1 case(s) the runs disagree on first, then the rest in the last run's order
  case                                    priced  gemma4-12b  gemma4-e4b
  cites-what-it-read                           ✓           ✓           ✗
  outlines-before-reading                      ✓           ✓           ✓
  cannot-write-even-when-asked                 ✓           ✓           ✓
  has-no-way-to-write                          ✓           ✓           ✓
  delegation-works-end-to-end                  ✓           ✓           ✓
  the-checker-is-actually-on-the-roster        ✓           ✓           ✓
  passed                                     6/6         6/6         5/6
  tokens                                   33937       33902       15419
  cost                                   $0.0127        free        free
```

The one row that matters is on top. Without `--sort` it would be second.
That is no great distance at six cases, and it is the whole point at
forty. The `tokens` row gives the other half of the story: the small
model used less than half the tokens because it skipped the reading
that the failing case checks for.

The same three files at `COLUMNS=60`:

```
3 columns are wider than this terminal (60); shown in 2 blocks, the case names repeated on each
  columns 1-2 of 3
  case                                    priced  gemma4-12b
  outlines-before-reading                      ✓           ✓
  cites-what-it-read                           ✓           ✓
  ...
  passed                                     6/6         6/6
  tokens                                   33937       33902
  cost                                   $0.0127        free
  columns 3-3 of 3
  case                                   gemma4-e4b
  outlines-before-reading                         ✓
  cites-what-it-read                              ✗
  ...
  passed                                        5/6
  tokens                                      15419
  cost                                         free
```
