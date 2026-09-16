# 38 · Giving it back — two things that assumed the process would exit

Every piece of this harness was written for a shape of program that runs
once: you type a command, a conversation happens, the process exits, and
the operating system tidies up whatever was left open. That is a fair
assumption for a CLI, and it is invisible until somebody embeds the
library in something that does not exit.

Then two small habits turn into bugs, and they are the same bug wearing
different clothes: *the harness took something and never gave it back.*

## One: two connection pools and no door out

A `Provider` opens two HTTP connection pools in its constructor, which is
deliberate and documented — one pool per provider, reused across every
request, rather than a fresh connection per model call:

```python
self.client  = httpx.Client(...)        # the sync pool
self.aclient = httpx.AsyncClient(...)   # the async pool
```

Nothing closed either one. There was no `close()` to call. A host that
wanted its sockets back had to reach into `provider.client` and
`provider.aclient` and close them itself — which works, and which was
never anything the harness promised, so it could have stopped working in
any commit.

Here is the leak, counted. Fifteen providers, one real model call each
against a local Ollama, and the sessions held alive the way a service
holds them:

```python
live = []                      # a service holds its sessions
for i in range(1, 16):
    p = get_provider("ollama", load_settings("ollama"))
    p.complete(messages=[...], model="qwen3.8:latest", max_tokens=4)
    if give_back:
        p.close()
    live.append(p)
    print(len(os.listdir("/proc/self/fd")), "open file descriptors")
```
```
start: 4 open file descriptors
after  5 providers (no close): 9 open file descriptors
after 10 providers (no close): 14 open file descriptors
after 15 providers (no close): 19 open file descriptors

start: 4 open file descriptors
after  5 providers (close()): 4 open file descriptors
after 10 providers (close()): 4 open file descriptors
after 15 providers (close()): 4 open file descriptors
```

One descriptor per provider, for as long as the process lives. A CLI
never sees this, because the CLI is gone. A service that builds a
provider per conversation walks into its own file-descriptor limit some
hours later, with a stack trace pointing at whatever unlucky thing asked
for a socket last.

### Why there are two methods and not one

```python
def close(self) -> None:        # the sync pool
async def aclose(self) -> None: # both
```

This asymmetry is not a design preference, it is httpx's rule and ours by
inheritance: an `AsyncClient` can only be closed from inside a running
event loop. There is no honest synchronous way to close it, so `close()`
does not pretend — it gives back what it can and says so in its
docstring. `aclose()` closes the async pool first and then the sync one,
so a host writing async code needs exactly one line at shutdown rather
than remembering there were two pools all along.

Both context-manager forms exist too (`with provider:` and
`async with provider:`), which is what a script or a test wants.

### The composite has to reach everything it holds

`FallbackProvider(primary, secondary)` behaves like `primary` until
`primary` fails, and holds both. Closing it closes **every** provider it
holds, including the one it never fell back to — and that is precisely
the pool a hand-written shutdown forgets, because it belongs to the
provider that never did anything.

### Three places in this repo were already leaking

Writing the method is half of it; the other half is that this repo had
the bug too, in three places nobody had reason to look at:

* `/provider` in the REPL swaps `agent.provider` for a new one. Flip
  between Anthropic and Ollama six times in a long session and you have
  left six pools behind. The outgoing provider is now closed — but only
  **after** the new one is successfully built, because if the switch
  fails the old provider is still the one in use and closing it would end
  the session over a typo.
* `POST /api/provider` in the web UI, the same swap through a different
  door. It already calls `require_idle()`, so nothing is mid-request.
* The CLI's own exit path, which had `mcp_manager.shutdown()` in a
  `finally` and now gives the connection pool back in the same breath.
  This one is cosmetic — the process is about to exit — and it is in
  because a teardown path that tears down *most* things is the kind of
  example other people copy.

## Two: a checkpoint holds two different things

`apply_payload(agent, payload)` restores a saved session into a live
agent. It restores six things, and until now it restored all six every
time:

| what | it is… |
|---|---|
| `history` | the conversation |
| `total_usage` | what the conversation cost |
| `provider`, `model` | who was answering |
| `system` | what they were told to be |
| `max_iterations` | how hard they were allowed to try |

The top two are the conversation. The bottom four are the agent's
**identity**, and restoring those is exactly right at a keyboard: `/load`
should hand you back the session you left, model and prompt included.
Anything else would be astonishing.

