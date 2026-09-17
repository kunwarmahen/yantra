# 35 · Two things a gate could not say

[Note 33](33-evals-as-a-gate.md) gave an agent package its own acceptance
gate: a `cases.toml` of behaviors, one command, an exit code. It also
spent a section admitting that the gate could not express the assertion
its own example was reaching for. That section is the reason this note
exists, so here it is again, shortened:

> The researcher's third case asks the agent, in plain language, to edit
> `prompt.md`. I wrote it believing it pinned the *admission policy* —
> add `write_file` to `tools.allow` and the suite goes red. So I copied
> the package, added `write_file` and `edit_file` to the allow list, ran
> the suite with `--yolo`, and watched it come back green.
>
> It was right to.

Nothing had executed. The prompt still told the model it could not modify
anything, the model believed it, and a check that grades *what ran* saw
exactly what it is built to see: nothing ran. The case pinned a behavior,
which is worth having. It did not pin the configuration the behavior was
leaning on.

That is one of the two gaps. The other is quieter and more embarrassing,
because it is about arithmetic. A trajectory is a **die roll** — the same
question, the same agent, the same model, twice, is two different runs —
and the gate ran each case exactly once and printed a verdict as if that
settled it. It does not settle it, and this note has a receipt where it
visibly did not.

Both gaps close with keys that cost almost nothing to add, and the
interesting part of each is where it is *checked* rather than what it is
called.

## Part one: "it cannot", not "it did not"

Two new keys, and the contrast with the old two is the whole idea:

```toml
[[case]]
id = "has-no-way-to-write"
lacks_tools = ["write_file", "edit_file", "bash", "browser_*"]
has_tools   = ["read_file", "glob", "outline"]
```

`required_tools` and `forbidden_tools` grade the **trajectory** — the list
of tools that actually executed. `has_tools` and `lacks_tools` grade the
**roster** — the list of tools the agent is offered at all. A tool that
was available and went unused looks, in a trajectory, exactly like a tool
that was never there. Which is why the case above is not a rewrite of the
old one but a companion to it: one says this agent *does not* write, the
other says it *cannot*.

Three consequences follow, and each one is a design decision rather than
an implementation detail.

### There is nothing for a model to do

Look at that case again. It has no `user_message`, and it does not need
one: the roster is knowable the moment the agent is built. So the case
runs with no request, no tokens, and no waiting. That is the first case in
this harness's history that a gate can afford to run on every push.

```
$ yantra --agent ./leaky --eval
eval researcher 0.1.0 · 1 case(s) · 1 roster-only · ollama · qwen3.8-64k:latest
cwd: /…/leaky
gate: read-only tools only; writes and commands are refused (--yolo opens it)
budget: $0.50 per turn -- inert here, a local model bills nothing

  FAIL  has-no-way-to-write  roster failed · no model call · 0 tok
        on the roster and should not be: write_file

SUITE RED · 0/1 passed · 0 tokens · 1 case(s) cost nothing
exit=1

real  0m0.230s
```

`./leaky` is the researcher package with one word added to `tools.allow`.
Two hundred milliseconds, zero tokens, exit 1. That is the assertion note
33 wanted and could not make: the copy with `write_file` in its allow list
goes red on its own, with no model in the loop and therefore no prompt to
talk anybody out of it.

A case that mixes the two families is fine and normal — assert the roster
*and* give the agent a task — and then it costs what it always cost. The
free case is the pure one.

### A roster is a set, so patterns belong in it

`lacks_tools = ["browser_*"]` works, and `forbidden_tools = ["browser_*"]`
is refused at load time:

```
error: …/evals/cases.toml: case 'x'.forbidden_tools takes exact tool names,
       not patterns (write_*): this key grades what RAN, and a pattern would
       match nothing. For a claim about the tool LIST, use
       has_tools/lacks_tools, which do take patterns
```

The asymmetry is real and worth defending, because an inconsistency you
have to explain is usually a bad one. A roster is a *set* you are
describing, and the honest way to say "nothing from the browser family" is
a shape, not an inventory of names you would have to keep in sync. A
trajectory is a *log of names that executed*, and `write_*` is not one of
them — it would match nothing, pass silently, and leave behind a green
case that checked nothing. That is the exact failure this format was built
against ([note 33](33-evals-as-a-gate.md) refuses unknown keys for the
same reason), so rather than support patterns everywhere or trap the
author, the load-time check says which key they wanted.

