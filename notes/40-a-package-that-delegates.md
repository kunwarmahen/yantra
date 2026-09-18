# 40 · A package that delegates

[Note 08](08-sub-agents.md) gave the harness one tool called
`spawn_subagent`. The model calls it, and a fresh agent appears: empty
history, its own system prompt, a tool list the model typed into the
call, and one job. When it finishes, only its final answer comes back —
a forty-step research run arrives in the parent's conversation as one
paragraph instead of forty turns of tool noise.

That is the valuable part, and it is worth being precise about why.
Delegating is not about parallelism here. It is about the **context
window**: checking one claim means opening files that are of no further
interest the moment the answer is known, and doing that inline drags the
whole search into a conversation somebody still has to finish.

But look at what the model is deciding, in that call, at that moment:

```json
{"objective": "...", "tools_allowed": ["read_file", "bash"],
 "justification": "faster this way", "max_iterations": 40}
```

The model is writing its own child's permissions. That is why
`spawn_subagent` is behind an operator flag (`--subagents`) and why it
demands a written justification — friction, deliberately, to make
over-delegation visible.

[Note 31](31-agent-packages.md) then made an agent into a **directory**:
a file that says which model, which tools, which skills, which servers,
and a reviewer who can read it before running anything. Every capability
an agent has is declared in that file — except one. Delegation was still
something the model improvised at runtime.

This note closes that. A package can now declare a sub-agent:

```toml
[[subagent]]
name = "fact_checker"
description = """
Check one factual claim against the files in the working directory. \
Give it the claim in a single sentence; it returns a verdict and a quote \
with a file and line.
"""
prompt = "subagents/fact_checker.md"
tools = ["read_file", "glob", "grep", "outline"]
max_iterations = 12
output_format = "SUPPORTED / CONTRADICTED / UNSUPPORTED, then the quote."
```

**A DECLARED SUB-AGENT'S TOOL LIST IS THE AUTHOR'S, NOT THE MODEL'S.**
Every other difference follows from that one sentence.

## What the model is left to decide

One string.

```
fact_checker(task="Verify: prompt.md tells this agent to outline long
                   documents before reading them.")
```

There is no `tools_allowed`, because it is in the file. No
`max_iterations`, because it is in the file. And no `justification`,
which is the one omission worth arguing: `spawn_subagent` requires one
because a model talking itself into delegating is the failure mode being
priced. A declared sub-agent is the *package author* having decided, in a
diff somebody could review, that this work should happen in a fresh
window. Asking the model to justify calling a tool its own package ships
for exactly this purpose is friction against the wrong decision, and
friction with no target is how people learn to type anything into a
required field.

The same reasoning makes it **not** an opt-in. `--subagents` exists
because the freeform tool hands the model a blank cheque; a declared
sub-agent is part of what the package *is*, with the same standing as the
package's own tools and skills. A package that has to be run with an
extra flag to be itself is a package with a footnote.

## One tool each, not one dispatcher

The alternative shape was `spawn(agent="fact_checker", task=...)` — one
tool, a name argument, a roster hidden in the description. Each declared
sub-agent becomes its own tool instead, and the reason is that **the tool
list is the menu**. A model decides whether to delegate by reading tool
descriptions; a roster buried inside one tool's description is a roster
that tool selection ([note 17](17-tool-selection.md)) cannot rank and a
long conversation will page out. The generated description keeps the
author's sentence and adds the part only the harness knows:

> Check one factual claim against the files in the working directory.
> Give it the claim in a single sentence; it returns a verdict and a
> quote with a file and line. **Delegates to a sub-agent with a fresh
> context window: it sees NOTHING from this conversation except the task
> you write here, may use only read_file, glob, grep, outline, and
> returns only its final answer.**

The cost of one-tool-each is name collisions, and the registry was
already loud about those — `duplicate tool name: 'grep'`. The build
catches it and says which package is responsible, because that message
on its own sends the reader to entirely the wrong place.

## The prompt that does not ask

Here is the thing the tests caught that nobody predicted.

