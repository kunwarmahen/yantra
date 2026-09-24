# 62 — The reports you already have

Every `--eval --report FILE` leaves a small JSON file behind: which
cases passed, how many times each was tried, what it cost
([notes/42](42-two-runs-of-the-same-suite.md)). After a few weeks there
is a folder of them. Three things you would naturally want from that
folder were missing:

* **Lining them up.** Four reports on disk, and the only way to compare
  them was `--against`, which compares against a run that is *happening
  now*. To look at four old reports you had to pay for a fifth.
* **Knowing why the cost moved.** A report stores the dollar figure its
  run cost ([notes/48](48-what-the-run-cost.md)), but not the prices
  behind it. A run that got cheaper might have spent fewer tokens, or the
  vendor might have cut a price. The figure alone cannot tell you which.
* **Adding runs together.** One run of three attempts says "3 of 3",
  and [notes/47](47-what-seven-of-ten-is-evidence-of.md) showed that
  this is consistent with a true pass rate as low as 44%. Run it every
  day for a month and you have thirty of those wide ranges. Pooled
  together, they would be one narrow range, which is the number worth
  having.

All three are the same move: **treat the reports as the data.** They
were already written down; nothing read them except a run in progress.

## Reading without running

```
$ yantra --reports runs/qwen-1.json runs/gemma.json

against researcher 0.1.0 on ollama/qwen3.8:latest, 2026-09-24T02:30:31Z (1/1 passed)
different model: ollama/qwen3.8:latest → ollama/gemma4:e4b
  broke  cites-what-it-read  3/3 → 1/3  (intervals overlap: not evidence of a change)
tokens: 31774 → 21246 (-10528)
```

Two files print what moved. Three or more print the table from
[notes/49](49-three-runs-side-by-side.md). It is the same rendering
`--against` uses. The only difference is that nothing ran first.

Note 42 refused this on purpose. Its argument was that a comparison
belongs to the run being judged, and the file is JSON, so anyone can
write the ten lines to read it. That was fine while there were one or
two reports. It stopped being fine when every report on disk could only
be read by running the suite again.

**READING IS NOT JUDGING.** `--reports` exits 0 whatever the reports
say. A folder full of red reports is still a folder you successfully
read. If this mode could fail on a red report, CI jobs would start using
it as a second gate. Its job is to answer a question, not to issue a
verdict. The one thing it does refuse is a file it cannot read, because
a comparison you asked for and did not get looks like everything is
fine.

It needs no package, no provider and no key. A report is a file, and
reading one is dispatched before Yantra looks for any of those.

## The prices behind the figure

A report now carries one more block:

```json
"pricing": {
  "source": "built-in 2026-08",
  "input": 3.0,
  "output": 15.0,
  "cache_read": 0.3,
  "cache_write": 3.75
}
```

These are the four rates (dollars per million tokens) that the run's
figures were priced at, and where they came from. `source` names the
built-in table, `YANTRA_PRICES` for your own override file, `free` for
a local model, or `unpriced` for a hosted model with no known price.

Notes/48 left this as an open question: per case, or per run? It is per
run, because every figure in a run is priced by one row: the row for
the model you ran against. The figure covers the top-level agent's
tokens only; a sub-agent's are not in it. So one row is what was used,
and one row is what gets written down. Storing it per case would repeat
the same four numbers down the file.

A comparison now says which story it is telling:

```
cost: $0.0200 → $0.0100 (-0.0100)
prices: $3.00 in / $15.00 out per Mtok (built-in 2026-08) → $1.50 in / $7.50 out per Mtok (YANTRA_PRICES) -- part of the cost change is the price, not the agent
```

or, when nothing moved underneath:

```
prices: the same rates both runs, so the cost change is the agent's
```

or, against a report written before this block existed, that it cannot
tell. The rates are recorded when the run happens, like the figure
itself, so a table that changes later rewrites nothing already on disk.

On a local run the block just says `free`, and the line never prints,
because a run that billed nothing has no cost change to explain.

**Why it does not reprice.** The obvious next step would be to say "at
last year's prices, today's run would have cost $X". That needs the
split between input, output and cached tokens, and a report stores only
a total. It would also be the first number in a report that nobody paid.
Naming the moved price is enough for a person to tell the two stories
apart. The token line printed just above shows whether the agent moved
too.

## Pooling

