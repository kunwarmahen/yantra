# 101 — Looking back

[Note 100](100-what-it-knows-about-you.md) gave the agent a memory of
the person: what they told it before goes into the prompt, and a
`remember` tool keeps new facts. But nobody says *"remember that I live
near RDU"*. They say *"any tips for flights from RDU to Denver?"* and
move on. Whether RDU is kept then depends on the model deciding, in the
middle of answering a question about flights, to stop and write down a
fact about the person. Some models do. Nothing makes them.

This note is about catching what was said in passing: a short **look
back over the conversation when it ends**.

## When a conversation ends

The look back runs once, at the end:

* `/quit`, or Ctrl-D;
* `/clear`, which starts a new conversation;
* `/load`, which switches to another one (on the page: *clear* and
  *restore*);
* the end of a one-shot run (`yantra "..."`), which is a whole
  conversation;
* just before compaction ([note 07](07-reliability-and-scale.md)) folds old
  turns into a summary, because a detail folded into a summary is a
  detail the end-of-conversation look can no longer see.

And whenever you ask: `/remember` in the terminal, or *remember from
this conversation* in the page's memory panel.

**Once per conversation, not once per turn.** A look after every turn
would pay for a model call on *"thanks"* and *"try again"*. The end is
where everything the conversation will say has been said. The cost is
that a fact reaches the prompt from the *next* conversation, and the
memory layer is filled at a conversation's first message anyway.

## One small call, to the model you already have

The look back is one plain request to the same provider and model the
session uses, with no tools and a fresh context. It gets:

* the part of the conversation it hasn't looked at yet (so `/remember`
  followed by `/quit` only reads the new turns, and a conversation
  compacted twice isn't read twice);
* what is already remembered, so it doesn't propose it again;
* the three tests from note 100, spelled out: *about the person, not
  the task; still true in months; would change an answer in a later
  conversation that doesn't mention it*.

It answers with at most five lines like:

```
fact: Lives near RDU (Raleigh-Durham area).
preference: Prefers short answers.
```

or `NONE`. Anything else in the reply — a preamble, a model thinking
out loud — is ignored. A line that says the same thing as a memory you
already have is dropped before you see it.

**On the local road this needs no second model.** The model that just
answered does the look back, on the same machine. On a cloud model the
conversation goes where every turn of it already went. The store never
sees a transcript either way: it only gets the sentences you keep.

## Nothing is kept without a yes

```
worth remembering about you, for later conversations?
  1. fact: Lives near RDU (Raleigh-Durham area).
keep [a]ll, numbers (1 3), or [N]one >
```

A bare Enter keeps nothing. A candidate you drop isn't offered again in
the same session. On the page the same list comes up as a dialog with
every box unticked.

That default earns its place. In a first try at the receipt below, the
message ended *"Keep it short."*, and the look back proposed *"Prefers
short, concise answers"*. That might be a standing preference or might
have been about that one question. The person knows, and the model
can't.

`--reflect` sets the mode (or `YANTRA_REFLECT`):

| mode | what happens | for |
|---|---|---|
| `ask` | shows each candidate; you keep or drop | the default where someone is at the terminal or page |
| `auto` | keeps them all, and says so at startup | unattended runs that asked for it |
| `off` | never looks by itself | `/remember` still looks when you ask |

Like `--learn` ([note 96](96-solve-it-once.md)), `ask` turns itself off
when nobody can answer: a piped run doesn't stop to wait for a keypress
that will never come.

Before compaction there is also nobody to ask, because it happens in the
middle of a turn. So what that look finds waits, and is offered when the
turn ends.

## Scrubbed before it is read

The conversation goes through the same secret patterns the skill
learner uses (tokens, `KEY=value` lines, `Authorization` headers)
before the look back sees it. If the session records a trace with
`--trace-redact` patterns or `--trace-redact-words` lists
([notes 79](79-scrubbed-before-it-is-written.md), [86](86-names-on-a-list.md)),
those apply too. What the trace would scrub, the look back never reads.

A candidate that still has `[redacted]` in it is dropped rather than
offered. A memory with a hole in it is either useless or shaped like a
secret. What you keep is scrubbed once more on the way into the store.

## What was said, not what was thought

