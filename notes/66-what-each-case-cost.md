# 66 — What each case cost, and a pool you can keep

[Note 62](62-the-reports-you-already-have.md) taught `--reports --pool`
to add a folder of eval reports together case by case, so thirty wide
"3 of 3" ranges become one narrow one. It left two things on the table,
both from the same folder.

**Which case got expensive.** Every report already stores what each
case cost on the day it ran ([notes/48](48-what-the-run-cost.md)). The
pool added up the passes and ignored the dollars. So "the suite costs
twice what it did in August" could be read off two reports, and "*this*
case is the one that doubled" could not, without a spreadsheet.

**Keeping the pool.** The pooled numbers were printed and then gone. A
dashboard that wanted to watch a case's range narrow week by week had to
scrape a terminal.

## Dollars per run, oldest against newest

The pool now prints a cost line under every case that some report
priced. Here it is on two small reports a month apart, the ones the
tests use; a local run has no dollars to show:

```
pooled 2 run(s) of pkg 0.1 on p/m
2026-09-01T00:00:00Z .. 2026-09-20T00:00:00Z
  x         2/2 over 2 run(s) · 0.34..1.00 · claims 1 · holds
    $0.0100 → $0.0400 per run (x4.0)
  dearest move: x costs x4.0 per run what it did in the oldest priced report
```

**PER RUN, NOT PER REPORT.** A report of `--repeat 10` and one of
`--repeat 3` are not the same bill, and comparing their totals would
make every change in `--repeat` look like a change in the agent. So the
figure is the case's dollars divided by its runs in that report.

**OLDEST AGAINST NEWEST, NOT AN AVERAGE.** The question is whether a
case got dearer, and an average over thirty days answers a different
one: what it usually costs. The two ends are what moved.

The last line names the one case whose per-run cost grew the most. It is
not a warning. A case that now does more work for the same verdict may
well be worth it. It is the line a long list of costs would otherwise
hide.

**A MOVED RATE IS NAMED.** Each report stores the prices it was worked
out with (note 62). If the oldest and newest reports that priced a case
used different rates, the line says so:

```
    $0.0100 → $0.0200 per run (x2.0) -- the rates moved between those reports, not only the agent
```

The rule is the same one `--against` already follows: the figure is what
it cost on the day, and the reader is told when part of the change is the
vendor's.

Two figures are left out on purpose, the same way the run summary
leaves them out. A case no report priced has no line: an unknown cost is
not zero. A case that was free both times has no line either: a local
model's `$0.0000` under every case reads as a broken meter
([notes/64](64-a-price-for-the-free-road.md)).

## `--pool-json`

```
yantra --reports runs/*.json --pool-json runs/pool.json
```

This pools the reports exactly as `--pool` does, prints the same thing,
and also writes the figures to a file:

```json
{
  "format": "yantra.pool.v1",
  "at": "2026-09-24T12:02:52Z",
  "pools": [{
    "suite": "researcher 0.1.0",
    "where": "ollama/qwen3.8:latest",
    "runs": ["2026-09-24T12:01:14Z", "2026-09-24T12:02:17Z"],
    "roster_only": ["has-no-way-to-write", "the-checker-is-actually-on-the-roster"],
    "cases": [{"id": "outlines-a-one-word-lookup", "passes": 0, "attempts": 2,
               "runs": 1, "low": 0.0, "high": 0.6576, "min_pass_rate": 1.0,
               "standing": "below", "claim_changed": false, "disagree": false,
               "usd_first": 0.0, "usd_last": 0.0, "price_moved": false}, …]
  }]
}
```

**THE DERIVED NUMBERS ARE WRITTEN, NOT ONLY THE COUNTS.** The interval
and the `standing` word can be worked out again from `passes` and
`attempts`, but only with the confidence code from
[notes/47](47-what-seven-of-ten-is-evidence-of.md). The reader of this
file is a program that should not need Yantra installed to know what
"unsettled" means.

**A POOL IS NOT A REPORT, AND HAS ITS OWN FORMAT TAG.** It has no
single run behind it and no verdict, so `--against runs/pool.json`
refuses it by name rather than comparing a run against it.

Like the rest of `--reports`, it exits 0 whatever the pool says. Only a
file it cannot read or write is an error.

## Both roads

A local model's report records `0.0` for every case, so its pool writes
`0.0` and prints no cost line. A cloud model's pool prints both ends.
Pass counts pool the same way on either road.

## What was deliberately not built

**No cost over the whole series.** Only the two ends are kept. A
sparkline of thirty points is a dashboard's job, and the reports it
would draw from are already on disk.

**No cost verdict.** `dearest move` never turns anything red. A gate on
cost already exists, per turn, in the package ([notes/34](34-budgets.md)).

## What is not here yet

* **A version that was never bumped still pools silently.** Still true
  from note 62: `disagree` only catches a change big enough that two
  ranges miss each other entirely.
* **Tokens are not pooled.** Dollars move with prices and tokens do
  not, so a per-run token line would separate "the agent works harder"
  from "the vendor charges more" even for reports written before rates
  were kept.

## Receipt

The two local reports from [note 65](65-the-turn-behind-the-red-line.md)'s
receipt, pooled and written, on `qwen3.8:latest`:

```
$ yantra --reports runs/today.json runs/red.json --pool-json runs/pool.json

pooled 2 run(s) of researcher 0.1.0 on ollama/qwen3.8:latest
2026-09-24T12:01:14Z .. 2026-09-24T12:02:17Z
  outlines-a-one-word-lookup        0/2 over 1 run(s) · 0.00..0.66 · claims 1 · below
  outlines-before-reading           1/1 over 1 run(s) · 0.21..1.00 · claims 1 · holds
  cites-what-it-read                1/1 over 1 run(s) · 0.21..1.00 · claims 1 · holds
  cannot-write-even-when-asked      1/1 over 1 run(s) · 0.21..1.00 · claims 1 · holds
  delegation-works-end-to-end       1/1 over 1 run(s) · 0.21..1.00 · claims 1 · holds
  roster only, nothing to pool: has-no-way-to-write, the-checker-is-actually-on-the-roster

pool: runs/pool.json
```

No cost lines: both runs were free. The file is the JSON above, and the
exit code is 0 with a case `below` its claim.

`1711 passed, 1 skipped` (was 1688).
