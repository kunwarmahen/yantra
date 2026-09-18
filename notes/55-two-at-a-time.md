# 55 · Two at a time, and a word for what went wrong

[notes/40](40-a-package-that-delegates.md) gave a package declared
children, and ended with two admissions about what happens when several
of them run at once:

> **Nothing bounds concurrent children.** Two delegations in one batch
> really do run at once […] and the ceiling on that is
> `max_parallel_tools`, which was sized for file reads rather than for
> whole agents. Eight children in flight is eight conversations sharing
> one spawn budget and one dollar meter, and neither of those was
> designed with a race in mind.
>
> **A child's failure is a string.** `[INCOMPLETE -- sub-agent hit its
> iteration cap]` reaches the parent as prose.

Both are fixed here. They are one note because they are the same
mistake in two places: a child was being treated as if it were a tool
call.

## A child is not a file read

`max_parallel_tools` defaults to eight. That number was chosen for
reading eight files, which is eight short blocking calls that finish in
milliseconds and cost nothing.

A delegation on that list is a *whole conversation*: its own iteration
cap, its own tool calls, its own model spend, running for as long as it
takes. Eight of those at once is eight agents against one provider, one
spawn budget and one dollar meter.

So children get their own ceiling, and its default is **two**:

```python
SubagentSpawner(agent, max_parallel=2)
spec.build(max_parallel_children=4)      # a host that wants more
```

Two, and not eight, for two independent reasons:

* **Concurrency does not create hardware.** Roughly half of this repo's
  readers run a local model, and one GPU serves one model. Note 35
  measured exactly this for the eval runner: on one desktop GPU the
  suite got *slower* under `--async`, because the requests queued in the
  server instead of on the client. Eight children on a local box is
  eight queued conversations and one hot GPU.
* **The meter is shared.** Every child charges the parent's budget
  ([notes/34](34-budgets.md)). The ceiling is checked between calls, so
  the number of conversations in flight is exactly the amount of spending
  that can happen after the last check and before the next one. Two is a
  small overshoot; eight is not.

Two is enough to overlap two waits, which is the whole of what
concurrency buys a delegating agent that is not doing search.

The slot is held around the child's **run**, not around validation or
the budget check. What is being limited is conversations in flight, and
a spawn that was refused never became one.

There are two slot counters, one per call path — a thread cannot wait on
an `asyncio.Semaphore` and an event loop must not block on a threading
one. They never both apply: a child's kind is keyed to its caller
([notes/40](40-a-package-that-delegates.md)), so a session drives the
sync path or the async path, not both.

## The race that was actually there

Bounding concurrency is not the same as making the budget safe, and the
budget was not safe:

```python
if self.spawned >= self.max_per_session:
    raise ToolError(...)
self.spawned += 1
```

A check and an increment. Two operations. The synchronous loop runs a
batch on a thread pool, so two children can both read "4 of 5 used", both
pass, and both spawn — and the failure does not raise anything. It
quietly spends more of somebody's money than they authorised, which is
the same shape of bug as a permission approval filed against the wrong
call ([notes/39](39-a-clock-and-a-word.md)): silent, rare, and about
consent.

It is a lock now, because the check and the increment have to be one
thing. The test drives twelve threads at a budget of five through a
barrier and asserts that exactly five got through — a test that
constructs a spawner and reads `max_per_session` back would pass either
way.

## A word beside the sentence

```python
SubagentResult(summary=..., error="sub-agent hit its iteration cap: ...",
               code="iteration_cap")
```

Two codes, because two things go wrong and they want **opposite
handling**:

| code | what it means | what a host does |
|---|---|---|
| `provider_error` | the model API failed inside the child | retry later |
| `iteration_cap` | the child ran out of rounds | do NOT retry — it needs a different task, not another go at the same one |

This is [notes/39](39-a-clock-and-a-word.md)'s argument in a second
place, and it is the same argument: a caller that had to tell those
apart by matching on English breaks the day somebody improves the
sentence, and the sentence *should* be improvable — the prose is written
for the parent model, which reads it and decides what to say next.

Nothing in this repo branches on the code yet, exactly as nothing
branched on a refusal code when note 39 shipped it. The field is on
`SubagentResult`, which is already where a host looks (`spawner.results`
is the observability surface), and it costs one word.

## What is not here yet

* **The cap is not in the manifest.** `max_parallel_children` is a build
  argument, not an `agent.toml` key, for the reason `max_parallel_tools`
  is not one either: it describes the host's appetite for parallelism
  rather than the agent's character. A package author does not know
  whether their agent will run on a laptop's GPU or on a metered API.
* **A queued child holds its spawn.** The budget is charged when the
  spawn is attempted, so a child waiting for a slot has already spent one
  of the five. That is the right way round — a budget that only counted
  children that got started would be a budget a slow queue could hide
  from — but it does mean "budget exhausted" can arrive while two
  children are still waiting to run.
* **Nothing measures the right number.** Two is argued, not measured.
  The honest experiment is a package with several independent
  delegations, run at 1, 2, 4 and 8 against both a local server and a
  metered API, and it needs a package that genuinely wants parallel
  children — the example package's one fact-checker does not.
* **A child's transcript is still gone** once the turn is over
  ([notes/40](40-a-package-that-delegates.md)), so a code says what went
  wrong and nothing says where.
