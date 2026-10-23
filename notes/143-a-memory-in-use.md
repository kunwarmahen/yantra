# 143 — A memory in use does not age

You told the agent you live near RDU a year ago. Since then it has used
that every time you asked about a flight. Meanwhile you mentioned a
pottery class, a cat, a new phone, a dozen small things. Then one day a
conversation starts and RDU isn't in the prompt any more.

Nothing deleted it. It aged out. Every store ranks by how new a memory
is, at least partly. The built-in file's `list` is newest first, and the
prompt layer tops up from that list ([note 100](100-what-it-knows-about-you.md)).
Smritikosh's search gives 30% of its score to recency, counted from when
the memory was written. Neither ever learned that RDU was in use. A
memory leaned on every week and one nobody has needed since March were
the same age.

## Shown is not used

The obvious fix is to count a memory as used when it is recalled.
That's wrong here. The prompt layer carries twenty memories into every
turn, so every memory in it is "recalled" on every turn. If being shown
were enough, everything in the layer would stay fresh forever and
nothing outside it ever could.

Smritikosh already keeps a count of how often a memory turns up in a
search (`recall_count`). That is the server's own record of what it
showed. Only the client saw the answer, so only the client can say
whether the answer drew on the memory.

## The fifth verb

**`used(user, ids)` is optional.** `MemoryStore` stays four verbs, so a
store written against four still plugs in. A store that has `used`
counts a memory's age from its last use, and its `list` puts the most
recently used or written first. Over MCP it's a tool named `used`,
taking `memory_ids` and `user_id` and answering `marked`
([note 102](102-kept-somewhere-else.md)). A server without the tool is
simply not told, the same as a missing `forget`.

**Yantra decides with no model.** When a turn ends with an answer, it
looks at every memory the model was shown in this conversation: the
layer, plus whatever `recall_memory` returned. For each one it asks:
does the answer, or the arguments to a tool, carry something from this
memory that the person's own message did not?

* a **name**: a capital past the first word, an acronym, or a number
  (*RDU*, *Priya*, *4090*); one is enough;
* otherwise **two words** of four letters or more (all of them, when only
  one is left), skipping the verbs every memory starts with (*lives*,
  *uses*, *prefers*).

Words the person said themselves don't count. An answer about Python to
a question about Python did not draw on *"prefers Python"*.

**Once per conversation.** A memory is reported once per conversation,
the first time an answer draws on it. It's sent on its own thread
behind the answer, so a slow store never delays the next message. If
the store fails, Yantra says so once a session and the turn carries on.

The rule leans towards *not used*. A memory wrongly kept fresh is the
smaller harm, but reporting the whole layer every turn would turn `used`
back into *shown*.

## The store's side

Smritikosh grew the other half:

* `POST /memory/used` stamps `events.last_used_at` (migration 0031).
  Ids that aren't the caller's are skipped, so one person's `used` can't
  keep another's memories fresh.
* Hybrid search counts recency from the later of written and used.
  `GET /memory/{user}?order=active` lists by the same date, and the MCP
  `list_memories` always does, because that's the list a client tops
  its prompt up from.
* The pruner measures age the same way, so a used memory is never pruned
  for being old.

Looking at this turned up two bugs in the weekly decay of facts, the
graph side that Yantra's own statements never reach:

* **It compounded.** Each weekly pass decayed a fact by its age since it
  was last seen, so week 8 took off 8 weeks again on top of the 7 already
  taken. Each pass now stamps `decayed_at` and counts from the later of
  that and `last_seen_at`.
* **It lost the months.** `duration.between(a, b).days` in Cypher is
  only the days left over after whole months, so 56 days read as 25, and
  `purge_unseen_facts` could never pass a cutoff over a month.
  `duration.inDays` counts them all.

A fact unseen for 56 days, then one more week, on a scratch Neo4j:

```
fact unseen 56 days, first pass:  0.524  (half-life says 0.524)
one more week (63 days in all):   0.483  (half-life says 0.483)
```

## Receipt

`qwen3.8:latest` on Ollama, the built-in store, two memories written a
year ago and two today:

```
$ yantra --provider ollama --model qwen3.8:latest --memory local --reflect off \
    "I want to fly to Denver next Friday. Where would I fly from, and which
     seat should I ask for? Answer in two sentences."
You'd fly from Raleigh-Durham (RDU), since that's your local airport,
departing next Friday (October 30). Ask for an aisle seat, as you prefer
aisle over window.
```

```
#2 Prefers aisle seats      | written 2025-01-01 | used 2026-10-23T14:13:56
#1 Lives near RDU (...)     | written 2025-01-01 | used 2026-10-23T14:13:56
#4 Started a pottery class  | written 2026-10-23 | used -
#3 Has a cat called Biscuit | written 2026-10-23 | used -
```

The two year-old memories the answer drew on are now listed first. The
cat and the pottery class were shown but not used, and stayed as they
were. The first version of the rule missed the aisle seat: the question
said *seat*, which left only *aisle* from the memory, and the rule still
asked for two. Now it counts only the memory's words the person didn't
say.

On Smritikosh, against a scratch Postgres: before any use, a 3-day-old
memory had recency 0.905 and the year-old RDU had 0.0. After `used`, RDU
had 1.0 and came first in both search and list. A `used` naming
another person's memory marked 0.

`/memory` and the page say when each memory was last used:

```
 * #1  Lives near RDU (Raleigh-Durham airport)  (used 2026-10-23)
```

## What was deliberately not built

* **A model deciding what was used.** One more call every turn to save a
  date. The word rule is free, and its mistakes are cheap.
* **Raising importance on use.** Smritikosh's `POST /feedback` does that
  for a person saying a memory was good or bad. Being used isn't a
  judgment of the memory, so it changes when the memory counts as new,
  not how much it counts.
* **Reporting a memory again later in the same conversation.** Once is
  enough for its date. A conversation that runs for days might deserve a
  second report; none has needed one yet.