A sub-agent tool is marked "not read-only", which means the permission
gate stops and asks a human before every delegation. That is right for
`spawn_subagent` — the model just wrote the child's tool list, and by the
time you could inspect it, the blast radius is whatever it typed.

For a declared sub-agent the question has an answer. `fact_checker` may
use `read_file`, `glob`, `grep` and `outline`. All four are read-only.
There is nothing to approve. A prompt with nothing to decide in it is
worse than no prompt: it teaches people to hit `y` without reading, which
is precisely the habit the gate exists to prevent.

So `DeclaredSubagent.read_only` is a **property**, unique in this repo,
and it is computed at call time rather than at construction:

```python
registry = self.spawner.parent.registry
known = set(registry.names())
return all(name in known and registry.get(name).read_only
           for name in self.declared.tools)
```

Call time, because MCP tools register *after* the agent is built — a
list naming one would otherwise be judged against a registry that had not
met it yet. And a name the registry does not know counts as unsafe,
which is the same pessimism MCP's own `readOnlyHint` gets, for the same
reason: a guess in this direction costs a prompt, and a guess in the
other costs whatever the tool does.

## One level deep, checked in both orders

Three 85%-reliable agents in series is 61% end to end, so children cannot
have children. `spawn_subagent` has always rejected itself in
`tools_allowed`; a manifest is a new place to try the same thing, and a
more tempting one, because a hierarchy written in TOML looks like
architecture.

The obvious check — as each entry is read, reject a tool name belonging
to a sub-agent already seen — passes this manifest:

```toml
[[subagent]]
name = "checker"
tools = ["look", "reader"]    # reader has not been read yet

[[subagent]]
name = "reader"
tools = ["look"]
```

So the check runs over the whole list after parsing, and a test pins the
bottom-up spelling specifically. Writing the test was how the gap was
found; the first implementation had it.

## What the manifest refuses

Everything decidable from the file alone is decided when the file is
read, because the alternative is a `ToolError` in front of a user, in the
middle of a turn, after the spawn budget has already been charged for a
child that could never have run.

* **Exactly one of `prompt` (a file) or `instructions` (inline text).**
  The same rule `[[mcp]]` has for `command`-or-`url`, for the same
  reason: "exactly one" is checkable and "whichever one you meant" is
  not.
* **A description is required.** It is the entire basis on which the
  model decides to hand work over. An undescribed sub-agent is one that
  gets called for the wrong things, or never at all.
* **A non-empty tools list.** A child with no tools is a second opinion
  from the same model on a smaller prompt. Occasionally that is what
  somebody wants; it is never what they wrote this table for.
* **A name the model can type**, lower-case and underscored, because it
  *is* a tool name.
* **No tool the package's own `[tools]` policy excludes.** Both lists are
  in one file, so this is exact rather than a guess.

That last one has an asymmetric twin worth knowing. If `tools.allow` does
not name the *sub-agent itself*, the sub-agent is **refused, not
rejected** — startup prints `package excludes: fact_checker` and the
agent runs without it. That is [note 32](32-package-tools.md)'s rule for
package tools, and a declared sub-agent is a tool the package brought
with it. The difference from the case above: a host may legitimately
merge a `deny` in, and a package that stopped loading because its
operator narrowed it would be punishing the operator for using the
feature.

## The child a service gets

`[[subagent]]` was built because a package should declare everything it
can do. Building it surfaced something the freeform tool had been getting
away with for a while.

`SubagentSpawner` always built a **synchronous** child, even when the
parent was an `AsyncAgent`. Under the async agent, the spawn tool ran
through the default `arun` — push the blocking `run()` onto a worker
thread — which works, and which quietly breaks one thing:
[note 37](37-a-gate-that-can-wait.md) made permission gates awaitable so
the person approving a call can be somewhere other than this keyboard. A
synchronous child handed an awaitable gate refuses **every dangerous call
it makes**, because `decide()` refuses to read a coroutine as a yes. In a
service — the only place an awaitable gate exists — every sub-agent was
quietly unable to do anything that needed approval.

So the child is now built to match the **call path**:

```python
cls = (AsyncAgent if want_async and isinstance(self.parent, AsyncAgent)
       else Agent)
```

