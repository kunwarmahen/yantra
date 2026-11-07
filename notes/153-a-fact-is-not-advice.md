# 153 — A fact is not advice

[Note 152](152-what-the-store-made-of-you.md) put a store's facts in the
prompt, one line per fact, on every turn. Then the facts were read. One
of them, kept from a look back over an inbox:

> Has Acme Mutual as their auto insurer (policy ending 4K, visible in
> their mail inbox), so a later "who handles my car insurance" or
> "check my auto renewal" question should default to Acme Mutual rather
> than asking which carrier.

That sentence holds four things. The fact (*Acme Mutual is their auto
insurer*). A detail that identifies it (*policy ending 4K*). Where it
was seen (*visible in their mail inbox*). And advice to the agent on
how to answer a question nobody has asked yet. Only the first two
belong in a fact. The rest takes up room in every prompt, and its words
(*question*, *default*, *asking*) pull the fact into searches it has
nothing to do with.

## Where it came from

The wording rule did it. [Note 107](107-a-fact-that-says-what-it-is.md)
asked a look back to say what a fact *is* and "what it plainly implies
that a later question might turn on". The first half took the time zone
fact from eighteenth to second. Some models read the second half as an
invitation to write the later question into the fact, and to say what
to do about it.

And the store kept the advice. Smritikosh copied a labelled statement
into its graph word for word, as the fact
([note 150](150-a-place-for-each-fact.md)). The memory and the fact were
one sentence.

## Two changes

**The rule asks for less.** The look back and the `remember` tool now
ask for what the thing is, with *at most one short detail in brackets*
for what it plainly means (*"Lives in Lisbon, Portugal (Western
European Time)"*). And, in so many words, only the fact: not how to use
it later, not where it was seen.

**The store words the fact itself.** A memory is still the sentence the
agent kept and the person approved, word for word. That's what the
memory list shows. But Smritikosh now makes the *fact* from it at its
next consolidation cycle (hourly), in the slot the agent gave it, worded
short: the thing and its value. Until the cycle runs, the memory has no
fact, and `profile` hands it over as a memory instead, so the prompt
never goes without it. If the cycle's model call fails, a plain rule
cuts the "so a later …" clause and the "visible in …" aside, and the
fact is filed anyway.

Doing this when the memory is saved was the other choice. It would make
every `remember` wait on a model, and fail when that model is down.
Background work is where a store already turns memories into facts and
beliefs, so this goes there too.

## Receipt

Four statements (the insurer and the email changed for this page) worded by Smritikosh's consolidation model,
`qwen3.8-64k:latest` on Ollama, in one call (28 s):

```
finance/auto_insurer: Their auto insurer is Acme Mutual (policy number ends 4K).
location/city: Lives in Cary, NC (Eastern time).
identity/naina.email: Naina's school email is naina@school.example.
finance/brokerage: Their brokerage for stocks is Robinhood.
```

The second was short already, and came back unchanged. The others went in as
*"…, which they use for her teacher contacts, so school emails should
go there"* and *"(account seen in a screenshot), so portfolio questions
should check Robinhood first"*.

The rebuild's dry run over one real store's 81 memories gave 68 facts
in place of 69. *"Has a Coinbase account and uses it to check their
crypto portfolio value, so a later 'check my crypto' …"* became *"Has a
Coinbase account."*; a home Wi-Fi fact lost *", so references to 'my
Wi-Fi' or home networking …"*. A list of playlists stayed long: a list
is the fact.

## The cost

Note 107 measured the longer wording helping the built-in `local`
store. That store matches words, and *"… for interpreting 'what time is
it for me'"* shares words with *"what time is that for me"*: found went
from 14 to 22 of 35 on `qwen3.8:latest`. The bracket stays, and it
carries most of that (*"(Indian Standard Time)"*). The later question's
own words don't. On `local` most of that gain went. The recall trial
(`examples/memory_recall_trial.py --store local --distractors 30
--repeat 5`, `qwen3.8:latest`) was run again with this wording: found
by search 22 → 11 of 35, answered from memory 20 → 16, kept 35 → 33,
and the week's trip still never kept (0/5). The bracket kept
`timezone` at 2 of 5 found; `packages`, whose fact had carried the
later question's *"dependency"*, fell from 5 to 1. Accepted: a prompt
read on every turn is worth more than words for a store that matches
words ([note 107](107-a-fact-that-says-what-it-is.md), *With one short
bracket*).

## What was deliberately not built

* **Rewording memories.** The memory is what the person approved, so it
  stays as it was said. Only the fact, which is derived, is reworded.
* **Letting the store pick the slot.** The agent's topic and key decide
  where a fact goes, and the store only words the value. A store picking
  keys is how one thing ended up under two names before note 150.
* **A model call when the memory is saved.** See above.
