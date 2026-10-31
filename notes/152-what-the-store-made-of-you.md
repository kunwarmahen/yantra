# 152 — What the store made of you

[Note 150](150-a-place-for-each-fact.md) gave every memory a place and
made a store's facts and beliefs worth having. Then it kept them out of
the prompt: the model saw the memories the agent kept, and the facts
were "for other agents". That left the obvious question unanswered.
What are facts and beliefs *for*, if the agent that kept the memories
never sees them?

## Three things, not one

* **A memory** is one sentence the agent kept, as it was said, with a
  date. Two can overlap or contradict: "lives in Cary" this year,
  "moved to Austin" the next.
* **A fact** is the current answer to one question about the person,
  one per question. Where do they live: Cary. It is grouped (who they
  are, where they live, their accounts, the people in their life), and
  a new answer replaces the old instead of sitting beside it.
* **A belief** is a pattern a store found across several memories, with
  a confidence: "prefers a chart to a table, 70%". Nobody said it. It is
  a guess.

A memory is evidence. A fact is the answer. A belief is a hunch. The
prompt was getting only the evidence: twenty sentences, picked by the
first message and by how recent they were, some saying the same thing
twice, some out of date.

## The layer, from a profile

A store may now answer an optional verb, `profile(user, query)`, and the
layer is built from what it hands back:

```
# What you remember about this person
They told you these in earlier conversations. Any of them may be out of
date: when the current message says otherwise, the current message wins. ...

What they prefer:
- Dislikes red-eye flights

Where they live:
- Lives in Cary, NC; RDU is their airport, 12 minutes away

Who they are:
- Their personal email is asha@example.com

What they seem to be like -- guesses drawn from many conversations, not
things they said; check before an answer rests on one:
- prefers a chart to a table (70% sure)

Other things they told you:
- Started a pottery class on Thursdays
```

**CORE FACTS ALWAYS, THE REST WHEN ASKED.** Who they are, where they
live and what they prefer go into every conversation; the rest come
when the first message reaches them. An account number or a street
address is not core. "Find me a flight to Denver" carries the home
airport and not the brokerage account, so a cloud model is not handed
account numbers on every turn for nothing. Smritikosh finds the
relevant ones through the memories its search found for the message;
the built-in store, by shared words.

**A GUESS SAYS IT IS ONE.** Beliefs come under words that call them
guesses, with how sure the store is. A model told "prefers a chart" as a
fact would argue with a person who asked for a table.

**NOTHING SAID TWICE.** A memory a fact was made from is left out: the
fact already says it, more cleanly. What remains under *Other things
they told you* is what no fact covers yet (a memory with no topic, one
kept a minute ago).

**THE STORE'S ADMIN DECIDES WHAT GOES IN.** Smritikosh's dashboard turns
facts, beliefs and memories on or off, for everyone or one person. The
profile says which; the layer shows what it was given. A store without
`profile` gets the twenty memories, as before, and so does a store
whose profile fails (said once, like every memory failure).

**THE BUILT-IN STORE ANSWERS TOO.** A memory with a slot (topic plus key)
is already one current answer for its place, so it is a fact; the core
topics are home, contact and preference. It has no beliefs: nothing
there looks across memories. So both roads read the same way.

## Use, not being shown

[Note 143](143-a-memory-in-use.md) made a memory in use stop ageing:
what the answer or a tool's arguments carried, past the person's own
words, is reported with `used`. A fact or a belief is reported the same
way, by its own id (`fact:location/city`, `belief:…`). Smritikosh then
marks the memories behind the fact used and resets the fact's decay, so
the statement a fact you rely on came from is never pruned from under
it.

Being shown is not being used: twenty lines are shown on every turn,
and a rule that kept whatever was shown would keep everything. That is
the default. Smritikosh's admin can choose "shown" instead, for a house
that would rather lose nothing.

## Live receipt

`qwen3.8-64k:latest` on Ollama, the built-in store in a scratch state
folder holding five memories: home (Cary/RDU), contact (email), account
(a Fidelity number), preference (no red-eyes), and the pottery class
with no topic. Asked *"I'm booking a flight to Denver next Friday. From
what you already know about me, which airport do I leave from and what
should I avoid? Don't search, just answer."* The layer was the one
above, without the belief line (the built-in store has none). The
brokerage account was left out: nothing in the question reached it.

```
- **Airport:** Raleigh-Durham International (RDU) — you live in Cary,
  about 12 minutes away, so that's your departure point.
- **Avoid:** Red-eye flights — you've told me you dislike them.
So for your Denver trip on Friday November 6, book a daytime departure
out of RDU. Just flagging that both of these come from earlier
conversations — correct me if either has changed.
── end_turn · 3371 in / 196 out · 1 iteration(s)
```

and the store afterwards: the two facts it leaned on marked used, the
other three not.

```
1|Lives in Cary, NC; RDU is their airport, 12 minutes away|2026-10-31T22:33:43+00:00
2|Their personal email is asha@example.com|
3|Their Fidelity brokerage account is Z12345678|
4|Dislikes red-eye flights|2026-10-31T22:33:43+00:00
5|Started a pottery class on Thursdays|
```

## What was deliberately not built

* **Every fact, every turn.** Sixty-odd facts are 6,700 characters, and
  among them account numbers and an address. Core plus relevant was the
  person's choice.
* **A belief Yantra works out itself.** Finding patterns across memories
  is the store's job, done in its own time; the built-in store has none.
* **Switches in Yantra.** What a prompt carries about someone is decided
  where their memories are kept, by that store's admin, so every agent
  reading them gets the same answer.
