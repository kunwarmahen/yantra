# 39 · A clock on the question, and a word for the answer

[Note 37](37-a-gate-that-can-wait.md) taught the permission gate to wait.
A gate is a function the harness calls before any tool that could change
something — write a file, run a command — and after note 37 that function
was allowed to *suspend*: it can send the question to a person who is
somewhere else entirely and hand the event loop back while they decide,
so the other conversations in the process keep running.

Which left an obvious hole, and note 37 said so in as many words:

> **No timeout.** A gate that waits forever waits forever; the harness
> offers no deadline and no default answer.

It also said why that was deliberate — *a timeout that denies is a
policy, and policy belongs to whoever owns the conversation* — and
predicted that the first service to need one would wrap its own. That
happened. The wrapper was about fifteen lines, and three of them were
wrong in ways that took a while to see.

This note is the deadline moving into the library **with its policy left
out**, which turns out to be a shape worth explaining, plus the small
second feature the deadline forced into existence: a machine-readable
word for *why* a call was refused.

## The part that was never the library's to decide

Every harness that grows a timeout grows a default with it, and the
default is always the same:

```python
gate = with_deadline(ask_the_owner, seconds=30)   # ... and then what?
```

Then *what*? There is only one answer that feels safe, so that is the one
that gets written: unanswered means no. And it is right — for a deploy,
for a production database, for anything where the absence of a person is
itself a reason to stop.

It is wrong for the overnight batch. Somebody there set a deadline
precisely so the run would **go ahead** when nobody was around to nod at
it; a deadline that denies turns their nightly job into a pile of refused
calls and an empty report. Both of those people are using the same
harness, and neither of them is more correct than the other.

So `on_timeout` has no default. It is a keyword argument with no value
behind it, and the wrapper cannot be constructed without one:

```python
from yantra import with_deadline

gate = with_deadline(ask_the_owner, 30, on_timeout="deny")    # a deploy
gate = with_deadline(ask_the_owner, 30, on_timeout="allow")   # a batch
gate = with_deadline(ask_the_owner, 30)                       # TypeError
```

A deadline is two things welded together in most implementations: a
**stopwatch**, which is mechanism, and a **verdict on silence**, which is
policy. Splitting them is the entire design here. The library keeps the
stopwatch — which is the half that is easy to get wrong — and refuses to
hold an opinion about what the silence meant.

## The three lines that were wrong in the hand-rolled version

If the policy is the caller's, why does the stopwatch belong here at all?
Because these are the parts nobody gets right the first time, and every
host hits all of them.

**Cancelling the question, not just abandoning it.** When the clock runs
out, the pending question is *cancelled*, which means a gate waiting on a
chat message sees `CancelledError` and can take the buttons away. Without
that, a chat window keeps offering **Approve** for a call that can no
longer happen, and the person who finally taps it is told nothing
happened.

```python
async def gate(request):
    try:
        return await self.desk.ask(request)
    except asyncio.CancelledError:
        await self.desk.withdraw(request)     # now possible
        raise
```

**Telling an expired clock apart from a dropped connection.** These look
identical from inside the gate and mean opposite things. A deadline
expiring is a decision: somebody's policy said what silence means. A
turn being cancelled — the HTTP connection dropped, the user closed the
tab — is *nobody's* decision at all, and recording it as a refusal writes
a lie into the conversation history that the model will read on the next
turn. `asyncio.timeout` distinguishes them correctly, which is exactly
why the wrapper uses it instead of the `wait_for` that comes to mind
first, and a test pins the difference rather than trusting it.

**Zero.** In roughly half the config files in the world, `timeout = 0`
means *off*. Here it would mean every question expires before the person
it was sent to can possibly answer, and the symptom is a service that
refuses everything with no error anywhere. So zero and negatives raise,
and the message says the thing the reader is about to assume:

```
a permission deadline must be positive, got 0; 0 does not mean 'no
deadline' here -- it means every question expires before it can be
answered (for no deadline, do not wrap the gate)
```

