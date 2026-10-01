# 107 — A fact that says what it is

When a conversation ends, Yantra looks back over it and proposes facts
about you to remember ([note 101](101-looking-back.md)). How a fact is
**worded** decides whether it can be found later.

[Note 105](105-a-rate-not-a-picture.md) caught this on a store that
searches by meaning (Smritikosh). Asked *"how do I set up format-on-save
for Python?"*, the search ranked *"Works with Python."* first, and the
fact that mattered, *"Uses Neovim."*, didn't make the top twenty. It's
true, but it doesn't say what Neovim *is*, so nothing about editors
points to it. When qwen kept the same fact as *"Uses Neovim as their
text editor"*, it was found every time.

So this is a change to what the look back asks for, not to any store.

## The rule

Both ways a fact gets kept now ask for its meaning. That's the look
back's prompt and the `remember` tool's description:

```
Word each one so someone asking about it later finds it: say what the
thing IS. "Uses Fish as their command-line shell", not "Uses Fish";
"Their dog is named Biscuit", not "Biscuit".
```

**THE EXAMPLES ARE NOT THE TRIAL'S ANSWERS.** The recall trial's
scenarios include Neovim and Celsius. A prompt that said *"Uses Neovim
as their text editor, not Uses Neovim"* would teach the model the answer
to one test, and the trial would measure copying, not the rule. So the
examples are a shell and a dog, and a test checks that no trial answer
appears in either prompt. (RDU is the one exception: it was the look
back's example before the trial existed.)

## The first version kept nothing

The first version put the rule between *"Reply with one line per fact"*
and the example lines. Both local models then wrote good sentences and
dropped the `fact:` at the start of each line:

```
Uses Neovim as their text editor.
```

The look back only keeps lines that start with their kind (`fact:`,
`preference:`, `correction:`). That's deliberate: it's how a preamble,
or a model thinking out loud, comes to nothing. So every candidate was
thrown away. On Smritikosh, gemma4:12b kept **0 of 35** facts, and qwen3.8
proposed 2 in 22 before the run was stopped. No error was reported,
because a reply with no candidate lines is a normal *"nothing worth
remembering"*.

The unit tests couldn't see it, because their model replies are
scripted. The trial saw it in the first table it printed. The fix puts
the rule in its own paragraph and states the line format directly
before the examples: *"Start every line with its kind -- fact:,
preference: or correction: -- then one short self-contained sentence"*.
A test now pins that order, with the reason in a comment.

That's the case for running the trial after any prompt change. A prompt
is code whose tests are only as good as the model's actual replies.

## Receipt

`examples/memory_recall_trial.py`, Smritikosh (pgvector,
`nomic-embed-text`), each fact buried under 30 others kept after it, 5
repeats, on the same Ollama:

```
uv run python examples/memory_recall_trial.py --model gemma4:12b \
  --store smritikosh --mcp-config smritikosh.json --distractors 30 \
  --repeat 5 --out gemma.jsonl
```

| buried, on Smritikosh | gemma4:12b before | gemma4:12b after | qwen3.8 before | qwen3.8 after |
|---|---|---|---|---|
| kept by session 1 | 20/35 (41-72%) | 28/35 (64-90%) | 30/35 (71-94%) | 30/35 (71-94%) |
| in session 2's prompt | 16/35 (30-62%) | 28/35 (64-90%) | 30/35 (71-94%) | 30/35 (71-94%) |
| **answered from memory** | **16/35 (30-62%)** | **28/35 (64-90%)** | **27/35 (61-88%)** | **28/35 (64-90%)** |
| same question, no memory | 0/35 | 0/35 | 0/35 | 1/35 |
| `recall_memory` called | 0/35 | 0/35 | 0/35 | 0/35 |
| kept "flying to SFO Tuesday" | 0/5 | 0/5 | 0/5 | 0/5 |

*Before* is note 105's run with the same code minus this change.

**gemma's ranges no longer overlap.** It answered from a buried fact 28
times in 35, against 16. Two things moved:

* **Everything it kept was found.** Before, 4 of gemma's 20 kept facts
  never reached the prompt, and the editor fact (kept as the bare
  *"Uses Neovim."*) was among them. After, 28 of 28 were found. Every
  editor fact gemma kept now reads *"Uses Neovim as their text editor"*
  (or *"code editor"*), 4 runs in 5.
* **It kept more.** 20 went to 28. Keeping wasn't the target, and the
  noise in a single five-run cell is large (note 105 saw gemma keep
  `packages` 0 times in 5 in one setting and 3 in the next). But the
  rule also seems to help it word facts like `uv` as worth keeping:
  *"Uses uv as their package manager"* 4 times in 5.

**qwen didn't need it.** It already worded the editor fact with its
meaning, and it stays at 28 within the noise. That's the expected result
for a model that was already doing what the rule asks.

**What it doesn't fix:** `units`. gemma kept *"Uses Celsius for
temperature"* once in 5, and qwen never did. qwen keeps *"Runs 10k
distances"* instead, which is true but not the thing that mattered. That's
a choice about **what** to keep, not how to word it. (Fixed since: see
*A habit said in passing* below.)

**Runs are saved as they go.** The first attempt at these numbers died
40 minutes in when the store's server stopped, and the trial lost every
row: it wrote `--out` only at the end. It now writes each row the moment
it's graded, and a test kills a run halfway and checks that the rows
before the crash are on disk.

`2459 passed, 1 skipped` (was 2455). The new tests are in
`tests/test_reflection.py` and `tests/test_memory_recall_trial.py`.

## A habit said in passing

*"It's 31 degrees Celsius here today. Too hot for a 10k run at noon?"*
shows how the person measures temperature. Both models read it as part
of the task: the heat and the run were what was being asked about, and
the rule says *"not about the task"*. So they kept the run.

The look back's prompt now says that how a person says things counts as
something they plainly showed:

```
Plainly showed includes HOW they say things: the currency they quote
prices in, the units they measure in, the language they write in. Said
in passing inside a task, a habit like that is still about the person.
```

The `remember` tool's description says the same in a few words. The
examples are categories, not answers: neither *Celsius* nor *metric*
appears, and the test that keeps the trial's answers out of both
prompts still passes.

`--store local --distractors 30 --repeat 5`, same Ollama:

| | gemma4:12b before | gemma4:12b after | qwen3.8 before | qwen3.8 after |
|---|---|---|---|---|
| `units` kept | 0/5 | **5/5** | 0/5 | **5/5** |
| `units` answered from memory | 0/5 | **5/5** | 0/5 | **5/5** |
| all seven kept | 27/35 (61-88%) | 32/35 (78-97%) | 30/35 (71-94%) | 35/35 (90-100%) |
| kept "flying to SFO Tuesday" | 0/5 | 0/5 | 0/5 | 0/5 |

*Before* is note 105's buried `local` run, which also came before the
wording rule, so the *all seven* row may move for either reason. The
`units` rows don't: with the wording rule alone (the Smritikosh run
above), `units` was kept 1 time in 5 and 0 times in 5.

