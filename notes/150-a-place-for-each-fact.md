# 150 — A place for each fact

You told the agent you live in Cary. A year later you move to Austin and
say so. Now it remembers both. The next time you ask for flights, the
prompt layer carries *"Lives in Cary, NC"* and *"Lives in Austin, TX"*
side by side, and the model picks one. Sometimes the wrong one.

Nothing in memory knew the two were about the same thing. A memory was a
sentence and nothing else ([note 100](100-what-it-knows-about-you.md)).
Two sentences that can't both be true were just two sentences.

The opposite problem sat next to it. A memory never went away unless you
forgot it by hand. That's right for where you live. It's wrong for
*"working on the Q3 launch"*, which is still being offered to the model
in March.

## Topic and key

Each memory can now say what it's about:

* a **topic** from a short fixed list: home, work, family, contact,
  health, diet, account, tool, preference, habit, project, other;
* a **key** for the one thing it names: `city`, `brokerage`, `email`;
* for someone else's fact, **whose**: your brother's email is
  `contact`, about `Ravi`, key `email`.

**The list is short on purpose.** A local model picks reliably from
twelve plain words. A store that files facts by category maps each one
to a category of its own (Smritikosh: home → location, account →
finance).

## A new one in the same place replaces the old

Topic, whose and key together are the memory's **slot**: `home||city`,
`contact|ravi|email`. Two memories in one slot can't both be true, so
keeping a new one forgets the old one, and says so:

```
Kept:
- Lives in Austin, TX (Central time) (instead of: Lives in Cary, NC (Eastern time))
```

The ask before a `remember` shows the same *(instead of: …)*, because
forgetting is part of the write.

**Written first, forgotten after.** If the store fails between the two,
you have both and a notice saying the old one stayed, never neither.

**It's Yantra's, not the store's.** The replace is a `list` and a
`forget` (`Memory.remember_replacing`), so it works on the built-in file,
on Smritikosh, or on anyone's store that has those two verbs. A store
only has to hand back the `topic` and `slot` it was given.

**Only a key makes a slot.** The first version let home and work replace
with no key, since a person has one of each. Then a model filing a
house's facts put the thermostats, the stairs light and the fridge under
`home`, and each one would have replaced where you live. With no key, a
memory replaces nothing.

**The look back is shown the keys.** It writes each line as
`fact [home/city]: …`, and the list of what is already remembered comes
with each memory's own `[topic/key]`. That's how the same thing gets the
same key next time, and how a change it notices lands on the right
memory. A line with no bracket, or no key, is still kept, just with no
slot. A look back that keeps the fact unlabelled beats one that loses
it ([note 107](107-a-fact-that-says-what-it-is.md) has the time a
wording change lost every fact).

## How long it lasts unused

Time is a poor signal for where someone lives. They move, and they say
so, and that's a replace. It's a good signal for what someone is
working on. So the topic carries a lifetime:

| Topic | Lasts unused |
|---|---|
| home, work, family, contact, health, diet, other | until replaced or forgotten |
| account, tool, preference | 365 days |
| habit | 180 days |
| project | 60 days |

**The agent decides; the store obeys.** Yantra sends the lifetime as
`lifetime_days` with the memory. The built-in file drops a memory past
it whenever it's read. Smritikosh's nightly pruning deletes it, along
with the fact it filed.

**Use resets the clock.** Days count from the later of written and last
used ([note 143](143-a-memory-in-use.md)). The account a morning brief
reads every day never lapses. The one you closed last spring does.

## Account numbers, not passwords

The look back used to drop account numbers with passwords and tokens.
It doesn't any more: which brokerage you use, and its account numbers,
are what lets a brief read the right accounts without asking. Passwords,
tokens, card numbers and ID numbers are still never kept. A remembered
account is a starting point; a skill still checks the live account list.

## What the store does with it

Smritikosh files a labelled statement straight into its fact graph, word
for word, with the memory as its source. Before this, a second model
re-read Yantra's already-worded facts every hour and filed them under
keys it made up, so the same thing ended up under two keys. Its own side
of this is in its README, under *A statement that says where it goes*.

## Receipt

One look back on `qwen3.8:latest`, run straight against Ollama, over a
short conversation, with two facts already kept and shown with their
keys (`[home/city] Lives in Cary, NC…`, `[contact/ravi.email] Their
brother Ravi's email is ravi@example.org.`):

```
correction [contact/ravi.email]: Their brother Ravi's email is ravi.new@example.org (replacing ravi@example.org).
fact [tool/editor]: Uses Neovim as their text editor for all editing work.
```

It reused the key it was shown, so keeping the first line replaces the
old address. It gave the new fact a key of its own. Both lines parse to
a topic and key.

The recall trial after the look-back prompt changed
([`examples/memory_recall_trial.py`](../examples/memory_recall_trial.py),
`qwen3.8:latest`, local store, three repeats):

```
case        remember    kept        search      in prompt   answer
airport     2/3         3/3         0/3         3/3         3/3
diet        3/3         3/3         0/3         3/3         3/3
packages    0/3         3/3         3/3         3/3         3/3
timezone    2/3         3/3         1/3         3/3         3/3
editor      0/3         3/3         2/3         3/3         3/3
daughter    2/3         3/3         3/3         3/3         3/3
units       0/3         2/3         2/3         2/3         2/3

kept 20/21 · answered from memory 20/21 · baseline 0/21 · kept what it should not 0/3
```

The new line format cost nothing. Every fact but one was kept and
answered from, and the trip that should be let go was let go every
time. The miss was a habit said in passing (units), once in three. The
trial now records where the look back filed each fact
(`proposed_where`).

On Smritikosh's side, its rebuild script's dry run over one person's 87
real memories (`qwen3.8-64k:latest`, nothing written): 79 of them
labelled, 99 facts became 58, and 554 beliefs became 2, each shown by
three memories. The labeller filed a house's thermostats and fridge
under `home`, which is why only a key makes a slot.

## What was deliberately not built

* **A topic in the prompt layer.** The layer still lists bare sentences.
  `[home/city]` in front of each would show `remember` the keys too, but
  it would also tempt the model to quote the brackets back. Keys reach
  `remember` through its description and the person's own words.
* **Relabelling old memories in Yantra.** Memories kept before topics
  have none and replace nothing. Smritikosh's rebuild script can file
  them (`--label`). The built-in file has no such pass; a memory said
  again with a topic gains it.
* **A lifetime the person sets.** The table is the agent's. A person who
  wants a project kept says so, and the agent keeps it under another
  topic.