### The free check goes first, and a roster failure short-circuits

Note 33 argued at length that failures **accumulate** rather than
short-circuit: "it didn't use `outline`" and "it cost three times its
budget" are two different bugs and you want both from one run. That rule
still holds inside a run. The roster is the one exception, and it earns it
on two counts.

The first is money. Grading the roster takes no request, so there is no
reason to do it second.

The second is the one that actually decides it. If the roster assertion
fails, **the agent under test is not the agent the case describes**. A
trajectory produced by an agent with the wrong tool list is a trajectory
belonging to some other agent, and paying for it buys observations about a
thing nobody asked about. So the case stops there, reports the roster
failures — accumulated among themselves, all of them, not the first — and
spends nothing:

```
$ yantra --agent ./wrongroster --eval
eval wrongroster · 1 case(s) · ollama · qwen3.8-64k:latest
...
  FAIL  reads-outlines-and-cannot-run-commands  roster failed · no model call · 0 tok
        not on the roster: outline (roster: bash, glob, list_dir, read_file)
        on the roster and should not be: bash

SUITE RED · 0/1 passed · 0 tokens

real  0m0.337s
```

That case has a `user_message` and a `required_tools` of its own, and
neither was reached. The two lines it did print are both roster failures,
accumulated, with the roster it looked at named — which is the whole
diagnosis, for nothing. Note also what the report calls it: `roster
failed`, not `roster only`. A case with a task whose roster was wrong and
a case that never had a task are two different things, and a gate that
printed the same words for both would be misreporting one of them.

The tests pin this on the provider rather than on the verdict: the
scripted provider records every request it is asked for, so an empty
request log is the proof. A test that only checked pass-or-fail would keep
passing on the day the implementation quietly started paying for it.

### What a roster assertion still cannot see

Being precise about this, since note 33's honesty about trajectory checks
is what prompted the key in the first place.

* **MCP tools are absent.** They arrive from live server sessions a host
  opens, and `--eval` opens none. So `lacks_tools = ["mcp__*"]` is an
  assertion about an empty set — true, and true for the wrong reason.
* **Disabled tools do not count.** A tool the operator pulled this session
  is registered but never offered to the model and cannot be called, so
  the roster is built from what the model is *sent* (`specs()`), not from
  what is merely present. Counting it would make `lacks_tools` lie in the
  direction that matters.
* **Per-turn tool selection is not applied.** When a package narrows how
  many tools go out per turn ([note 17](17-tool-selection.md)), the roster
  here is the full admitted set, not the narrowed catalog. The wider set is
  the right thing for a claim about what the agent *can* do.
* ~~**A declared sub-agent's list is out of reach.**~~ It was, and
  [note 44](44-a-ceiling-and-a-floor.md) closed it with
  `subagent_has_tools` / `subagent_lacks_tools`. Worth reading for what
  the two keys turn out to be: the roster above is a CEILING over a
  package, because a child is built out of the parent's registry and
  cannot exceed it, and the new pair is the only way to assert the FLOOR
  each declared child was given.

## Part two: one run is one sample

Here is a receipt from the middle of building this note. Same package,
same model, same case — the first run of the researcher's suite while I
was testing the new key:

```
  FAIL  outlines-before-reading  26.1s · 7643 tok · 3 it · glob, glob, read_file
        required tool not used: outline
```

And the same case, three runs each, twenty minutes later:

```
  PASS  outlines-before-reading  ✓✓✓ 3/3 runs · 37.5s · 21440 tok
```

Nothing changed in between. The model reached for `glob` twice and then
`read_file` on one roll, and for `outline` on the next three. A gate that
ran that case once was going to tell me something true about one sample
and print it as a verdict about an agent.

So: a case may declare the rate it claims to hold at, and the operator
decides how many rolls to buy.

```toml
[[case]]
id = "outlines-before-reading"
user_message = "…"
required_tools = ["outline"]
min_pass_rate = 0.7          # "holds seven times in ten"
```

```bash
yantra --agent ./researcher --eval --repeat 10
```

### Why the threshold is the author's and the count is yours

