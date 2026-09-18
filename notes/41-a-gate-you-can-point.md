# 41 · A gate you can point, and one that runs without a key

[Note 33](33-evals-as-a-gate.md) gave an agent package its own acceptance
gate: `evals/cases.toml`, one `[[case]]` per behaviour the package
promises, and `--eval` turning the lot into an exit code.
[Note 35](35-roster-and-pass-rates.md) added the two things that gate
could not say — assertions about the tool **roster**, which cost nothing
because they are knowable before any request, and `min_pass_rate`, which
is an author being honest that a trajectory is a die roll.

That note ended with five admissions. Three of them were the same
complaint from different directions: **the gate ran the whole suite, the
way the whole suite was written, against whatever the environment
happened to be holding.** You could not run it cheaply, you could not
aim it, and it quietly graded a smaller agent than the one that ships.

This note closes those three.

## The gate that needed a key to spend nothing

Note 35's headline was that a roster assertion is free:

```toml
[[case]]
id = "has-no-way-to-write"
lacks_tools = ["write_file", "edit_file", "bash", "browser_*"]
```

No task, no model, no tokens — a claim about the agent's *configuration*,
which is the one thing a trajectory can never check, because a tool that
was available and went unused looks exactly like a tool that was absent.

And then:

```
$ yantra --agent . --eval
error: no provider found: set ANTHROPIC_API_KEY (or OPENAI_API_KEY) ...
```

Grading a roster means **building** the agent, and an agent takes a
provider. So a suite that made no request still demanded a key, or a
`--provider ollama` and a server running, to reach zero tokens. That is
not merely an annoyance: the whole pitch of a roster gate is "CI can
afford this on every push", and a CI job that needs a secret to check a
tool list is a CI job somebody has to go and configure.

Note 35 named the obvious fix and refused it: *"not by duplicating
`AgentSpec.build`'s assembly in a second place — which is the only reason
it is still open."* Assembling the agent by hand would have worked and
would have created a second copy of the order things get wired in
([note 31](31-agent-packages.md)) — a copy that drifts the first time
somebody adds a step.

The fix, once seen, is small: **the provider is the part that gets
replaced.**

```python
class OfflineProvider(Provider):
    name = "none"

    def _refuse(self):
        raise ConfigError(
            "this eval run resolved no provider because every selected case "
            "grades the roster, and something asked for a model anyway ...")
```

`AgentSpec.build` runs unchanged, in its own order, with its own rules.
The provider it is handed will not answer, and **every method raises**
rather than returning something empty. If a run's accounting of which
cases are free is ever wrong, that must be a loud error, not a
mysteriously blank answer that grades as a failed case.

Two smaller things had to move with it:

* **The CLI resolves the provider later.** `--eval` is now dispatched
  *before* `guess_provider()`, because whether a key is needed is a fact
  about the cases, and the cases are inside the package the spec just
  named.
* **A dollar ceiling is dropped for the run.** `[budget]` refuses to be
  *built* against a model nobody can price ([note 34](34-budgets.md)) —
  correct, and beside the point when the model is `(no model needed)`. A
  run that spends nothing cannot cross a ceiling, so there is no ceiling.

```
$ yantra --agent . --eval --case "has-no-way*" --case "the-checker*"
eval researcher 0.1.0 · 2 case(s) · 2 roster-only · none · (no model needed)
no provider resolved: every selected case grades the roster, so this run
makes no request and needs no key

  PASS  has-no-way-to-write  roster only · no model call · 0 tok
  PASS  the-checker-is-actually-on-the-roster  roster only · no model call · 0 tok
```

No key, no `.env`, no local server. Zero tokens and zero setup, which
were always supposed to be the same claim.

## The per-case run count that was the wrong question

The second leftover:

> **A per-case run count.** `--repeat` applies to the whole suite, so one
> genuinely probabilistic case drags every deterministic one along with
> it. The fix is a key, and a key that spends the operator's money is
> exactly what the section above refused — so it wants a shape nobody has
> proposed yet.

Note 35 had already settled the principle, twice over: the author
declares `min_pass_rate` (a claim about their case), the operator passes
`--repeat N` (a decision about their money). A `repeat` key in
`cases.toml` would let an author spend somebody else's budget, so it is
refused by name, with the reason in the error.

The shape that was missing is not a per-case count at all. **It is a way
to point the count.**

```bash
yantra --agent . --eval                             # the gate: everything, once
yantra --agent . --eval --case "flaky-*" --repeat 10   # ten rolls of the one
```

`--case PATTERN` is fnmatch against case ids and repeatable. The run
count stays global and stays the operator's; what changes is which cases
the run covers. Nothing moves into the manifest, no author gains the
ability to spend, and the operator gets exactly the thing they wanted:
ten samples of the case that needs evidence without paying for ten
samples of the twenty that do not.

