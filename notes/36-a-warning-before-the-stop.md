# 36 · A warning before the stop — and why the obvious version is useless

[Note 34](34-budgets.md) gave a turn a ceiling in dollars, and then ended
with a list of what it had not done. One bullet was a complaint about
itself:

> **A warning before the stop.** Nothing says "you are at 80% of this
> turn's budget"; you find out by being stopped.

This note builds that warning. It also throws the first version away,
because the thing that bullet asked for does not work, and finding out
why turns out to be the interesting part.

## The complaint, restated

A ceiling that stops a turn is doing its job. But from where you are
sitting it happens without warning: panels stream past, the agent looks
busy, and then a yellow line says the turn is over and you are out
$0.07. Nothing told you it was coming. You could not have hit Ctrl-C
sooner, could not have raised `--max-usd`, could not have decided the
question was worth more than you had set aside for it.

So the loop should say something first. That is the whole feature, and
it sounds like an afternoon's work.

## The obvious version, which does not fire

Here is the version everyone writes, including me: keep the running
total the meter already has, and when it passes 80% of the ceiling,
print a line.

It is four lines of code, and the first real turn I pointed it at said
nothing at all before the stop.

The turn was the researcher package reading two notes and summarising
how they relate — four model calls, a ceiling of $0.05. Here is what each
call carried and what each one cost, measured against a local model with
a hand-written price in `$YANTRA_PRICES` so the meter had something to
read:

```
iter   context tokens   this call   cumulative
   1             2478    $0.0088      $0.0088
   2             2588    $0.0095      $0.0183
   3             3058    $0.0108      $0.0290
   4            13948    $0.0435      $0.0725
```

Read the context column. Three calls of roughly three thousand tokens,
and then one of fourteen thousand — because between call three and call
four the agent ran two `read_file`s, and two whole notes went into the
context. The fourth call cost **four times** the third.

Now put the 80% rule against that. The ceiling is $0.05, so the warning
band starts at $0.04. After call three the meter reads $0.0290: under the
band, nothing said. After call four it reads $0.0725, which is not in the
band either — it is 145% of the ceiling, and by then the stop has already
fired. The turn went from 58% to 145% in one step and was never once
observed inside the window the warning was watching.

This is not bad luck. It is the normal shape of a tool-using turn. **The
price of a model call is mostly the price of its context**, and a context
does not grow smoothly — it grows when a tool returns something, in
whatever size that thing happened to be. One `read_file` is a step
change. Any rule built on money already spent is looking at the wrong
column.

That kills "another call like the last one would cross it" too, which
was my second attempt. There is no function of $0.0088, $0.0095 and
$0.0108 that predicts $0.0435.

## What the loop actually knows

The fix comes from noticing where the information is.

At the top of every iteration, *before* the request goes out, the agent
has assembled exactly what it is about to send. The tool results are
already in the history. The size of the next call is not a mystery at
that moment — it is sitting there, in a list of messages, waiting to be
counted.

So the forecast is of **the call about to be made**, not the one just
finished:

```python
messages = self._before_model_call(self.history)
if self.budget is not None:
    advice = self.budget.take_warning(
        self, next_input_tokens=self._forecast_tokens(messages),
        model=self.model)
    if advice is not None:
        yield BudgetWarning(...)
response = collect(self._tee(self.provider.stream(messages=messages, ...)))
```

`take_warning` prices those tokens and asks one question: *does this fit
in what is left?* If it does not, this is the last moment anybody can be
told.

## The ceiling is read; the warning is estimated

That is the rule the whole note turns on, and it is a deliberate
inconsistency with [note 34](34-budgets.md), which spent a section
insisting that a ceiling never estimates:

> Any design that promised a hard cap would be estimating the bill before
> making the request instead of reading it afterwards, and an estimate is
> precisely what you do not want standing between a runaway and your card.

Still true — **for the stop**. A stop takes the rest of your turn away,
so it is only ever made on money the provider has actually billed.

A warning takes nothing away. Being wrong costs a line of text. That
asymmetry is not a loophole, it is the licence: a decision that can hurt
you is made on evidence, and a decision that cannot is free to guess.
Once you see that, guessing is not merely allowed, it is *required* —
because the honest evidence-only version is the one that stayed silent
above.

## Anchoring the guess on something billed

The first forecast used `estimate_history`, the chars-over-4 proxy the
context-pressure bar runs on — a labelled guess, used where no real count
is available ([note 07](07-reliability-and-scale.md)). On the turn above
it read **17 tokens** for a first call the provider billed at **2478**.

