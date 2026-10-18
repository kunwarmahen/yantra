# 130 — every conversation, kept to read again

*[Note 38](38-giving-it-back.md) made a checkpoint carry a conversation
without its agent, and `/save` and `/load` have kept sessions since the
first REPL. This note is about the other thing people want from old
conversations: not to carry one on, but to read it again.*

## The problem, as you would meet it

Last week the agent told you what pressure your boiler should show, and
how to reset the router. Today you want that answer again.

`/load` can't help unless you typed `/save` at the time, under a name you
now have to remember. If you did, it puts the whole thing back into the
agent, replacing the conversation you're in, so you can scroll it. What
you wanted was to search "boiler" and read.

## A log beside the checkpoints

`history.py` keeps every conversation through Yantra by itself, in the
terminal and on the page:

```
/chats              every conversation kept, newest first
/chats boiler       only the ones that say "boiler", the words marked
/chats 2            read conversation 2 from that list
/chats open 2       carry it on here (this one stays kept)
/chats delete 2     delete it for good (asks first)
```

On the page it is the **history** button in the top bar: a search box,
the list, and a reading view with **carry on here** and **delete**.

WRITTEN AS IT GOES, NOT AT THE END. Each turn, however it ends, writes
the conversation's row again: when it started, its first words as its
title, which road (terminal, page) and which folder, the model, and the
conversation itself in the checkpoint's own shape (`session.snapshot`,
which `SessionStore.save` now shares). A conversation cut off by a
crash or a closed lid is the one most likely to be looked for, and a
log written at `/quit` would never have it. `/clear` and `/load` start a
new row; `/chats open` carries on the old row, which is why the list
holds one line per conversation and not one per sitting.

ONE ROW, REPLACED; NOT APPEND-ONLY. The checkpoints' append-only promise
(session.py) is about a session that may continue, where any earlier
version should stay loadable. This log is a record of what was said.
Every version of it would make "delete" mean "delete all the versions
you can find", and it would grow by a whole conversation per turn.

FOUND BY ITS WORDS. The person's words and the agent's answers go into
an SQLite full-text index (FTS5): local, instant, nothing spent and no
model asked. Every word typed must appear, each matching the start of a
word (`rout` finds *router*). The search box is built from word
characters only, each one quoted, so a stray `"` or the word `NEAR` is
a word and never FTS5 syntax. A tool's output is not indexed: a page of
search results the agent read is not what anybody remembers a
conversation by. The reading view shows the tools anyway, folded, with
what came back.

DELETED MEANS GONE. `delete` drops the row and its index entry with
SQLite's `secure_delete` on, so the freed pages are overwritten, and
then merges the index so no older segment still holds the words. The
test for it reads the file's raw bytes after a delete and finds no trace
of the word. A history you can only hide is one you can't trust with
anything. Not the one open now, though: it would be written again at the
end of the next turn, so the terminal and the page both say to `/clear`
first. A checkpoint you made with `/save` was a separate, deliberate
copy and is not touched.

ONE FILE PER MACHINE, NOT PER PROJECT. It lives at
`~/.local/state/yantra/conversations.sqlite`, beside the memory store
(memory/local.py) and for the same reason: you look for a conversation
by what was said, not by which folder you happened to start Yantra in.
Each row still says the folder. `YANTRA_HISTORY=PATH` puts it
elsewhere; `YANTRA_HISTORY=off` or `--no-history` keeps nothing.

AN UNATTENDED RUN IS NOT A CONVERSATION. A schedule firing every half
hour (`--unattended`) would bury what people said under what Samay
checked. Samay's logbook already keeps those runs.

## Live receipt

`qwen3.8:latest` on Ollama, in a scratch folder with `YANTRA_HISTORY`
pointing at a scratch file. A one-shot question, then a REPL:

```
$ yantra --provider ollama --model qwen3.8:latest "In one sentence: what pressure should a home boiler show when cold?"
A home boiler should read roughly 1–1.5 bar (about 1.25 bar at midpoint) on the
pressure gauge when cold, ideally in the middle of the manufacturer's green zone ...

> In one short sentence: name a good time to visit Lisbon.
Spring—particularly May or early June ...
> /chats
conversations kept, newest first:
    1. In one short sentence: name a good time to visit Lisbon. (this one) --
Sun 18 Oct 16:14, terminal, 1 message
    2. In one sentence: what pressure should a home boiler show when cold? --
Sun 18 Oct 16:14, terminal, 1 message
> /chats boiler
conversations that say 'boiler', newest first:
    1. In one sentence: what pressure should a home boiler show when cold? --
Sun 18 Oct 16:14, terminal, 1 message
       …what pressure should a home [boiler] show when cold? A home [boiler]…
```

Then `yantra --web` on the same file, one question asked through the page
(*how do I bleed a radiator?*). `/api/chats?q=radiator` listed it as
`road: page`, open now, with the word marked. In the browser, at desktop
width and at 390 px: the list, the search for *boiler* narrowing it to
1 of 3, the reading view with *boiler* marked in the question and the
answer, **carry on here** on the Lisbon conversation (the page's
conversation became it; `current` moved to its id), and **delete** →
*delete for good* on the boiler one. Afterwards the list held two, and
`grep -c boiler` on the database file counted 0.

## Tests

`tests/test_history.py` (26): kept with its first words as its title;
each turn replaces the row; nothing kept before the person says
anything; newest first; search finds words in the answer; every word
must match, by prefix; typed syntax is never FTS5 syntax; tools read
with their results; long output shortened; a delete is gone from the
list, the search, and the file's bytes; off and default path; the
terminal keeps every turn, `/clear` starts a second row, `/chats`
lists, searches and reads, `open` grows the same row, `delete` asks and
a no keeps it, the open one can't be deleted; the page lists one as
open now, searches, reads, deletes, refuses the open one (409), carries
one on into its own row, and starts a new row after clear.

## What is not here yet

* ~~**Dvara's own roads.**~~ A conversation through Telegram or
  Sarathi's home page is Dvara's, and Dvara keeps it in this same log,
  one file per person, each agent named as the row's folder
  (`listed(folder=...)`); what its old `/new` book held came over with
  its own dates (`keep(at=...)`). Its `/chats` searches and deletes, and
  Sarathi's home page has a History button (Dvara's notes/50).
* **Searching tool output.** Left out on purpose (above). If "the agent
  found it on some page" turns out to be how people remember, it is one
  more column in the index.
* **Export.** Reading is here; saving one conversation as a file to send
  somebody is not.
