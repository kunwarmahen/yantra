# 56 · Not like that, like this

A permission gate has had three answers since
[notes/20](20-approve-with-edits.md): **yes**, **no**, and **yes, but
with these arguments instead**. [notes/37](37-a-gate-that-can-wait.md)
added a sentence to the no — `refuse(request, "...")`, and the model
reads that instead of "Permission denied by user." — and then wrote down
what was still missing:

> **No way to ask the person a question other than yes/no.** The gate
> can now speak when it refuses, but it still cannot say *"not like
> that, like this"* except by using approve-with-edits, which needs a UI
> that can construct arguments.

That last clause is the whole problem. Approve-with-edits is a good
feature aimed at a narrow case: you can see the exact wrong character in
the command and you fix it. Here is what it asks of you when the
correction is not one character:

```json
{
  "command": "mkdir build-output && ls -d build-output"
}
```

Edit that dict, by hand, into the command you would have preferred.
Fine for a path. Hopeless for *"not in the repo root — try /tmp/scratch
instead"*, which is a sentence a person produces in two seconds and a
JSON edit they would rather not attempt at 11pm.

Meanwhile the library could take that sentence all along. `refuse()` has
accepted one since note 37. **Neither frontend could produce one**, so
the capability existed and no human could reach it.

## One more letter

```
╭─ approve bash()? ──────────────────────────────────────────╮
│ $ mkdir build-output && ls -d build-output                  │
╰─────────────────────────────────────────────────────────────╯
run it? [y/n/e/s] (n): s
no, because (or why not): not in the repo root -- try /tmp/scratch instead
```

And the model reads exactly that:

```
╭─ bash()  [refused: user] ──────────────────────────────────╮
│ bash was denied. The person said: not in the repo root --   │
│ try /tmp/scratch instead                                    │
╰─────────────────────────────────────────────────────────────╯

· thinking
The person is saying: "It's not at the repository's root -- instead try
using /tmp/scratch."
→ bash()

╭─ approve bash()? ──────────────────────────────────────────╮
│ $ mkdir -p /tmp/scratch/build-output                        │
╰─────────────────────────────────────────────────────────────╯
```

A real run against `qwen3.8:27b` on a local server, unedited. The
correction took eleven words and no JSON, and the model — a 27B model
running on one desktop GPU, not a frontier one — rebuilt the call
correctly on the first try.

The browser modal gets the same thing as a text box beside the deny
button, with one detail worth naming: **Enter in that box denies with
the sentence** rather than approving. Enter approves everywhere else in
that modal, and a person who has just typed "no, use staging" and
pressed Enter did not mean yes.

## Attributed, always

```
bash was denied. The person said: not in the repo root -- try /tmp/scratch instead
```

Not `bash was denied: not in the repo root...`. The words are marked as
a **person's**, because the model has to be able to tell an instruction
from a human apart from the harness's own voice, and the two deserve
different treatment: policy is a wall to route around, and a person's
correction is an instruction to follow. Blurring them teaches a model to
argue with the harness and to ignore its owner, or the reverse, and
neither is recoverable from inside one turn.

An empty sentence is a plain no. `""` reaching the model would be a
refusal that says nothing at all — strictly worse than the default
denial, which at least says a person was asked.

## Which half of the bullet this closes

The bullet held two things, and this note closes one of them.

**"Not like that, like this" — closed.** A person can refuse in their
own words, from either frontend, and the words reach the model.

**"Ask the person a question other than yes/no" — still open, and
probably belongs elsewhere.** A gate that wanted to ask *"which
database — staging or prod?"* would need to pose a question and receive
a choice, which is precisely what `ask_user` already does
([notes/24](24-todo-lists.md) has the channel; the browser has the
modal). The difference is who initiates: `ask_user` is the model asking,
and a question from the *gate* would be the harness asking on the tool
call's behalf. Building that means a second question type on the same
channel, an answer shape that is not a bool, and a `PermissionFn` whose
return type stops being `bool | Awaitable[bool]` — a large change to the
smallest interface in the harness, for something a refusal in English
now covers at conversational cost.

The cheap version won, which is usually the right outcome when the
expensive one needs a new type.

## What is not here yet

* **Nothing carries the sentence anywhere but the model.** It is not
  stored, not counted, and a session that refused the same thing four
  times in four different words has no record of that. A host wanting
  one reads `request.reason` in its own gate — which is where a service
  would keep it anyway.
* **The terminal asks for the sentence on one line.** No editor, no
  multi-line, no history. Long corrections want `e` and a file, and at
  that point the JSON edit may genuinely be the better tool.
* **The model is not told this answer exists.** Nothing in the system
  prompt says "a refusal may carry the person's own instruction", and it
  does not seem to need to — the sentence arrives as an error result and
  models act on it. If that turns out to be model-dependent, the fix is
  a line in the prompt rather than a change here.
* **A gate still returns a bool.** Everything above is a refusal
  carrying prose. The interface has not grown a third state, and the
  moment it does, every host's gate has to learn it.
