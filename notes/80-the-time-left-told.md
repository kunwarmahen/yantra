# 80 — The time left, told

[Note 51](51-a-turns-worth-of-waiting.md) gave a turn a fixed amount of
time it may spend waiting for a person to approve things, and
[note 78](78-a-clock-on-the-page.md) put that clock in the browser. The
person could see it count down. The model could not.

The model found out about the clock only when it hit it. It would ask to
write a file, the prompt would sit unanswered, and the refusal would say
*"this turn may spend 8 seconds in total waiting for approval, and that
is now spent."* That is useful news, but it comes too late to act on.
Suppose the model had three writes planned and knew three seconds were
left. It could have asked for the one that mattered most first. Instead
it learned the rule after the time was gone.

Note 51 named the fix in its "not here yet" list: the budget notice
([note 43](43-a-bar-and-a-deadline.md)) already warns the model before
the dollar limit stops it, and the same idea works for time. *There are
twelve seconds of approval left in this turn, so ask for the one thing
you most need approved.*

## What the model reads

After an approval prompt has used some of the time, the next request to
the model carries one extra paragraph:

```
[approval notice] 3 of this turn's 8 seconds for waiting on approval are
left. Each approval prompt spends from it, and once it is gone anything
that needs approval is refused without asking. If you still need
approvals, ask for the one you need most first.
```

and once the time is gone:

```
[approval notice] This turn has used all 8 seconds it may spend waiting
for approval. Anything that needs approval will now be refused without
the person being asked. Read-only tools still work. Finish with what you
have, and say what you would need approved.
```

Three rules decide when it is said:

* **Nothing until some time has been spent.** Most turns never ask for
  approval. Adding a paragraph about a clock to every one of them would
  be noise for the sake of the few that do.
* **Only when the sentence changes.** The seconds are rounded up to a
  whole number and the notice is sent when that number moves, not on
  every request. A model does not need "3 seconds left" repeated to it
  on each step of a turn in which nobody was asked anything new.
* **Sent, never stored.** The notice rides on the request the way the
  budget notice does, and is never written into history. History gets
  resumed and replayed, and a clock from a turn last week read back as
  something the *user* said would be worse than no clock.

## Why this one gives a number

The budget notice deliberately gives **no** figures. Its argument
(`Budget.notice`) is that a model told "you have $0.08 left" has been
handed a quantity to optimise, and it optimises badly: it cuts the answer
short to save money nobody asked it to save.

That argument does not carry over to seconds, because **THE MODEL DOES
NOT SPEND THEM**. The person spends them, by being slow to answer. The
one thing the model controls is how many prompts it puts in front of
that person. "Three seconds left" helps it choose which prompt to ask
for, and gives it no reason to shorten anything. A number the model
cannot spend is a number it cannot game.

## Where the clock lives

The time left is known only to whatever keeps the clock, and there are
two of those: the library wrapper `with_wait_budget`
([note 51](51-a-turns-worth-of-waiting.md)) and the browser session,
which keeps the same rule itself because its gate blocks a thread and
cannot be wrapped ([note 78](78-a-clock-on-the-page.md)).

Both now expose the same question, *what should the model be told about
turn T?* The agent loop asks it before each model call. The agent does
not keep a clock of its own. **THE SENTENCES ARE SHARED**, for the reason
`wait_spent` is shared: `approval_notice()` in `permissions.py` writes
the words for both, so the model reads the same thing from either
clock.

The browser wires itself up. A host using the library wrapper hands the
agent the wrapper's notice beside the gate:

```python
gate = with_wait_budget(ask_the_owner, 30, on_timeout="deny")
agent = AsyncAgent(provider, model=..., permissions=gate)
agent.approval_notice = gate.approval_notice
```

Leaving that line out leaves the model uninformed and changes nothing
else. The clock still refuses exactly what it refused before.

## Both roads

The notice is plain text added to the last user message, so every
provider carries it the same way. The receipt below is `qwen3.8:latest`.

## What was deliberately not built

**No notice in the terminal.** The terminal has no clock (note 78 argues
why), so there is nothing to tell.

**No notice before any waiting.** "You have 60 seconds of approval this
turn" could help a model plan a batch before it sends one. It would also
sit in every turn that never asks for approval at all. Once the first
prompt has used some time, the model hears about it.

**No switch to turn it off.** The budget notice is opt-in because it
changes how the model writes its answer. This one changes only which
approvals the model asks for, and when there is a clock at all, asking
for fewer and better approvals is what the person who set it wanted.

## What is not here yet

* **A sub-agent is not told.** A child agent's approvals spend its
  parent's time in the browser, but only the parent is given the notice.
  The parent hears about it on its next step, after the child returns.
* **A refused turn still cannot be resumed.** Still true from note 51.

## Receipt

The browser session with `--wait-budget 8 --on-timeout deny`, on
`qwen3.8:latest`, driven in-process by a small websocket client. The
task asks for three writes, one at a time. The first prompt was approved
after 5.2 seconds and the rest were left unanswered. The `MODEL WAS
TOLD` lines are printed from the request as it goes to Ollama, on the
agent's thread, so they can print slightly before the page's envelope
for the step before:

```
{'id': '6697c176', 'tool_name': 'write_file', 'wait_left': 8}
  approved after 5.2s
MODEL WAS TOLD: [approval notice] 3 of this turn's 8 seconds for waiting on approval are left. Each approval prompt spends from it, and once it is gone anything that needs approval is refused without asking. If you still need approvals, ask for the one you need most first.
{'type': 'tool_result', 'name': 'write_file', 'refusal': None}
{'id': '17331ecd', 'tool_name': 'write_file', 'wait_left': 2.8}
MODEL WAS TOLD: [approval notice] This turn has used all 8 seconds it may spend waiting for approval. Anything that needs approval will now be refused without the person being asked. Read-only tools still work. Finish with what you have, and say what you would need approved.
{'type': 'tool_result', 'name': 'write_file', 'refusal': 'timeout'}
{'type': 'turn_end', 'text': "Done with what I could: **a.txt** was created containing `one`.\n\nI couldn't create the remaining two because the approval for the next write timed out and was treated as declined:\n\n- **b.txt** — needs `two`\n- **c.txt** — needs `three`\n\nApprove the file writes and I'll create both, or just say the word and I'll retry."}
```

After the second notice the model did not try the third write, which
would have been refused without anybody being asked. It stopped and
said which two writes it still needed.

`1891 passed, 1 skipped` (was 1877).
