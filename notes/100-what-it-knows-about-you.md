# 100 — What it knows about you

Tell the agent on Monday that you live near RDU, the Raleigh-Durham
airport. On Thursday, in a fresh session, ask it to find flights to
Austin. It asks where you're flying from, because nothing survived.
`write_note` ([note 04](04-tools.md)) doesn't help here. It remembers
things about a *project* — a file layout, a dead end — in that
project's `.yantra/` folder. Where you live isn't about any project,
and it has to follow you from one to the next.

This note is about memory of the **person**: what they told the agent
in earlier conversations that should change an answer in a later one.

## What is worth remembering

A fact earns a place when it passes three tests at once:

1. **It's about you, not the task.** "I live near RDU" passes. "The
   flight is at 9" doesn't.
2. **It stays true for months.** "Flying to SFO on Tuesday" is wrong
   by Wednesday.
3. **It changes an answer in a different, later conversation.** "Find
   me flights to Denver" should start from RDU without being told.

Where you live, what you use ("uv, not pip", "I run Ollama"), what you
prefer ("short answers"), and corrections ("no, my manager is Priya")
pass. The task at hand, and next week's plans, don't.

## Put it in the prompt, don't wait to be asked

The third test decides the whole design. "Find me flights to Austin"
never mentions an origin, so the model has no reason to go looking for
one. A `recall_memory` tool on its own would sit unused. On a local
model, which rarely calls a tool it wasn't pushed towards, it would sit
unused every time.

So what's remembered goes **into the system prompt**, as its own
`memory` layer beside `env` and `skills` ([note 29](29-environment-awareness.md)).
The model doesn't have to think of looking. The layer is built from
what matches the conversation's first message, then topped up with the
most recent memories until it holds twenty. The top-up is the RDU case:
"flights to Austin" shares no word with "lives near RDU", and the fact
has to be there anyway. A person's standing facts are few, so carrying
them whole is cheap.

The layer is filled **once per conversation**, at the first message. A
layer rewritten every turn would throw away the cached prompt prefix
([note 13](13-caching.md)) and pay the store's lookup time on every
turn. The cost is that something remembered mid-conversation reaches
the prompt only in the next conversation. The model knows it anyway,
because it just said it.

## The message in front of it wins

A memory can be out of date. You move, or you fly out of Boston for a
conference. So the layer says so plainly:

```
# What you remember about this person
They told you these in earlier conversations. Any of them may be out of
date: when the current message says otherwise, the current message
wins. When an answer leans on one, say so in a few words ("from RDU, as
before -- say if not") so a wrong one gets corrected.

- Lives near RDU (Raleigh-Durham International Airport) in North Carolina.
```

That last sentence does two jobs. It makes the answer honest about
where "RDU" came from. It also gives you the chance to correct a wrong
memory: you see it in the reply, and `/memory forget 3` removes it.

## The agent decides what to keep, and asks first

The model writes a memory with a `remember` tool, as one short
sentence. That tool isn't read-only, so it **asks before writing**,
like `write_file` does. You see the exact sentence that will be kept
about you before it is kept.

The store never reads a transcript and never decides anything. It is
handed finished sentences, keeps them, and searches them. That keeps
any store simple, and it means the model deciding what matters is the
one already in the conversation, which knows why it mattered.

## Whose memories

On your own terminal or page, the memories are yours: `$YANTRA_USER`,
else your login name. A service passes each end user's identity instead.
With **no identity, there is no memory**: nothing is read and nothing is
written, so one person's RDU can never reach somebody else's session by
default. Every read and delete in the store is scoped by that identity.

Memory is **on by default for your own sessions**. An agent package is
somebody else's agent, so it gets memory only if its `agent.toml` asks
for it:

```toml
[memory]
via = "local"          # local | off; a package that says nothing gets off
```

On a cloud model, what is remembered rides inside every request to
the provider, the same tradeoff as the city in `--env-context full`. On
Ollama it never leaves the machine.

`--memory off` (or `YANTRA_MEMORY=off`) turns it off for your own
sessions. `--memory local` turns it on for a package that didn't ask.
Evals never touch it: a verdict that depended on what this machine's
owner once said wouldn't be a verdict on the package.