It turns out to be the flag you want while *writing* a case, too, which
is how a feature earns its place twice.

## A subset is not a gate

Filtering creates a new way to lie, and it is a good one:

```
SUITE GREEN · 1/1 passed
```

That sentence is what somebody pastes into a pull request. It is true
about the case that ran and false about the package, and the exit code
cannot correct it because the exit code is also 0 — correctly, since the
case that ran passed.

So the word changes:

```
filtered: alpha -- 1 of 2 case(s); this is not the package's gate

SUBSET GREEN · 1/1 passed · 1 case(s) not run
```

**A FILTERED RUN IS NOT A GATE**, said in the header before the run and
in the verdict after it. A reader who sees only the last line still
learns that something was left out.

And a pattern matching nothing is an error, not a pass:

```
error: no case matches gamma -- this suite has: alpha, beta
```

Same rule an empty `cases.toml` gets ([note 33](33-evals-as-a-gate.md)):
a suite that runs nothing passes everything, and every route to that
outcome has to be closed separately.

## The servers the gate was pretending about

The third leftover was the sharpest, because it was a gate reporting
green about an agent nobody ships:

> **A roster assertion about MCP tools.** `--eval` opens no servers, so
> the set is empty and an assertion about it is vacuous.

An agent package can declare MCP servers ([note 09](09-mcp.md),
[note 31](31-agent-packages.md)), and in a session those servers start
and their tools register as `mcp__<server>__<tool>`. Under `--eval` they
did not. So `has_tools = ["mcp__docs__*"]` graded an empty set and
*passed by being about nothing* — the precise failure mode this format
was built against, in the feature that was supposed to prevent it.

Note 35 offered two honest options: connect them, or refuse `mcp__*` in
these keys outright. Connecting them is the one that agrees with the rest
of the design. The whole reason the runner takes `spec=` rather than
building a bare agent is that **a suite must grade the agent that ships**
— its prompt, its skills, its own tools, its admission policy. Its
servers are on that list.

```
mcp 'tiny': 2 tool(s) under test -- mcp__tiny__echo, mcp__tiny__add

  PASS  the-server-is-under-test  roster only · no model call · 0 tok
```

They register into the runner's **base** registry, before any case copies
it, so the package's own admission policy still applies per case: a
package that narrows to a whitelist must still name `mcp__*` to keep
them, exactly as in a session. A test pins that, because "connecting a
server smuggles its tools past the package's own list" is the bug this
shape could have had.

### The one place a dead server is fatal

Everywhere else in this CLI, an MCP server that will not start is a
yellow warning: a dead optional integration should not kill an
interactive session somebody is sitting in front of.

A gate is the opposite case.

**A SERVER THE PACKAGE DECLARED AND THE SUITE COULD NOT REACH IS A RED
SUITE, NOT A SMALLER AGENT.** Its job is to answer "does the agent that
ships still work", and an unreachable server silently turns that into a
different question answered in the same green letters — with the added
insult that the roster assertions written *about* those tools would go
red and read as a code problem.

```
$ yantra --agent ./with-server --eval
error: mcp server declared by this package is unreachable, so the agent
under test would be smaller than the one that ships: mcp server 'tiny'
exited unexpectedly (code 2) during 'initialize'
```

Exit 2 — a configuration problem, distinct from exit 1, which means the
agent was tested and found wanting.

`--no-mcp` exists for the offline CI case, and it does not get to be
quiet either:

```
mcp: skipped (--no-mcp) -- 1 declared server(s) are NOT under test, and
neither are their tools

  FAIL  the-server-is-under-test  roster failed · no model call · 0 tok
        not on the roster: mcp__tiny__* (roster: glob, read_file)

SUITE RED · 0/1 passed
```

Red, and the header says why. A flag that made the suite pass by removing
what it checks would be worse than no flag.

## The first thing it found

`--case` plus `--repeat` earned itself within an hour of existing, on
this repo's own worked example.

The researcher package's suite had a case called
`outlines-before-reading`. It had been passing. Then a full run went red
on it, which could equally have been a regression, a bad roll, or the
model having a bad day — and before this note there was no cheap way to
tell, because finding out meant running the whole suite several times.

```
$ yantra --agent . --eval --case "outlines-before-reading" --repeat 5
  FAIL  outlines-before-reading  ✗✓✓✗✗ 2/5 runs
        required tool not used: outline (3 of 5 runs)
```

Two in five. Not a regression — a case that had been claiming 1.0 while
holding at about 0.4, and getting away with it because one run is one
sample. Running the same five rolls against the package as it stood
before that week's work gave **1 of 5**, which settled the other
question: nothing recent had broken it, and it had never really worked.

