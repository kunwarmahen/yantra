# 37 · A gate that can wait, and one that can say why

[Note 20](20-approve-with-edits.md) left the permission gate in what
looked like a finished state: a plain function, one call per dangerous
tool call, `True` runs it and `False` turns into an error the model can
read and plan around. Every frontend in this repo fits that shape — the
terminal's y/n/e prompt, the browser's modal, `--yolo`, the read-only
auto-approver.

Then somebody embedded the harness in a service, and two sentences in
that paragraph turned out to be assumptions rather than facts. (Two more
assumptions turned up elsewhere in the same week;
[note 38](38-giving-it-back.md) has those.)

The first is **"one call"**. A function call finishes. The gate the
service needs does not: it sends a message to a person somewhere else and
waits for them to pick up their phone. In an `async` program, waiting
inside a plain function is not waiting — it is *stopping*. The whole
process stops, including every other conversation it is holding.

The second is **"the model can read it"**. What the model actually read
was the string `Permission denied by user.`, because that sentence was
typed into both agent loops as a literal. In a service there is often no
user in the room at all, and the one sentence the model gets about why it
was blocked was a guess about a person who did not exist.

Both are one-line changes to look at and neither is a one-line change to
get right, which is why they get a note.

## The expensive half: a coroutine is a yes

Here is the old line, from `AsyncAgent._gate`:

```python
allowed = self.permissions(request)
if not allowed:
    return ToolResult(call.id, "Permission denied by user.", is_error=True)
```

Hand that an `async def` gate and Python does exactly what it is told. It
calls the function, gets a **coroutine object** back without running a
single line of the body, and asks whether it is truthy.

A coroutine object is truthy.

```python
async def careful_gate(request):
    request.reason = "the owner refused this."
    return False                  # a NO -- if anyone ever awaits it

print("what the old line saw:", bool(careful_gate(request)))
```
```
RuntimeWarning: coroutine 'careful_gate' was never awaited
what the old line saw: True
```

That gate was written to say **no**. Every dangerous call in the session
would have run. There is no exception, no red text — the only trace is a
`RuntimeWarning` on a stream nobody is reading, arriving whenever the
garbage collector gets round to it.

This is the worst failure mode a gate can have, and it is also the most
likely one, because an `async def` gate is precisely what a person writes
the first time they embed the harness in an async app. So the fix is not
only "await it in the async agent". It is "make the synchronous agent
REFUSE it", loudly, at the point of use:

```python
def decide(gate, request) -> bool:
    answer = gate(request)
    if inspect.isawaitable(answer):
        ...                      # close it, then raise
    return bool(answer)
```

The raise lands in the loop's own `except` around the gate, which already
exists for gates that crash — so the call is **refused**, and the model is
told what went wrong in the same breath.

Handing that same gate to the synchronous `Agent` against a local
`qwen3.8:latest`, the model's own reply is now this:

```
The command was refused. I was told: the permission gate (`careful_gate`)
failed — it responded with an awaitable, and a gate that suspends needs
`AsyncAgent` (`Agent` is synchronous and can't await one).
```

Two small things in there are deliberate. The error names the class you
need, because "TypeError: not awaitable" would send you reading the
harness's source instead of changing one word in yours. And the coroutine
is `close()`d before the raise, so you get one error rather than an error
plus a warning about a coroutine nobody awaited — a second message that
describes the same mistake is a second thing to debug.

## The other half: awaitable-tolerant, not async

`AsyncAgent._gate` becomes a coroutine and awaits the answer *if there is
something to await*:

```python
async def adecide(gate, request) -> bool:
    answer = gate(request)
    if inspect.isawaitable(answer):
        answer = await answer
    return bool(answer)
```

**TOLERANT, NOT ASYNC-ONLY.** The alternative — declare that async agents
take async gates — would have been cleaner to type and would have broken
every gate in this repo, including the one `--yolo` uses and the one the
browser UI already has working. It would also have forced anyone writing
a gate to pick a colour for it in advance, which is exactly the decision
they do not yet have enough information to make. A plain `lambda r: True`
still works, in both agents, unchanged.

The gate *wrappers* needed nothing at all, which is a small piece of luck
worth naming: `trust_sandbox` and `SwitchableGate` both end in
`return inner(request)`. They pass the answer out without looking at it,
so an awaitable travels through them intact.

### The gates in a batch are awaited one at a time

When a model asks for three tool calls at once, the loop gates all three
before running any. That pass stays **sequential**:

```python
results = [await self._gate(c) for c in calls]
```

`asyncio.gather` would be faster and would be wrong. A gate that reaches a
human takes as long as the human takes, and three questions arriving in
someone's chat window simultaneously is not a permission prompt, it is a
pile. One question, one answer, next question. The sync agent has always
worked this way for a duller reason (the gates share one terminal); the
async twin now has the interesting one.

## The receipt that is the whole point

Two conversations in one process, on one event loop. Amara asks for a
shell command, and her gate has to reach an owner who is not at this
keyboard. Bhaskar asks an unrelated question *while that gate is waiting*.

```
$ uv run python examples/async_gate_demo.py
· ollama/qwen3.8:latest -- two conversations, one event loop

[  0.1s] amara     turn starts
[  1.5s] amara     gate: asking the owner about bash() ...
[  1.5s] bhaskar   turn starts
[  2.4s] bhaskar   turn ends: 'The capital of Senegal is Dakar.'
[  7.5s] amara     gate: the owner said no
[  8.8s] amara     turn ends: 'I was refused — the message said: **"bash is
                    off for chat sessions. Read the file instead if you nee...'
```