What was kept: *"Uses Celsius for temperature."* (gemma, 5 runs in 5),
*"Measures temperature in Celsius."* and *"Uses degrees Celsius (°C)
for temperature."* (qwen). Every one says what the unit is for. The run
is still kept alongside, about half the time. The trip is still never
kept, so the new paragraph didn't loosen the "still true in months"
rule.

Buried on `local`, qwen answered from memory 11 of 35 times, against 13
in note 105, and gemma 9 against 4. The noise in either is large. One
row moved the wrong way: qwen kept `packages` 5 times in 5 as *"Uses uv
as their Python package manager"*, but word-overlap search found it 0
times in 5, where note 105 found it 3. The later question, *"How do I
add requests as a dependency to my project?"*, shares no word with that
wording. Note 105's rows weren't saved, so the old wording can't be
checked. This is the `local` store's known limit (below), not a fact
that was lost.

## What was deliberately not built

* **Accepting lines without a kind.** The parser could take bare
  sentences too. But the kind is what tells a candidate apart from a
  model's preamble or its reasoning spilling into the reply. Losing that
  to rescue one prompt mistake would be a bad trade.
* **Rewording facts already kept.** A store may hold *"Uses Neovim."*
  from before. `/memory forget` and `/memory add` fix one by hand.
  Rewriting someone's memories automatically is not something to do
  without asking.
* **The `local` store.** Word-overlap search gains little from wording.
  *"format-on-save for Python"* shares no word with *"Uses Neovim as
  their text editor"* either. The prompt's recent top-up is what carries
  a fact there ([note 103](103-said-once-found-later.md)).
