# 34 · A ceiling in dollars — the limit that is in the unit of the bill

[Note 31](31-agent-packages.md) made an agent a directory you can hand to
someone, [32](32-package-tools.md) let it bring its own Python, and
[33](33-evals-as-a-gate.md) let it carry proof that it works. One key was
missing from the format the whole time, and note 31 said so out loud:

> **`[budget]`** — a per-run cost ceiling. Deliberately absent rather than
> present-and-ignored.

It was absent because enforcement had nowhere to live. This note gives it
somewhere, and the key ships in the same commit that makes it bite.

## The failure it is for

An agent that goes wrong expensively does not look wrong. It looks busy.

Here is the shape, and everyone who has run one of these has seen it: the
model fetches a page, decides it needs more context, reads three files,
re-fetches the same page because the first read scrolled out of its
attention, summarises, decides the summary is thin, and fetches again.
Nothing crashes. No tool errors. The terminal fills with plausible,
confident panels. Twenty-five iterations later, each one carrying a
context that grew every round, you have a bill.

`max_iterations` is supposed to be the stop for this, and it is the wrong
instrument — not because it fails, but because it measures the wrong
thing. Twenty-five iterations of a short conversation with a small model
and twenty-five iterations of a long one with a frontier model are the
same number and differ by two orders of magnitude on the invoice. A limit
that cannot tell those apart is not a spending limit. It is a patience
limit that happens to correlate with spending, badly.

So: a limit in dollars.

```toml
[budget]
max_usd_per_turn = 0.50
```

```bash
yantra --agent ./researcher --max-usd 0.50 "summarise the notes directory"
```

## What one turn is, and why the ceiling covers exactly that

A *turn* is one thing the agent was asked to do — your message, its
answer, and every tool round trip in between. The meter zeroes at the top
of each one.

That choice is the whole design, so it is worth defending. The obvious
alternative is a ceiling over a whole session, and it loses on a simple
question: **who could possibly fill the number in?**

A package author can estimate a turn. They wrote the prompt, they chose
the tool list, they know roughly what answering one question with it
should cost. They have no idea how many questions you are going to ask,
on what day, in what mood. A per-session number from an author is a guess
about your behaviour dressed up as a guess about their agent.

The second reason is that a session ceiling is not really a spending
control either — it is a spending control with no memory. Close the
terminal, open it again, and you have a fresh allowance. Anything that
genuinely bounds what a person spends needs a store and an identity to
attach the total to, which is a service's problem, not a harness's.
Better to ship the unit that is honest than the unit that sounds bigger.

The consequence is visible and deliberate. In the REPL, turn four gets a
fresh $0.50; the running session total lives in `/usage`, where it always
did.

## A stop, not a cap

The only way to find out what a model call costs is to make it.

So the meter is read *between* iterations, and the overshoot is bounded
by exactly one call. A $0.50 ceiling can end a turn at $0.53. The live
run below ended a $0.02 ceiling at $0.0785, because the iteration that
crossed the line was the one that had just read two long files into
context.

Any design that promised a hard cap would be estimating the bill before
making the request instead of reading it afterwards, and an estimate is
precisely what you do not want standing between a runaway and your card.
Naming it a *stop* rather than a *cap* is not a hedge; it is the accurate
word, and it is the tradeoff this feature accepts.

Where the check sits in the loop is two decisions, both worth stating:

**After the answer, so an answer is never thrown away.** If the model
returns a final reply that happens to cross the ceiling, you get the
reply. The money is spent either way — discarding the result would turn
a budget into a device for wasting money rather than saving it.

**Before the tools, so nothing runs for nobody.** If the model asks for
more tool calls and the meter is over, those calls never execute. They
would cost wall-clock time, they can touch the world, and nothing was
going to read their results. The outstanding call ids still get
synthesized results, exactly as the iteration cap does: the history
invariant ([notes/05](05-agent-loop.md)) does not care why a turn ended.

The stop has its own reason, so nothing has to infer it from a silence:

```
── turn ended: over_budget -- spent ~$0.0785 of the $0.02 ceiling for this turn
   (after 3 iteration(s))
```

`TurnEnd` gained a `detail` line to carry that sentence. "over_budget" on
its own is not an answer to *over what, and by how much* — and a stop
reason an operator has to go and investigate is a stop reason that gets
ignored.

## Delegation is not a way out

`spawn_subagent` ([notes/08](08-sub-agents.md)) hands work to a fresh child
agent with its own history. If each child also got its own fresh ceiling,
`max_usd_per_turn = 0.50` would silently mean *fifty cents per spawn*, and
the cheapest way around the only limit that costs real money would be for
the model to ask for help.