The look back reads what you said and what the assistant replied. It
doesn't read the model's reasoning, and it sees each tool call by name
only, without its arguments. Both belong to the assistant, and both can
quote the system prompt. A model thinking *"the user is mahen, in
/home/mahen"* once led the look back to keep *"Their Linux username is
mahen"*, which the person never said
([notes/105](105-a-rate-not-a-picture.md)).

## A conversation you restore counts as looked at

`/load`, the page's *restore*, and `--resume` bring back a conversation
that was looked back over when it ended. Its turns count as seen, so
something you dropped then doesn't come back because the same turns
were loaded again. The price: a conversation saved with `/save` and
never ended isn't looked back over for the turns before the save.
`/remember` right after loading doesn't help either, because the
loaded turns already count as seen. The fix is to run `/remember`
*before* you save.

## Receipt

`qwen3.8:latest` on Ollama, a scratch store, identity `asha`.

**Session 1.** The `remember` tool is pulled
(`YANTRA_DISABLED_TOOLS=remember`), so the look back is the only way
anything can be kept:

```
$ YANTRA_DISABLED_TOOLS=remember yantra --provider ollama --model qwen3.8:latest
disabled 1 tool(s): remember
> Any tips for booking flights from RDU to Denver? Just general advice
  in three bullets, no need to look anything up.
- Fly midweek if you can — Tuesday and Wednesday departures from
  Raleigh-Durham to Denver usually run cheapest; …
── end_turn · 2370 in / 185 out · 1 iteration(s)
> /quit
worth remembering about you, for later conversations?
  1. fact: Lives near RDU (Raleigh-Durham area).
keep [a]ll, numbers (1 3), or [N]one > a
remembered 1 (#1 -- from the next conversation on; /memory to see or forget)
```

Nobody said *"I live near RDU"*. The look back worked it out from
*"flights from RDU"*, and asked.

**Session 2**, a new process that never mentions an origin:

```
$ yantra --provider ollama --model qwen3.8:latest \
    "Find me flights to Austin next month. Just tell me which airport
     you'd search from and the route; no need to look anything up."
Since you use the Raleigh-Durham area, I'd search from RDU
(Raleigh-Durham International) to AUS (Austin-Bergstrom International)
… or if a different origin airport fits better (e.g., FAY, PSK, or ATL
if you're willing to fly out further).
```

No recall call, and it says where RDU came from.

**The same question as `ben`**, on the same machine and store:

```
I can suggest a sensible route, but I need one detail first: where to
fly from? Your home airport depends on where you are, and I don't have
that yet.
```

`2397 passed, 1 skipped` (was 2364). The tests check that:

* a fact said in passing, with no `remember` call, is in the next
  session's prompt once kept, and never in another identity's;
* a bare Enter keeps nothing, and a dropped candidate isn't offered
  again;
* turns already looked at aren't sent again, and a conversation with
  nothing new costs no model call;
* a token in the conversation, and anything `--trace-redact` names,
  never reaches the look back's prompt;
* compaction looks back first, and under `ask` the offer waits for the
  turn to end;
* `off` never looks, `/remember` still does, and `auto` keeps without
  asking;
* a provider that fails costs one line, not the session;
* the page's button asks, and ending a conversation there follows the
  mode.

## What was deliberately not built

* ~~**Learned-skill inputs from the same look back.** Device ids, a home
  server's name, a city: these are facts about the person, and they are
  also what a learned skill ([note 96](96-solve-it-once.md)) needs as
  inputs. One look back could propose both. The skill learner already
  has its own write-up step, and both halves are still settling, so
  they stay separate for now. Joining them later means a second kind of
  line from the same call, not a second call.~~ Built the other way
  round: the skill's write-up lists the facts it kept out, in this
  note's line format, and they go through this note's rules
  ([note 106](106-what-the-recipe-leaves-out.md)).
* **Corrections that replace.** *"correction: Their manager is Priya,
  not Sam"* is kept as a new memory. The old one stays until you
  `/memory forget` it. Deciding which old sentence a correction
  replaces is a judgement, and a wrong one would delete something true.
* **A package setting.** A package that asks for memory
  (`[memory] via`) gets the operator's `--reflect`. It has no
  `[memory] reflect` key of its own yet.
* **A look back when the page closes.** A browser tab closing is not a
  conversation ending that anyone is there to answer for. On the page
  the look back runs on *clear*, *restore*, and the button.
