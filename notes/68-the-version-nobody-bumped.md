# 68 — The version nobody bumped

[Note 62](62-the-reports-you-already-have.md) taught `--pool` to add a
folder of eval reports together, case by case, so thirty wide "3 of 3"
ranges become one narrow one. It is careful about one thing: only runs
that are samples of *the same agent* are added together. Nine passes out
of ten on one model and two out of ten on another are not eleven out of
twenty of anything.

"The same agent" meant the same package name and version. The trouble is
that a version is a string somebody types. Edit `prompt.md` on Monday,
forget to bump `0.1.0`, and the pool adds a week of the old prompt to a
week of the new one and reports a pass rate that belongs to neither.
Notes 62 and 66 both listed this as open. The only guard was `disagree`,
which fires when two runs' ranges miss each other entirely, so it only
catches an edit that changed the results enormously.

## A fingerprint of what the agent was

Every report now carries the package's **fingerprint**: a short hash of
the files the agent is built from, taken when the suite ran.

```json
"suite": "researcher 0.1.0",
"package": "54226bfdb601",
```

Change one character of the prompt, a tool, a skill or a sub-agent's
instructions, and the fingerprint changes, whatever the version says.

**WHAT IS HASHED: EVERYTHING THE AGENT IS BUILT FROM.** Every file under
the package directory, any `tools.dirs` or `skills.dirs` that point
outside it, the prompt as loaded (it can live outside too), and the
installed version of every named tool pack
([notes/53](53-a-tool-that-arrives-by-pip.md)). That includes files the
agent only *reads*, like the researcher's `sources/` folder. If the
material a case asks about changed, the runs before and after are not
samples of one rate either.

**WHAT IS NOT: THE GRADING SIDE AND THE MACHINE'S COPIES.** `evals/` is
left out, because editing one case must not split the history of every
other case. A changed claim is already named per case
([notes/62](62-the-reports-you-already-have.md)). Hidden files are left
out too. The researcher package keeps its session database in
`.yantra/`, which changes on every run, so hashing it would give every
run a fingerprint of its own. So are `__pycache__` folders and `.pyc`
files, which are the machine's copies of files already hashed.

Twelve hex characters, on purpose. A person reads it in a terminal and
compares two by eye. A collision between two edits of the same package
is not a risk worth a longer line.

## One version, two packages, two pools

When one suite label on one model turns out to hold more than one
fingerprint, `--pool` pools each package on its own and says why, once:

```
pooled 1 run(s) of researcher 0.1.0 on ollama/qwen3.8:latest (package 54226bfdb601)
  researcher 0.1.0 ran as 2 different packages under one version; each is pooled on its own -- bump the version when the agent changes
  ...
pooled 1 run(s) of researcher 0.1.0 on ollama/qwen3.8:latest (package a89ed1c578e1)
```

**SPLIT, NOT WARN.** A warning above a pooled number still leaves the
number wrong. Note 62's rule was "samples of the same thing or nothing",
and runs of two different prompts are not samples of the same thing. So
they are not added together. The line says what happened and what would
have prevented it.

`--against` says it too, above the case-by-case lines, because a case
that "broke" between two runs of one version may be the edit rather than
the model's luck:

```
same version, different package: 54226bfdb601 → a89ed1c578e1 -- the agent was edited without a version bump, so what moved below may be the edit
```

## A report from before is unknown, not different

Every report written before this has no fingerprint. Treating "none" as
a fingerprint of its own would split everybody's existing history on the
day they upgraded. Treating it as a match would claim something nobody
recorded. So:

* **No run has a fingerprint:** one pool per label, exactly as before.
* **One package is known:** the old reports join it, as they always
  would have, and the pool says how many it counted that way.
* **Several packages are known:** the old reports cannot be placed, so
  they pool together, apart from the rest, and are labelled
  `(package unknown)`.

`--against` follows the same rule. An old report on one side is not
evidence of an edit, so it prints nothing.

## Both roads

The fingerprint is a property of the package, not of the model, so it
is the same on Anthropic, OpenAI and Ollama. The receipt below is a
local run. Pooling was already keyed by model, so the same package on
two models is still two pools.

## What was deliberately not built

**No fingerprint of the grader.** An edited grader in `evals/graders.py`
changes pass rates as surely as an edited prompt. Including `evals/`
would fix that and split every case's history on every case edit. A
per-case fingerprint (the case's own definition plus the graders it
names) would be the precise answer, and it is more machinery than the
problem has earned so far.

**No fingerprint of the model's weights.** An Ollama tag like
`qwen3.8:latest` can point at new weights after an `ollama pull`. The tag
is what the report records. Asking the server for the model's digest is
possible, and it would be the same argument this note makes, one layer
down.

**No refusal.** A pool never refuses to run because the author forgot a
version bump. It splits and says so, and exits 0 like the rest of
`--reports`.

## What is not here yet

* **An edited grader still pools silently**, for the reason above.
* **A re-pulled model tag still pools silently**, for the reason above.

## Receipt

A copy of `examples/agents/researcher` on `qwen3.8:latest`: one run,
then one line added to `prompt.md` ("Before answering, restate the
question in one sentence.") with the version left at 0.1.0, then a
second run against the first:

```
$ yantra --agent researcher --eval --report runs/fp2.json --against runs/fp1.json
...
SUITE GREEN · 6/6 passed · 41076 tokens · 2 case(s) cost nothing

against researcher 0.1.0 on ollama/qwen3.8:latest, 2026-09-24T14:43:28Z (6/6 passed)
same version, different package: 54226bfdb601 → a89ed1c578e1 -- the agent was edited without a version bump, so what moved below may be the edit
  no case changed verdict or pass count
tokens: 38290 → 41076 (+2786)
```

Then pooled with a report written earlier the same day, before
fingerprints existed:

```
$ yantra --reports runs/a.json runs/fp1.json runs/fp2.json --pool

pooled 1 run(s) of researcher 0.1.0 on ollama/qwen3.8:latest (package 54226bfdb601)
2026-09-24T14:43:28Z
  researcher 0.1.0 ran as 2 different packages under one version; each is pooled on its own -- bump the version when the agent changes
  outlines-before-reading           1/1 over 1 run(s) · 0.21..1.00 · claims 1 · holds
  ...
pooled 1 run(s) of researcher 0.1.0 on ollama/qwen3.8:latest (package a89ed1c578e1)
  ...
pooled 1 run(s) of researcher 0.1.0 on ollama/qwen3.8:latest (package unknown)
2026-09-24T14:11:29Z
  these reports predate fingerprints, so which package they ran cannot be told; pooled apart from the ones that can
```

Before this, all three would have been one pool of three runs.

`1750 passed, 1 skipped` (was 1734).
