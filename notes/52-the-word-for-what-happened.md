# 52 · The word for what happened

Every refusal in this harness has carried a machine token since
[notes/39](39-a-clock-and-a-word.md) — `user`, `timeout`, `policy`,
`unattended`, `unspecified` — reaching a host on
`ToolExecuted.refusal`. That note ended with an admission:

> **Nothing reads the code in this repo's own frontends.** The terminal
> and the browser both render the sentence, which is the right thing for
> a human to see. The token is for hosts, and this repo is not one.

The first half is true and the second half was wrong. A terminal *is* a
host. Here is what it was drawing:

```
╭─ bash()  ───────────────────────────────────────────╮
│ { "command": "rm -rf build" }                       │
│ Permission denied by user.                          │
╰─────────────────────────────────────────────────────╯
```

Red border, error panel. That is the same thing it draws when a tool
throws. And a refused call did not fail — **it never ran at all**, which
is a different event with a different cause and a different thing to do
about it.

The reason both look alike is a deliberate decision one layer down: the
loop turns a denial into an error `ToolResult` rather than an exception,
so nothing downstream has to learn a second shape
([notes/37](37-a-gate-that-can-wait.md)). That decision is still right.
It just means the frontend has to read the one field that distinguishes
them, and neither of ours did.

## Three states, not two

```
╭─ bash()  [refused: user] ───────────────────────────╮
│ { "command": "rm -rf build" }                       │
│ Permission denied by user.                          │
╰─────────────────────────────────────────────────────╯
```

Yellow, not red — the colour the approval prompt already uses, because
this is the same conversation. A tool that crashed is still red. And the
code is printed rather than translated, because the difference between
`user` and `timeout` is the difference between **a decision and an
absence**, and no single word for "refused" covers both.

That distinction is exactly what a person needs at the end of a long
turn, so the turn says it once more, grouped:

```
── 3 call(s) refused: timeout 1 · user 2
```

Grouped by cause rather than listed per call: the panels above already
said which calls. The question left over is *whether anything was
refused for a reason that was not me* — one timeout in that line means
somebody's phone was face down, and the turn was shaped by nobody's
decision.

The tally is per turn and cleared at the start of the next one. It also
prints under a turn that ended badly, which is when it matters most:
`over_budget` after three refusals reads very differently depending on
who did the refusing.

## The browser gets the token, not a translation

```python
self.broadcast({
    "type": "tool_result",
    "is_error": result.is_error,
    "refusal": refusal,
    ...
})
```

The envelope carries the code as-is and the page decides how to draw it
— a `refused · timeout` pill in the warning colour instead of an `error`
pill in the danger one. The server does not send "Refused because nobody
answered": prose belongs to whoever is rendering, and a host writing its
own UI against this stream gets the same field this repo's own page
reads.

**A replayed call carries no code.** Reconnect, and the transcript is
rebuilt from history — which holds the error `ToolResult` the model saw
and has never held the gate's reason for it. The replay shape therefore
has no `refusal` key at all. Inventing one from the text would be
guessing at somebody's decision after the fact, and the page falls back
to what actually happened: an error result, exactly as the model read
it.

## One thing that had never rendered

Building this turned up a small, old bug in the same three lines. The
title was assembled like this:

```python
title = f"{name}()" + ("  [error]" if is_error else "")
```

A `rich` panel title is rendered **as markup**. `[error]` is a tag as far
as `rich` is concerned, so it was quietly swallowed — every error panel
this repo has ever printed had a red border and two blank spaces where
the word should have been. Nobody noticed, because the border says
"error" loudly enough that the missing word never looked missing.

It is fixed here (`escape()`), and it is worth stating why it stayed
hidden: the redundant signal covered for the broken one. A test that
asserted on the *colour* would have passed forever.

## What is not here yet

* **Nothing offers to retry.** A call refused with `timeout` is one the
  person never saw, and "ask me again" is the obvious next move; a call
  refused with `user` must never be re-asked without being told to. The
  codes make that distinction available — nothing acts on it yet, and
  acting on it means a queue rather than a renderer.
* **The tally is not in the browser.** The page shows per-card pills and
  no per-turn summary; the turn footer there is about tokens and money.
* **History still forgets.** A code lives on the event and nowhere else,
  so `--resume` and a browser reconnect both come back with error
  results and no causes. Storing it means a column in the checkpoint
  format ([notes/38](38-giving-it-back.md)) and a decision about whether
  a refusal is part of a conversation or part of a session's log.