That gap is not a bug in the estimator. It is counting the transcript,
and the transcript is not the request: the system prompt, the skills
roster and every tool's JSON Schema all ride along, and none of them are
messages. For an agent package with a prompt and a skill roster, that
fixed overhead is thousands of tokens on *every* call.

So the forecast anchors on a number somebody actually billed:

```python
if self.last_context_tokens and self._sent_through <= len(messages):
    return (self.last_context_tokens
            + estimate_history(messages[self._sent_through:]))
return estimate_history(messages)
```

The last response reported its own window footprint. Everything that was
in that request is still in this one, so the only thing to estimate is
what has been *appended since* — which is precisely the tool results,
which is precisely where the surprises live. Measured, plus a guess about
the new part, instead of a guess about all of it.

The same turn again, with what the forecast said next to what the
provider then billed:

```
iter     spent   forecast tok   actual tok   forecast $   actual $    err
   1   $0.0000             17         2478      $0.0001    $0.0088   -99%
   2   $0.0088           2543         2588      $0.0076    $0.0095    -2%
   3   $0.0183           2941         3058      $0.0088    $0.0108    -4%
   4   $0.0290          11386        13948      $0.0342    $0.0435   -18%
```

Within a few percent once there is anything to anchor to. Two errors
remain and both are worth naming rather than smoothing over.

**The first call of a turn reads low** — catastrophically, −99% — because
nothing has been billed yet and the proxy is counting a
one-sentence transcript. That is the best possible place for the error to sit: the
first call is when almost nothing has been spent, so a missed warning
there costs nothing, and the forecast is at its most accurate deep into a
turn, which is exactly when anyone needs it.

**The forecast always reads low**, by the length of the reply and by
whatever the chars-over-4 proxy misses in the delta — −18% on call four,
the decisive one. It fired anyway: $0.0290 spent plus $0.0342 forecast is
$0.0632 against a $0.05 ceiling, and the real figure was larger still.
Under-forecasting is the direction that loses warnings, never the one
that invents them, so the next section puts a floor under it.

## The floor under the forecast

The forecast prices the request. It cannot price the *reply*, because
nobody can know how long a reply will be before it arrives.

That undercounts, and there is a turn shape where the undercount matters:
one whose cost is mostly output — a short prompt and a long answer, over
and over. Each next request looks affordable, and the money goes out the
other side. So the old 80% rule stays on as a second trigger:

```python
if (self.spent + forecast < self.max_usd
        and self.spent < self.max_usd * WARN_AT):
    return None
```

Two triggers, and each one covers the other's blind spot: the forecast
catches turns that leap, the fraction catches turns that creep. `WARN_AT`
is a module constant and deliberately **not** a key. The ceiling is
configurable at three levels because somebody's money depends on the
number; the point at which a line of text appears costs nothing to get
wrong, and a third way to spell the same intent is surface nobody asked
for.

## Advice is a new kind of event

`BudgetWarning` joins `AgentEvent`, and it is the odd one in that union.
Every other member reports that something *happened*. This one reports
that something is *likely to*.

Nothing is required of a consumer that receives it. No tool was blocked,
no call was skipped, the turn continues exactly as it would have. That is
why it is an event and not a stop — and why the tests assert that a
warned turn can still end with `end_turn`, having been wrong in the
direction that costs nobody anything.

It is deliberately not a general `Notice(level, text)` channel. There is
one message on this channel, and it carries `spent` and `max_usd` as
numbers so a browser can draw a bar with them. When a second kind of
advice turns up, that is the moment the union earns a general name — not
before.

## Where a warning must not go

The sharpest decision here is a consequence of the meter being shared,
and it is the one that would have been easiest to get wrong quietly.

Sub-agents spend against the parent's ceiling ([note 34](34-budgets.md)
argues why: a ceiling a model can reset by delegating is the same lie as
a ceiling that does nothing). So a sub-agent can be the one whose call
crosses the line. But a sub-agent's events do not go to your screen —
they go into a tool result, which the parent model reads and you mostly
do not. A warning raised there is delivered to nobody.

Worse: the warning is a one-shot per turn. A child that triggers it would
*consume* the copy the human was going to get. The feature would be
silently worst in exactly the situation it was built for.

So `take_warning` takes an owner, the same way `begin_turn` does, and
answers only the agent whose turn it is:

