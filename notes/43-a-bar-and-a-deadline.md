# 43 · A bar for the person, a deadline for the model

[Note 34](34-budgets.md) gave a turn a ceiling in dollars, and
[note 36](36-a-warning-before-the-stop.md) gave it a heads-up before the
stop — one sentence, once per turn, priced from the request about to go
out rather than from a percentage of what has already been spent. Note 36
then ended with two things it had not done, and said why each was its own
argument rather than an oversight:

> A live remaining-budget readout belongs with the context-pressure bar
> (note 22), not in the event stream. The browser now has the numbers.

> **Nothing acts on the warning.** Telling the AGENT it has one call left
> is a different feature with a different risk — a model that knows it is
> metered starts optimising for the meter — and wants its own argument.

Two readers, then. One of them is a person watching a bar, and one of
them is a language model that will do something with whatever you tell
it. They want opposite things, and the useful part of this note is
working out what "opposite" means.

## The person gets the numbers

This half is small and mostly a matter of putting the thing where the eye
already is.

The browser header carries a context-pressure bar ([note 22](22-web-ui.md)):
a 56-pixel strip that fills as the conversation approaches the window,
green to amber to red at the same thresholds auto-compaction uses. A
dollar ceiling is the *same kind of fact* — how much of something finite
this turn has used — so it goes on an identical bar, immediately beside
it, sharing the same CSS and the same thresholds.

```
[ ████░░░░░░ 41% ]  [ ███████░░░ $0.07 left ]
    context              budget
```

Three decisions in that, and only the third is interesting.

**What is left, not what is spent.** `$0.07 left` is the figure somebody
acts on — "do I ask the follow-up now or start a new turn?" — and
`$0.43 spent` is the figure they would then have to do arithmetic on.