Both agents run against the same local Ollama model (`qwen3.8:latest`),
so there is no cloud spend in this receipt and you can reproduce it on
your own machine: the demo is
[`examples/async_gate_demo.py`](../examples/async_gate_demo.py), and
`--approve` shows the other branch (`Ran fine — hello, exit code 0.`).

Bhaskar's entire turn — model call, reply, done — happens between 1.5s and
2.4s, inside the six seconds Amara's gate spends waiting for a person.
Before this change those six seconds were six seconds of nothing: one
blocked event loop, every conversation in the process frozen behind one
person deciding about one `echo`.

## Denial with a reason

The second half is much smaller and reads as an apology for the first
version. `PermissionRequest` gains one optional field:

```python
def gate(request):
    request.reason = "your owner refused this: bash is off for chat sessions."
    return False
```

and the loop uses it in place of the default. This is deliberately the
**same shape as approve-with-edits** ([note 20](20-approve-with-edits.md)):
the request is a mutable scratchpad that the gate may write on while it
decides, and the loop reads back what it finds. No new return type, no
`Decision` object, no signature to migrate. A gate that has a user behind
it may write nothing and let the model read `Permission denied by user.`,
which for that gate is true. (The terminal and browser gates stopped
relying on that default in [note 39](39-a-clock-and-a-word.md) — not
because the sentence was wrong, but because a refusal with a person
behind it is worth saying out loud rather than by falling through.)

The most useful consumer turned out to be inside this repo, not outside
it. `allow_read_only` is the DEFAULT gate — it is what you get in an eval
suite, in a one-shot run, in anything unattended. It refuses every tool
that did not declare itself read-only, and it used to report that refusal
as a decision by a user who was never consulted. Now it says what actually
happened:

```
bash was denied: this session runs unattended and auto-approves read-only
tools only. Nobody is available to ask.
```

That is not politeness. A model that believes a person said no will try to
persuade the person; a model told that nothing is attached will go and
find a read-only route, which is the only thing that can work. In the
two-conversation receipt above, the model quotes its refusal back verbatim
— it treats the sentence as information, because it is.

`deny_all` gained one for the same reason.

## What the tests pin

The tests live in `tests/test_gate_async.py`, and the thing most of them
assert is **whether the tool RAN** — a class-level list the tool appends
to — rather than what text came back. A test that only read the
`ToolResult` would keep passing on the day a coroutine started being
treated as a yes, because the text would still say "denied" somewhere.

* An async gate's answer is awaited and obeyed, both ways.
* A plain function gate still works on the async agent, untouched.
* An async gate on the **sync** agent refuses, the bomb does not go off,
  and the message names `AsyncAgent`.
* Refusing one leaves no un-awaited coroutine (asserted with
  `RuntimeWarning` promoted to an error).
* A suspended gate does not block the loop — asserted as *other work
  making progress*, not as elapsed time, which would pass just as happily
  on a blocked loop that happened to be slow.
* Gates in one batch overlap exactly zero times.
* A cancelled turn raises rather than becoming a denial: a dropped
  connection is not a human saying no, and history must not record one.
* A gate's reason reaches the model on both paths; a refusal with no
  reason still says the old sentence; a reason set while APPROVING is
  ignored.
* Approve-with-edits still works through a gate that suspends.

## What is not here yet

* ~~**No timeout.**~~ A gate that waits forever waits forever; the
  harness offered no deadline and no default answer. Shipped in
  [note 39](39-a-clock-and-a-word.md) — and the deliberate part survived
  intact: `with_deadline` owns the stopwatch and still refuses to own the
  verdict, so `on_timeout` has no default and a caller that does not
  state its policy gets a `TypeError` rather than somebody else's.
* **No way to ask the person a question other than yes/no.** Half of
  this is closed: ~~"not like that, like this"~~ is a person's own
  sentence now, typed at the prompt and read by the model
  ([note 56](56-not-like-that-like-this.md)) instead of hand-edited into
  an arguments dict. What is still true is the other half — a gate
  cannot POSE a question ("staging or prod?") and receive an answer that
  is not a bool, and that shape probably belongs to `ask_user` rather
  than to the smallest interface in the harness.
* ~~**The reason is a string, not a structured refusal.**~~ A caller that
  wanted to distinguish "policy said no" from "nobody answered in time"
  had to parse prose. Shipped in [note 39](39-a-clock-and-a-word.md), and
  the deadline is what earned it: those two refusals are the first pair
  that look identical in English and demand opposite handling.
* ~~**A gate cannot tell which call it is deciding.**~~ Never written
  down here, and it should have been. A host that only approves or
  refuses needs nothing from the id; one that RECORDS what happened had
  to pair a decision with the `ToolExecuted` it produced by COUNTING —
  which works, and is the wrong thing to depend on. It rests on three
  invariants of this loop (gates sequential, one gate per call, results
  in submission order), none of them promised to callers, and a drift in
  any of them does not raise: it files one person's approval against a
  different call. `PermissionRequest.call_id` closes it, defaulted so a
  request built by hand is still a valid one.
* **`PermissionRequest.reason` is advisory in the other direction too.**
  Nothing stops a gate from writing a reason and then approving; the loop
  simply ignores it. Enforcing that would cost a check in the hot path to
  prevent a mistake with no consequence.