There is a fourth, inherited from note 37 and worth restating because it
is invisible: **a deadline only binds a gate that suspends.** If the gate
is a plain function — including one blocking on `input()` at a terminal —
it has already returned an answer by the time the wrapper could have
started a clock. Nothing can be done about that from here; interrupting a
blocking call means a thread and a cancellation story the callee never
agreed to. So the wrapper passes an inline answer straight through, and
a test pins it, so the limitation stays a documented shape rather than
turning into a bug report.

## The word: a refusal the caller can branch on

Note 37 gave a refusal a **reason** — a sentence, written for the model,
that replaced the hardcoded `Permission denied by user.` in a program
where there was frequently no user. That sentence does its job. It is
useless to the *other* reader.

The deadline is what made that concrete. A service now has two refusals
that produce identical-looking English and demand opposite handling:

* *nobody answered in time* — worth retrying, worth telling the owner
  their agent is stuck waiting on them, not a mistake anybody made;
* *your policy forbids this* — never retry, and the person who set the
  policy already knows.

A caller that had to tell those apart by matching on prose would work
until somebody improved the wording, and would then break silently. So
every refusal now carries a **code** beside the reason: a short machine
token, never shown to the model, that arrives on the event stream.

```python
for event in agent.run_streaming("tidy up the logs"):
    if isinstance(event, ToolExecuted) and event.refusal is not None:
        metrics.increment(f"refused.{event.refusal}")      # a label
        if event.refusal == REFUSED_TIMEOUT:
            notify_owner("your agent is waiting on you")
```

**A CODE IS A TOKEN, NOT A SENTENCE**, and the library enforces that at
the point of writing rather than the point of reading:

```python
refuse(request, "the owner is asleep", code="The owner is asleep.")
# ValueError: refusal code 'The owner is asleep.' is not a token; a code
# is matched with == by callers (want lower-case letters, digits and
# underscores -- the sentence goes in reason)
```

Put prose in the code field and nothing goes wrong for months. It
surfaces as somebody else's `==` comparison that silently never matches,
in a service, at three in the morning.

The ones shipped here are `user` (a person was asked and said no),
`unattended` (nobody was there to ask), `timeout`, `policy`, and
`unspecified` for a gate written before any of this existed. The field is
a plain string, not an enum: a host with refusals of its own — *out of
credit*, *this guest is rate-limited* — names them without sending a
patch to this repo.

The two readers stay separated on purpose. `denial_text` gives the model
prose; `denial_code` gives the caller a token; neither is derived from
the other, because a token guessed from a sentence is worse than an
honest `unspecified`.

### The gate that says "no" honestly

One consequence worth naming. The terminal's y/n prompt is the *only*
gate in this repo where `Permission denied by user.` was ever true, and
it now says so deliberately rather than by falling through to a default:

```python
return refuse(request,
              f"{request.tool_name} was denied: you said no at the "
              f"approval prompt.", code=REFUSED_USER)
```

`refuse()` returns `False`, always, and that is the point of its
existence: a gate ends `return refuse(...)` and cannot write a reason and
then approve by accident.

## Receipts

Same absent owner, same question, same four-second deadline, one word
different. Run against a local Ollama model — `qwen3.8:latest`, no key
and no spend — with
[`examples/gate_deadline_demo.py`](../examples/gate_deadline_demo.py):

```
· ollama/qwen3.8:latest -- one absent owner, two policies

--- on_timeout='deny', deadline 4s ----------------------------------
[  8.1s] owner     a question about bash() goes out ...
[ 12.2s] owner     the question expired and was withdrawn
[ 12.2s] host      bash() refused, code='timeout'
[ 14.1s] model     final answer: 'I tried to run `echo hello`, but the command
                    was denied: "bash was denied: the approval request went
                    unanswered for 4 seconds and expired."'

--- on_timeout='allow', deadline 4s ----------------------------------
[ 16.1s] owner     a question about bash() goes out ...
[ 20.1s] owner     the question expired and was withdrawn
[ 20.1s] host      bash() ran: 'hello\nexit code: 0'
[ 21.2s] model     final answer: 'Ran it — output: `hello` (exit 0).'
```

The host's two lines come off the event stream, and it never parses a
sentence to produce them: `event.refusal` is `'timeout'` in the first run
and `None` in the second.