**The numbers, not the sentence.** The state envelope already carried
`budget` as prose ("$0.50 per turn — a heads-up once one more call would
not fit"), and a bar cannot be drawn from prose. So the meter rides
separately as three fields, and the terminal keeps the sentence. A
terminal can only print the sentence; a browser can draw the bar; parsing
the first back into the second is how those two drift apart.

**An inert ceiling draws an empty bar and says "free".** This is the one
worth arguing. A local model bills nothing, so its ceiling exists and can
never fire ([note 34](34-budgets.md) keeps it rather than dropping it,
precisely so a host can say so). Drawing that as a *full* bar would be
honest about the arithmetic and dishonest about the meaning — it looks
like protection. Drawing it as empty and green, labelled `free`, is what
is actually true.

The meter updates mid-turn, on every tool result, because that is a
moment when the model call that requested the tool has already been
billed. A bar that only moved at the end of a turn would be a receipt,
and the point of a bar is that it moves while there is still time to
interrupt.

## The model gets a deadline

Now the half with a risk attached.

Note 36's warning goes to a human, who can hit Ctrl-C, or let the turn
finish and go over, or shrug. The obvious next feature is to tell the
*agent* — it is, after all, the one making the expensive decisions — and
the reason note 36 refused is one sentence long:

> a model that knows it is metered starts optimising for the meter.

That is not a vague worry, and it has two shapes. Tell a model "you have
$0.08 left of $0.50" and it either **trims the answer** — dropping the
detail somebody was paying for, to save tokens nobody asked it to save —
or it **budgets**, reasoning about how many more calls it can afford and
then spending exactly that. Neither is the work. Both are a model doing
arithmetic with somebody else's money instead of answering the question.

The escape is to notice that a number and a deadline are different kinds
of thing. A number is a quantity to optimise. A deadline is a constraint
on the *shape* of what remains — and a model can act on a deadline
without having anything to game.

**THE AGENT IS TOLD THE DEADLINE, NEVER THE METER.** Here is the whole
message, and the absence of digits in it is the design:

```
[budget notice] This turn is nearly out of its spending limit and will be
stopped shortly, probably after the next model call. Finish with what you
already have: give your best answer now, and say plainly what you did not
get to. Do not start new work, do not open anything further, and do not
shorten the answer itself to save room -- the limit is on the work, not on
the reply.
```

A test asserts that no digit appears in it at all. That is a blunt check
and it is the right one: every softening of this rule starts with "well,
one number would be helpful".

The last clause is there because of the obvious misreading. "You are
nearly out of budget" sounds like "be brief", and being brief costs the
user the one thing they were paying for. The limit is on the *work* —
the tool calls, the extra files, the second pass — not on the reply.

### Sent, never stored

The notice is appended to the message list **that goes out**, not to
`self.history`.

That distinction is doing real work. History gets replayed, resumed from
a checkpoint, and saved to SQLite ([note 38](38-giving-it-back.md)). A
budget notice is true of exactly one moment — *this* turn, nearly out of
meter — so writing one into history means a turn next week, with a full
ceiling, reads back an urgent instruction to wrap up. And it reads it
back as something the **user** said, since a user message is the only
place it could live.

So the loop copies the outgoing list, adds one text block to its last
message, and sends that. History never learns. The tests assert on the
provider's request and on the agent's history *separately*, because the
whole point is that the two differ.

(It rides in the last user message rather than as a new one because the
last message before any model call is always a user message — the user's
own, or a batch of tool results — and two user messages in a row is a
shape some wire formats reject.)

### Off unless the operator asks

`--budget-notice`, and it is not a package key.

`[budget] max_usd_per_turn` is the author's estimate of what one task
should cost, and the operator may raise it ([note 34](34-budgets.md)
argues that asymmetry at length). This is a different kind of thing: it
changes how the model behaves. The author has an opinion about what the
work costs; the person running it is the one who cares whether the answer
arrives rushed, and whether a model that has been told to hurry is a
model they still trust the output of.

And the startup line says when it is on:

```
budget: $0.02 per turn -- a heads-up once one more call would not fit
        (the agent is told too)
```

An operator should not discover that their model was being coached by
reading a transcript.

## Receipts

Rehearsed against a local model, with a made-up price in
`$YANTRA_PRICES` so the ceiling is real and the spend is not
([note 34](34-budgets.md)'s trick). `qwen3.8:latest`, priced as though it
were a frontier model, with a two-cent ceiling:

```
$ yantra --provider ollama --model qwen3.8:latest --max-usd 0.02 \
         --budget-notice --yolo \
  "Read README.md, then notes/36..., then tell me in three sentences
   what the warning forecasts."

· budget: the next call carries ~12,775 tokens of context, about $0.0383
  before the reply -- and ~$0.0085 is left of the $0.02 ceiling for this turn
```

The operator's sentence, unchanged from note 36. The model's answer then
ends like this:

```
(Nothing was left unfinished — you asked for three sentences, above is
the answer. The README read was context only.)
```

It gave the full three sentences — long ones, with the note's own
measured figures in them — and then said plainly what it had and had not
done. That closing line is the notice working: the model was told to
finish and to say what it missed, and it did both, without ever being
told a dollar figure.

The same turn without the flag, for contrast: same warning to the
operator, same two iterations, a slightly longer answer, and no
acknowledgement of anything — which is correct behaviour for a model that
was never told there was a deadline.

Both turns ended `end_turn` at about $0.055, well over the two-cent
ceiling. That is not a bug and it is the third receipt: **a warned turn
still goes ahead and crosses.** Note 36's rule holds — the stop happens
between iterations, and an answer already paid for is never thrown away.
This feature changes what the model *knows*, never what the loop *does*.

## The tradeoff

**The notice is advice to something that may ignore it**, and unlike the
operator's warning, its effect is unmeasurable. A person who gets a
heads-up and carries on made a decision. A model that gets one and starts
a fresh file read might be ignoring it, might have judged the read
essential, or might not have attended to the last block of a twelve
thousand token request at all — and there is no way to tell those apart
from outside.

The honest position is that this makes a good outcome more likely and
guarantees nothing, which is why the ceiling is still enforced by the
loop, in code, between iterations. If a prompt were the enforcement, this
would be a security control made of politeness.

## What was deliberately not built

**A number in the notice.** The whole note. Not "you have $0.08 left",
not "about one call remaining", not a percentage.

**A second notice.** One per turn, latched on the same one-shot as the
operator's warning ([note 36](36-a-warning-before-the-stop.md)) and
re-armed by `begin_turn`. A model told twice in one turn learns that the
deadline is soft.

**Telling a sub-agent.** The meter is shared, and the one-shot belongs to
it, so a child never consumes the parent's notice — the same rule the
operator's warning already had, for the same reason: a sub-agent's events
end up inside a tool result, addressed to nobody.

**A `[budget] notify_agent` key.** Argued above: it changes model
behaviour, so it is the operator's, not the author's.

**A budget figure in the CLI header bar.** The terminal prints the
sentence at startup and the warning when it fires. A live readout wants a
region of the screen that is redrawn, and this REPL is a scrolling
transcript by design ([note 06](06-cli.md)); the browser is where the
bar belongs and now has one.

## What is not here yet

* **No forecast of the reply.** Unchanged from note 36: output tokens are
  unknowable in advance, so an output-heavy turn still leans on the
  `WARN_AT` floor rather than on the number that would help.
* **Nothing measures whether the notice works.** An eval case could
  compare a warned turn against an unwarned one — same prompt, same
  ceiling, does the warned one stop opening files? — and it would need
  many samples of a genuinely borderline turn to say anything
  ([note 41](41-a-gate-you-can-point.md) made those samples cheaper to
  buy, and nobody has bought them).
* **The browser bar knows nothing about the SESSION total.** It shows one
  turn's ceiling against one turn's spend; the running session cost is a
  footer string beside it, and the two never meet. A session ceiling
  needs a store and an identity, which is still a service's problem
  ([note 34](34-budgets.md)).
* **A sub-agent's spend is invisible in the bar.** It charges the same
  meter, so the number is right; what the bar cannot show is that
  three-quarters of this turn's money went to one delegated child
  ([note 40](40-a-package-that-delegates.md)).
