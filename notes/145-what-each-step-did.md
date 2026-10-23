# 145 — What each step did

A long phone job ends with *"I couldn't find the Return button."* Which
step went wrong? What did the agent see on the screen at step four, what
did it tap, did anybody say yes, and was the minute it took spent
thinking or waiting? Finding why X refused a sign-in from a streamed
window took a day of reading logs for exactly these questions.

## The conversation already has most of it

Every road keeps the whole conversation:

* the terminal and the page in History ([note 130](130-every-conversation-kept.md));
* Dvara per person;
* a scheduled run in its `--job` file ([note 144](144-a-job-a-restart-stopped.md)).

Each tool call is there with its arguments, each result in full, and
the pictures a tool returned. The trace ([note 57](57-a-turn-written-down.md)) is
something else: opt-in, and by default the turn's shape only.

What a conversation couldn't say was **when** each step ran, **how
long** it took, and **who said yes**. So the loop now writes a small note
per step and per turn, and `snapshot` keeps them beside the history in
the same payload:

```json
"steps": {"c2": {"turn": "run-42", "at": "2026-10-23T10:04:12+00:00",
                 "waited": 12.3, "ran": 1.4, "answered_by": "you, on the page",
                 "pictures": 1, "refused": null}},
"turns": {"run-42": {"started": "...", "ended": "...", "outcome": "end_turn",
                     "kind": "message", "message": "...", "answer": "..."}}
```

**Waiting is not running.** `waited` is the time at the permission gate
and `ran` the tool's own. A person's yes can take a minute, and that
minute isn't a slow tool.

**Who said yes** is a new field on the permission request,
`answered_by`. A gate that asks somebody fills it in: *"you, in the
terminal"*, *"you, on the page"*, and *"asha on telegram"* when Dvara
answered a scheduled run's question. It stays empty when nobody was
asked (a read-only tool, yolo, a standing rule).

**A turn's key** is the host's tag when it gives one (`agent.turn_tag`).
Dvara tags each turn with its run id, so a run in its ledger opens its
own steps. Otherwise the turn's own id is used.

**Nothing new is kept.** Arguments and results stay where they already
were, under the conversation's own rules: History's *delete* deletes
them. A note holds only times and names. A note whose step is no longer
in the history (after `/clear`) isn't written into the next
conversation.

## One reading, three pages

`steps.timeline(payload, which)` turns one turn into `yantra.steps.v1`:
the message, how long the turn took and how it ended, then each step
with its tool, arguments, result (and a one-line summary), time, wait,
run time, who answered, any refusal code, and its pictures. Yantra's
page, Dvara's page and Samay's page all draw this one format. A
conversation from before the notes still opens, with its steps but
without the times.

Where you open it:

| where | how |
|---|---|
| terminal | `/steps [N]`, `/steps export [N] [FILE]`, `/chats steps N [K]` |
| Yantra's page | **steps** under each answer and under each answer in History, a panel with **earlier/later** and **file for a bug report** |
| a program | `yantra --steps FILE [--turn N] [--json \| --markdown]`, no model |
| Dvara | **Steps** on each run on its page, `/steps` in the chat, `GET /runs/{id}/steps` (Dvara's note 60) |
| Samay | **Steps** under each run on its page (Samay's note 11) |

**For a bug report**, `steps.markdown` writes the turn as a file. Its
first line says it holds what the tools were given and what they read,
so look before you send it. Pictures are counted, not embedded.

## Receipt

`qwen3.8:latest` on Ollama, a two-file job with `--job`, then:

```
$ yantra --steps job4.sqlite
turn 1 of 1: Read a.txt and b.txt, one at a time, then tell me which number is larger. -- 13s, end_turn
  1. read_file  12:04:06 · ran <1ms
     {"path": "a.txt"}
     -> 1 17
  2. read_file  12:04:11 · ran <1ms
     {"path": "b.txt"}
     -> 1 25
```

Thirteen seconds for the turn, under a millisecond for each read. The
five seconds between the two steps was the model. The first version
printed `ran 0s` for a fast step; durations under a second now show as
milliseconds.

## What was deliberately not built

* **Each model call's own time and tokens.** The gaps between steps are
  the model's, and the turn's footer already has its tokens. Splitting
  them per call would mean a record per model call, kept with the
  conversation forever.
* **Pictures in the bug-report file.** A phone screenshot is a person's
  screen. The file counts them, and the page shows them.
* **Steps of a sub-agent.** A child's turn is its own conversation; the
  parent's step shows what it was given and what it said back.