So a budget is **a meter, not a counter**. One object, shared: the child
charges the same meter its parent does, and five children spend one
ceiling between them.

Sharing creates exactly one new problem, which is that the child starts a
turn too — and a turn zeroes the meter. So the meter remembers who it
belongs to. The first agent to start a turn on it claims it; everything
else holding it is spending *under* that agent's turn and can charge it
but never clear it.

```python
def begin_turn(self, owner):
    if self._owner is None:
        self._owner = owner
    if self._owner is owner:
        self.spent = 0.0
```

## The part where half the readers get nothing

`price_for()` ([notes/21](21-cost-accounting.md)) returns `None` for a
model it has never heard of, and it refuses to guess — a wrong price
shown confidently is worse than no price. That rule is right, and it
leaves a dollar ceiling with two very different kinds of hole in it.

**A local model has no price because it costs nothing.** If you run
Ollama, `qwen3.8:latest` is not an unpriced model, it is a free one. The
ceiling can never fire, and the only wrong answer here is to say nothing
— an operator reading silence will read it as protection. So the ceiling
is kept, marked inert, and announced:

```
budget: $0.02 per turn -- inert here, a local model bills nothing
```

**A metered model with no price is a hole**, and it gets the opposite
answer. Point a ceiling at a gateway slug the table has never seen and
the meter reads $0.00 forever: you set a limit, saw no complaint, and are
not protected. That is the exact failure this whole feature exists to
refuse, so it is refused before a token is spent:

```
error: budget: no list price is known for 'gizmo-9', so a $0.50 ceiling
could never stop anything. Add the model to a $YANTRA_PRICES file, or
drop the ceiling
```

The same rule holds mid-session. Switch to an unpriced model with
`/model` and the meter goes blind; a blind meter stops the turn and says
which model it stopped seeing, rather than quietly counting zero.

**And a price you wrote down yourself always wins — local server
included.** `$YANTRA_PRICES` beats the built-in table at every stage in
`pricing.py`, and the same holds one level up: give your own Ollama tag a
price and the ceiling is real against it. Nobody is billing you, so the
number is one you invented — which is exactly right when what you are
testing is whether the ceiling fires at all, and you would rather find
that out on hardware you already own than on an account with a card
behind it. Every receipt in this note was produced that way.

## The author's number, and yours

A package's ceiling is the author's estimate of what one task should
cost. `--max-usd` on the command line **overrides** it, up or down.

That is the opposite of how `tools.deny` merges, where the command line
deliberately cannot lift a restriction the package imposed
([notes/31](31-agent-packages.md)), and the difference is worth being
explicit about because it looks inconsistent until you name it:

> A restriction you cannot lift is a security control. A number you can
> lift is a guard rail. `deny` is the first kind; `[budget]` is the
> second.

`deny` protects you *from the package* — from an agent that says it only
reads files and then reaches for `bash`. The author is the party you are
guarding against, so they get the last word. `[budget]` protects you from
*your own bill*, and you are the one paying it, so you do.

## What this does to the acceptance gate

A package's own eval suite ([notes/33](33-evals-as-a-gate.md)) runs one
turn per case against a fresh agent built from the same spec — so the
package's ceiling applies to every case, and a suite can now go red on
*cost* rather than on behaviour:

```
FAIL  reads-before-answering
      crashed: RuntimeError: turn ended without a response (over_budget
      after 2 iterations): spent ~$0.6000 of the $0.50 ceiling for this turn
```

That is a real finding — the agent took a more expensive route than its
author budgeted for — but it is a different finding from "the answer was
wrong", so `--eval` says the ceiling in its header rather than letting
the first over-budget case read as a bug (the header also counts the
cases that need no model at all —
[notes/35](35-roster-and-pass-rates.md) — which leaves the budget line
below exactly as it reads here):

```
eval researcher 0.1.0 · 3 case(s) · ollama · qwen3.8:latest
cwd: .../examples/agents/researcher
gate: read-only tools only; writes and commands are refused (--yolo opens it)
budget: $0.50 per turn -- inert here, a local model bills nothing
```

Note the last line, because it is the honest caveat: a suite that runs
green against a local model has said nothing about whether it fits the
package's budget on a metered one.

## The receipts

All three against `qwen3.8:latest` on a local Ollama server, with a
hand-written price in `$YANTRA_PRICES` so the ceiling had something to
meter. First, the stop, with the ceiling set implausibly low (2 cents) so
that reading two notes would cross it:

