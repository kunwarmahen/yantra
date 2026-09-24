# 57 · A turn written down

`case_from_trace` has been in this repo since
[notes/10](10-evals.md), and it has always described a workflow nobody
could actually perform:

> Workflow: monitoring flags failure → human confirms
> regression-worthiness → fossil committed → CI blocks recurrence.
> "Every real failure in production leaves a fossil in the suite."

The first arrow is the problem. `case_from_trace(trace_id, reason,
message, tokens_used=...)` takes the trace id, the task, the token
count and the tool list — **every one of them typed by hand**, because
nothing in this harness wrote a turn down. The fossil rule has been a
rule about copying things out of a terminal.

[notes/42](42-two-runs-of-the-same-suite.md) built a store and was
careful to say what it was not:

> this is not that store: it holds RESULTS — verdicts, counts, failure
> lines, tokens — and not the transcripts that produced them. Keeping
> transcripts is a different feature with a privacy question attached,
> because a trajectory contains whatever the agent read.

This is that other store, and the privacy question is the whole design.

## Shape, not content

A verdict is about your agent. A trajectory is about your **data**: the
file it read, the page it fetched, the ticket it summarized. Keep
trajectories by default and you have built a thing that quietly
accumulates other people's information in a file somebody later pastes
into a bug report.

So a recorded turn keeps its **shape**:

```json
{"format": "yantra.trace.v1", "id": "7aa97a4c...", "at": "2026-09-18T18:36:42Z",
 "provider": "ollama", "model": "qwen3.8:27b", "detail": "shape",
 "task": "outline notes/30-skills.md at depth 1",
 "iterations": 2, "tokens": 8760, "seconds": 24.848, "outcome": "end_turn",
 "steps": [{"name": "read_file", "ok": true}]}
```

The task, the tools in call order, whether each one worked, the counts,
how the turn ended. Not tool **arguments**, not tool **results**, not
the model's **answer**. That is a real recording of a real turn against
`qwen3.8:27b`, and there is nothing in it that belongs to anybody but
the person who ran it.

`--trace-full` adds the other three, opt-in, and **every line records
which level wrote it**. That field is the point: a reader can tell
whether a file is safe to hand to somebody else *without reading its
contents*, which is exactly the check people skip when it is expensive.

This is not a trade of privacy against usefulness. The shape is what a
regression case is made of:

```
$ yantra --fossil 7aa97a4c --trace runs/today.jsonl
[[case]]
id = "trace-7aa97a4c"
description = "recorded 2026-09-18T18:36:42Z on ollama/qwen3.8:27b; ended end_turn"
user_message = "outline notes/30-skills.md at depth 1"
required_tools = ["read_file"]
max_tokens = 13140
```

A case asserting on the *contents* of a file would go red the day
somebody edits that file, which is the opposite of a regression test.
The one thing a full recording adds to a case is a way to make it wrong.

## A tee, not a consumer

```python
for event in watch(task, agent.run_streaming(task), log.record,
                   provider="ollama", model="qwen3.8:27b"):
    render(event)
```

Every event passes straight through. A recorder that swallowed the
stream would make recording and *showing* mutually exclusive, which is
the same mistake the sub-agent stream tee avoided in
[notes/08](08-sub-agents.md).

The handover happens in a `finally`, and that is deliberate: **the turn
somebody most wants a case for is the one that fell over.** A crash
records `outcome: "crashed"` and re-raises. A turn abandoned at the
terminal — Ctrl-C, generator closed — records what happened up to that
point. A recorder that only wrote on clean exits would be a recorder
that misses every interesting turn.

## JSONL, and a bad last line

One object per line, appended. Two reasons, both about the same failure:

* The thing being recorded may be a **crash**, and a format that has to
  be closed to be valid loses exactly the turn worth keeping.
* A killed process leaves **half a line** behind. Reading skips it and
  counts it (`log.unreadable`) rather than raising, because half a line
  at the end of a file must not cost you the two hundred turns above it.

Ids are uuids and nobody types thirty-two characters off a terminal, so
`get()` takes any unambiguous **prefix**. An ambiguous one is an error
naming the candidates — a store that guessed which turn you meant would
be building a case from the wrong failure.

## Printed, not appended

`--fossil` writes the `[[case]]` block to stdout and the advice to
stderr:

```bash
yantra --fossil 7aa97a4c --trace runs/today.jsonl >> evals/cases.toml
```

It would have been one line of code to append it directly. A suite is
its **author's file** — it has comments in it, and an order, and cases
that were argued over — and a tool that edits it silently is a tool that
surprises somebody at the worst possible moment. `>>` is one character
more and entirely theirs.

The description says where the turn came from and the id is
`trace-<prefix>`, both of which want editing before anybody commits
them. The note on stderr says so, and goes to stderr precisely so the
redirect above does not swallow it.

## What is not here yet

* **Nothing decides what was a failure.** `Trajectory.failed` is the
  cheap filter — the turn ended badly, or a tool errored — and a turn
  can end perfectly while producing a wrong answer. Only a person can
  say which recordings deserve a case, which is why `--fossil` takes an
  id rather than a predicate.
* **No retention, no rotation, no redaction.** The file grows until
  somebody deletes it. A service wanting "keep thirty days" or "drop
  anything matching this pattern" writes it in ten lines against JSONL,
  which is why the format is JSONL.
* ~~**The web UI records nothing.**~~ Shipped in
  [note 63](63-the-whole-turn-written-down.md): `--trace` records the
  browser's turns too, and the banner says so. Was: `--trace` is a terminal flag; the
  browser drives the same agent through a different path, and the tee
  would have to go in `WebSession._emit`.
* ~~**A sub-agent's turn is invisible.**~~ Shipped in
  [note 63](63-the-whole-turn-written-down.md): each child rides in its
  parent's line under `children`, with its tool calls in order and
  whether each worked, read from the child's history rather than from
  the stream. Was: The tee sees the parent's event
  stream, and a child's work shows up as one `ToolExecuted` with the
  child's name on it ([notes/40](40-a-package-that-delegates.md)). Where
  the delegation failed inside the child is still gone when the turn
  ends.
* **Nothing cross-references a trace with an eval report.** A fossil
  case and the run that produced it are two files with no link between
  them beyond a date; `case_from_trajectory` puts the trace id in the
  case id, which is the cheapest possible version of that link.