```
$ yantra --reports runs/qwen-1.json runs/qwen-2.json runs/gemma.json --pool
2 different suite/model pairs in these reports; each is pooled on its own -- runs of
different models or package versions are not samples of one rate

pooled 2 run(s) of researcher 0.1.0 on ollama/qwen3.8:latest
2026-09-24T02:30:31Z .. 2026-09-24T02:31:35Z
  cites-what-it-read      6/6 over 2 run(s) · 0.61..1.00 · claims 1 · holds

pooled 1 run(s) of researcher 0.1.0 on ollama/gemma4:e4b
2026-09-24T02:32:08Z
  cites-what-it-read      1/3 over 1 run(s) · 0.06..0.79 · claims 1 · below
```

Each case's passes and attempts are added up across every report you
name. The line then gives the range of true pass rates those counts are
consistent with (the same Wilson interval as notes/47), and one word
comparing that range with what the case claims:

* **holds**: even the low end of the range reaches the claim.
* **below**: even the high end misses it.
* **unsettled**: the range spans the claim. More runs would narrow it,
  and the closing line says how many.

A case that claims 1.0 ("every run passes") is read the way the gate
reads it. One failure breaks the claim. No number of greens can lift a
range's low end all the way to 1, so for this case "holds" means "no
run failed".

Notes/47 said pooling needed "a store keyed by case rather than by run".
**THE REPORTS ARE THE STORE.** Each one already holds every case's
counts. All that was missing was reading them as samples, not as
separate runs. That also answers note 47's other question, "how far back
to look". You decide by which files you name, and a shell wildcard
(`runs/2026-09-*.json`) is the obvious way to name a month. Nothing new
is kept anywhere.

**SAMPLES OF THE SAME THING OR NOTHING.** This is where pooling can lie,
so it is where the rules are strict:

* **A different model is a different pool.** 9/10 on one model and 2/10
  on another is not 11/20 of anything. The receipt above shows it: qwen
  and gemma are pooled separately, and the header says why.
* **A different package version is a different pool.** Reports are
  grouped by the suite's label, which carries the package version. That
  only works if the author bumps the version. An edited prompt under the
  same version pools as if nothing changed. So there is a second check:
* **Two runs that disagree outright are flagged.** If one run's range
  and another's do not even overlap (10/10 one day, 0/10 the next), the
  line says something changed between them and the pooled number is an
  average of two different agents.
* **The same file named twice counts once.** Otherwise it would be
  double the evidence for free.
* **A roster-only case is named, not pooled.** It grades the tool list
  and never reaches a model, so there is no chance involved and nothing
  to add up. A case whose roster check failed before its task ran is
  left out too, because that is a verdict about the tool list, not about
  the model.

If a case's claim changed between runs, the newest one is used and the
line says so.

Pooling is not a verdict either. `below` is a statement about evidence,
and the exit code is 0.

## Both roads

None of this depends on the provider. The receipts above are local runs
of `examples/agents/researcher` on `qwen3.8:latest` and `gemma4:e4b`,
three attempts each. They cost nothing and pool exactly the way cloud
runs do. The price line is the one part a local run never prints,
because there is no bill to explain. It is covered by tests rather than
by a paid run.

## What was deliberately not built

**No database.** A store keyed by case, with retention and an index, is
what notes/47 imagined. It would be one more place for results to live
and drift from the reports people actually keep and commit. Reading the
files is slower than a database only when there are thousands of them.

**No automatic "how far back".** A window like "last 30 days" would
quietly pool across a prompt change the author forgot to version. The
file list is explicit on purpose.

**No pooling inside `--eval`.** A run in progress is judged on its own
counts, by its own gate. Pooling it with last month's runs would change
what the gate means.

**No per-case price.** As argued above: one run, one row. If sub-agent
tokens are ever priced into the figure on their own models, the block
becomes per-model, and a new key is added rather than an old one
changing meaning, so older readers keep working.

## What is not here yet

* **Pooled figures do not go into a file.** The pool is printed, not
  written. A JSON pool would let a dashboard track the narrowing range
  over time.
* **No cost per case over time.** The same files hold every case's
  dollars. "Which case got expensive" is the same kind of sum, and is
  not printed.
* **A version that was never bumped cannot be caught.** The disagreement
  flag only catches a change large enough that the two ranges miss each
  other entirely. A small prompt edit that moved a rate from 0.9 to 0.8
  pools silently.

## Receipt

`1662 passed, 1 skipped` (was 1630).
