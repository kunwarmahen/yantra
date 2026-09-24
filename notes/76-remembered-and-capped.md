# 76 — Remembered, and capped if you ask

[Note 69](69-a-reply-the-turn-has-seen-before.md) taught the budget
warning to expect a reply as long as the longest one the turn had seen.
[Note 73](73-the-turn-before.md) let a turn's first call borrow the
previous turn's. Two gaps were left:

* **A fresh agent still started with nothing.** An eval case, a one-shot
  command or a new process has no turn before, so its first call was
  priced on its context alone.
* **A reply longer than any before it** is, by definition, not
  forecast by anything that looks at history. Only the 80% floor
  (`WARN_AT`) could catch it, and a single reply can jump past that too.

This note closes the first gap, and gives you the choice to close the
second.

## Remembered on disk

The previous turn's largest reply is now also written to
`.yantra/replies.json`, next to the session database, keyed by package
and model:

```json
{
 "researcher|qwen3.8:latest": 5992
}
```

A new agent on the same package and model starts from that figure, so
its first call gets the same forecast a turn in a long session would.
The rules from note 73 still hold: it is the **latest turn's** largest
reply, not the all-time largest, so one enormous answer does not make
every later run warn early. It is only written while a ceiling is
actually metering. A file that cannot be read counts as nothing
remembered, and one that cannot be written leaves the figure in memory.
A forecast is advice, and advice is not worth crashing over.

### A bug the receipt found

The first receipt run warned with "the next call carries **~19 tokens**
of context". A fresh agent has no billed response to anchor its
estimate on, and the fallback counted only the conversation, not the
system prompt or the tool definitions, which were most of the request.
The old docstring admitted this and called it harmless, since nothing
was forecast on top of it. Once a remembered reply was added to that
figure, it mattered. The fallback now counts the system prompt and the
tool schemas too: the same call reads ~2,253 tokens.

## Capped, if you ask

```
yantra --max-usd 0.006 --budget-cap-reply "..."
```

With `--budget-cap-reply`, each call's `max_tokens` is set to what is
left of the turn's ceiling once that call's context is paid for. No
single reply can then carry a turn past its ceiling.

**IT REVERSES A RULE, SO IT IS YOURS TO SWITCH ON.** Everywhere else in
this module a final answer is never discarded
([notes/34](34-budgets.md)): a reply that crosses the ceiling is kept and
paid for. A capped reply can arrive **cut off**. Some operators would
rather have a truncated answer than an overspend, and some would not.
That makes it an operator's flag, like `--budget-notice`, and never a
package key.

**WHAT IS CUT OFF IS KEPT, AND SAID.** The turn ends `over_budget`, with
the answer as far as it got, and the reason names the cap:

```
── turn ended: over_budget -- the reply was cut off at 843 tokens, what was left of the $0.006 ceiling for this turn (--budget-cap-reply)
```

A tool call cut off halfway through its arguments is never run.

**ON A THINKING MODEL, THE THINKING SPENDS IT TOO.** The cap is on all
output tokens, and a model that thinks first uses part of it before
writing a word of the answer. In the receipt, qwen's 843 tokens went
partly to thinking, and the visible answer stopped mid-sentence.

Found while wiring this: a turn that ended `over_budget` *with* a
response (only possible now) matched neither the terminal's nor the
browser's rendering, and ended in silence. Both now show the text so
far and then the reason.

## Both roads

The same on every provider. On a local model both parts need a price
in `$YANTRA_PRICES` ([notes/64](64-a-price-for-the-free-road.md)),
because an unpriced ceiling is inert. The receipts are local.

## What was deliberately not built

**No cap by default.** See above.

**No memory across packages or models.** A figure from another agent
is not a forecast of this one.

## What is not here yet

* **A reply longer than anything before it, with no cap,** is still
  caught only by `WARN_AT`. With the cap it cannot overspend, which is
  the only complete answer, and it costs the answer's ending.

## Receipt

**The memory**, on `qwen3.8:latest` priced at $1 / $5 per million
tokens, the researcher package. Two separate processes; process 2's
ceiling was chosen to land between its context alone and its context
plus the remembered reply, to show the mechanism:

```
--- process 1
end_turn, spent ~$0.0324, largest reply 5,992 tokens
{
 "researcher|qwen3.8:latest": 5992
}
--- process 2 (a fresh agent)
call 1: WARNING: the next call carries ~2,253 tokens of context, and replies last turn have run to ~5,992 tokens -- about $0.0322 for both, and ~$0.0090 is left of the $0.009 ceiling for this turn
end_turn, spent ~$0.0039, largest reply 289 tokens
--- process 2 again, with no memory file
end_turn, spent ~$0.0031, largest reply 132 tokens
```

As in note 73, the warning in process 2 was not needed: its reply was
289 tokens. A process that follows a long answer with a short question
gets a false alarm; one that runs the same kind of task again gets a
warning before the call that crosses.

**The cap**, the same model, one command each:

```
$ yantra --provider local --model qwen3.8:latest --max-usd 0.006 --budget-cap-reply "Without using any tools, explain in about 600 words what rate limiting is."
budget: $0.006 per turn -- a heads-up once one more call would not fit; replies capped at what is left
...
Where does it apply? HTTP APIs are the obvious case — the `429 Too Many Requests` response with a `Retry
── turn ended: over_budget -- the reply was cut off at 843 tokens, what was left of the $0.006 ceiling for this turn (--budget-cap-reply) (after 1 iteration(s))

$ yantra --provider local --model qwen3.8:latest --max-usd 0.006 "Without using any tools, explain in about 600 words what rate limiting is."
...
── end_turn · 1934 in / 1351 out · ~$0.0087 · 1 iteration(s)
```

Uncapped, the full answer arrived and the turn cost $0.0087 against a
$0.006 ceiling. Capped, it stopped at the ceiling, mid-sentence.

`1826 passed, 1 skipped` (was 1809).
