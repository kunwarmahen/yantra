# 109 — Fewer lines, fewer facts

At the start of every conversation, Yantra puts up to twenty things it
remembers about you into the model's instructions
([note 100](100-what-it-knows-about-you.md)). That's the memory layer.
[Note 103](103-said-once-found-later.md) caught it failing in a way that
looked like crowding: *"Has a daughter named Meera"* was one of twenty
lines, and the bedtime story never used the name. With one or two lines
it always did. The note's guess was that a store that ranks well would
do better with fewer, better-ranked lines.

This note tests that guess, and it doesn't hold up. The layer stays at
twenty.

## What was tried

The layer is filled the same way either way. The store's search for
your first message goes first, then the most recent memories fill the
rest. The narrow version capped the search at five lines and the whole
layer at eight. A person who has told Yantra eight things or fewer
sees all of them either way, so only someone with a lot remembered could
notice the difference.

When the store held more than fit, the narrow layer also said so: *"they
told you more than this … `recall_memory` searches the rest."* Without
that line, a short list looks complete, and the model has no reason to
search.

## How it was measured

The recall trial from [note 103](103-said-once-found-later.md), with
repeats from [note 105](105-a-rate-not-a-picture.md). A fact is said
once, in passing. The conversation ends, and the look back keeps it.
Then thirty newer, unrelated facts bury it. A fresh conversation asks a
question that needs the fact without naming it, like *"My team standup
is at 10am Eastern. What time is that for me?"* after *"I'm in Chennai"*.

That's seven facts times five repeats, 35 later conversations per
column. The trial ran on two local models on Ollama (`qwen3.8:latest`,
`gemma4:12b`) and on two stores: the built-in `local` (word overlap) and
Smritikosh (meaning search, `nomic-embed-text`). The "before" runs used
the twenty-line layer, the "after" runs the eight-line one, on the same
day and the same machine.

| buried under 30 | qwen, Smritikosh | gemma, Smritikosh | qwen, local | gemma, local |
|---|---|---|---|---|
| found by the store's search | 35 → 34 | 32 → 30 | 14 → 13 | 8 → 8 |
| in the layer | 35 → 33 | 32 → **25** | 14 → 13 | 8 → 8 |
| **answered from memory** | **30 → 31** | **31 → 25** | **16 → 15** | **8 → 8** |
| `recall_memory` called | 0 → 0 | 2 → 1 | 0 → 3 | 2 → 1 |
| same question, no memory | 0 → 0 | 1 → 1 | 1 → 1 | 0 → 0 |

Each cell is twenty lines → eight lines, out of 35. Ranges are wide at
this size: 25 of 35 is 55–84%, and 31 of 35 is 74–95%.

## What it says

**On the word-overlap store, the size of the layer doesn't matter.**
Word overlap either finds the fact or doesn't. When it does, the fact
ranks near the top, and eight lines hold it as well as twenty. When it
doesn't, neither layer reaches it. Both models scored about the same
before and after.

**On a store that searches by meaning, a narrow layer loses facts the
store ranks low.** gemma kept the time zone as *"Lives in Chennai."*.
For *"what time is that for me?"*, the embedding search ranked that
eighteenth out of thirty-one, in all five repeats. *"Wakes up around
6am"* sounds more like a question about time. The twenty-line layer
carried the fact anyway, and gemma answered *"7:30 PM for you"* four
times out of five. The eight-line layer cut it, and gemma answered from
the machine's own clock:

> Since you are in the Eastern Time zone, your team standup is at
> **10:00 AM** for you.

It didn't search, though the layer said more was kept. qwen kept the
same fact as *"Lives in Chennai, India (IST, UTC+5:30)"*, which ranked
second, so qwen barely noticed the change.

Across every Smritikosh run, the planted fact ranked first 66 times,
within the top five 119 times, and below fifth 12 times. Twenty lines
catch those twelve. Eight lines don't.

**The crowding is real but small.** On Smritikosh, once the fact was
in the layer, the model used it 61 of 67 times with twenty lines (91%) and 56 of 58
with eight (97%). That's the effect note 103 saw once, now measured as a
rate. It's worth a few points, and losing the low-ranked facts costs
more.

**Saying "there's more" barely moved the search rate.** qwen called
`recall_memory` 3 times in 35 on `local` with the hint and 0 without,
and gemma's count went down. A model that has a fitting fact in front of
it doesn't go looking. A model that doesn't have one rarely suspects it
should. That's the same finding as note 103, from the other side.

## What stayed

* **The layer's size is two numbers now.** `PROMPT_LIMIT` (20) is how
  many lines the layer holds. `PROMPT_SEARCHED` (also 20) is how many of
  them the search may fill before the most recent memories top up the
  rest. Behaviour is unchanged. The next attempt at this is a change to
  one constant and a trial run.
* **The trial's `search` column looks twenty deep, always.** It used to
  look as deep as the layer, so a smaller layer would have quietly made
  "found by search" look worse.
* **Every saved row says what layer it ran with.** Rows from before
  this was recorded count as the twenty-line layer, which is what they
  ran with. `--rescore before.jsonl after.jsonl` then puts the two side
  by side and names the layer in each column, and only when the columns
  differ.

## Running it

```
uv run python examples/memory_recall_trial.py --distractors 30 \
    --repeat 5 --out after.jsonl
uv run python examples/memory_recall_trial.py --store smritikosh \
    --mcp-config smritikosh.json --distractors 30 --repeat 5 \
    --model gemma4:12b --out after-gemma.jsonl
uv run python examples/memory_recall_trial.py --rescore before*.jsonl after*.jsonl
```

For the "before" side, run the same commands from a checkout of the
earlier commit (`git worktree add`). Each run of 40 took about 40
minutes on one RTX 3090.

Run trials against a store that embeds on the same GPU **one at a
time**. With two trials sharing the card, Smritikosh's embedding call
waited behind the other trial's generation, and keeping a fact failed:
*"mcp server 'smritikosh' timed out after 20.0s during 'tools/call'"*.
Alone, the same case passed with the fact ranked first.

## What was deliberately not built

* **A middle size.** Twelve lines with ten searched would still miss a
  fact ranked eighteenth. Search ranking is the problem, and a layer
  size only decides how much bad ranking it forgives.
* **The "there's more" line, at twenty.** It was only measured on the
  narrow layer, where it did little. A prompt line is code whose only
  real test is a trial run
  ([note 107](107-a-fact-that-says-what-it-is.md)), so it doesn't ship
  untested.
* ~~**Rewording the time zone fact.** *"Lives in Chennai."* ranks
  eighteenth, and *"Lives in Chennai, India (IST, UTC+5:30)"* ranks
  second. That's note 107's rule (a fact says what it is about) applied
  to what a fact implies, a time zone. It's the better lever, and the
  look back's job, not the layer's.~~ Built: the look back asks what a
  fact implies. gemma's time zone went from eighteenth to second, and
  buried gemma on Smritikosh answered 33 of 35
  ([note 107](107-a-fact-that-says-what-it-is.md#what-a-fact-implies)).
