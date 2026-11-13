# 159 — A model standing by

When Ollama stopped (a restart, a crash, the computer asleep), every road
in the house failed the same way. The terminal printed `[Errno 111]
Connection refused`. A Telegram message got *that went wrong at my end*. A
schedule's run failed with an exception's name in the log. Nothing said
that the model was the problem, or that nothing had been done.

`providers/fallback.py` has held the mechanism since [note 07](07-reliability-and-scale.md):
try providers left to right, and commit to the first one that opens. But
nothing could switch it on, and it had two gaps that kept it a library
piece. It sends the **same model name** to every backend, so falling from
Ollama's `qwen3.8` to a cloud provider asks the cloud for a model it has
never heard of. And it fails over **silently**, so a person whose words
had just gone to a different company was never told.

## The setting

```
yantra --fallback "anthropic:claude-sonnet-5-5"
YANTRA_FALLBACK="ollama:gemma4:12b@http://other-box:11434/v1, anthropic:claude-haiku-5-5"
```

These are models to try, in order, when yours doesn't answer. Each entry
is `PROVIDER:MODEL`, and the model keeps its own colons (`gemma4:12b`).
`@ADDRESS` points one entry somewhere else, which is how a second
computer running Ollama stands by for this one. Keys and addresses come
from the environment, as they would for the primary. A standby with no
key is left out, with a line saying so (*yantra: standby anthropic:…
left out: …*), and it never stops the start. Sarathi sets the same thing
from Settings → Sarathi → *When it doesn't answer*.

## The rules

**EACH BACKEND IS ASKED FOR ITS OWN MODEL.** `providers/standby.py` pairs
every standby with its model, and the primary keeps the model the agent
was given.

**YOU HEAR WHICH.** Each switch is written down as it happens, and the
agent puts the turn's switches on its `TurnEnd` (`switched`). The
terminal prints a line under the footer, Yantra's page draws it under the
answer, and Dvara puts it under the reply and in the run's ledger row:

```
↪ qwen3.8:latest (ollama) didn't answer (nothing is listening there), so
gemma4:12b (ollama at http://127.0.0.1:11434/v1) answered
```

**AN AGENT MAY SAY WAIT.** `[model] when_down = "wait"` in a package keeps
that agent on its own model, whatever the house has standing by. An agent
that reads your mail has a good reason never to send it anywhere else, and
for that agent *the model is down, ask again later* is the right answer.
The default is `"switch"`. Any other word is refused when the package
loads, and the error names the two words that are allowed.

**THE SAME COMMIT RULE.** A switch happens only before the first event of
a stream, so nothing is ever said twice. What counts as worth a switch is
the same as in fallback.py: unreachable, throttled, refusing the key, or
5xx. A context overflow or an ordinary 400 is the same request everywhere,
so it isn't tried elsewhere.

**A COMPOSITE THAT STILL LOOKS LIKE ITS PRIMARY.** `name`, `settings` and
every other attribute are the primary's. Session files store a provider
by name, and the picture checks ask the provider in use what it can see.
The standby changes which backend answered a request, and nothing else
about the agent.

**DOWN, IN WORDS.** When every model fails, or there's no standby and the
one model isn't answering, the person reads *The model didn't answer
(nothing is listening there), so nothing was done. Ask again in a little
while.* That's in the terminal, on a `--json` run's `error`, and in
Dvara's reply (`standby.down_words`). Any other failure is still reported
as itself.

## Live receipt

A scratch home, `OLLAMA_BASE_URL` pointed at a port where nothing
listens, and the real Ollama as the standby:

```
$ yantra --provider ollama --model qwen3.8:latest \
    --fallback "ollama:gemma4:12b@http://127.0.0.1:11434/v1" "say hi in three words"
standing by: gemma4:12b (ollama at http://127.0.0.1:11434/v1) (when qwen3.8:latest doesn't answer)
...
Hello there, hi!
── end_turn · 2662 in / 39 out · 1 iteration(s)
↪ qwen3.8:latest (ollama) didn't answer (nothing is listening there), so
gemma4:12b (ollama at http://127.0.0.1:11434/v1) answered
```

With the standby dead too:

```
no model answered: qwen3.8:latest (ollama): nothing is listening there;
gemma4:12b (ollama at http://127.0.0.1:8/v1): nothing is listening there
```

And with no standby at all, where `[Errno 111] Connection refused` used to
be printed, the terminal now says *The model didn't answer (nothing is
listening there), so nothing was done. Ask again in a little while.*

## What is deliberately not here

* **No automatic choice of a standby.** The house never picks a cloud
  model on its own. Sending your words to another company is the owner's
  decision, written down.
* **No switching back mid-conversation.** Each request tries the primary
  first, so the next message after Ollama comes back is answered by
  Ollama again.
* **No wait-and-retry for a schedule.** A scheduled run whose model is
  down fails with the plain reason, and the next scheduled time runs as
  usual. Retrying the same job later is Samay's question, not the
  provider's.
* **The page's model switch is not wrapped.** Choosing a provider by hand
  on Yantra's page means *this one*.

## Tests

`tests/test_standby.py`: entries keep the model's own colons and an
address, and a bad entry says the shape. A standby that can't be built is
left out and said. `wait` and an empty list give the primary back, and
the composite still looks like its primary. Each backend gets its own
model and the switch is said. A healthy primary says nothing. A
request-shaped failure isn't tried elsewhere. A stream that began is never
said twice. All down is one error naming each. `TurnEnd` carries the
switch in both twins. A down model with no standby is said in words.
`when_down` is read and checked, and a built agent waits or switches as
its package says.