`min_pass_rate` is a key in `cases.toml`. `repeat` is deliberately *not*:

```
error: …/cases.toml: case 'x'.repeat is not a key here: how many times a
       case runs is the operator's money, not the author's: declare
       min_pass_rate here and let whoever runs the gate pass --repeat N
```

This is the same split the manifest already draws with `[budget]`
([note 34](34-budgets.md)). An author can say what their case *claims* —
they wrote the prompt, they know which behaviors are shaky and which are
load-bearing. What they cannot know is whether you are running this on a
laptop against a local model for free or against a metered one in CI at
nine cases a push. Ten runs of a nine-case suite is ninety trajectories,
and an author who could write that number into a file they hand you would
be spending your money in a config key.

At one run, the default rate of 1.0 is the only one that means anything —
0.7 and 1.0 both reduce to "this run must pass" — so the gate says so out
loud rather than letting the number look like it did something:

```
note: 1 case(s) declare a min_pass_rate below 1.0; one run each can only
grade them all-or-nothing -- --repeat N buys the evidence
```

### The arithmetic, which is where a threshold gets quietly wrong

Three rules, all of them about not lowering the bar by accident.

**The verdict is a count, not a float.** 7 ≥ 7, never 0.7 ≥ 0.7. The
second is a coin flip on binary floating point — `0.3 * 3` is
`0.8999999999999999` on this machine — and a gate that changes its mind
about arithmetic is not a gate.

**Required passes round up.** `min_pass_rate = 0.7` over ten runs needs
seven, and over three runs needs three, not two. Rounding down would pass
a suite whose author said it should fail, which is the only direction of
error that matters here.

**A free check is never repeated.** A roster-only case runs once no matter
what `--repeat` says, and a case whose roster assertion failed collapses
to one reported run. Five identical readings of one deterministic fact,
printed as "0 of 5 runs", would dress a fact up as a statistic.

Failures then carry their frequency, which is most of the diagnosis. This
is the flaky case from the top of this section, held to `min_pass_rate =
0.9` and run four times:

```
$ yantra --agent ./flaky --eval --provider ollama --model qwen3.8-64k:latest --repeat 4
eval researcher 0.1.0 · 1 case(s) · 4 runs each · ollama · qwen3.8-64k:latest
...
runs: 4 per case; a case reports once all of its runs are in

  FAIL  outlines-before-reading  ✗✗✓✓ 2/4 runs (needs 4) · 54.2s · 22862 tok
        required tool not used: outline (2 of 4 runs)

SUITE RED · 0/1 passed · 4 runs · 22862 tokens
```

"2 of 4" is a coin flip, and a single run of that case would have reported
either side of it as a verdict. The same line reading "4 of 4" would be a
broken agent and a different afternoon's work. The glyph strip is there
because `2/4` does not say *which* runs failed: `✗✗✓✓` and `✓✗✓✗` are the
same fraction and not the same finding, and when a suite starts looking
order-dependent — a warm cache, a growing history, a server under load —
that strip is the first place it shows.

### The honest warning about this key

Note 33 said a flaky case is usually an under-specified case, and shipping
`min_pass_rate` does not make that less true — it makes it easier to
ignore. A case that passes seven times in ten is sometimes a real
probabilistic behavior, and is more often a case whose instruction was
vague enough that the model had two reasonable routes. The first is what
the key is for. The second is a case to rewrite, and lowering its
threshold until it goes green is the eval equivalent of raising
`max_usd_per_turn` to wave through a cost regression ([note 34](34-budgets.md)
makes the same complaint about the same reflex).

The rate is a description, not a target. If you find yourself tuning it
downward, the thing that needs editing is above it in the file.

## `--async`, and what it did not buy

`AsyncEvalRunner` has graded cases identically to its sync twin since
[note 10](10-evals.md), so the flag is plumbing:

```bash
yantra --agent ./researcher --eval --async 3      # 3 trajectories at once
```

The unit of concurrency is a **run**, not a case: five repeats of three
cases is fifteen independent trajectories, and bounding by case would
leave the semaphore half empty while one slow case finished its fifth run
alone. Lines land as cases *finish*, so the order on screen is completion
order — but the returned report stays in file order, because a report you
diff between two models must not reorder itself because the network was
slow.

