# 161 — The house's day

[notes/160](160-the-house-s-meter.md) gave the house one meter: every
program's turns, one line each, in one file. A meter only tells you
afterwards. A house running five programs on a cloud key (Dvara answering
people, Samay's schedules, Prahari's questions, Vidhi's steps, the page)
had no single line it could not cross. A turn's budget
([notes/34](34-budgets.md)) stops one turn. Dvara's allowances stop one
person. Nothing stopped *the house*, and a watch asking every five minutes
could spend a day's money while everyone was asleep.

## The setting

```bash
YANTRA_HOUSE_DAY_USD=2.00      # the house's billed dollars a day, every program together
```

Sarathi writes it into every container and every plain program from
`[usage] day_usd` in sarathi.toml (Settings → Sarathi → *Which model
answers*).

## The rules

**THE SAME FILE IS THE METER AND THE CEILING.** The day's total is the
usage file's lines since local midnight. There's no second counter
anywhere, so the number `sarathi usage` shows and the number that stops a
turn can't disagree. The file is read from its end backwards, and reading
stops at the first line from before midnight. A year of lines costs what
today's do: the test reads 20,000 old lines in under 0.2 s.

**A BILLED MODEL IS PASSED OVER LIKE A DOWN ONE.** The check sits in the
standby layer ([notes/159](159-a-model-standing-by.md)), before each
billed model is asked. When the day's dollars are gone, the billed model
isn't called at all. A local model standing by answers, and the switch
says why:

```
↪ claude-sonnet-5-5 (anthropic) wasn't asked: the house's $2.00 for today is
spent ($2.10 so far), so qwen3.8-64k:latest (ollama) answered
```

With nothing local standing by, the turn ends in words, never a bill
nobody agreed to:

```
Nothing was done: the house's $2.00 for today is spent ($2.10 so far), and no
local model stands by. Billed models answer again after midnight.
```

Dvara puts the same sentence in its reply, so someone asking on Telegram
hears it too.

**A LOCAL MODEL IS NEVER HELD BY DOLLARS.** Ollama bills nothing. A
ceiling about money that stopped a local model would be a power cut, not
a ceiling. (Sarathi's `day_minutes` is the line for a local house. It is
said, never enforced.)

**"WAIT" STILL HOLDS.** An agent with `when_down = "wait"` keeps its own
model and never switches. It is still held to the ceiling. Without the
ceiling, an agent with no standby would be the one way past it.

**A STOP, NOT A CAP.** A turn's budget works the same way: the call that
crosses the line is the last one. A long turn that starts at $1.98 can end
the day a few cents over $2.00. Stopping before every call on an estimate
would mean guessing at the bill.

**THE LINE SAYS WHO REALLY ANSWERED.** A switch now names the backend
that answered (`"provider": "ollama"`, `"local": true`), not just
`standby`. Without that, the local model's answers after the ceiling
would be counted as billed in `sarathi usage`, and a house that had
switched to free turns would look like it was still spending.

## Live receipt

A scratch usage file with $2.10 already billed today, `YANTRA_HOUSE_DAY_USD=2`,
and a cloud key that would fail if it were ever used (`sk-not-real`):

```
$ yantra --provider anthropic --model claude-sonnet-5-5 --prompt "say hi"
held to the house's $2.00 a day for billed models
Nothing was done: the house's $2.00 for today is spent ($2.10 so far), and no
local model stands by. Billed models answer again after midnight.

$ yantra --provider anthropic --model claude-sonnet-5-5 \
    --fallback "ollama:qwen3.8-64k:latest" --prompt "name one colour, one word"
Blue
── end_turn · 2681 in / 25 out · 1 iteration(s)
↪ claude-sonnet-5-5 (anthropic) wasn't asked: the house's $2.00 for today is
spent ($2.10 so far), so qwen3.8-64k:latest (ollama) answered
```

The key was never sent: the first run would have failed on it if
it had been. The second run's line in the file:
`"provider":"ollama","model":"qwen3.8-64k:latest","local":true`.

## What is deliberately not here

* **No warning sent the moment 80% is passed.** Yantra has no channel
  of its own to tell the owner. Sarathi's doctor says it (amber near
  the line, amber at it, and the morning after a day that reached it),
  and its morning check sends only what's new.
* **No per-person or per-program share of the day.** Dvara's
  allowances are per person already. Dividing the house's day between
  programs would need someone to decide whose turn matters more.
* **Smritikosh's own calls aren't held.** It calls its model without
  Yantra. `sarathi usage` shows them beside the rest, and it runs on
  the local model.

## Tests

`tests/test_house_ceiling.py`: only today's priced lines count, read from
the file's end. No ceiling unless one is set in dollars. A local model
is never held. Under the line, the billed model answers. Over it, a local
standby answers and says why, or the turn ends in words. An agent that
waits is held too, the async twin the same, and the usage line names the
local model that really answered.
