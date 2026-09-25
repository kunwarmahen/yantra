# 85 — What the child read

A sub-agent is a second agent that the main one hands a job to. It gets
its own fresh conversation and its own tools, does the work, and hands
back a summary. The main agent (the *parent*) never sees how the child
got there. That is the point of a child: its reading stays out of the
parent's context window.

It also meant that nobody else could see how the child got there
either. [Note 63](63-the-whole-turn-written-down.md) put children into
the recording: which tools each child called, in order, whether each
worked, and how it stopped. `--trace-full` added the task the parent
gave the child and what the child answered. That still left a gap, and
[note 40](40-a-package-that-delegates.md) and
[note 55](55-two-at-a-time.md) both listed it as open:

```
child 1 fact_checker · grep ✓ · grep ✓ · read_file ✓ · finished
```

The parent's own steps, at `--trace-full`, come with the arguments each
tool was called with and what it returned. The child's steps came with
names only. If a child gave a wrong verdict, the recording could tell you
*that* it read a file. It could not tell you which file, or which lines,
or what it searched for. As note 55 put it: a code says what went wrong,
and nothing says where.

## A child at full is kept exactly as a parent at full

With `--trace-full`, each of a child's steps now carries the same two
things a parent's step does: the **arguments** it was called with, and
the **result** it got back, clipped to the same 2,000 characters.

```json
"children": [{
    "number": 1, "agent": "fact_checker", "model": "qwen3.8:latest",
    "steps": [
        {"name": "grep", "ok": true,
         "arguments": {"pattern": "token bucket", "case_insensitive": true},
         "result": "examples/agents/researcher/sources/rate-limiting.md:60: ### Token bucket\n..."},
        ...
    ]
}]
```

**ONE ROW SHAPE FOR BOTH.** This did not add a new detail level or a
new flag. The rule is *parity*: once you have learned what a full line
holds for the parent, you already know what it holds for every child.
The parent's steps and the child's steps are now written by the same
function, so they cannot drift apart.

**THE DEFAULT IS STILL SHAPE.** Without `--trace-full`, a child's step
is still `{"name": ..., "ok": ...}` and nothing more. A child reads
files for a living, so its results are exactly the kind of content
[note 57](57-a-turn-written-down.md) keeps out of a default recording.

**THE SCRUBBER REACHES THE CHILD.** `--trace-redact`
([note 79](79-scrubbed-before-it-is-written.md)) scrubbed a child's task
and answer before a child had anything else worth scrubbing. It now
scrubs a child's arguments and results too, and counts those matches in
the same `redacted` total. That mattered more than anything else here: a
scrubber that skipped the new field would have written the very thing
the flag promises to remove.

## Where the clipping happens

A child's conversation is thrown away when it finishes, so its steps
have to be copied out at that moment, before anyone knows whether a
recorder will want them. The sub-agent code therefore keeps the
arguments and result for every step, always. The recorder decides what
to write, because that decision is argued in the recorder, not in the
sub-agent code.

Results are clipped **when they are kept**, not when they are written.
A child that reads a 200 KB file would otherwise hold all of it in
memory for the rest of the session, just in case. The tradeoff: a host
that reads the spawner's results directly, not through a recording,
gets clipped results too. A host that needs a child's whole read has to
record it itself, through the child's stream.

## Both roads

Nothing here depends on the model. What gets kept is what the tools
returned, and a tool returns the same thing whether a local model or a
cloud one called it. The receipt below is a local model. A cloud run
records the same fields.

## What was deliberately not built

**The child's words between tool calls.** When a model thinks out loud
between tool calls ("the first grep found nothing, try the plural"),
that text is not kept. The parent's text between its own tool calls is
not kept at full either. Parity cuts both ways: if it is ever worth
keeping, it is worth keeping for both levels at once.

**A file per child.** The recording is one line per turn, and every
tool that reads it (`--turns`, `--fossil`, `--mark`, `--trace-prune`)
relies on that. A second file per child would be a second thing to
prune, scrub and lose track of.

**Grandchildren.** There are none to keep. A child is not allowed to
spawn children of its own ([note 08](08-sub-agents.md)), so a flat list
of children is the whole story.

## Receipt

`examples/agents/researcher` on `qwen3.8:latest`, asked to have its
`fact_checker` child check a claim:

```
$ yantra --agent examples/agents/researcher --provider ollama \
    --model qwen3.8:latest --trace child.jsonl --trace-full \
    --prompt "Use fact_checker to check this claim against the sources: \
'a token bucket allows short bursts above the average rate'. \
Then give me its verdict."
...
Verdict: **SUPPORTED.**
── end_turn · 3155 in / 231 out · 2 iteration(s)
```

The parent's own line shows one step, `fact_checker ✓`, as it always
did. The child's steps, printed from the file with each string cut to
160 characters for this page:

```
child 1 fact_checker qwen3.8:latest finished · 4 iterations
{"name": "grep", "ok": true, "arguments": {"pattern": "token bucket", "case_insensitive": true}, "result": "examples/agents/researcher/sources/rate-limiting.md:60: ### Token bucket\nexamples/agents/researcher/sources/rate-limiting.md:73: Equivalent to token bucket for …"}
{"name": "grep", "ok": true, "arguments": {"pattern": "burst", "case_insensitive": true}, "result": "examples/agents/researcher/sources/rate-limiting.md:57: a few percent under bursty traffic, and a small constant per client.\n…"}
{"name": "grep", "ok": true, "arguments": {"pattern": "rate.?limit", "case_insensitive": true}, "result": "TUTORIAL.md:174:   cannot be crashed by a tool. Provider errors — auth, rate limits,\n…"}
{"name": "glob", "ok": true, "arguments": {"pattern": "*"}, "result": "README.md\nTUTORIAL.md\n…"}
{"name": "read_file", "ok": true, "arguments": {"path": "examples/agents/researcher/sources/rate-limiting.md", "offset": 40, "limit": 50}, "result": "    40\tcan send its whole allowance in the last second of one window and again\n…"}
{"name": "read_file", "ok": true, "arguments": {"path": "examples/agents/researcher/sources/rate-limiting.md", "offset": 60, "limit": 9}, "result": "    60\t### Token bucket\n    61\t\n    62\tA bucket holds up to `burst` tokens and refills at `rate` per second;\n    63\teach request takes one. Allows a burst up to…"}
```

Before this change, all the file could say was *grep, grep, grep, glob,
read_file, read_file, all fine*. Now it shows the route. The child found
the right section with its first search. Its third search, `rate.?limit`,
matched the tutorial as well as the sources, and the child then listed
the whole repository root before going back to the file it had already
found. The verdict was right, and the detour cost two tool calls. That
detour is also the thing a names-only line cannot show: six steps, all
fine, and no way to tell a child that went straight to the answer from
one that searched the whole repository first.

`1970 passed, 1 skipped` (was 1960).
