# 105 — A rate, not a picture

[Note 103](103-said-once-found-later.md) measured whether a fact said
once, in passing, comes back in a later conversation. It ran each
setting once, on one model. *6 of 7* reads like a result, but it's one
roll of a die. The look back keeps a different fact on a different run,
and a model has off turns. Run it again and you might get 4 of 7 or 7 of
7. From one run you can't tell which of those is the real rate.

So a change based on that trial (a narrower prompt layer, a different
store) couldn't be judged either. A difference smaller than the noise
looks exactly like an improvement.

This note adds repeats and a second model, and reports what the rates
say.

## Repeats, with an honest range

```
uv run python examples/memory_recall_trial.py --repeat 5 --out qwen.jsonl
uv run python examples/memory_recall_trial.py --repeat 5 --model gemma4:12b --out gemma.jsonl
uv run python examples/memory_recall_trial.py --rescore qwen.jsonl gemma.jsonl
```

**EVERY REPEAT IS A DIFFERENT PERSON.** Each repeat of a scenario runs
under its own identity (`trial-<run>-airport-r2`). If repeats shared an
identity, repeat 2 could pass by finding repeat 1's fact in the store,
and the rate would count the same memory twice.

**A COUNT COMES WITH ITS RANGE.** With repeats, the per-case table shows
counts (`4/5`) and the summary line gives a 95% interval for each:
`answered from memory 29/35 (67-92%)`. It's a Wilson interval, which
behaves at the small numbers a trial has. Even *5 of 5* only means
"somewhere above 57%". Two settings whose ranges overlap a lot haven't
been shown to differ.

**A SAVED ROW SAYS WHAT IT WAS RUN WITH.** Every row records its
provider, model, store and distractor count. `--rescore` takes several
files, reports each setting, and then puts them side by side, as in the
table below. Rows saved before this took their setting from the command
line.

## What came back

