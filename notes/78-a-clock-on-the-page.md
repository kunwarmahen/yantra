# 78 — A clock on the page

[Note 51](51-a-turns-worth-of-waiting.md) built `with_wait_budget`: one
turn may spend so many seconds, in total, waiting for a person to
approve things. [Note 71](71-a-limit-on-waiting-not-on-work.md) fixed
how it behaves once that time is used up. Both notes ended the same way:
*no frontend passes one.* The library could do it, and neither of
Yantra's own frontends offered a way to turn it on.

The terminal still does not, and that is deliberate. It asks you
directly, in the same window, and nothing waits anywhere: the agent is
stopped until you type. A clock there would only interrupt you while
you were still reading the prompt.

The browser is different. You start a turn, switch to another tab, and
the approval prompt sits there. The agent waits too, holding its turn
open, for as long as you are away. That is the waiting note 51 set out
to limit.

## `--wait-budget` and `--on-timeout`

```
yantra --web --wait-budget 60 --on-timeout deny
```

Each turn may now spend 60 seconds in total waiting on approval prompts.
The rules are the ones note 51 and note 71 set out, unchanged:

* **Each prompt waits for whatever is left.** Five prompts in one turn
  do not get sixty seconds each.
* **An unanswered prompt is withdrawn** when the time runs out. The page
  closes it, so nobody clicks Approve on something already decided. The
  model is told `timeout`: somebody was asked and did not answer.
* **Once the time is used up, nothing more is asked that turn.** The
  next prompt is never shown; the model is told `out_of_time`, meaning
  nobody was asked.
* **Reads are never asked about, so they are never refused.** Running out
  of time for approvals does not stop the agent from working.
* **Every turn starts with the full allowance.**

`--on-timeout` is required and has no default, for the reason
`with_deadline` gave in [note 39](39-a-clock-and-a-word.md): a timer can
measure silence, but it cannot know what the silence means. `deny` is
right for a deploy. `allow` is right for a job you left running
precisely so it would carry on without you. Only the person starting the
server knows which one this is.

The prompt in the page counts down, so the person can see how much time
is left and what will happen if they do not answer:

```
approval needed
write_file()
41s of this turn's waiting left — unanswered, it is refused
```

It turns red under ten seconds. The server keeps the real clock. The
page's countdown is for the person's benefit only, so a slow tab or a
clock that has drifted cannot change what happens.

## Why this is not `with_wait_budget` itself

Wrapping the browser's gate would have been the obvious move, and it
does not work. `with_wait_budget` can only time a gate that
**suspends**: one that hands back an awaitable and lets the loop get on
with other things. The browser's gate does not. It **blocks** a worker
thread until an answer arrives ([notes/22](22-web-ui.md)), and a wrapper
cannot time an answer that has already arrived by the time it gets
control.

So the rule is kept a second time, in the web session, where the gate
already waits for answers. It checks the cancel flag in that same wait,
and now checks the deadline there too. **THE SENTENCES ARE SHARED.**
What the model reads when it is refused comes from one function,
`wait_spent`, which both the wrapper and the browser call. A model gets
the same explanation whichever frontend refused it, and the tests hold
the browser to the promises the wrapper makes.

**THE TURN IS TOLD, NOT GUESSED.** The allowance is reset where the web
session starts a turn, which is exactly where a turn begins. Note 51
made `turn_id` part of the request for the same reason: working out
turns from the gaps between questions would reset the allowance at the
wrong moment and never raise an error.

## Both roads

The clock measures a person, not a model, so the provider makes no
difference. The receipt below is `qwen3.8:latest`.

## What was deliberately not built

**No clock in the terminal.** See above. There, the person *is* the
channel, and nothing is waiting for them.

**No clock on `ask_user`.** The agent asking you a question is not an
approval, and refusing an answer because time ran out has no sensible
meaning. The only choices would be to make one up or to end the turn.
This budget is for approvals, as note 51's was.

**No per-question clock (`with_deadline`) from the command line.** One
flag answers the question people actually have ("how long may this turn
keep me waiting?"). A per-question limit on top of that would be a
second number to reason about, protecting against a case the turn's
allowance already covers.

## What is not here yet

* ~~**Nothing tells the model how much time is left.**~~ It is told
  since [note 80](80-the-time-left-told.md): after a prompt uses some of
  the time, the next request says how many seconds remain. Was: still
  true from note 51.
* ~~**A refused turn cannot be resumed.**~~ `--on-timeout hold` since
  [note 88](88-not-yet.md): the turn waits in the page until you answer,
  or until a new message sets it aside. Was: still true from note 51.

## Receipt

The server started with `--wait-budget 8 --on-timeout deny`, on
`qwen3.8:latest`, with nobody at the page. The turn asked for two writes
in one go:

```
{'type': 'permission_request', 'id': '1101bbb0', 'tool_name': 'write_file', 'wait_left': 8.0}
{'type': 'resolved', 'id': '1101bbb0'}
{'type': 'tool_result', 'name': 'write_file', 'refusal': 'timeout', 'output': 'write_file was denied: the approval request went unanswered. This turn may spend 8 seconds in total waiting for approval, and that is now spent. Nobody refused it -- try a read-only route, or say what you need and let the person answer in their own time.'}
{'type': 'tool_result', 'name': 'write_file', 'refusal': 'out_of_time', 'output': 'write_file was denied: nobody was asked, because this turn had no waiting left. ...'}
{'type': 'turn_end', 'text': "The two `write_file` calls I issued together (a.txt <- 'one', b.txt <- 'two') were not approved in time, so neither file was created.\n\nIf you'd like me to retry, just say so and approve the writes when prompted ..."}
{'type': 'turn_done'}
```

One prompt was shown and withdrawn after eight seconds. The second write
was never shown. The model understood that nobody had said no, and
offered to try again.

`1852 passed, 1 skipped` (was 1840).
