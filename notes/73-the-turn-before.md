# 73 — The turn before

[Note 69](69-a-reply-the-turn-has-seen-before.md) taught the budget
warning to expect a reply as long as the longest one the turn has had so
far. It left one gap, and said so: **the first call of a turn** has no
replies to go on, so it was priced on its context alone. A model that
thinks at length before every answer would cross the ceiling on that
first call without warning, turn after turn.

## Last turn stands in

A turn is rarely the first thing an agent does. In a session (the
terminal, the browser, a dvara conversation), the same agent on the same
model answered the turn before. So until this turn has a reply of its
own, the forecast uses **the previous turn's largest reply**, and the
warning says where the figure came from:

```
the next call carries ~4,671 tokens of context, and replies last turn have run to ~1,978 tokens -- about $0.0146 for both, and ~$0.0120 is left of the $0.012 ceiling for this turn
```

Once this turn has a reply, that reply takes over, as in note 69.

**ONLY THE TURN BEFORE, NOT THE WHOLE SESSION.** A session's all-time
largest reply would be the more cautious figure, but one enormous
answer early in a session would then make every later turn warn early,
for as long as the session lasts. The previous turn is recent enough to
describe how the model is behaving now.

**A FRESH AGENT STILL HAS NOTHING.** A one-shot run, an eval case, or the
trial script (which builds a new agent for every turn) has no turn
before. Its first call is priced on its input, exactly as before. This
note does not change note 69's measurement, which used fresh agents.

## A small bug the receipt found

The first run of the receipt printed `~$0.0120 is left of the $0.01
ceiling`. The ceiling was $0.012 and every message rounded it to two
decimals. A ceiling set between whole cents is now printed as written
(`$0.012`), and one set in whole cents keeps its two decimals (`$0.25`,
`$2.00`).

## Both roads

The same on every provider. The receipt is a local model with a made-up
price ([notes/64](64-a-price-for-the-free-road.md)).

## What was deliberately not built

**No figure carried across agents.** A new agent is a new question, and
a guess borrowed from somebody else's session is not a forecast.

## What is not here yet

* **A reply longer than anything before it** still surprises the
  forecast. The `WARN_AT` floor is all that catches it, as note 69 said.

## Receipt

A two-turn session on `qwen3.8:latest`, the researcher package, priced
at $1 / $5 per million tokens, with a $0.012 ceiling on each turn. Turn 1
asks for a 400-word explanation with no tools. Turn 2 asks a short
follow-up.

```
turn 1: end_turn, spent ~$0.0123, largest reply 1,978 tokens
turn 2, call 1: WARNING: the next call carries ~4,671 tokens of context, and replies last turn have run to ~1,978 tokens -- about $0.0146 for both, and ~$0.0120 is left of the $0.012 ceiling for this turn
turn 2: end_turn, spent ~$0.0043, largest reply 261 tokens
```

The same script against the previous version gave no warning in turn 2:
its first call's context alone (~$0.005) fit easily.

**The ceiling was chosen to land between those two forecasts**, so this
shows how the mechanism works, not how often it helps. And in this run
the warning was **not needed**: turn 2's actual reply was 261 tokens,
far shorter than turn 1's, and the turn finished at ~$0.0043. That is
the cost note 69 accepted: a warning a call early costs a sentence, and
here it cost one. On a model that thinks at length on every call, the
turn before is a good guess. On a conversation that goes from a long
answer to a short one, it is a false alarm.

`1796 passed, 1 skipped` (was 1792).