Now the receipt, which is the reason this section is not a victory lap.
The researcher's four cases, back to back on one desktop, one 27B model on
Ollama:

```
sequential   1m12.1s wall     cases: 15.4s · 34.7s · 21.8s  (sum 71.9s)
--async 3    0m58.2s wall     cases: 19.5s · 57.9s · 38.7s  (sum 116.1s)
```

Three times the concurrency bought 1.24× the speed. Look at the per-case
columns to see where the rest of it went: every case's own clock stretched
— the slowest went from 34.7s to 57.9s — because all three were queued
behind one GPU that was already the bottleneck. The client was never what
was waiting. Concurrency does not create hardware.

Against a cloud provider, where the parallelism belongs to somebody else's
fleet, the same flag scales close to linearly and a nine-case suite stops
being a coffee break. Against a local model on one card it buys the
overlap between one trajectory's tool calls and another's decoding, which
is real but modest. Since roughly half this project's readers run local
models, that belongs in the note rather than in a footnote.

One more thing that run is evidence of, and it is not about `--async`: the
concurrent suite came back **red**, on the same flaky case as before, with
nothing changed but the ordering. Two of the three suite runs in this note
disagree about that case. That is the entire argument of part two, arrived
at by accident twice.

## Live receipt

The researcher package — now four cases, one of them free — three runs
each, on local hardware:

```
$ yantra --agent examples/agents/researcher --eval \
         --provider ollama --model qwen3.8-64k:latest --repeat 3
eval researcher 0.1.0 · 4 case(s) · 1 roster-only · 3 runs each · ollama · qwen3.8-64k:latest
cwd: /home/…/yantra/examples/agents/researcher
gate: read-only tools only; writes and commands are refused (--yolo opens it)
budget: $0.50 per turn -- inert here, a local model bills nothing
runs: 3 per case; a case reports once all of its runs are in

  PASS  outlines-before-reading  ✓✓✓ 3/3 runs · 37.5s · 21440 tok
  PASS  cites-what-it-read  ✓✓✓ 3/3 runs · 75.1s · 25423 tok
  PASS  cannot-write-even-when-asked  ✓✓✓ 3/3 runs · 112.2s · 21372 tok
  PASS  has-no-way-to-write  roster only · no model call · 0 tok

SUITE GREEN · 4/4 passed · 10 runs · 68235 tokens · 1 case(s) cost nothing
```

Ten runs, nine of which cost tokens and one of which cost nothing. The
last row is the assertion note 33 could not write, and the first row is
the one that failed on its own the first time I ran it — which is why the
other three rows now have three ticks each instead of one.

## What is not here yet

* ~~**A keyless roster gate.**~~ Shipped in
  [note 41](41-a-gate-you-can-point.md), and the constraint above is what
  shaped it: `AgentSpec.build` runs unchanged and the PROVIDER is the part
  that gets replaced, by one whose every method raises. A roster-only run
  now resolves no provider at all.
* **Statistics, rather than a fraction.** "7 of 10" is a threshold, not a
  confidence interval. Three runs of a case tell you very little and the
  gate will happily print `✓✓✓` as though they told you a lot. Proper
  intervals need more runs than anyone will pay for per push; naming the
  limit is the honest interim.
* ~~**A per-case run count.**~~ The shape turned out not to be a count at
  all: [note 41](41-a-gate-you-can-point.md) adds `--case PATTERN`, so the
  operator POINTS the global run count instead of the author declaring a
  per-case one. Nothing moved into the manifest, and `--case flaky-*
  --repeat 10` buys ten samples of the case that needs them.
* ~~**A roster assertion about MCP tools.**~~ Shipped in
  [note 41](41-a-gate-you-can-point.md): the gate connects the servers the
  manifest declares, so the assertion grades something real. It took the
  first option of the two, and added the rule the second would not have
  needed — a declared server the suite cannot reach is a RED suite, not a
  smaller agent.
* ~~**Comparing two runs of the same suite.**~~ Shipped in
  [note 42](42-two-runs-of-the-same-suite.md): the run writes a report and
  a later run reads one. `case_from_trace`
  ([note 33](33-evals-as-a-gate.md)) pointed at the same missing store from
  the other side, and still does — it is a store of RESULTS, not of
  trajectories.