Keyed to the call path and not to the parent, which is the detail that
took a failing test to learn. A synchronous `spawn()` on an async parent
must still get a synchronous child — otherwise it returns a coroutine to
a caller that will never await it, and the child never runs at all. The
symptom was a green-looking eval whose final answer was the *child's*
last line, with `RuntimeWarning: coroutine 'AsyncAgent.run' was never
awaited` on a stream nobody reads. That is the second time in three notes
that an un-awaited coroutine has produced a plausible wrong answer
instead of an error.

That test also exposed a latent bug one layer away. The eval runner wraps
every tool in a recording proxy, and the proxy implemented `run` but not
`arun` — so it inherited the base `arun`, which threads the *synchronous*
path. Any tool with a real async implementation was silently running its
blocking body under the async runner: a suite grading a shape that
production does not use. Forwarding `arun` is four lines, and finding it
took building something that finally had a reason to override `arun`.

## Money

A declared sub-agent may name its own `model` — a cheaper slug for the
grunt work, the expensive one for the writing. It is a **slug on the
parent's provider**, never a provider of its own: a manifest that could
name a second provider is a manifest that can open a connection to a
service the operator never configured and does not know they are paying
for.

The ceiling ([note 34](34-budgets.md)) covers it, because a child spends
the *parent's* meter. That meant extending note 34's refusal: a ceiling
that can be priced for the parent's model and not for the child's is a
ceiling that stops the turn the first time anybody delegates, and the
operator who set it would find out at the worst possible moment. So the
build prices every declared child's model too, and refuses with the
sub-agent's name in the message.

The spawn budget is shared. The spawner is built once and published on
the agent, so a later `--subagents` joins the same counter rather than
opening a second one beside it — five spawns means five spawns, however
they were asked for.

## Receipts

The worked package gained one. Run against a local Ollama model —
`qwen3.8:latest`, no key and no spend:

```
$ yantra --agent . --provider ollama
agent: researcher 0.1.0 -- .
package tools: outline
sub-agents: fact_checker
budget: $0.50 per turn -- inert here, a local model bills nothing
skills: 1 loaded -- source-brief
```

Asked to check a claim, with no permission prompt anywhere — the child
can only read:

```
→ fact_checker()
╭─ fact_checker() ──────────────────────────────────────────────────────╮
│ { "task": "Verify this claim: prompt.md tells this agent to outline    │
│   Markdown files before reading them." }                              │
│                                                                       │
│ SUPPORTED.                                                            │
│                                                                       │
│ > `prompt.md:22-25`: "**Outline every Markdown file before you read   │
│ it.** Not "if it looks long": you cannot tell how long a file is      │
│ until you have opened it, and opening it is the cost you are trying   │
│ to avoid. So call `outline` first, every time ..."                    │
╰───────────────────────────────────────────────────────────────────────╯
```

The child went and found that on its own. None of the searching is in
the parent's conversation — the parent's history contains one tool
result, and it is the paragraph above.

And the package's own gate, still green with the new cases:

```
eval researcher 0.1.0 · 6 case(s) · 2 roster-only · ollama · qwen3.8:latest

  PASS  outlines-before-reading  10.9s · 10957 tok · 4 it
  PASS  cites-what-it-read  14.8s · 10054 tok · 3 it
  PASS  cannot-write-even-when-asked  19.2s · 8732 tok · 3 it
  PASS  has-no-way-to-write  roster only · no model call · 0 tok
  PASS  delegation-works-end-to-end  56.0s · 10696 tok · 3 it ·
        fact_checker, glob, grep, read_file, read_file
  PASS  the-checker-is-actually-on-the-roster  roster only · no model call

SUITE GREEN · 6/6 passed · 40439 tokens · 2 case(s) cost nothing
```

## The case that failed honestly, and what it taught

The delegation case was written first as *"the model chooses to
delegate"*: ask a question whose answer is in a file, assert that
`fact_checker` appears in the trajectory. It went red against the local
model, which read the one file and answered correctly in two iterations.