## The local store

The built-in store is one sqlite file at
`~/.local/state/yantra/memory.sqlite`, next to the MCP tokens, because
it belongs to you rather than to a project. It searches by word
overlap, so "flights from RDU" finds "lives near RDU" and "somewhere
warm" finds nothing. It isn't clever. It exists so memory works with
nothing installed: no server, no embeddings, no second model. The
prompt layer's top-up covers most of what cleverer search would.
Saying the same thing twice keeps one copy.

The plumbing names no store. A store is four verbs — `remember`,
`recall`, `forget`, `list` — and building the prompt block is
Yantra's job, not the store's.

## A broken store costs the memory, never the turn

If the store can't be opened, startup says so and the session goes on
without memory:

```
memory: off -- cannot open the local store ([Errno 20] Not a directory: …)
```

If it fails at a conversation's first message, the turn goes on without
the layer, and the terminal prints a yellow line (on the page, a banner)
saying why.

## Reading and correcting it

```
> /memory
memory: local, for asha -- 1 remembered
 * #1  Lives near RDU (Raleigh-Durham International Airport) in North Carolina.
```

`*` marks what this conversation's prompt carries. `/memory forget ID`
removes one, `/memory add TEXT` keeps something in your own words, and
`/memory find WORDS` searches. None of them costs a model turn. On the
page, a bookmark chip in the header opens the same list, with a
*forget* button on each row and a field to add one.

## Receipt

`qwen3.8:latest` on Ollama, a scratch store, identity `asha`.

**Session 1** — RDU is mentioned in passing, and nobody says
"remember":

```
$ yantra --provider ollama --model qwen3.8:latest --yolo \
    "I live near RDU and I'm planning a trip. What should I think about
     when booking flights from RDU to Denver? Keep it short."
→ remember()
│ remembered (#1): Lives near RDU (Raleigh-Durham International Airport) in
│ North Carolina.
A few things worth thinking about for RDU → DEN: …
```

The local model called `remember` by itself, prompted only by the
layer's instruction. (`--yolo` here so a one-shot run doesn't stop to
ask. At a terminal, the call waits for your y/n.)

**Session 2** — a new process, and an origin is never mentioned:

```
$ yantra --provider ollama --model qwen3.8:latest \
    "Find me flights to Austin next month. Just tell me which airport
     you'd search from and the route; no need to look anything up."
memory: local for asha -- 1 remembered (/memory; --memory off)
From RDU (Raleigh–Durham, near where you live), the route would be
RDU → AUS (Austin–Bergstrom International). Want me to check actual
options and prices when you're ready?
```

It made no recall call, and it says where RDU came from.

**The same question as `ben`**, on the same machine and the same store:

```
I don't know their home location. There's absolutely no memory of the
person. … ask for the departure city.
→ ask_user()
```

`2364 passed, 1 skipped` (was 2328). The tests check that:

* one identity never sees, finds, or deletes another's memories;
* a fact that shares no word with the first message still reaches the
  prompt;
* the prompt doesn't change for the rest of a conversation after a
  mid-conversation write, and a new conversation (`/clear`) sees it;
* a store that throws costs the layer, not the turn, and says why;
* a package that says nothing, a library build that says nothing, and
  every eval get no memory;
* `[tools] allow` can leave the two tools out while the layer stays;
* `remember` asks first, and a refused call writes nothing;
* `/memory` and the page list, add, and forget without a model turn.

## What was deliberately not built

* ~~**Remembering what was said in passing.**~~ In session 1 the model
  happened to call `remember`. A model that doesn't won't keep "RDU"
  at all. Built since: a short look back over the conversation when it
  ends (`/quit`, `/clear`, `/load`, before compaction, or `/remember`)
  proposes facts you keep or drop —
  [note 101](101-looking-back.md).
* **Outside stores.** The four verbs are the whole interface, but only
  `local` exists. An outside store will connect as an MCP server, with
  a map from the four verbs to its tool names, so no store's name or
  address ever appears in Yantra's code.
* **Clever search.** Embeddings would need a model and a dependency. A
  store that has them can offer them.
* **Rewriting the layer mid-conversation.** It would cost the cached
  prefix on every change. Changes apply from the next conversation.
