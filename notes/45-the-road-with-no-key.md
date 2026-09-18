# 45 · The road with no key, and how it says its name

Every page of this repo offers two roads. One rents a model from a
company and pays per token; the other runs a model on the machine in
front of you and pays nothing. The README says so, the tutorial says so,
and roughly half the people reading either one are on the second road.

The tutorial also said this, in its setup section:

> and then `uv run yantra` guesses the provider from whichever key it
> finds.

That sentence is true, and it is the bug. A key is what the cloud road
has. The local road's entire selling point is that there is no key —
nothing to sign up for, nothing to leak, nothing to bill. So a startup
that decides what to run by looking for keys can see one road and is
structurally blind to the other.

Here is what that blindness looked like from the outside. A `.env` with
Ollama configured and no key anywhere — the file a local-first reader
ends up with after following the tutorial — and then the command the
tutorial's next section tells them to type:

```
$ uv run yantra
error: no API key found: set ANTHROPIC_API_KEY (or OPENAI_API_KEY) in the
environment or .env -- or run a local model with: yantra --provider ollama
```

The error is polite, accurate, and names the fix. It is still the wrong
answer: the model tag was sitting three lines down in the same file the
harness had just read.

## Why "is a server listening?" is not the fix

The obvious repair is to ask the network. Ollama answers on
`localhost:11434`; curl it, and if something is there, use it.

The old code refused that, and the refusal was right:

> Ollama is never guessed: it needs no key, so "a local server might be
> running" is not evidence that a local model is what you meant.

Two things are wrong with probing. The first is that a running server is
a fact about the *machine*, not about the person's intent — plenty of
laptops have Ollama up for something else entirely, and a cloud user who
mistyped their key would silently get a local 8B model instead of an
error, which is the worst possible outcome because the run *succeeds*.
The second is that startup would now depend on a network call, so every
`yantra --help` pays a timeout when the daemon is wedged.

So the question was never "is Ollama available?" It was: **what can a
setup with no secrets in it use to say what it meant?**

## A line typed by hand is a declaration

The answer is that the keyless road already writes things down; nobody
was reading them. `OLLAMA_MODEL=qwen3.8:latest` in a `.env` is not an
accident and not an ambient fact about the machine — somebody chose a
tag and typed it. It is exactly as deliberate as an API key, and it
carries exactly as much intent. The default line ships **commented out**
in `.env.example`, so an uncommented one is a decision.

The resolution ladder, top rung first:

1. `YANTRA_PROVIDER` — say it outright, and nothing is guessed.
2. A key: `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`), then
   `OPENAI_API_KEY`, then `RESPONSES_API_KEY`.
3. An `OLLAMA_*` line — model, base URL, or the API key a proxy in front
   of Ollama might want.
4. Otherwise, the error, which now names both roads and the new rung.

A `--provider` flag beats all four, and a package's `agent.toml` beats
them too: this ladder only runs when nothing has been declared anywhere
else. Nothing here touches the network.

Same `.env` as the failure above, unchanged, on the new ladder:

```
$ uv run yantra --prompt "say OK and nothing else"
· qwen3.8:latest

· thinking
The user wants me to say "OK" and nothing else.
OK
── end_turn · 2750 in / 18 out · 1 iteration(s)
```

And with nothing declared at all, the error that is still correct:

```
$ uv run yantra
error: no provider found: set ANTHROPIC_API_KEY (or OPENAI_API_KEY) in the
environment or .env -- or run a local model with: yantra --provider ollama
(YANTRA_PROVIDER=ollama in .env makes that the default)
```

## Why the Ollama rung sits at the bottom

Rung 3 is below the keys, and that ordering is the one judgement call in
this note worth defending.

A machine with `ANTHROPIC_API_KEY` set *and* `OLLAMA_MODEL` set is a
machine whose owner does both things. Before this change, bare `yantra`
on that machine went to Anthropic; putting Ollama above the keys would
have silently moved every such session onto a local model — a behaviour
change for people who never asked for one, and one they would notice as
"why did my agent get worse?" rather than as a config problem. Below the
keys, the new rung can only fire where the old code raised an error, so
it converts failures into sessions and changes nothing else. That is the
whole reason `YANTRA_PROVIDER` exists: someone who runs both and prefers
local now has one line that says so, instead of a precedence rule they
have to remember.

**The tradeoff:** `.env` files accumulate. Somebody who once tried a
local model, uncommented `OLLAMA_MODEL`, and later let their cloud key
expire will now get a local run where they used to get an error telling
them the key is gone. The run will be slower and probably worse, and the
error that would have explained it never arrives. That is a real cost,
paid by a rare setup, to remove a wall that every local-first reader
walked into on their first command. A stale uncommented line is a
cheaper mistake than a road nobody can start on.

## What was deliberately not built

**A probe of `localhost:11434`.** Covered above: a listening daemon is
not a statement of intent, and it would make startup wait on a socket.

**`YANTRA_MODEL`.** A companion to `YANTRA_PROVIDER` looks symmetric and
is not: `{PREFIX}_MODEL` already exists per provider and is read *after*
the provider is known, so a second, provider-less model variable would
have to either lose an argument with it or shadow it. The provider was
the only unanswerable question.

**Guessing a fourth dialect from its base URL.** `RESPONSES_BASE_URL`
pointed at `localhost:11434/v1` is a local setup too ([note
19](19-responses-api.md)), and it could have been a fifth rung. It was
left out because that variable's whole point is that it aims at three
different places and the URL is the only thing that distinguishes them —
a rung there would guess wrong for the two remote cases. `RESPONSES_API_KEY`
is the honest signal, and against a local Ollama that key is a
placeholder somebody still has to type.

**Fixing `examples/run_evals.py`.** That demo script keeps its own
two-rung ladder and its own `--ollama` flag, on purpose: it is a hundred
lines of "here is what an eval harness looks like", and it teaches better
with the flag visible than with a resolution ladder imported from the
library it is demonstrating.

**A `/provider` change that rewrites `.env`.** The REPL's `/provider`
command switches dialects for the session, and it does not persist. A
harness that edits the user's config file is a harness you cannot trust
with a config file.

## What is not here yet

* **The error still cannot tell you about the tag it found.** If
  `OLLAMA_MODEL` names a tag that was never pulled, the ladder is happy
  and the failure arrives one layer down, from Ollama, as a 404 on the
  model name. `start.sh`'s local preset warns about this before starting;
  the library does not, because checking means a network call and the
  whole point of rung 3 is that it makes none.
* ~~**`YANTRA_PROVIDER` is not readable from a package.**~~ Closed in
  [note 54](54-the-word-for-a-road.md), and the shape is the one this
  bullet argued for: leaving `provider` unset is still the mechanism, and
  `provider = "auto"` is a word for it, so a package that follows the
  machine says so instead of leaving a gap a reviewer has to interpret.
* ~~**No way to say "local, and I don't care which tag."**~~ Closed in
  [note 54](54-the-word-for-a-road.md): `local` is now another word for
  `ollama` wherever a provider name is accepted, so the word people
  actually reach for no longer has to be spelled as a base URL — or as a
  brand they may be meeting for the first time in an error message.
