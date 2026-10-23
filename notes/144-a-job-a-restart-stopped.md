# 144 — A job a restart stopped, carried on

You ask the agent to return the blue kettle. That's a dozen steps on the
phone: open the app, Orders, the kettle, Return, a reason, a drop-off
point, Confirm. On step six you run `sarathi down && sarathi up` because
an update came in. Or the computer reboots, or the container dies.

Before this note, the job was simply gone. The steps already done were
done: the return form was half filled. Nothing remembered them. You'd
asked, seen *Working…*, and then heard nothing. Asking again started over
from the first tap, in an app that was no longer on the first screen.

## Saved after every step

The checkpoints Yantra already had ([note 38](38-giving-it-back.md),
`session.py`) were written when a turn ended. That's no help to a turn
that never ends.

So a host can now set **`agent.on_step`**. It's called:

* as the turn begins, with the person's message already in the history;
* after every batch of tools;
* once more as the turn ends.

Each of those moments leaves the history with no tool call waiting for
its result. That's the invariant `session.py` has always relied on, so
a checkpoint written then is always loadable. It now holds at every
step, not only at a turn's end.

Every host sets the hook:

| road | where each step goes |
|---|---|
| terminal | the conversation's row in History (`history.py`) |
| page | the same, road `page` |
| `yantra --job FILE` | that file (what Samay passes for a scheduled run) |
| Dvara | its session store, per person, per agent, per thread |

## Open until it ends

A checkpoint has to say whether the turn was still going. You can't tell
from the history's shape: a turn that hit its iteration cap also ends on
tool results. So it's a flag, **`agent.turn_open`**:

* set when a turn begins;
* cleared when it ends for any reason (an answer, the cap, a budget, a
  hold for approval);
* cleared when the person stops it (Ctrl-C, the page's Stop).

A process that's killed never clears it. Neither does a cancelled async
task, which is what a service's shutdown does to the turns in flight.
While it's set, `snapshot` writes an **`unfinished`** block:

```json
"unfinished": {"started": "...", "saved": "...",
               "message": "return the blue kettle",
               "steps": 6, "tools": ["phone_open_app", "phone_tap", ...],
               "read_outside": ["..."]}
```

`read_outside` is there for [note 142](142-outside-text-cannot-give-orders.md):
a job that read an email before the restart has still read it. Carrying
on must not be a way past that guard.

## Carried on, not run again

`agent.carry_on()` (and the async twin's) goes on from the last saved
step. **Nothing already done is done twice.** Before the model's next
call, it gets one message of its own:

> [The work on this stopped here at 10:00, when the service restarted,
> and is being carried on now (10:05). The last tool called was
> phone_tap. Anything on a screen, a page or a phone may have changed
> since, and the last step may or may not have happened. Look again
> before you act on what you saw before. If things no longer match
> where you left off, don't guess: stop, say what you found, and ask
> whether to carry on.]

That's the "carry on with …?" for when the screen has moved. The agent
looks before it acts, and asks when what it sees isn't where it left
off. The gate asks for whatever it would have asked for anyway.

**Whether to carry on is the host's decision, not the agent's:**

* **A person decides.** In the terminal, `/carry`. The banner names a job
  the last session left open, and `/chats open N` says so for an old
  conversation. On the page, a bar: *A job stopped part way when Yantra
  restarted: "…" (2 steps done: read_file)*, with **carry on** and
  **leave it**. Behind Dvara, a message when it starts again, and
  `/carry` (Dvara's note 59). Anything else they send leaves the job
  as it is.
* **A schedule decides by itself**, within limits. Samay carries a run on
  only soon after it stopped (within the schedule's grace), only from
  what was saved, and never beside a Yantra still running it (Samay's
  note 10).

## `--job FILE`

```bash
yantra --unattended --json --job ~/.jobs/check-mail.sqlite --prompt "..."
```

Saves the run after every step in FILE. If FILE holds a run that was
stopped part way, that run is carried on and the prompt isn't started
again. If FILE holds a finished run, it starts fresh. While it runs it
holds a lock on `FILE.lock`, so a second Yantra on the same job is
refused, and Samay can tell a job that stopped from one that is still
going.

## Receipt

`qwen3.8:latest` on Ollama. Three files (17, 25, 8), and a prompt to read
them one at a time and add them up. The run was killed with `SIGKILL`
the moment the first step was saved:

```
killed with 1 step(s) saved          # a.txt read
```

The same command again, with the same `--job` file:

```
carrying on "Read a.txt, then b.txt, then c.txt, ..." (1 step done: read_file), stopped by a restart
→ read_file()   "path": "b.txt"
→ read_file()   "path": "c.txt"
{"ok": true, "stop_reason": "end_turn", "text": "... Their sum is **50**."}
```

`a.txt` wasn't read again. The file now holds a finished run.

The same through Dvara (`dvara say`, scratch state): killed after step
1, then `/carry` in the same conversation:

```
Reading them in order: a.txt = 17, b.txt = 25, c.txt = 8.
**The sum is 50.**
[end_turn · $0.0000 · 6809in/134out · run ce74531ce0de]
2026-10-23 15:38  cli  owner/reader  end_turn  'Read a.txt, then b.txt, ...'
                  read_file -> read_file
```

Two reads in the carried-on run, and the run row is the job's own
request, not `/carry`.

The first live attempt found a bug. A `--job` run that finished normally
still had *unfinished* as its last checkpoint, because nothing saved at
the end. Samay deletes the file after a run, which hid it. `on_step` now
fires once more as the turn ends.

## What was deliberately not built

* **Carrying on mid-tool.** A step cut off in the middle (a tap sent, the
  answer not back) is saved as *interrupted*, and the note tells the
  model the last step may or may not have happened. Replaying the tool
  could tap twice. Looking costs one screen read.
* **Carrying on a person's job by itself.** An hour later the bus may
  have gone. The person says.
* **A job that outlives `/clear`.** Clearing the conversation is the
  person ending it.
