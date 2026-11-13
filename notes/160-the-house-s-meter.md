# 160 — The house's meter

Every program in the house uses a model, and each one counted its own
use, or didn't count at all. Dvara's runs know their cost. The page shows
a session's. A Prahari watch's yes/no question, the picture it looks at,
and a Vidhi step left no trace anywhere. So *"what did the house use
today, and on what?"* had no answer. A floor or an alert could only ever
meter one program at a time.

Continues [21 — cost accounting](21-cost-accounting.md) and
[159 — a model standing by](159-a-model-standing-by.md).

## One line per turn, wherever it ran

As each turn ends, the agent appends one line to
`$XDG_STATE_HOME/yantra/usage.jsonl` (`usage_log.py`).
`YANTRA_USAGE_FILE` names another file, and `YANTRA_USAGE=off` writes
none. Every program runs its model through Yantra, and Sarathi's
containers share that folder, so one file holds the whole house:

```
{"format":"yantra.usage.v1","at":"2026-11-13T06:30:06-0500","by":"samay",
 "who":"Samay: Weather check (abc123) · mahen","provider":"ollama",
 "model":"qwen3.8:latest","local":true,"input_tokens":3101,"output_tokens":60,
 "cache_read_tokens":0,"usd":null,"seconds":9.46,"iterations":1,
 "reason":"end_turn","switched":[]}
```

**WHO ASKED, NOT WHAT WAS SAID.** `by` is the program. Dvara, Samay,
Prahari and Vidhi each set `YANTRA_USAGE_BY` as they start, and the
terminal and the page say `yantra`. `who` is a label: the person's id
that Dvara sets (with the way the turn came in), or the
`YANTRA_ASKED_BY` a program already gives its turns for the phone's log.
No message, answer or tool result is ever in the file. It's a meter, and
a meter's file is one an owner may hand to anybody.

**NO PRICE IS NOT ZERO.** A local model's turn has `"usd": null`. So does
a cloud model with no list price, and `sarathi usage` counts that one as
*with no price*, so an unknown bill never passes for a free one
(pricing.py's rule).

**ONE APPEND, ONE LINE.** Each line is opened with `O_APPEND` and written
in one call, well under the size an append writes whole. Programs writing
at the same moment never splice their lines together.

**A RECEIPT NEVER COSTS AN ANSWER.** A full disk, or a folder that can't
be made, is skipped silently.

A sub-agent's turn is its own line. Its tokens were never in its
parent's turn, so nothing is counted twice. A standby's answer is marked
(`provider: standby`, the switch in `switched`).

## Live receipt

A one-shot on `qwen3.8:latest`, run as Samay would run it
(`YANTRA_USAGE_BY=samay`, `YANTRA_ASKED_BY` set), wrote the line above.
Sarathi's `sarathi usage` read it beside Smritikosh's own calls:

```
The house's models, today: 1 turns, 0 minutes of answering, nothing billed
  samay       1 turns  local        3.1k in /     60 out    0.2 min
           qwen3.8:latest ×1
           for mahen ×1
  smritikosh   51 calls  filing memories    10.7k in /    488 out
           ollama_chat/qwen3.8-64k:latest ×51
```

## What is deliberately not here

* **No per-call line.** A turn is what a person asked for, and it's the
  unit Dvara's ledger and the budget already use. A turn's iterations are
  in the line.
* **No pruning yet.** At a few hundred bytes a turn, a busy house writes
  well under a megabyte a week.
* ~~**Floors and alerts don't read it yet.**~~ A house ceiling reads it
  now: `YANTRA_HOUSE_DAY_USD`, the house's billed dollars a day, from this
  same file ([notes/161](161-the-house-s-day.md)).

## Tests

`tests/test_usage_log.py`: one line per turn, with who and what and never
the words. A local turn has no dollars, never zero. A priced cloud turn
has its dollars. A program's `YANTRA_ASKED_BY` is the who. `off` writes
nothing, and an unwritable file costs the turn nothing. Reading back
skips what can't be read.
