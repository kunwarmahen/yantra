# 72 — What changed under a name

[Note 68](68-the-version-nobody-bumped.md) stopped `--pool` trusting a
version string. Every report carries a fingerprint of the package, so an
agent edited without a version bump is pooled apart from the one before.
It left two ways a pass rate can change while every name stays the same:

* **An edited case.** `evals/` is left out of the package fingerprint on
  purpose, so that editing one case does not split the history of every
  other one. But a case whose grader was loosened still pooled its
  strict runs together with its lenient ones.
* **A re-pulled model.** `ollama pull qwen3.8:latest` can put new weights
  behind the same tag. The report recorded the tag, so the old model and
  the new one pooled as one.

## A fingerprint per case

Each case now carries its own fingerprint: its `[[case]]` table as
written, plus the grader module when it names one
(`check = "graders:..."`).

**`description` IS LEFT OUT.** It is prose for the person reading the
file. Fixing a typo in it grades nothing differently.

**THE WHOLE GRADER MODULE, NOT THE ONE FUNCTION.** A grader leans on the
file around it, like a list of file names or a helper. Hashing only the
function's own source would miss the edit that mattered.

**IT SPLITS THAT CASE AND NO OTHER.** When one case holds two
definitions across the reports, `--pool` gives it one row per
definition, oldest first, and says why once:

```
  outlines-a-one-word-lookup was edited between these runs (2 definitions); each is pooled on its own
  outlines-a-one-word-lookup      0/1 over 1 run(s) · 0.00..0.79 · claims 1 · below (definition d0cb072bbf49)
  outlines-a-one-word-lookup      1/1 over 1 run(s) · 0.21..1.00 · claims 1 · holds (definition ae5031b3aca8)
```

`--against` marks it on the case's own line, because a case that
"fixed" may have been made easier rather than passed.

## The weights behind a tag

On Ollama, a suite run asks the local server (`/api/tags`) for the
digest of the weights behind the model tag, and the report records it:

```json
"model": "qwen3.8:latest",
"weights": "22130167c4c2",
```

One tag holding two digests across the reports pools as two, the same
way two package fingerprints do. `--against` says `same tag, different
weights`.

**UNKNOWN, NEVER A FAILED RUN.** A cloud provider does not report its
weights, so there it is unknown. If the local server is down, or does
not list the tag, it is unknown too. A report is not worth failing over
this.

## One rule for all of them

Every fingerprint in a pool now follows the same rule, and it is one
function: **unknown is not different.** If no value is known, or only
one, everything is one group, and an older report joins the only thing
it could have been. If several are known, each is its own group, and the
reports that cannot be placed pool together, last. Groups come oldest
first.

## Both roads

The case fingerprint is the same on every provider. The weights are an
Ollama fact: on a local model they are recorded, and on a cloud model
they are unknown and change nothing.

## What was deliberately not built

**No weights from cloud providers.** They do not say, and a model name
like `claude-sonnet-5` is the vendor's promise that it means one thing.

**No split on a `description` edit**, as above.

## What is not here yet

* **A model file changed on disk without a new digest.** Not a thing
  Ollama does. Noted only so nobody assumes it is covered.

## Receipt

Note 65's strict scratch case, run once on `qwen3.8:latest`, then
loosened (`required_tools = ["outline"]` became `["grep"]`) and run again
against the first:

```
$ yantra --agent researcher --eval --case outlines-a-one-word-lookup --report runs/d2.json --against runs/d1.json

against researcher 0.1.0 on ollama/qwen3.8:latest, 2026-09-24T15:48:56Z (0/1 passed)
  fixed  outlines-a-one-word-lookup  0/1 → 1/1  (intervals overlap: not evidence of a change)  (the case was edited between these runs)
tokens: 7986 → 5263 (-2723)
```

It "fixed" because the test got easier, and the line says so. The
pooled view is the block above. The report recorded
`"weights": "22130167c4c2"`, the digest Ollama lists for
`qwen3.8:latest`.

The tag split itself is pinned by the tests, not shown live here.
Re-pulling a tag to new weights is a network download that may not
change anything, and a receipt that edited a digest by hand would not be
a receipt.

`1792 passed, 1 skipped` (was 1772).
