# 46 · The cases that were red last time

A suite run leaves a file behind ([notes/42](42-two-runs-of-the-same-suite.md)):
every case, whether it passed, how many attempts it took, what it cost.
That file was built to answer one question — *was that better or worse
than yesterday?* — and it answers it well.

It is the wrong question to ask first. Here is the real morning, in the
order it happens:

```
$ yantra --agent . --eval --report runs/today.json
SUITE RED · 4/6 passed · 47236 tokens
```

Two cases are red. You fix them. Now what? The suite is the only thing
you can run, so you run all six, pay for all six, and wait for all six
to tell you about two. On this machine that is 47,000 tokens and about
two minutes to re-check a one-line edit.

The information needed to run only the two was sitting in
`runs/today.json` the whole time. Nothing could read it: `--against`
opens that file at the *end* of a run, to compare. Nobody had opened it
at the beginning, to choose.

## The same file, read at the other end

That is the whole feature.

```
$ yantra --agent . --eval --failed runs/today.json
```

`--failed FILE` selects the cases the report recorded as red, and runs
those. With no `FILE` it uses the report `--against` names, because
"re-run what broke, then tell me what moved" is one sentence and should
be one command:

```
$ yantra --agent . --eval --failed --against runs/today.json
```

A live run of that, against `qwen3.8:27b` on a desktop GPU. The package
is `examples/agents/researcher`; the red run above came from widening
one sub-agent's tool list by a single word, which turned the roster case
red and — because the child then read differently — took the delegation
case down with it. The word was put back, and then:

```
filtered: red in runs/red.json -- 2 of 6 case(s); this is not the package's gate

  PASS  delegation-works-end-to-end  23.0s · 8423 tok · 3 it · fact_checker, …
  PASS  the-checker-is-actually-on-the-roster  roster only · no model call · 0 tok

SUBSET GREEN · 2/2 passed · 4 case(s) not run · 8423 tokens

against researcher 0.1.0 on ollama/qwen3.8:27b (4/6 passed)
  fixed  delegation-works-end-to-end  0/1 → 1/1
  fixed  the-checker-is-actually-on-the-roster  0/1 → 1/1
tokens: 47236 → 8423 (-38813)
```

Eight thousand tokens instead of forty-seven thousand, and twenty-three
seconds instead of two minutes, to learn the same thing about the edit
that was actually made.

## Why it is a file and not a word

The obvious spelling was `--case failed` — the filter flag already
exists, and "failed" reads like a pattern anybody would guess.

It is also a pattern anybody could *write*. `--case` matches case ids
with fnmatch, so a suite holding a case called `failed` — not a strange
name for a case about failure handling — would have two meanings for one
string, and the wrong one would be chosen silently. There is a whole
class of bug this repo keeps refusing
([notes/44](44-a-ceiling-and-a-floor.md) turned a typo'd sub-agent name
from a green case into an error for the same reason): a selection that
quietly runs something other than what was asked for looks exactly like
success.

So the report is named, not implied. The two flags compose, and each one
still means one thing: `--failed` chooses, `--case` narrows, and a run
with both says so.

## A report names ids; a suite owns cases

The two can disagree, and the disagreement is worth more than the run.

A red case whose id is not in the suite any more is *usually* a rename,
occasionally a deletion, and never something the program can tell apart.
So it is printed and not acted on:

```
not in this suite any more: renamed-since -- red in runs/red.json, and gone since
```

…and the cases that do still exist run anyway. The one exception is the
case where *every* red id has vanished: the selection is then empty, and
A RUN OF NOTHING PASSES EVERYTHING. That is the one verdict this may
never print, so it is an error with a sentence and exit 2.

The happier empty selection has the opposite answer. A report with no
red cases in it means there is nothing to re-run, which is success:

```
$ yantra --agent . --eval --failed runs/green.json
every case in runs/green.json passed -- nothing to re-run
```

Exit 0, no provider resolved, no request made. Same shape as a
roster-only run ([notes/41](41-a-gate-you-can-point.md)): the work that
does not have to happen should not cost a key.

## It is a subset, and it says so

Everything [notes/41](41-a-gate-you-can-point.md) argued about `--case`
applies unchanged here, because this is the same kind of restriction
arriving by a different route. The header names it, the verdict word is
`SUBSET` rather than `SUITE`, and the report this run writes records
`filtered: ["red in runs/red.json"]` so that tomorrow's comparison knows
it is looking at two runs that graded different sets.

`SUBSET GREEN` after `--failed` means *the things that were broken are
not broken now*. It does not mean the package is green, and the one
person who most needs to be told that is the person who just fixed two
cases and feels finished.

## What is not here yet

* **Which report is "last time" is still the operator's problem.** There
  is no `--failed` with no argument and no `--against`, no "the newest
  file in `runs/`", no convention about where reports live. Guessing
  would mean re-running against a file nobody named, which is the one
  thing this feature exists to avoid.
* **The comparison prints a `gone` line per case that was not re-run.**
  After `--failed` those lines are, by construction, the cases that
  passed — four of them in the receipt above. The header says the
  added/gone lines are about the selection, which is true and is still
  four lines of noise; a comparison that knew it was reading its own
  filter could say "4 passing cases not re-run" in one.
* **Nothing re-runs the cases whose pass RATE dropped.** A case that went
  9/10 → 6/10 is still `passed`, so `--failed` does not select it, and it
  is exactly the case somebody would want more samples of. The selection
  reads one boolean; a threshold on the rate would be a second flag and a
  second argument about what number.
* **No `--failed` for a comparison.** `--against` takes one report, so
  "the cases that broke between these two runs" — a narrower and more
  useful set than "everything red in the later one" — has no spelling.