```python
if self._owner is not owner:
    return None
```

The child's spending still counts — it is the same meter. It surfaces on
the parent's very next iteration, in the stream somebody is reading.

## Live receipt

The researcher package against a local `qwen3.8:latest`, with a
hand-written price in `$YANTRA_PRICES` so the ceiling had something to
meter, and `--max-usd 0.05`:

```
$ YANTRA_PRICES=prices.json yantra --provider ollama --model qwen3.8:latest \
    --agent examples/agents/researcher --max-usd 0.05 --yolo \
    --prompt "Read notes/33 and notes/32, then summarise how they relate."

agent: researcher 0.1.0 -- examples/agents/researcher
package tools: outline
budget: $0.05 per turn -- a heads-up once one more call would not fit
skills: 5 loaded -- eval-suite, new-tool, notes-entry, repo-survey, source-brief
· qwen3.8:latest

· thinking
The user wants me to read notes/33 and notes/32, then summarize the
relationship between them. First, let me find these files.
→ glob()
...
· thinking
Let me read both notes in full. Each is around 250-350 lines.
→ read_file()
→ read_file()

· budget: the next call carries ~11,313 tokens of context, about $0.0339 before
the reply -- and ~$0.0219 is left of the $0.05 ceiling for this turn
· qwen3.8:latest

· thinking
Both are already fully read. Next, summarize the relationship.
→ load_skill()

── turn ended: over_budget -- spent ~$0.0743 of the $0.05 ceiling for this turn
   (after 4 iteration(s))
```

This is a different run of the same command as the tables above, so the
figures are close but not equal — the model picks a slightly different
route each time, and the point of a forecast is that it tracks whichever
route it gets.

The warning lands between the two `read_file` results and the call they
made expensive, which is the only place it could have been useful. It
says the two numbers that matter — what the next call will cost, and what
is left — and then the turn goes ahead and crosses anyway, because a
warning is advice and the operator is the one who decides.

The startup line matters as much. `a heads-up once one more call would
not fit` is there so nobody learns the warning exists by receiving it;
by then it is too late to have set a different ceiling.

## What the tests pin

* The forecast trigger, on a turn at 20% of its ceiling where a fraction
  rule is still silent and one fat tool result makes the next call
  unaffordable.
* The fraction trigger, on a turn that creeps in small steps where the
  forecast keeps saying "fine".
* One warning per turn, re-armed by the next `begin_turn`.
* A sub-agent does not consume the turn's one warning — asserted on the
  parent's meter after the child has spent, not on a rendering.
* A turn that is already over the ceiling is **stopped, not warned**; and
  a blind meter (a charge for a model with no list price) stops without
  ever having warned, because the first evidence of trouble *is* the
  stop.
* The warning arrives before the model call it is about, asserted as an
  event *order*, not a count.
* A warned turn can still end `end_turn`. Advice is not a decision.
* It reaches both screens: the terminal renderer and the browser
  envelope, with `spent` and `max_usd` as numbers.

## What is not here yet

* **No forecast of the reply.** Output tokens are unknowable in advance,
  so a turn whose cost is mostly output leans on the `WARN_AT` floor
  rather than on the number that would actually help. `max_tokens` is an
  upper bound and a useless one — priced in full it would warn on every
  call of every turn.
* **The first call of a turn still reads low**, by however many tokens
  the system prompt, skills roster and tool schemas come to. Fixable by
  estimating those too, or by asking the provider to count; neither is
  worth it while the error sits where it does least harm.
* ~~**One warning, not a running figure.**~~ A turn that has been warned
  told you nothing further, however long it went on. The readout this
  bullet asked for shipped in [note 43](43-a-bar-and-a-deadline.md),
  where it was always supposed to live: on the same bar as the
  context-pressure one ([note 22](22-web-ui.md)), with the same
  thresholds, showing what is LEFT rather than what is spent — and
  drawing an inert ceiling as empty and "free" rather than as a full bar
  that implies a protection nobody has.
* ~~**Nothing acts on it.**~~ Shipped in
  [note 43](43-a-bar-and-a-deadline.md), and the risk named here is
  exactly what shaped it: the agent is told the DEADLINE and never the
  meter, because a number is a quantity to optimise and a deadline is a
  constraint on the shape of what remains. No digit appears in what the
  model reads, the notice is sent rather than stored in history, and it
  is off unless the operator asks for it — this changes how a model
  behaves, which is not the package author's call.