It is exactly wrong for a host that rebuilds its agent every turn. That
agent's identity comes from a package on disk — which the owner may have
edited five minutes ago — and a restore that quietly reinstates the
system prompt from last Tuesday's checkpoint makes editing the package
look broken. You change `prompt.md`, you restart nothing because there is
nothing to restart, and the agent carries on being who it used to be.

Here is that, in a host that rebuilds its agent each turn from a prompt
on disk, with the prompt edited between the two turns:

```
### without the flag
turn 1 (prompt: 'Answer in English, one short sentence.')
  -> The capital of Senegal is Dakar.

... the owner edits prompt.md to say: answer in French ...

turn 2 (prompt: 'Answer in French, one short sentence.')
  -> The capital of Mali is Bamako.

### with history_only=True
turn 1 (prompt: 'Answer in English, one short sentence.')
  -> The capital of Senegal is Dakar.

... the owner edits prompt.md to say: answer in French ...

turn 2 (prompt: 'Answer in French, one short sentence.')
  -> La capitale du Mali est Bamako.
```

Same model (`qwen3.8:latest`), same edit, same second question. In the
first run the edit had no effect at all and there was nothing on screen
to say why. In the second the conversation carries over — turn two
answers "And the capital of Mali?" as a follow-up, not as a fresh
question — while the identity is today's.

So:

```python
apply_payload(agent, payload, history_only=True)
```

**Usage rides with the history, not with the identity.** That was the one
judgement call in this half. `total_usage` could plausibly go either way,
and it goes with the conversation because it is the record of what *this
thread* has spent: a host that dropped it would restart every cost
readout at zero, every turn, and any per-actor ledger built on those
numbers would undercount by an amount nobody could reconstruct after the
fact.

A flag rather than a second function (`apply_history`) because there is
one payload format and one place that knows how to read it. Two entry
points into the same match/case is how block-kind handling drifts.

**And it says when the two disagree.** A history-only restore whose
checkpoint names a different model appends a note:

```
restored 12 message(s), 4180in/612out · history only (checkpoint was anthropic/claude-sonnet-4-5)
```

Silence there is how somebody spends an afternoon wondering which model
actually answered.

## What the tests pin

The bias in both halves is that the failure is INVISIBLE. A leaked pool
produces no output; a wrongly restored prompt produces a perfectly
plausible answer. So nothing in these tests asserts on a log line or a
return value where it could assert on the thing itself.

* A used pool reports itself closed afterwards; a closed provider refuses
  to send; closing twice is not an error (shutdown paths run twice more
  often than anyone plans for).
* `close()` leaves the async pool open — the documented limitation,
  pinned so it cannot quietly change.
* Both context managers close, including when the body raises.
* `FallbackProvider` closes every provider it holds, sync and async.
* The REPL's `/provider` closes the outgoing provider — and a **failed**
  switch closes nothing, which is the half that matters.
* A history-only restore leaves `model`, `system`, `max_iterations` and
  the provider OBJECT alone, and does not even consult the provider
  factory.
* It still restores history and usage, still works on a payload with no
  identity recorded at all, and the plain `apply_payload` still restores
  everything exactly as `/load` has always done.

## What is not here yet

* **Nothing closes a provider for you.** There is no registry of live
  providers, no `atexit` hook, no weak-reference cleanup. A host owns its
  provider's lifetime, the same way it owns its agent's. Anything else
  means the harness deciding when somebody else's socket should die.
* **A closed provider cannot be reopened.** `close()` is terminal; build
  a new provider. Reopening would need a lazily rebuilt pool, which is a
  way of saying the harness would silently undo the thing you asked for.
* **`history_only` does not check whether the restore makes sense.** It
  will happily load a conversation held with a different model into an
  agent pointed at this one, which is usually the intent (that is the
  whole feature) and is occasionally how a cached thinking signature ends
  up somewhere it cannot be verified. The line about the checkpoint's
  model is a warning, not a guard.
* **No `close()` on the agent.** An `Agent` holds a provider, a sandbox,
  MCP sessions and background jobs, each with its own shutdown, and
  bundling them behind one method would put the harness in charge of a
  lifetime it does not own. The CLI's `finally` block is the worked
  example of doing it by hand; a service does the same thing in its own
  shutdown.
