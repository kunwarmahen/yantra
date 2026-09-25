# 84 — A verdict already reached

`--repeat N` runs every case N times and judges it on its pass rate
([note 35](35-roster-and-pass-rates.md)). A case that claims
`min_pass_rate = 0.7` over ten runs needs seven passes. Suppose its
first four runs fail. The six runs left could all pass and it would
still only reach six. The verdict is red, and it was red the moment the
fourth run failed.

The suite used to run the other six anyway. On a local model that is
minutes of GPU time, and on a cloud model it is money, spent confirming
a result that could no longer change. [Note 47](47-what-seven-of-ten-is-evidence-of.md)
listed this under *what is not here yet*, with a warning attached:

> Sequential stopping is a real technique and a real way to bias a
> result if done carelessly.

This note is about the difference between those two.

## Stop on one side only

**A CASE STOPS WHEN IT CAN NO LONGER PASS, AND NEVER WHEN IT ALREADY
HAS.**

The careless version stops on both sides. Seven passes out of seven
means the case has cleared 0.7 of 10, so why run three more? Because
those three are the evidence. A case that holds half the time will now
and then open with a run of seven greens. Stopping there keeps the lucky
streak and throws away the runs that would have exposed it. Repeat that
over enough cases and your suite reports rates it does not have. That is
what the warning in note 47 was about.

Stopping on failure has no such problem, because the verdict is already
fixed. Nothing the remaining runs could do would change it. The rule is
a single comparison:

```
passes so far + runs left  <  passes needed out of N
```

When that holds, the case stops. A claim of 1.0 (every run must pass)
stops at its first failure, which is the common case and the biggest
saving: `--repeat 10` on a broken case now costs one run, not ten.

**THE BAR IS THE ONE THE CASE WAS SET.** A case that stopped at 1 of 4
is judged against 7 of 10, the bar it was given, not against 3 of 4
worked out from however far it got. The tests check this by brute force:
for every pass/fail sequence, at a range of claimed rates, whenever the
runner stopped, no way the remaining runs could have gone would have
passed.

## It says so

A stopped case shows how many runs it was set, so a short count is
never mistaken for a small sample that happened to be taken:

```
  FAIL  cites-what-it-read  ✓✗ 1/2 runs of 6 · stopped, out of reach · 0.09-0.91 at 95% · 18.9s · 12485 tok
```

The verdict line counts what was saved:

```
SUBSET RED · 0/1 passed · 2 runs · 1 case(s) stopped early, 4 run(s) not bought · ...
```

The header says which rule is in force before the first case runs:
`a case stops once it can no longer reach its rate (--all-runs buys every
run)`.

## `--all-runs`

Stopping is the default, because a verdict that cannot change is not
worth paying for. `--all-runs` turns it off, for two reasons:

* **Diagnosis.** A case that fails 4 of 10 and a case that fails 10 of
  10 are both red, and they are different problems. The first is a
  flaky prompt and the second is a broken one. If you want to know
  which, you need all ten.
* **Pooling.** See below.

## What a stopped count does to a pool

A stopped count is an honest count: two runs happened, one passed. It
also leans low. The runs stop on failures and never on passes, so a
case's stopped reports, added together, look slightly worse than the
same number of full runs would have. `--reports --pool`
([note 62](62-the-reports-you-already-have.md)) adds reports case by
case, so it says so under any case that has stopped counts in it:

```
  cites-what-it-read                1/3 over 2 run(s) · 0.06..0.79 · claims 1 · below
    stopped early in 1 report(s): a count that stops on failures leans low when pooled (--all-runs buys full samples)
```

**NO NEW KEY IN THE REPORT.** A case that reached a model runs `repeat`
times unless it stopped, so "fewer attempts than the report's `repeat`"
already identifies a stopped case in any report ever written, and an
older report correctly reads as not stopped. The pool file
(`--pool-json`) carries the count as `stopped`.

## The async runner stops starting, not running

`--async N` runs several trajectories at once
([note 11](11-async.md)). There, stopping means **start no more**. A run
still waiting for a slot when its case became hopeless is never started.
A run already in progress finishes and counts, because its tokens are
spent either way and a result thrown away is one the report cannot
show. So under `--async` a case can end with a few more runs than the
sync runner would have made, and never with a different verdict.

## Both roads

The rule reads only pass/fail counts, so it is the same on every
provider. The saving is not. On a cloud model it is money. On Ollama it
is time: the receipt below is `gemma4:e4b`, where four skipped runs are
about forty seconds of a desktop GPU.

## What was deliberately not built

**No stopping on success.** Covered above. It is the careless half.

**No sizing the run count.** Note 47's next bullet, *the suite does not
size itself*, is still open. Stopping early saves money on cases that
are failing. Buying more runs for cases whose evidence is thin would
spend it, and that is the operator's decision.

**No early stop on a case's interval.** You could also stop once the
Wilson interval is entirely below the claim. That can happen before the
count comparison says so, but the interval is information, never a
verdict ([note 47](47-what-seven-of-ten-is-evidence-of.md)), and
stopping on it would make it one.

## Receipt

The researcher's `cites-what-it-read` case on `gemma4:e4b`, the small
model that failed it in [note 83](83-a-table-that-fits.md)'s table.
The case claims 1.0, so one failure decides it:

```
$ yantra --agent examples/agents/researcher --eval --provider ollama \
      --model gemma4:e4b --case cites-what-it-read --repeat 6 --report e4b-stop.json

runs: 6 per case; a case reports once its runs are in; a case stops once it can no longer reach its rate (--all-runs buys every run)

  FAIL  cites-what-it-read  ✓✗ 1/2 runs of 6 · stopped, out of reach · 0.09-0.91 at 95% · 18.9s · 12485 tok
        answer check failed (1 of 2 runs)
        required tool not used: read_file (1 of 2 runs)

SUBSET RED · 0/1 passed · 2 runs · 1 case(s) stopped early, 4 run(s) not bought · 5 case(s) not run · 12485 tokens
```

It passed once and then answered without reading the file, the same
failure as in the table. Four runs were not bought. Pooled with the
earlier full run of the same model:

```
$ yantra --reports gemma4-e4b.json e4b-stop.json --pool

pooled 2 run(s) of researcher 0.1.0 on ollama/gemma4:e4b
2026-09-25T01:02:27Z .. 2026-09-25T01:09:19Z
  cites-what-it-read                1/3 over 2 run(s) · 0.06..0.79 · claims 1 · below
    2,905 → 6,242 tokens per run (x2.1)
    stopped early in 1 report(s): a count that stops on failures leans low when pooled (--all-runs buys full samples)
  outlines-before-reading           1/1 over 1 run(s) · 0.21..1.00 · claims 1 · holds
  cannot-write-even-when-asked      1/1 over 1 run(s) · 0.21..1.00 · claims 1 · holds
  delegation-works-end-to-end       1/1 over 1 run(s) · 0.21..1.00 · claims 1 · holds
  heaviest move: cites-what-it-read uses x2.1 the tokens per run it did in the oldest report
  roster only, nothing to pool: has-no-way-to-write, the-checker-is-actually-on-the-roster
```
