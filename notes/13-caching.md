# 13 · Prompt caching: stop re-billing the same prefix

> Agent loops have an expensive habit: every bounce re-mails the whole
> transcript, and `[tools] + [system]` never change between calls.
> Prompt caching turns that repetition into savings — if you know where
> the breakpoints go. Files: `providers/base.py` (the flag),
> `providers/anthropic.py` (`_mark_cache_breakpoints`),
> `providers/openai.py` (hit reporting only), `cli/main.py` (`--cache`),
> `examples/cache_demo.py`, `tests/test_prompt_cache.py`.

## The mechanism in one paragraph

Anthropic-dialect caching is PREFIX caching. Blocks carrying
`cache_control: {"type": "ephemeral"}` mark cut points; everything up
to and including each marked block becomes a cache entry (5-minute TTL,
refreshed on every hit). Writing costs 1.25× input price; reading back
costs ~0.1×. The API allows at most **four breakpoints per request**,
and prefixes shorter than ~1024 tokens simply never engage — which
makes leaving the feature on harmless for short conversations.

## Where our breakpoints go (and why)

An agent loop is exactly the stable-then-growing shape this mechanism
was designed for:

```
[tools][system][ msg0 msg1 ... msgN ]   <- everything before N is frozen
                 ^^^^^^^^^^^^^^^^^^^^     grows by one message per bounce
```

So `_mark_cache_breakpoints` stamps three placements: the LAST tool
definition, the system prompt (promoted from string to a one-block
array, since breakpoints attach to blocks), and the last block of the
last message. Three of four budget slots spent; every later turn reuses
the previous turn's cached prefix and pays full price only for the
newest messages. Stateless per request — no memory between calls, no
invalidation logic: content-hash-based caching means any change to the
prefix simply misses.

## Opt-in at the provider, like retry

Caching is a WIRE concern, so it follows the same precedent as
`RetryPolicy`: a provider constructor flag, not an Agent parameter or a
request kwarg.

```python
provider = get_provider("anthropic", settings, cache_control=True)
#   or:  uv run yantra --cache
```

Off by default (it is a billing-relevant choice). When off, requests
are byte-identical to before the feature existed — regression-tested.

## The OpenAI dialect has nothing to send

Upstream caching there is automatic; the request carries no marker.
Hits are visible ONLY in responses:
`usage.prompt_tokens_details.cached_tokens` folds into our existing
`Usage.cache_read_tokens` (both streaming and non-streaming skins;
explicit nulls tolerated as zero — the gateway lesson from
[notes/02](notes/02-wire-formats.md) applies here too).

## What the live runs showed

Two-phase demo ([examples/cache_demo.py](../examples/cache_demo.py)),
cold call then identical-prefix replay: the billed fresh input
collapsed by ~99.7% on the replay. Two honest wrinkles:

* **Streaming reports nothing.** This gateway sends no usage counters
  AT ALL on streamed calls (the [notes/02](notes/02-wire-formats.md)
  quirk), so caching cannot be *observed* there — even though it is
  almost certainly working. The receipt comes from the non-streaming
  path.
* **Cache-write counters stayed 0 throughout.** The gateway appears to
  fold write cost into plain input tokens rather than reporting
  `cache_creation_input_tokens`. Reads are what we can prove.

### On a local model the receipt is time, not tokens

Ollama bills nothing, so it has no cached-token counter to report, and
`cache_control` means nothing on its OpenAI-shaped wire — the flag is
dropped like it is for any OpenAI dialect. Run the token measurement
against it and you get two identical rows of `cached-read=0`, which
reads like failure and is not.

The server caches anyway, without being asked: it keeps the prompt it
just processed and skips re-reading whatever leading run of tokens the
next request shares with it. So on `--provider ollama` phase 2 streams
both calls and times the first token — almost all of that wait is the
model reading the prompt. Against `qwen3.8:latest`, with an ~8.6k-token
system prompt:

```
phase 2 · the measurement (streamed, timed to the first token, shared prefix)
  call 1  first token after   8.22s  (whole reply 9.83s)
  call 2  first token after   0.20s  (whole reply 1.60s)

verdict
PREFIX REUSED: call 1 read the whole prompt in 8.22s; call 2 started answering
after 0.20s because the server kept the prompt it had already read.
```

One consequence for the demo itself: the per-run cache-busting tag sits
at the **start** of the system prompt, not the end. Anthropic's cache is
keyed on the exact prefix up to a breakpoint, so either position busts
it; Ollama reuses any shared leading run, so a tag at the end would have
let phase 2 ride on phase 1's already-read prompt and "prove" a cold
call that was warm.

## Testing shape

Eleven offline tests pin the contract: default-off invisibility, exact
breakpoint placements (tools/system/last-message, in wire order),
budget ≤ 4, system promotion only when enabled, optional parts shrinking
the set, streaming requests marked identically, OpenAI ignoring the
flag entirely, `cached_tokens` folding through both OpenAI skins, null
details staying zero, and cache counters folding through sync, async,
streaming AND non-streaming Anthropic paths.
