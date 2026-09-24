# 51 · A turn's worth of waiting

[notes/39](39-a-clock-and-a-word.md) put a clock on a gate that
suspends:

```python
gate = with_deadline(ask_the_owner, 30, on_timeout="deny")
```

Thirty seconds to answer, and a refusal with a code if nobody does. The
note shipped with its own complaint attached:

> **No turn-level budget of waiting.** A turn with five refused calls
> waits five deadlines. The unit that would fix it is a turn, and
> nothing in the gate layer knows about turns.

Take that arithmetic seriously for a moment. A model decides to run five
commands in one batch. Nobody is at the keyboard. Every question waits
its full thirty seconds, in sequence, and the turn sits there for **two
and a half minutes** before reporting that five things were refused.

The person who typed `30` was not describing a call. They were
describing their own patience — how long this thing may sit waiting for
them before it gets on with something else. Patience does not multiply
by however many tools the model decided to try.

## The gate has to be told where a turn begins

The reason this could not be fixed inside `with_deadline` is in the
bullet: the gate layer sees a stream of questions, and nothing in that
stream marks where one thing the agent was asked to do ends and the next
begins.

Three ways to guess, all bad:

* **A gap in timing.** "No questions for a while, must be a new turn."
  A slow model with a long tool call looks exactly like a new turn.
* **Counting calls.** Needs to know how many a batch holds, which is the
  model's decision, made after the gate was built.
* **Watching the agent's events.** Now the gate layer depends on the
  loop's event stream, and a gate is a `Callable[[PermissionRequest],
  bool]` precisely so that it does not.

So the request says which turn it belongs to, exactly as it already says
which call it is deciding:

```python
@dataclass
class PermissionRequest:
    call_id: str = ""
    turn_id: str = ""
```

This is the same argument [as the one for `call_id`](39-a-clock-and-a-word.md):
the loop *could* be inferred from, and inference does not fail loudly.
It produces a budget that resets at the wrong moment, which looks like
a gate being strangely impatient on a Tuesday. A uuid stamped at the top
of `run_streaming` cannot drift — and it is a uuid rather than a counter
because an agent and its sub-agent are different turns, and two agents
that both counted from one would collide.

## Spending it down

```python
gate = with_wait_budget(ask_the_owner, 30, on_timeout="deny")
```

Thirty seconds *for the turn*. Each awaited answer is timed against
what is left, and what remains becomes the next question's deadline. A
turn is the unit because a turn is one thing the agent was asked to do —
the same unit the dollar ceiling uses ([notes/34](34-budgets.md)), for
the same reason.

**Nothing is asked once the allowance is gone.** The verdict comes back
without calling the inner gate at all. This is the part worth being
deliberate about: posting a question the wrapper will not wait for means
a person picks up their phone, reads a prompt, thinks about it, taps
Approve — and learns that the call was refused before they were asked.
A refusal is survivable. Being asked for a decision that had already
been made is not the same thing at all.

Which is also why there are two codes and not one:

| code | what happened |
|---|---|
| `timeout` | somebody was asked, and did not answer in time |
| `out_of_time` | nobody was asked; this turn had no waiting left |

They demand different handling — the first says a person is slow or
away, the second says the turn was already over its patience budget —
and a caller reduced to matching English would have to tell "went
unanswered" from "nobody was asked" by substring.

## The tradeoff, said out loud

A question that would have been answered in one second can be refused
because earlier questions in the same turn ate the budget.

That is not a wart; that is what a ceiling **is**. The alternative is
the behaviour this replaces, where the ceiling is per call and the turn
has none. But it does mean `with_wait_budget` is the wrong wrapper for a
conversation where each question genuinely deserves its own clock — an
interactive session with a person who is definitely there. Both
wrappers ship, and they compose in the obvious direction: a budget
around a deadline gives each question at most `n` seconds and the turn
at most `m`.

`on_timeout` still has no default, for note 39's reason exactly. A
stopwatch is mechanism and belongs in a library; a verdict on silence is
policy and belongs to whoever owns the conversation.

## What is not here yet

* ~~**A spent allowance refuses reads too.**~~ Fixed in
  [note 71](71-a-limit-on-waiting-not-on-work.md): the inner gate is
  still consulted, and only an answer that would wait is refused. Was:
  once spent, every call in the turn was refused without asking.

* ~~**No frontend passes one.**~~ The browser does since
  [note 78](78-a-clock-on-the-page.md): `--web --wait-budget SECONDS
  --on-timeout deny|allow`. The terminal still does not, by design. Was:
  The terminal gate answers inline (nothing
  is ever waited for, so nothing can be timed), and the browser's gate
  has no flag for it yet — the same state `with_deadline` has been in
  since note 39. The consumer for both is a service where the person is
  on the other end of a channel.
* ~~**A session has no budget of waiting.**~~ Built in dvara, the
  always-on service that runs Yantra agents for people and has the store
  and the identities this needed: `max_wait_per_day` on a person (its
  note 14). It is checked where a question is actually put, not by
  wrapping the gate like `with_wait_budget` does, because this wrapper
  used to refuse every call once its allowance was gone, read-only ones
  included ([note 71](71-a-limit-on-waiting-not-on-work.md) fixed that).
  Was: "this person may be asked for two minutes a day" needs a store
  and an identity, which is a service's problem.
* ~~**Nothing tells the model how much patience is left.**~~ It is told
  since [note 80](80-the-time-left-told.md), once some of the time has
  been spent, in whole seconds. Was: the budget
  notice ([notes/43](43-a-bar-and-a-deadline.md)) does this for dollars —
  a deadline the model can plan around — and the same trick would work
  here: *there are twelve seconds of approval left in this turn, so ask
  for the one thing you most need approved.*
* **A refused turn cannot be resumed.** When the allowance runs out the
  calls are refused and the turn carries on with error results; there is
  no "hold this and let me answer in a minute". That is a queue, not a
  budget, and it belongs to whatever owns the channel.