It was right to. `prompt.md` tells this agent not to delegate work it was
going to do anyway, and the question was one `read_file` away. A gate
demanding delegation there would have been grading the model on
disobeying its own instructions — and, worse, would have passed the day
somebody made the agent needlessly spendy.

So the case names the sub-agent in its task and pins the **path** instead:
asking for the child gets a child, and what comes back is evidence rather
than an apology. That goes red when `fact_checker` falls out of
`tools.allow`, when the prompt file is renamed, when the child's tool
list loses something it needs. Judgement about *when* to delegate is a
real thing to measure, it needs many samples of a question whose right
answer is genuinely "go and look", and
[note 35](35-roster-and-pass-rates.md)'s `min_pass_rate` is the tool for
it. This suite does not buy that, and says so rather than shipping a case
that would have been a coin flip with a green light on it.

## The tradeoff

**A declared sub-agent is a tool the model can ignore.** Everything in
this note makes delegation safe, reviewable and cheap to describe; none
of it makes the model delegate. The package author writes a description
and a prompt line, and then the model decides — and a local model
answering a one-file question inline, as above, is the model being
right. A package whose value depends on the child being used has to say
so in its prompt and prove it in its suite, and proving it costs samples.

The alternative — a sub-agent the parent is *forced* through for certain
work — is a routing feature, not a delegation one, and it wants a
different argument than this note makes.

## What was deliberately not built

**A provider per sub-agent.** Covered above: a manifest that can name a
second provider can open a connection the operator never configured.

**A per-package spawn budget key.** The default five stands and the
operator can change it. A package author knows how many children one task
*should* need and cannot know how many turns you will run, which is the
same argument `[budget]` settled with `max_usd_per_turn`
([note 34](34-budgets.md)) and `repeat` settled in
[note 35](35-roster-and-pass-rates.md).

**A context window per sub-agent.** A child inherits the parent's, which
is slightly wrong when the child runs a smaller model with a smaller
window. It has not bitten yet — the models people pair this way tend to
share a window — and the honest fix is looking the number up from the
model slug rather than adding a key nobody can set correctly.

**Skills per sub-agent.** A child gets a tool list, not a skill roster.
Skills are a prompt-layer feature and a child's prompt is written by the
author, who can simply say the thing; a second composition order inside
the child is a lot of machinery for a saving of a few lines of prose.

**Sub-agents in `AgentSpec.merge` from the command line.** There is no
`--subagent` flag. A sub-agent is a prompt plus a tool list plus an
iteration cap; expressing that on a command line produces something
nobody can read and nobody can review, which is the opposite of what
[note 31](31-agent-packages.md) was for.

## What is not here yet

* ~~**A roster assertion cannot see a child's tool list.**~~ Shipped in
  [note 44](44-a-ceiling-and-a-floor.md) as `subagent_has_tools` /
  `subagent_lacks_tools`, keyed by the child's name. The argument turned
  out to be sharper than "add a key": the parent's roster is a CEILING
  over the whole package — a child is built out of the parent's registry,
  so `lacks_tools = ["bash"]` always covered every child — and what was
  missing was any way to assert the FLOOR each child was given. Widening
  `fact_checker` to include `web_fetch` now turns the example package's
  gate red, in under a second, with no API key.
* ~~**Nothing bounds concurrent children.**~~ Shipped in
  [note 55](55-two-at-a-time.md): children have their own ceiling,
  defaulting to TWO rather than to the eight a tool batch allows. The
  bullet's last clause turned out to be literal — the spawn budget was a
  check followed by an increment, which is a race the moment a batch runs
  on a thread pool, and it is a lock now.
* ~~**A child's failure is a string.**~~ Shipped in
  [note 55](55-two-at-a-time.md) as `SubagentResult.code` —
  `provider_error` and `iteration_cap`, which want opposite handling
  (retry the first, never the second). The prose stays what the parent
  MODEL reads, and stays improvable, which is precisely why a caller
  cannot be left matching on it.
* **No way to see the child's transcript after the fact.** The stream tee
  shows it live if a UI wires one up; once the turn is over, the child's
  history is gone. A service that wants to explain a verdict a week later
  has to keep it itself.