Two local models on Ollama, `qwen3.8:latest` and `gemma4:12b`, on the
built-in `local` store (sqlite, word overlap). Each setting ran the
eight scenarios five times, so 35 graded later-sessions per column plus
five chances to keep the one-week trip. "Buried" is `--distractors 30`:
thirty unrelated facts kept after session 1, so only the store's search
can bring the fact back ([note 103](103-said-once-found-later.md#burying-the-fact)).

| | qwen3.8, local | gemma4:12b, local | qwen3.8, buried | gemma4:12b, buried |
|---|---|---|---|---|
| kept by session 1 | 31/35 (74-95%) | 25/35 (55-84%) | 30/35 (71-94%) | 27/35 (61-88%) |
| in session 2's prompt | 31/35 (74-95%) | 25/35 (55-84%) | 8/35 (12-39%) | 5/35 (6-29%) |
| **answered from memory** | **29/35 (67-92%)** | **22/35 (46-77%)** | **13/35 (23-54%)** | **4/35 (5-26%)** |
| same question, no memory | 2/35 (2-19%) | 0/35 (0-10%) | 0/35 (0-10%) | 0/35 (0-10%) |
| `recall_memory` called | 0/35 (0-10%) | 0/35 (0-10%) | **8/35 (12-39%)** | 1/35 (1-15%) |
| other identity saw nothing | 35/35 | 35/35 | 35/35 | 35/35 |
| kept "flying to SFO Tuesday" | 0/5 | 0/5 | 0/5 | 0/5 |
| store down: still answered | yes | yes | yes | yes |

Each column took about 38 minutes on one RTX 3090.

qwen3.8, buried, per scenario:

```
store=local model=qwen3.8:latest distractors=30 repeat=5

case        remember    kept        search      in prompt   answer      recall      baseline    isolated
airport     1/5         5/5         0/5         0/5         2/5         3/5         0/5         5/5
diet        4/5         5/5         0/5         0/5         0/5         0/5         0/5         5/5
packages    1/5         5/5         3/5         3/5         4/5         2/5         0/5         5/5
timezone    4/5         5/5         0/5         0/5         0/5         2/5         0/5         5/5
editor      0/5         5/5         0/5         0/5         3/5         1/5         0/5         5/5
daughter    4/5         5/5         5/5         5/5         4/5         0/5         0/5         5/5
units       0/5         0/5         0/5         0/5         0/5         0/5         0/5         5/5
```

## What it says

**Note 103's picture holds up as a rate.** Its *6 of 7* on qwen,
unburied, becomes 29 of 35 (67–92%). Its *0 of 7* without memory becomes
2 of 35, and both of those are the grader being generous rather than
the model knowing. One no-memory answer offered *"e.g., RDU"* as an
example of a departure city. The other listed Neovim first in a menu of
editors.

**The second model is worse at keeping, not at using.** gemma answered
from memory 22 of 35 times against qwen's 29. But once the fact was in
the prompt, the two were about the same: qwen used it 29 of 31 times,
gemma 22 of 25. The gap is upstream, in the look back. gemma kept the
fact 25 of 35 times: `packages` and `daughter` 3 of 5 each, and `units`
never. When gemma did have the airport in its prompt, it still asked
*"where are you flying from?"* 3 times out of 5.

So on a local model, the look back is where memory is won or lost.
Using a fact in the prompt is the easy part.

**`units` is the look back's blind spot on both models.** *"It's 31
degrees Celsius here today. Too hot for a 10k at noon?"* led to the
Celsius fact 1 time in 10 on qwen and 0 in 10 on gemma. Both keep
*"runs 10k distances"* instead, which is true and is closer to what the
person was talking about. Note 103 saw this once in four runs. Now it's
a rate.

**Buried, qwen goes looking, sometimes.** Note 103 found `recall_memory`
called in 0 of 28 later sessions and concluded that the fact has to be
in the prompt already. That holds when nothing is buried: 0 of 70
across both models. Buried, qwen called it 8 times in 35 and passed 5 of
those. For example, `airport` passed twice with the fact nowhere in the
prompt, after the model searched and found *"Lives near RDU"* itself.
gemma searched once in 35.

A prompt layer of twenty facts about the person, none of them the one
needed, seems to tell qwen that there's a memory worth searching. That
matters for the next change. A narrower prompt layer might also make the
model search less. That can only be measured, not argued.

**A neighbouring memory can do some of the work.** Two of qwen's buried
`editor` passes had no Neovim in the prompt and no search. They had
*"Uses a Python LSP"*, which word overlap *can* find, and the model
reasoned from it: *"since you're LSP-based, likely Neovim"*. The grader
counts that as a pass, and it is one, from a different memory than the
one planted.

**Isolation and the one-week trip held every time.** Another identity
started with an empty prompt 140 times in 140 (97–100%). The trip was
kept 0 times in 20 (0–16%).

## And on a store that searches by meaning

The same four settings on Smritikosh (pgvector, `nomic-embed-text` on the
same Ollama), reached as an MCP server through the verb map in
[note 102](102-kept-somewhere-else.md). These ran after the look back
fix below.

| | qwen3.8 | gemma4:12b | qwen3.8, buried | gemma4:12b, buried |
|---|---|---|---|---|
| kept by session 1 | 30/35 (71-94%) | 28/35 (64-90%) | 30/35 (71-94%) | 20/35 (41-72%) |
| in session 2's prompt | 30/35 (71-94%) | 28/35 (64-90%) | 30/35 (71-94%) | 16/35 (30-62%) |
| **answered from memory** | **28/35 (64-90%)** | **25/35 (55-84%)** | **27/35 (61-88%)** | **16/35 (30-62%)** |
| same question, no memory | 2/35 | 0/35 | 0/35 | 0/35 |
| `recall_memory` called | 0/35 | 0/35 | 0/35 | 0/35 |
| other identity saw nothing | 35/35 | 35/35 | 35/35 | 35/35 |
| kept "flying to SFO Tuesday" | 0/5 | 0/5 | 0/5 | 0/5 |

**Buried, the store is the whole difference.** On `local`, qwen answered
from a buried fact 13 times in 35. On Smritikosh it answered 27 times in
35, and the ranges don't overlap. Smritikosh's search put every fact qwen
kept into the prompt, 30 of 30, from under thirty others. gemma went
from 4 of 35 to 16. Unburied, the two stores are the same within the
noise (qwen 29 and 28, gemma 22 and 25), as note 103 predicted: the
recent top-up carries a fresh fact on any store.

**When the fact is in the prompt, nobody searches.** `recall_memory` was
called 0 times in 140 on Smritikosh. On `local`, buried, qwen searched 8
times in 35, and only then. The model reaches for the tool when the
prompt leaves it guessing, which is exactly when a store that ranks well
has already done the work.

**Meaning search needs the fact worded with its meaning.** gemma kept
the editor fact 4 times as the bare *"Uses Neovim."*, and the search
never found it: for *"format-on-save for Python"* it ranked *"Works with
Python."* first, and *"Uses Neovim."* didn't make the top twenty. qwen
kept *"Uses Neovim as their text editor"* and was found every time. How
the look back words a fact decides whether a store can find it later.
That is a prompt change to the look back, not a store change.

**Twenty lines still dilute.** qwen had *"Is vegetarian"* in its buried
prompt 5 times in 5 and used it twice. Unburied, with the same fact among
fewer lines, it used it 4 times in 5. The narrower prompt layer is aimed
at exactly this.

**The look back doesn't depend on the store.** It runs before anything
is buried or searched, so its misses are the same everywhere. gemma kept
`packages` 0 times in 5 in one setting and 3 in 5 in the next, which is
the noise in a single five-run cell.

## A leak the repeats found

Across 80 qwen sessions, the store ended up with a fact about the
*machine* 4 times: *"Their Linux username is mahen (home directory
/home/mahen)"*. The person never said it. The environment layer of the
system prompt did ([note 29](29-environment-awareness.md)). There were
four ways in:

1. **The model's reasoning.** The look back got each reasoning block's
   first 200 characters, and a qwen reasoning block read the system
   prompt. With no tool calls and nothing in the reply, the username
   could only have come from there.
2. **A tool call's arguments.** The model called `bash` with
   `cd /home/mahen/...`, and the look back read the command.
3. **The assistant's reply**, which quoted the path back to the person.
4. **The model's own `remember` call** in session 1. In a real session
   that asks first. The trial answers yes to everything.

The look back's rule is *"only what the person said or plainly showed,
not what the assistant suggested, guessed or looked up"*. The first two
break that rule by construction, so they are gone. The look back now
reads no reasoning, and sees tool calls by name only
([note 101](101-looking-back.md#what-was-said-not-what-was-thought)).
The reply stays, because *"Neovim"* only means something next to *"which
editor?"*. For replies, the prompt's rule is the only guard, and the
person's yes at the end of the conversation is the last one. gemma kept
a machine fact 0 times in 80.

After the reasoning fix, `packages` and `diet` ran three more times each
on qwen, buried. The look back proposed no machine fact in any of the
six. One path was still kept, *"Works on the project 'yantra' at
/home/mahen/..."*, but it came from the model's own `remember` call in
session 1 (the fourth route), and the look back proposed nothing that
conversation.

The 160 Smritikosh conversations ran with both fixes in place. The look
back proposed a machine fact in none of them, against 3 in 80 qwen
conversations before. One was still kept, *"Mahen is vegetarian"*, and
again it came from the model's own `remember` call, which named the
person after the machine's username. In a real session that call asks
first, and the person would see their username being used as their
name.

`tests/test_reflection.py` pins both: a username in reasoning or in a
tool call's arguments never reaches the look back, and the history
itself is untouched. `tests/test_memory_recall_trial.py` pins the
repeats: *5 of 5* is reported with its range, each repeat is a different
person, and a pile of saved runs is graded per setting.

## What was deliberately not built

* **A cloud model.** Both models here are local, so the numbers are
  free to reproduce. The trial takes any `--provider`/`--model`. On a
  frontier cloud model it costs four model calls per scenario per
  repeat.
* **A pass/fail threshold.** The trial reports; it doesn't gate. What
  counts as good enough for memory is a judgement about a product, not
  a number the trial can know.
* **Rerunning the `local` columns after the leak fix.** The fix changes
  what the look back reads, not how it chooses among real facts. The
  `local` rates were measured before it and the Smritikosh ones after,
  and the look back's keep rates match across the two within the noise.
* **Rewording what the look back keeps.** A fact kept with its meaning
  (*"Uses Neovim as their text editor"*) is found by a store that
  searches by meaning; a bare one isn't. That is a change to the look
  back's prompt, and it should be measured with this trial, not guessed
  at.
