# 75 — What answered

[Note 72](72-what-changed-under-a-name.md) made a report record the
weights behind a local model tag, so that `ollama pull` putting new
weights behind `qwen3.8:latest` could not pool the old model with the
new one. It left hosted models out: a cloud provider does not publish a
digest of its weights, so there was nothing to ask for.

But a hosted model can change under the same name too. An alias like
`claude-sonnet-4-5` moves to a new dated snapshot when the vendor
releases one. OpenAI's models change builds behind the same name.
Nothing asked for a digest, but the answer to every call already says
something close to one, and Yantra was ignoring it.

## What the response already says

**THE SNAPSHOT THAT SERVED THE CALL.** A request names an alias; the
response names what answered. The recorded Anthropic response in this
repo's test fixtures asked for a model and was answered by
`claude-sonnet-4-5-20250929`. That dated name is what changes when an
alias moves.

**THE BUILD FINGERPRINT.** OpenAI's API returns `system_fingerprint` with
every response: an identifier for the backend configuration that served
it. It changes when the serving build does, even under an unchanged
model name. The OpenAI adapters now keep it, from both the plain and the
streamed response (`ModelResponse.fingerprint`).

An agent keeps every identity that answered it (`agent.served`), written
as `snapshot` or `snapshot/fingerprint`. A suite on a hosted provider
records them in the report's `weights` field, the same field Ollama's
digest goes in. Several identities in one suite (an alias that moved
mid-run) are all kept, joined, so the change shows.

Everything note 72 does with that field now works for hosted models:
one name answered by two snapshots pools as two, and `--against` says
(the message's shape, with an illustrative pair of snapshots, pinned by
the tests rather than run against a vendor):

```
same model name, different snapshot answered: claude-sonnet-4-5-20250929 → claude-sonnet-4-5-20260101, so what moved below may be the model
```

An Ollama digest is still reported as `same tag, different weights`.
The wording follows the value: twelve hex characters is a digest, and
anything else is a name the provider gave.

## Both roads

On Ollama's own API nothing changes: the report records the weights
digest. On a hosted provider the report records what the provider said
answered. The receipt uses the third road: Ollama's OpenAI-compatible
endpoint, driven by Yantra's `openai` provider, which returns a
`system_fingerprint` of its own.

## What was deliberately not built

**No guess where the provider says nothing.** When a response names no
model, the identity falls back to the name that was asked for, and a
provider with no fingerprint records none. Nothing is inferred.

**It is the provider's word, not a hash.** A vendor could serve changed
weights under an unchanged snapshot name and fingerprint. That would be
invisible here. This records what a hosted provider tells you, and it
cannot be more than that.

## What is not here yet

* **Not measured on a live cloud model.** There were no cloud keys where
  this was built. The adapters are tested against recorded responses,
  and the end-to-end path against Ollama's OpenAI-compatible endpoint
  below.

## Receipt

```
$ OPENAI_BASE_URL=http://localhost:11434/v1 OPENAI_API_KEY=ollama \
  yantra --agent researcher --eval --case cites-what-it-read \
         --provider openai --model qwen3.8:latest --report runs/served.json
...
```

and in `runs/served.json`:

```json
"provider": "openai",
"model": "qwen3.8:latest",
"weights": "qwen3.8:latest/fp_ollama",
```

`fp_ollama` is the `system_fingerprint` Ollama's compatibility layer
returns. A hosted OpenAI model returns its own build fingerprint in the
same place.

`1809 passed, 1 skipped` (was 1803).
