# 63 — The whole turn, written down

`--trace FILE` ([notes/57](57-a-turn-written-down.md)) records every
turn of a session as one line of JSON: the task, which tools ran and
whether each worked, and what it cost. `--fossil` can then turn a
recorded failure into a regression case. Note 57 ended with two holes in
that recording. Both were invisible, which is the worst kind: a file
with a gap in it looks exactly like a complete file.

**The browser recorded nothing.** `--trace` worked in the terminal. With
`--web`, the same agent runs through a different loop, in the web
server, and that loop had no recorder attached. The flag was accepted,
the file stayed empty, and nothing said so.

**A sub-agent's work vanished.** When the agent hands a job to a
sub-agent (a *child*, [notes/08](08-sub-agents.md)), the parent's
recording showed one tool call, the child's name, marked as having
worked. Whatever the child did inside was gone when the turn ended. So a
child that failed three reads and came back with a guess was recorded
the same way as one that did its job. A delegation that went wrong inside
the child is exactly the failure someone wants to turn into a case, and
it was the one the recording could not show.

## The browser

The web server's turn loop now runs through the same recorder as the
terminal's. The recorder is a *tee*: it passes every event through to
the page unchanged and writes the line when the turn ends, including
when you press Stop. `--trace` means the same thing with `--web`, and the
banner says it is on:

```
  yantra web UI -> http://127.0.0.1:8398
  provider=ollama · model=qwen3.8:latest · tools=10 · mcp servers=0 · cwd=.../examples/agents/researcher
  recording turns -> runs/web.jsonl (shape)
  ctrl-c stops the server
```

One flag, both frontends. The rule behind this: **a recording with a hole
nobody can see is worse than no recording.** If `--trace` recorded the
terminal and silently not the browser, people would draw conclusions
from a file that was missing half the evidence.

## The child

A line now carries the children its turn spawned, under `children`:

```json
"steps": [
  {"name": "fact_checker", "ok": true}
],
"children": [
  {
    "number": 1,
    "agent": "fact_checker",
    "model": "qwen3.8:latest",
    "iterations": 2,
    "tokens": 3947,
    "code": null,
    "steps": [
      {"name": "glob", "ok": true},
      {"name": "read_file", "ok": true}
    ]
  }
]
```

That is a real turn: `examples/agents/researcher` on `qwen3.8:latest`,
asked to have its `fact_checker` child check a claim about its own
prompt. The parent's view is one call. The child's is a `glob` and a
`read_file`, both worked, and it finished (`code` is null; otherwise it
would be `provider_error` or `iteration_cap`, [notes/55](55-two-at-a-time.md)).
`number` matches the `#1` the live view already prints beside the
child's output, so the terminal and the file use the same name for the
same child.

**A CHILD'S TROUBLE IS THE TURN'S TROUBLE.** `Trajectory.failed`, the
quick filter a host uses to find turns worth a look, now counts a failed
step inside a child, or a child that did not finish. The parent often
smooths over a child's failure in its answer ("the sub-agent could not
find it, but..."). That is the model's decision. It does not make the
turn clean.

### Where the steps come from

The obvious source is the live stream the terminal already shows. It
turns out to be the wrong one. That stream carries only the child's raw
model output (text as it arrives, and the *start* of each tool call),
never whether a call succeeded. What does carry it is the child's own
history: every tool call it made and every result it got back, complete
whichever of the three ways the child stopped. So the spawner reads that
history once, when the child finishes, and keeps each call's name and
whether it worked. A call with no result at all, because the child was
cut off mid-batch, counts as not having worked.

The recorder then takes the children that finished *during this turn*:
it notes how many results the spawner had when the turn started, and
takes the ones after that. That is exact, because every host here runs
one turn at a time.

### A race found on the way

Each child's number was read from the spawn counter *after* the
spawner's lock had been released. [Note 55](55-two-at-a-time.md) put
that counter behind a lock, because two children starting at once could
both see "4 of 5 used". The number was then read a second time, outside
the lock, where two parallel children could both read `2`. That only
ever mislabelled the live view. In a file meant to say which child did
what, it would have been a wrong record. The number is now returned from
inside the lock, and a test starts forty spawns at once and checks for
forty different numbers.

## What stays out

The privacy rule from note 57 applies to children too. **SHAPE, NOT
CONTENT, IS THE DEFAULT.** A default recording keeps the child's tool
names, whether each worked, and the counts. It does *not* keep the task
the parent gave the child, and that one is less obvious than it looks.
The parent model wrote that task, and it wrote it out of whatever it had
just read, so it can quote a private file as easily as the file itself
can. `--trace-full` keeps it, along with the child's answer, clipped to
the same length as everything else at that level.

Child tool *arguments* and *results* are not recorded even at FULL. The
parent's own are, at FULL, because they come from the event stream as
the turn runs. A child's would have to be copied out of its history, and
that is a full transcript. Note 40 already says a service that wants the
transcript has to keep it itself.

## `--fossil` says what the children did, and does not assert it

```
note: sub-agent #1 fact_checker on qwen3.8:latest: glob, read_file; finished
```

This goes to stderr, next to the case block, not into it. A case that
pinned a child's inner steps would go red the first time the child found
a better route to the same answer. That is the brittleness note 57 turned
down for file contents, one level further in. What the child is *allowed*
to do is already assertable (`subagent_has_tools`,
[notes/44](44-a-ceiling-and-a-floor.md)), and allowed is the part that
stays stable.

## Both roads

Nothing here depends on the provider. Both receipts in this note are
local runs, and a cloud run records the same fields. The only difference
is `usd`, which the terminal session leaves empty for a local model the
same way it always has.

## What was deliberately not built

**No separate line per child.** A child is part of the turn that spawned
it, and a file where a turn's evidence is spread across several lines
has to be joined back together before anyone can read it. Nesting keeps
one turn on one line.

**No child-of-a-child.** Children are one level deep
([notes/08](08-sub-agents.md)), so `children` has no `children`. If that
rule ever changes, the key is already the right shape to nest.

**No per-child cost.** `tokens` is there; dollars are not. A child can
run on a different model from its parent ([notes/50](50-the-rest-of-what-a-child-is.md)),
and pricing it properly means the per-model price record that
[notes/62](62-the-reports-you-already-have.md) deliberately left for
later.

## What is not here yet

* ~~**The page does not show that it is recording.**~~ Shipped in
  [note 64](64-a-price-for-the-free-road.md): a `rec` chip in the header,
  red when the recording is FULL. Was: The banner in the
  terminal says so; the browser tab does not. Anyone using the page on a
  shared machine should be able to see it too.
* ~~**Refusal codes for a child's calls are not kept.**~~ Shipped in
  [note 65](65-the-turn-behind-the-red-line.md): each agent keeps its
  turn's refusals, and a child step carries the code. Was: The parent's steps
  record *why* the permission gate refused a call ([notes/39](39-a-clock-and-a-word.md)).
  A child's history only has an error result, not the code, so a child
  refused by the gate looks the same as a child whose read failed.
* ~~**Nothing cross-references a trace with an eval report.**~~ Shipped
  in [note 65](65-the-turn-behind-the-red-line.md). Was: Still true
  from note 57.

## Receipt

The terminal turn above, and the same through the browser, posted to the
running server with `curl`:

```
Use your fact_checker sub-agent to check: does prompt.md mention the outline tool?
parent: ['fact_checker'] end_turn
child #1 fact_checker: [('glob', True), ('grep', True), ('read_file', True)] None
```

`1674 passed, 1 skipped` (was 1662).
