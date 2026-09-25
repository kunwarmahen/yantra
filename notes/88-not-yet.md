# 88 — Not yet

[Note 78](78-a-clock-on-the-page.md) put a clock on the browser's
approval prompts. `--wait-budget 8 --on-timeout deny` gives each turn
eight seconds, in total, of waiting for you to click Approve. When the
time runs out, the prompt is withdrawn and the call is refused. The
model is told nobody answered, not that you said no, and it usually does
the sensible thing:

> The two `write_file` calls I issued together were not approved in
> time, so neither file was created. If you'd like me to retry, just say
> so and approve the writes when prompted.

Then you come back from lunch and have to ask again. The model plans
again, reads again, and asks for the same two writes a second time.
Notes [51](51-a-turns-worth-of-waiting.md) and 78 both listed this as
open:

> **A refused turn cannot be resumed.** That is a queue, not a budget,
> and it belongs to whatever owns the channel.

Half of that turned out to be true.

## What belongs to the channel, and what cannot

A **queue** is the service's job: storing the waiting questions,
telling the person, expiring old ones, knowing who is allowed to
answer. dvara, the always-on service that runs Yantra agents for
people, owns all of that.

But a queue needs something underneath it that only the agent loop can
provide: a turn that can **stop without an answer and carry on with
one**. Every model API has the same rule: each tool call the model made
must have a result before the next request is sent. Break it and the
request is rejected. Only the loop can safely break that rule for a
while and mend it again, because only the loop builds the history. A
service sitting outside the loop cannot pause a turn. It can only watch
one end.

So Yantra now provides the pause and the continue, and the queue can be
built on top of them.

## "Not yet" is a third answer

A gate used to have two answers: yes and no. It now has a third:

```python
from yantra import hold

def gate(request):
    if nobody_is_around():
        return hold(request)       # not yet
    return ask(request)
```

Or with the clock wrappers, where it is one more choice for what
silence means:

```python
gate = with_wait_budget(ask_the_owner, 30, on_timeout="hold")
```

In the browser it is a flag:

```bash
yantra --web --wait-budget 60 --on-timeout hold
```

**A HELD CALL IS NOT A REFUSED CALL.** When a gate says "not yet", the
loop finishes the rest of that batch and then stops:

* calls approved in the same batch **run**. You may have said yes to
  three of four before you walked away;
* calls refused in the same batch get their refusals, as always;
* the held calls get **nothing**. The turn ends with the reason `held`,
  and `agent.held` lists what is waiting.

The history now ends on a question nobody has answered, which is exactly
the state the model APIs reject. It is left that way on purpose, and
every way out of it mends it.

## Three ways out

**Answer it.** `agent.resume({...})` takes one answer per waiting call:

```python
agent.resume({
    "call_a": True,                          # run it
    "call_b": "leave b.txt alone",           # refuse it, in your words
    "call_c": {"path": "c.txt", "text": "…"} # run it with these arguments
})
```

Approved calls run through the normal execution path, hooks included.
Refused ones reach the model in your own words
([note 56](56-not-like-that-like-this.md)). All the results go back as
one batch, in the order the model asked for them, and the turn carries
on. An answer that is missing, or names a call that is not waiting, is
an error **before anything runs**. A wrong answer costs nothing.

**Ignore it.** Send a new message instead. The waiting calls get a result
saying they were set aside and never ran, and the new message proceeds
normally. Clearing or compacting the conversation also sets a hold
aside.

**Interrupt it.** Closing the stream at the moment of the hold, or
interrupting a resume while the approved calls are running, answers
every unanswered call as interrupted. Nothing is ever left dangling.

**A RESUME IS A NEW TURN.** It gets a new turn id, a fresh approval
allowance and a fresh dollar budget. You are there now. Something you
asked for an hour ago is not the same spend as something you ask for
this minute, and it would be odd to find your approval clock already
used up by the hour you were away.

## An approval given an hour late

**WHAT YOU APPROVE IS WHAT RUNS, BUT THINGS MAY HAVE CHANGED.** A
`write_file` approved an hour late writes over whatever is at that path
*now*. This is the approve-with-edits rule
([note 20](20-approve-with-edits.md)): the arguments you approve are the
arguments that run. The hold stretches that rule across time, and
Yantra cannot check whether the world moved in the meantime. What it
can do is say how old the question is. Every hold records when it
stopped, and the page shows it:

> This turn stopped 4 min ago. What you approve runs against things as
> they are now, not as they were then.

## A hold survives a restart

A person may answer tomorrow, and the process may have restarted in
between. So a saved session keeps its hold.

**A HELD TURN IS SAVED AS THE TURN BEFORE IT ASKED.** The session file
has always promised that a saved conversation has no unanswered tool
calls. That promise still holds. The saved history stops just *before*
the unanswered question, and the question goes in a separate `held`
block beside it, along with the results of any calls that already ran
and what is still waiting. An older Yantra that does not know about
holds ignores the block and loads a valid conversation ending on your
message. This version puts the question back and restores `agent.held`
with it.

