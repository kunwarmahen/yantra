# 103 — Said once, found later

Notes [100](100-what-it-knows-about-you.md),
[101](101-looking-back.md) and [102](102-kept-somewhere-else.md) built
memory of the person one piece at a time: a prompt layer, a look back
when a conversation ends, and stores behind MCP servers. Each piece was
checked by hand, once, on one fact. This note measures the whole road on
several facts, the same way on two stores, and says where it breaks.

## The trial

[`examples/memory_recall_trial.py`](../examples/memory_recall_trial.py)
runs each scenario in
[`memory_recall_cases.jsonl`](../examples/memory_recall_cases.jsonl) as
two separate conversations:

* **Session 1.** A fact is said *in passing*, never "remember this":
  *"Any tips for booking cheap flights from RDU to Denver in March?"*
  The model answers. Then the conversation ends, the look back proposes
  what to keep, and the trial keeps all of it, as a person answering
  **a** would.
* **Session 2.** A fresh agent that shares nothing but the store gets a
  question that needs the fact and doesn't name it: *"Find me flights to
  Austin next month."* A pattern grades the answer.

Seven scenarios have a fact worth keeping: a home airport, a diet, a
package manager, a time zone, an editor, a daughter's name, and units.
An eighth says something true for a week (*"flying to SFO next
Tuesday"*), and keeping it counts as a failure.

Each scenario also runs a **baseline**: the same later question, from a
*different* identity. That shows what the model does with no memory, and
proves one person's facts never reach another person's prompt. Each run
also asks one question against a **store that is down**.

### Burying the fact

With a handful of memories, every store looks the same. The prompt layer
tops up with the most recent memories, and the fact is one of them.
`--distractors 30` stores thirty unrelated facts about the same person
*after* session 1 (*"Has a cat named Biscuit"*, *"Plays badminton on
Sunday mornings"*). Now the fact is the oldest of about thirty-one, the
top-up of twenty doesn't reach it, and only the **store's search** can
put it in the prompt. The `search` column shows where the fact ranked
in that search, and it costs no model call.

## What came back

All runs used `qwen3.8:latest` on Ollama, one run per setting, seven
graded scenarios per run. The two stores were the built-in `local`
(sqlite, word overlap) and Smritikosh (pgvector, `nomic-embed-text`
embeddings on the same Ollama), reached as an MCP server through the
verb map in note 102.

| | local | Smritikosh | local, buried | Smritikosh, buried |
|---|---|---|---|---|
| kept by session 1 | 6/7 | 7/7 | 6/7 | 6/7 |
| found by the store's search | — | 7/7 | **2/7** | **6/7** |
| in session 2's prompt | 6/7 | 7/7 | 2/7 | 6/7 |
| **answered from memory** | **6/7** | **7/7** | **2/7** | **4/7** |
| same question, no memory | 0/7 | 0/7 | 0/7 | 0/7 |
| `recall_memory` called | 0/7 | 0/7 | 0/7 | 0/7 |
| other identity saw nothing | 7/7 | 7/7 | 7/7 | 7/7 |
| kept "flying to SFO Tuesday" | 0/1 | 0/1 | 0/1 | 0/1 |
| store down: still answered | yes | yes | yes | yes |

(The first local run predates the `search` column. Its layer never
needed search, since the fact was always among the most recent. The
buried local run's `timezone` row is from a re-run after the pattern
fix described below, since that row's prompt had not been saved and
couldn't be regraded.)

Smritikosh, buried:

```
store=smritikosh model=qwen3.8:latest distractors=30

case        remember    kept        search      in prompt   answer      recall      baseline    isolated
airport     no          yes         #2 of 31    yes         fail        no          no          yes
diet        no          yes         #3 of 32    yes         PASS        no          no          yes
packages    no          yes         #1 of 32    yes         PASS        no          no          yes
timezone    yes         yes         #2 of 31    yes         PASS        no          no          yes
editor      no          yes         #3 of 32    yes         PASS        no          no          yes
daughter    yes         yes         #1 of 31    yes         fail        no          no          yes
units       yes         no          missed      no          fail        no          no          yes

kept 6/7 · found by search 6/7 · in prompt 6/7 · answered from memory 4/7 · recall_memory called 0/7 · baseline 0/7 · isolated 7/7 · kept what it should not 0/1
store down: answered, with notice: memory unavailable (down: store is down); continuing without it
  airport: empty answer (no text, no error)
```

## What it says

**The road works, and the prompt layer does the work.** With few
memories, a fact said once in passing changed a later answer six or
seven times out of seven. With no memory the same question passed zero
times. The model called `recall_memory` in **none** of 28 later
sessions. Note 100 bet that a model won't look up a fact it has no
reason to suspect exists, so the fact has to be in the prompt already.
Both halves of that bet held. A typical session-2 answer:

> And just to confirm: RDU on the way out, as before? Say so if that's
> changed or you're starting from another city.

That is note 100's framing working: memory used, said out loud, and open
to correction.

**Stores only differ once the fact is buried, and then they differ a
lot.** *"Find me flights to Austin"* shares no word with *"Lives near
RDU"*. Word overlap found 2 of 7 buried facts, and both were luck of
wording: *"my daughter"* matches *"has a daughter named Meera"*, and
*"for Python"* matches *"Python LSP"*. Embeddings found 6 of the 6 facts
Smritikosh kept, each ranked in the top three of about thirty-one. That
is the gap note 100 described as *"that is what other stores are for"*,
now measured instead of assumed.

**Being in the prompt isn't the same as being used.** When buried, two
of Smritikosh's misses had the fact in the prompt:

* `daughter`: *"Has a daughter named Meera"* was one of twenty lines,
  and the bedtime story never used the name. With one or two lines in
  the prompt this never happened. Twenty memories dilute the one that
  matters.
* `airport`: the model returned an empty answer, with no text and no
  error. That's a model hiccup, not a memory miss. The report names it
  so nobody blames the store.

So a better search still gets diluted by a wide prompt layer.
`PROMPT_LIMIT` is twenty because a person's standing facts are few. A
store that ranks well would do better with fewer, better-ranked lines.

**The look back is a die roll, on both kinds of mistake.** *"It's 31
degrees Celsius here today"* led to *"Uses Celsius for temperature"* in
one run of four. The other three kept *"Runs 10k distances"*, which is
also true, and arguably the fact the person actually stated. The
one-week trip was never kept, in four runs out of four.

**Isolation and failing open held every time.** The other identity
started with an empty prompt in 28 of 28 sessions. A dead store cost one
line and never the answer.

## Grading, and three bugs it had

A trial is only worth its grading, and this one needed three fixes:

* **Leading beats mentioning.** The first `packages` pattern passed a
  no-memory answer that said `pip install requests` and then offered
  `uv add` as an alternative. Now a `rival` pattern (`pip install`) must
  come *after* the remembered answer (`uv add`). With that, the
  no-memory baseline fell from 2/7 to 0/7.
* **A conversion isn't a relapse.** *"16–19 °C (60–67 °F)"* failed a
  pattern that forbade °F anywhere. Now °C must simply come first.
* **Word boundaries.** The time-zone fact pattern `IST` matched inside
  *"lists"* in a distractor, so a fact that wasn't in the prompt was
  reported as if it were. It's now `\bIST\b`.

Each row keeps what it saw (the kept statements, the prompt, the
search results, both answers), so `--rescore` grades a saved run again
without calling the model. All four runs above were rescored after
these fixes.

## Running it

```
uv run python examples/memory_recall_trial.py                 # local
uv run python examples/memory_recall_trial.py --distractors 30
uv run python examples/memory_recall_trial.py --store smritikosh \
    --mcp-config smritikosh.json --distractors 30 --out run.jsonl
uv run python examples/memory_recall_trial.py --rescore run.jsonl
uv run python examples/memory_recall_trial.py --repeat 5 --out qwen.jsonl
uv run python examples/memory_recall_trial.py --rescore qwen.jsonl gemma.jsonl
```

`--repeat` and comparing saved runs came later
([note 105](105-a-rate-not-a-picture.md)).

Each scenario runs under an identity of its own. On an MCP store, the
trial forgets everything it kept when it finishes, including the
scenarios that crashed. A store's own write limit can stop a burst of
distractors (Smritikosh allows 60 writes a minute per user by default).
The row records it and the run goes on.

`tests/test_memory_recall_trial.py` pins the grading and the plumbing
with a scripted model: rival ordering, a buried fact that word overlap
can't find, a false keep, and a dead store.

## What was deliberately not built

* ~~**More than one model, and more than one run.**~~ `--repeat N` and
  a side-by-side `--rescore` of saved runs, measured on two local models
  ([note 105](105-a-rate-not-a-picture.md)).
* ~~**A narrower prompt layer.** The dilution finding points at fewer,
  better-ranked lines when a store searches well. That's a change to
  `PROMPT_LIMIT` and to how the layer mixes search with recency, and it
  should be measured with this trial before and after, not guessed at.~~
  Measured, and turned down: eight lines lifted use of a fact in the
  layer from 91% to 97%, but cut facts the store ranked below fifth, and
  gemma4:12b on Smritikosh fell from 31 to 25 of 35
  ([note 109](109-fewer-lines-fewer-facts.md)).
* **Smritikosh's own context block.** Its `get_context` tool builds a
  prompt block from its graph and profile. The verb map deliberately
  uses only the four verbs, and the prompt layer is Yantra's job.
* **An `EvalCase` for this.** Package evals ([note 10](10-evals.md)) are
  one conversation each, with memory off. Two sessions with a look back
  in between is a different shape, so it lives beside them as a trial,
  like the name-reader trial ([note 89](89-names-nobody-listed.md)).