The temptation at that point is `min_pass_rate = 0.4`, and
[note 35](35-roster-and-pass-rates.md) already refused it in advance:
*lowering the threshold until a suite goes green is how a real regression
gets waved through; if you are tuning it downward, edit the case
instead.*

So the case was wrong, and reading it showed why in one line. The
package's prompt said to outline a file *"before `read_file` on anything
long"* — and **nothing tells a model how long a file is until it has
opened it**, which is exactly the cost the instruction exists to avoid.
The model was being graded on a judgement it had no way to make, and
half the time it guessed the other way. It was not misbehaving; the
instruction was unfollowable.

Two edits, measured separately, each five runs:

| | result | tokens |
|---|---|---|
| as it was | `✗✓✓✗✗` 2/5 | 37,730 |
| prompt sharpened ("outline every Markdown file, full stop") | `✓✓✓✓✓` 5/5 | 45,913 |
| …and pointed at a 217-line document instead of a 52-line one | `✓✓✓✓✓` 5/5 | 31,242 |

The prompt fix is what made the case pass. The repointing is what made
it *worth* passing — and the token column is the argument: on a short
file, outlining first costs an extra call (45,913), and on a long one it
saves the whole document (31,242). A case that demonstrated the
instruction on a file where obeying it was a net loss was testing
compliance, not value.

None of that is a feature of this note. What this note contributed is
that the whole diagnosis cost five runs of one case instead of five runs
of six, twice — and that the flag was there at the moment somebody wanted
to know, rather than being the thing they wished they had.

## The tradeoff

**A suite now starts subprocesses and opens sockets.** A gate that was
previously a pure function of the package directory plus a model now
depends on whatever the declared servers depend on — and an author who
adds one `[[mcp]]` line has made everyone's `--eval` slower and newly
able to fail for reasons outside the package.

That is the right trade and it is still a trade. The alternative was a
gate that agreed with itself and disagreed with production, and those are
the expensive ones: a suite that only tells you about the agent you are
not running is worse than a suite that occasionally cannot run.
`--no-mcp` is the escape hatch, and it announces the smaller agent rather
than pretending.

## What was deliberately not built

**A `repeat` key in `cases.toml`.** Refused again, and by the same
argument as in note 35 — with `--case` there is now a way for the
operator to get what the key was wanted for, so the temptation is
smaller rather than larger.

**`--case` selecting by anything but id.** Not by tag, not by "only the
ones that need a model", not by "only the ones that failed last time".
Tags are a taxonomy nobody has asked for; the last of those needs a store,
which [note 42](42-two-runs-of-the-same-suite.md) then built — and left
that flag unbuilt anyway, because "last time" turns out to be a decision
rather than a fact.

**Warning when a filter excludes every roster case** (or every costly
one). The filter is the operator's, the header says what ran, and a
second opinion about their selection is noise.

**Reconnecting remembered MCP servers under `--eval`.** A session
reconnects servers saved by earlier sessions
([note 09](09-mcp.md)); a gate connects exactly what the *manifest*
declares and nothing else. A verdict that depended on which servers this
machine happened to remember would have the same problem as a verdict
that depended on which directory you were standing in.

**Refusing `mcp__*` in trajectory keys.** It was the other honest option
and it is now unnecessary: with servers connected,
`required_tools = ["mcp__docs__search"]` grades something real.

## What is not here yet

* ~~**Statistics, rather than a fraction.**~~ Shipped in
  [note 47](47-what-seven-of-ten-is-evidence-of.md), and this bullet's
  point survived intact: `--case` + `--repeat` makes samples cheaper to
  buy without making them mean more, so the interval is printed beside
  the fraction and the operator decides what to spend. The one number
  that changed is in the note this flag prints — "--repeat N" became
  "--repeat 9", because n is computable from the rate the author
  claimed.
* ~~**Comparing two runs of the same suite.**~~ Shipped in
  [note 42](42-two-runs-of-the-same-suite.md) — `--report` writes a run
  down and `--against` answers "was that better than yesterday?". It is a
  store of RESULTS; the store of trajectories `case_from_trace`
  ([note 33](33-evals-as-a-gate.md)) was waiting for is
  [note 57](57-a-turn-written-down.md), where the privacy question became
  the design rather than a caveat.
* **MCP servers under the async runner get no special handling.** Sessions
  are synchronous ([note 11](11-async.md)), so `--eval --async` drives
  concurrent trajectories through one set of stdio pipes — one writer
  lock and a reader thread per server. Nothing measures what that does to
  wall-clock time, and the honest guess is that a suite leaning hard on
  one slow server gets less out of `--async` than the tokens suggest.
* **A roster assertion still cannot see a sub-agent's tool list.**
  Unchanged from [note 40](40-a-package-that-delegates.md), and the widest
  remaining hole in what a free case can check.