That meant no session format change was needed. A format bump would not
have protected anyone anyway: older readers never checked the format
number.

## Who can hold

**NOT A SUB-AGENT.** A sub-agent cannot stop its parent's turn to wait.
If a child's gate says "not yet", the child's call is refused under the
code `held_in_child`, with a sentence telling it to finish without that
call and say what needed approval. The child carries on.

**NOT THE TERMINAL.** In the terminal, you are right there at the
prompt, so nothing is waiting on a clock ([note 78](78-a-clock-on-the-page.md)).
`--on-timeout hold` needs `--wait-budget`, which needs `--web`.

**THE BROWSER, WHICH IS WHERE THE RECEIPT COMES FROM.** A held turn
shows as a panel in the transcript, not a pop-up. There is no longer a
clock on it, so it waits where the turn stopped. Each call gets approve
or deny and an optional reason; typing a reason switches that call to
deny. **Carry on** sends the answers. A page opened or reloaded later
still shows the panel, because the server's state includes the hold.

## The recording

A held turn is recorded with the outcome `held`. Its waiting calls are
steps marked `held`, and they do not count as failures: `--turns failed`
does not flag a turn for stopping on purpose. The resumed turn is its
own line, recorded under the same task, with `resumes` pointing at the
held turn's id. That link is kept on the hold itself, and saved with
it, so it survives a restart too.

## Both roads

Holding happens in the approval gate and the loop, before any tool
runs, so it works the same whether a cloud model or a local one asked
for the call. The receipt uses `qwen3.8:latest`. What does depend on the
model is the answer after a resume: the model reads the results of calls
it asked for a while ago, and has to make sense of them. `qwen3.8`
handled a mixed approve-and-deny without being told anything more.

## What was deliberately not built

**A queue.** Expiry, notifications, deciding who may answer, several
people answering: all of that is the service's job.

**Re-checking an old approval.** See above. Yantra cannot know what
changed, and pretending it could would be worse than saying how old the
question is.

**Holding at a per-question clock.** `with_deadline(on_timeout="hold")`
works, but each question in a batch waits its full time before the turn
stops. `with_wait_budget` is the one to use. Once its allowance is spent,
the rest of the batch is held without anyone being asked.

## What is not here yet

* **A service that holds.** This is the primitive. dvara storing held
  turns per person and letting them answer later, from whichever channel
  they are on, is the consumer it was built for. That work belongs in
  dvara.

## Receipt

The browser server, `qwen3.8:latest`, nobody at the page:

```
$ yantra --web --provider ollama --model qwen3.8:latest \
    --wait-budget 8 --on-timeout hold --trace run.jsonl
```

The message *"Write 'one' to a.txt and 'two' to b.txt. Issue both
write_file calls together in one go."* The events the page received:

```
{"type": "permission_request", "tool_name": "write_file", "wait_left": 8.0}
{"type": "resolved"}
{"type": "turn_end", "reason": "held", "detail": "waiting for approval: write_file, write_file",
 "held": {"age": 0, "calls": [
   {"id": "call_6ptaeyez", "summary": "NEW FILE a.txt (1 lines)", ...},
   {"id": "call_6fayqkv8", "summary": "NEW FILE b.txt (1 lines)", ...}]}}
```

One prompt went up and was withdrawn after eight seconds. The second
write was never asked. Both are waiting. The session was saved and the
server **stopped**. A new server started, with no hold:

```
held before load: None
held after load: [('call_6ptaeyez', 'NEW FILE a.txt (1 lines)'),
                  ('call_6fayqkv8', 'NEW FILE b.txt (1 lines)')]
```

An answer for only one of the two was refused:

```
POST /api/resume {'status': 400, 'detail': 'resume() takes one answer per held call; unanswered: call_o6q6ac9p'}
```

Then both answered, approve and deny-with-a-reason:

```
{"type": "tool_result", "name": "write_file", "output": "created …/a.txt (3 bytes)"}
{"type": "tool_result", "name": "write_file", "refusal": "user",
 "output": "write_file was denied. The person said: leave b.txt alone, a.txt is enough"}
{"type": "turn_end", "reason": "end_turn",
 "text": "Done — `a.txt` now contains `one`. I left `b.txt` alone as per the operator's note."}
```

Only `a.txt` exists. The recording holds both turns, linked:

```
d7dec0e1 held      [('write_file', 'held'), ('write_file', 'held')]   resumes -
1fc1c03d end_turn  [('write_file', None), ('write_file', 'user')]    resumes d7dec0e1
```

(The 400 above comes from an earlier run of the same sequence, which
had different call ids. Every other line is from this one run.)

`2076 passed, 1 skipped` (was 2035).