The more interesting receipt is what the sentence does to the model. Ask
the same local model for something it needs a file to answer, with the
owner still absent and the deadline set to deny:

```
bash({'command': 'wc -l < README.md; ...'})   -> refusal='timeout'
read_file({'path': 'README.md'})              -> refusal='timeout'
grep({'pattern': '.', 'path': 'README.md'})   -> refusal='timeout'

FINAL: I couldn't check — my file-access requests (read `README.md`) are
expiring before you approve them, so I don't have a way to count lines
right now.

Two options:
1. Approve the pending permission request so I can read the file, or
2. Run `wc -l README.md` yourself and paste the number.
```

It tried three separate routes, and then diagnosed the situation
correctly to the person reading. That is what the reason buys, and it is
not politeness: a model told *a user refused you* argues with the user. A
model told *nobody answered* goes looking for another way, and when it
runs out of ways it says the true thing.

## The tradeoff

**The deadline is per call, not per turn**, and the receipt above is what
that costs: three attempts, three full four-second waits, twelve seconds
of a turn spent waiting for a person who was never going to answer. A
model that routes around a timeout pays the deadline again on every
route.

Per-call is the right unit for the *question* — each one is a separate
thing a person is being asked to approve, and a shared clock would mean
the third question gets whatever seconds the first two left over, which
is impossible to explain to the person answering. But it does mean a
deadline is a floor on how long a stubborn turn can take, not a ceiling,
and a host that wants a ceiling needs a turn-level budget of waiting that
does not exist here. That is a real gap, named rather than papered over.

## What was deliberately not built

**A default for `on_timeout`.** Considered for about as long as it takes
to type, because every caller writes `"deny"` and the ergonomics are
worse without it. Refused: the ergonomic cost is one word at the call
site, and the cost of the default is that the overnight-batch person
never discovers the decision was made for them.

**A timeout on the synchronous agent.** It would need a thread per
question and a way to abandon a blocking `input()`, and the answer to
"what happens to the thread that is still sitting on stdin" is *nothing
good*. Gates that reach a person are the gates that became awaitable in
note 37; that is where deadlines belong.

**A code the model can see.** The token is never rendered into the
sentence the model reads. Models are extremely good at pattern-matching
on short strings, and a `timeout` in the tool result is an invitation to
learn that some refusals are worth retrying immediately and hammering.

**An enum for the codes.** A closed set would mean a service cannot name
its own refusals without patching this repo, and the validation that
matters — *this is a token, not a sentence* — does not need one.

**Enforcing that an approval carries no reason.** Nothing stops a gate
writing a reason and then returning `True`; the loop ignores it. Catching
that would cost a check in the hot path to prevent a mistake with no
consequence. `refuse()` exists so the shape nobody writes by accident is
the convenient one.

## What is not here yet

* ~~**No turn-level budget of waiting.**~~ Shipped in
  [note 51](51-a-turns-worth-of-waiting.md) as `with_wait_budget`. The
  second half of this bullet was the whole problem and the whole
  solution: nothing in the gate layer knew about turns, so the request
  now SAYS which turn it belongs to — `turn_id` beside `call_id`, for
  the same reason that one exists.
* **Still no way to ask a question other than yes/no**, though the part
  this bullet actually complained about is gone: *"not like that, like
  this"* is a sentence the person types at the prompt now, and the model
  reads it verbatim ([note 56](56-not-like-that-like-this.md)). A gate
  that wants to POSE a question and receive something other than a bool
  is still unbuilt, and is `ask_user`-shaped rather than gate-shaped.
* **A code cannot carry structured detail.** `timeout` does not say how
  long it waited, and a caller that wants the number reads it from its
  own configuration. A code plus a payload is a bigger type than anything
  has needed.
* ~~**Nothing reads the code in this repo's own frontends.**~~ Shipped in
  [note 52](52-the-word-for-what-happened.md). The second sentence of
  this bullet was wrong: a terminal IS a host, and it was drawing a
  refused call as a crashed one — same red panel, same word — when a
  refused call never ran at all. Both frontends now read the code, and
  the terminal tallies a turn's refusals by cause.