```
$ YANTRA_PRICES=prices.json yantra --provider ollama --model qwen3.8:latest \
    --agent examples/agents/researcher --max-usd 0.02 --yolo \
    --prompt "Read notes/33 and notes/32, then summarise how they relate."

agent: researcher 0.1.0 -- examples/agents/researcher
package tools: outline
budget: $0.02 per turn -- a heads-up once one more call would not fit
skills: 5 loaded -- eval-suite, new-tool, notes-entry, repo-survey, source-brief
· qwen3.8:latest

· thinking
The user is asking me to read notes/33 and notes/32, and then summarize how
they are related. First, let me find these files.
→ glob()
→ list_dir()

· qwen3.8:latest

· thinking
I found both notes. Let me read them.
→ read_file()
→ read_file()

· budget: the next call carries ~11,189 tokens of context, about $0.0336 before
the reply -- and ~$0.0003 is left of the $0.02 ceiling for this turn
· qwen3.8:latest

· thinking
Both notes are short and fully read. My answer will run past a couple of
paragraphs and draws on two sources, so let me load the source-brief skill.
→ load_skill()

── turn ended: over_budget -- spent ~$0.0785 of the $0.02 ceiling for this turn
   (after 3 iteration(s))
```

The `· budget:` line is the heads-up, priced from the request that was
about to go out — [note 36](36-a-warning-before-the-stop.md) is why it
exists and why it is not a percentage.

Read the last two lines together, because they are the design working.
The model *asked* for `load_skill` on iteration three. It never ran. The
meter was already over when the request came back, and the gate sits
between the response and the batch.

Second, the same command with the price file removed — nothing else
changed:

```
agent: researcher 0.1.0 -- examples/agents/researcher
budget: $0.02 per turn -- inert here, a local model bills nothing
· qwen3.8:latest
ok
── end_turn · 2467 in / 17 out · 1 iteration(s)
```

Third, the refusal, on a metered provider and a slug nobody can price.
No request is made; this fails while the agent is still being assembled:

```
$ yantra --provider anthropic --model gizmo-9 --max-usd 0.50 --prompt "hi"
error: budget: no list price is known for 'gizmo-9', so a $0.50 ceiling
could never stop anything. Add the model to a $YANTRA_PRICES file, or
drop the ceiling
$ echo $?
2
```

## What the tests pin

`tests/test_budget.py`, 33 cases, written against one bias: *a ceiling
that fails to stop something is worse than no ceiling, because whoever
set it has stopped watching.* So most of them are about silent
non-enforcement — the unpriced model counting zero, the sub-agent
spending outside the meter, the child clearing its parent's spend, a
misspelled `max_usd_per_run` in a manifest configuring nothing. The rest
pin the arithmetic against hand-computed dollars, both loops stopping
identically, the history invariant surviving a budget stop, and the one
rule that goes the other way: a final answer that crosses the ceiling is
kept.

## What is not here yet

* **A session or daily ceiling.** Named above as the thing a harness
  cannot honestly offer: it needs a store and an identity to attach a
  total to. That is a service's job, and when it exists the two compose —
  the package's ceiling and the owner's policy, lower wins, and whatever
  stopped the run says which one it was.
* **A ceiling in tokens.** Dollars are the unit of the problem, and
  `EvalCase.max_tokens` already covers tokens for the one place they are
  the better unit ([notes/33](33-evals-as-a-gate.md)) — a regression case
  whose ceiling has to stay stable while prices move.
* ~~**A warning before the stop.** Nothing says "you are at 80% of this
  turn's budget"; you find out by being stopped. A `TurnEnd` is a poor
  place to learn it and the loop has no other channel for advice yet.~~
  Shipped in [note 36](36-a-warning-before-the-stop.md) — though not as
  an 80% rule, which that note measures and throws away: a tool-using
  turn leaps straight over the band. The loop forecasts the call it is
  about to make instead, and `BudgetWarning` is the channel for advice.
* **Counting what the provider does not report.** `Usage` zeros are
  normal on some streamed calls ([notes/21](21-cost-accounting.md)), and
  a turn billed in silence is a turn this meter does not see. Not
  fixable here — the number has to come from somewhere.
* **A per-package dependency on the price table.** Writing `[budget]`
  into a manifest means your package refuses to run on any metered model
  the table has never heard of. That is the cost of the key, it is paid
  by whoever runs your package rather than by you, and `$YANTRA_PRICES`
  is the only way out. Worth thinking about before you add three lines
  of TOML to something you are about to share.
