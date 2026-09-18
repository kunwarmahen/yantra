# 54 · The word for a road

[notes/45](45-the-road-with-no-key.md) taught startup to see the keyless
road: a `.env` with `OLLAMA_MODEL` in it now means "run locally" instead
of "no API key found". This note is about what that road is *called*,
and about a manifest that could not say it was following the machine.

Two small things. Both are about a person writing down what they meant.

## "local" is a word; "ollama" is a brand

Half the people reading this repo want to run a model on hardware they
own. Almost none of them start out wanting *Ollama* specifically — they
want the thing where nothing is billed and nothing leaves the machine,
and Ollama is the program that happens to serve it.

Which meant the first time many readers met the word was here:

```
error: no provider found: set ANTHROPIC_API_KEY (or OPENAI_API_KEY) in the
environment or .env -- or run a local model with: yantra --provider ollama
```

"Run a local model with `--provider ollama`" asks somebody to translate
their intention into a brand they may not have installed yet, in an
error message, which is the worst moment to be learning vocabulary.

So `local` is another word for it, everywhere a provider name is
accepted:

```bash
yantra --provider local "say hello in five words"
```
```bash
YANTRA_PROVIDER=local        # in .env
```
```toml
[model]
provider = "local"           # in agent.toml
```

**The alias is resolved at the edge, never carried inward.** Every name
arriving from a flag, a manifest or the environment goes through
`canonical_provider()` immediately, and what the harness holds after
that is always `"ollama"`. That rule is doing real work: a provider name
is a dictionary key in the price table
([notes/21](21-cost-accounting.md)), a column in an eval report
([notes/42](42-two-runs-of-the-same-suite.md)), and part of the identity
of a cached prompt. Two spellings reaching those would be two models as
far as any of them could tell — one road, two price lookups, two report
columns, and a comparison that says a model was replaced when nothing
moved.

Normalising is also not *guessing*. `canonical_provider("olama")`
returns `"olama"`, unchanged, and fails where names are checked. An
alias table maps one known word to another; it does not do spelling
correction, because a harness that quietly ran something adjacent to
what you typed is worse than one that says it does not know the name.

## A blank that can say what it means

A package's manifest has always been allowed to leave the provider out:

```toml
[model]
max_iterations = 20
# no provider, on purpose
```

…which means "whatever this machine prefers", and is usually right — a
package that names a provider cannot be run against anything else
without editing it, and `examples/agents/researcher` deliberately names
none so that both roads work with no diff.

The trouble is that it is invisible. A missing key reads as an
omission, and nobody reviewing the file can tell a decision from a gap.
Note 45 said this out loud and left it:

> A package that wants to follow the machine's preference has to leave
> `provider` unset, which is the right way round but reads as an
> omission rather than a choice.

Now it can be written:

```toml
[model]
provider = "auto"       # follow whatever this machine already prefers
```

`"auto"` parses to exactly what the blank parses to — `provider = None`,
resolved at build time by the same ladder every other flagless run uses.
**No new behaviour at all**, which is the whole design: this is a word
for something the format already did, not a new way for a manifest to
decide things. A key that behaved even slightly differently from the
blank would be a trap for the next person diffing two packages.

The manifest also checks the name now, against the file:

```
error: ./agent.toml: model.provider must be one of anthropic|local|ollama|
openai|responses or 'auto' (whatever the machine prefers), got 'gemini'
```

Previously an unknown provider in a manifest was carried all the way to
the point of use before failing, which is the slow version of the same
answer.

## What is not here yet

* **No alias for anything else.** `local` is the only entry in the
  table, because it is the only one where the word people reach for and
  the name of the thing differ. `claude` for `anthropic` is tempting and
  wrong: Claude is a model, Anthropic is the API, and a harness that
  blurred them would be teaching something false.
* **`auto` cannot be narrowed.** "Any local provider" and "any of these
  three" have no spelling; `auto` means the full ladder from note 45 and
  nothing else. A package that genuinely needs "cloud or nothing" still
  has to name one.
* **Nothing checks that a named tag was pulled.** Unchanged from
  [note 45](45-the-road-with-no-key.md): `OLLAMA_MODEL` naming a tag that
  was never pulled still fails one layer down, as a 404 from Ollama,
  because checking means a network call and rung 3's whole point is that
  it makes none.
