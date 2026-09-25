# 82 — What each line cost

[Note 48](48-what-the-run-cost.md) wrote down what every case in a suite
cost, in dollars, on the day it ran. The report has held that figure per
case ever since. The terminal printed only the total:

```
SUITE GREEN · 6/6 passed · 33937 tokens · $0.0127 · 2 case(s) cost nothing
```

That line is true, and it does not answer the question people ask right
after reading it: *which case spent it?* The expensive case is usually
the interesting one. It is the loop that read the same file three
times, or the sub-agent that did not need spawning. In a list of twenty
green lines it is the one nobody notices. The number was in the report
file, so you could dig it out, but nothing on the screen showed it.

## The figure goes on the case's own line

Every case line already ends with its tokens. A priced case now ends
with its dollars too:

```
  PASS  cites-what-it-read  11.2s · 10125 tok · 3 it · list_dir, read_file · $0.0035
```

Under the verdict, one more line names the dearest case and its share:

```
dearest case: cannot-write-even-when-asked · $0.0044 · 35% of what the priced cases cost
```

**A SHARE, NOT A RANK.** "The dearest" alone says nothing about scale.
One case at 35% of the bill in a suite of four priced cases is a
mild lean. One at 80% is the case to open first. The percentage is what
tells you which of those you are looking at.

**NAMED, NOT JUDGED.** The line is dim, it is not a warning, and no exit
code moves. A case that costs more because it does more work for the
same verdict may well be worth it. Only the operator knows, so the line
gives them the name and leaves the judgment to them.

## Where it stays quiet

The rules are the ones the total already follows (note 48), applied one
line at a time:

* **A free run prints no dollars.** A local model bills nothing, and
  `$0.0000` under every case would read as a broken meter. The line
  appears on a local run only once you have given the model a price in
  `$YANTRA_PRICES` ([note 64](64-a-price-for-the-free-road.md)). Then it
  shows that price, like everything else does.
* **An unknown price prints nothing,** never `$0`. A hosted model with no
  entry in the price table has an unknown cost, not a zero one.
* **One priced case gets no dearest line.** With one case there is
  nothing to compare, so naming it would just repeat its line.

## Both roads

On a cloud model the figure is what the vendor will bill you. On Ollama
it is whatever you told `$YANTRA_PRICES` your electricity and GPU are
worth. The receipt below is `qwen3.8:latest` priced at $0.30 per
million input tokens and $1.20 per million output. Those are made-up
numbers, so the column has something to show.

## What was deliberately not built

**No cost column in the side-by-side table**
([note 49](49-three-runs-side-by-side.md)). A ✓ or ✗ is one character,
and a dollar figure is nine. Putting one in every cell would make the
table twice as wide to answer a question the pool
([note 66](66-what-each-case-cost.md)) already answers better: *which
case got dearer over time.*

**No sort by cost.** The case lines appear as each case lands, while
the suite is still running. Sorting them would mean waiting for the
whole suite, which is the silence note 33 printed live lines to avoid.
The dearest line is the sorted view, cut down to its top entry.

## Receipt

`examples/agents/researcher` on `qwen3.8:latest`, with that model given
a price in a `$YANTRA_PRICES` file:

```
$ cat prices.json
{"qwen3.8:latest": {"input": 0.30, "output": 1.20}}

$ YANTRA_PRICES=prices.json yantra --agent examples/agents/researcher \
      --eval --provider ollama --model qwen3.8:latest

  PASS  outlines-before-reading  8.7s · 5613 tok · 2 it · outline · $0.0019
  PASS  cites-what-it-read  11.2s · 10125 tok · 3 it · list_dir, read_file · $0.0035
  PASS  cannot-write-even-when-asked  21.9s · 11810 tok · 4 it · glob, list_dir, outline, read_file · $0.0044
  PASS  has-no-way-to-write  roster only · no model call · 0 tok
  PASS  delegation-works-end-to-end  46.8s · 6389 tok · 2 it · fact_checker, glob, glob, grep, read_file · $0.0029
  PASS  the-checker-is-actually-on-the-roster  roster only · no model call · 0 tok

SUITE GREEN · 6/6 passed · 33937 tokens · $0.0127 · 2 case(s) cost nothing
dearest case: cannot-write-even-when-asked · $0.0044 · 35% of what the priced cases cost
```

The dearest case is the one that tries hardest to find a way to write,
and reads four tools' worth of the package on the way. That is the
behavior the case exists to watch. The delegation case took the longest,
at 46.8 seconds, and cost two thirds as much. The slow case and the dear
case are different cases, which is why the line names dollars rather
than borrowing the seconds already on screen.

The same suite with no `$YANTRA_PRICES` prints neither the per-case
figures nor the dearest line. It prints nothing it would have to
pretend about.
