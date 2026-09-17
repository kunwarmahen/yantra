---
name: eval-suite
description: Write and run an agent package's eval suite -- evals/cases.toml,
  graders, the --eval gate and what its output means. Use when asked to add
  evals to an agent, check whether a package still works, or make an agent's
  behavior hold in CI.
allowed-tools: read_file, write_file, edit_file, glob, grep, bash
---

# Evals for an agent package

An eval suite answers one question: **does this agent still do what its
author said it does?** You write down the behaviors you expect, they run
against a real model, and the exit code is the verdict.

This is not the same thing as the test suite. Tests are free,
deterministic, and run on every commit -- they prove the MACHINERY works
(`uv run pytest`). Evals cost money, come back probabilistic, and prove
the BEHAVIOR is still there. Run them before sharing a package, after
editing a prompt, and before switching models -- not on every push.
`notes/10-evals.md` argues the split; `notes/33-evals-as-a-gate.md`
argues this surface, `notes/35-roster-and-pass-rates.md` argues the two
assertions that came after it, and `notes/41-a-gate-you-can-point.md`
argues running it without a key, aiming it at one case, and putting the
package's own servers under it.

Not on every push -- with one exception. A run of nothing but roster
cases needs no key and spends nothing, so that half IS affordable on
every push: `--eval --case "<your roster case ids>"`.

## Run one that already exists

```bash
uv run yantra --agent examples/agents/researcher --eval                  # cloud key
uv run yantra --agent examples/agents/researcher --eval --provider ollama  # local
```

The package is named the usual way, so `cd researcher && yantra --eval`
works too -- `./agent.toml` is found without a flag.

## The layout

One file, in a folder beside `agent.toml`:

```
my-agent/
|-- agent.toml
|-- prompt.md
`-- evals/
    |-- cases.toml    the cases
    `-- graders.py    optional, only if you grade answer TEXT
```

The smallest suite that does something is three lines:

```toml
[[case]]
id = "reads-before-answering"
user_message = "What is this agent called? Check its config."
required_tools = ["read_file"]
```

`[[case]]` with double brackets is TOML for "another item in a list" --
repeat the block per behavior. `id` and `user_message` are the only
required keys.

## What a case may assert

```toml
[[case]]
id = "outlines-before-reading"
description = "the prompt says outline first; hold it to that"
user_message = "Which section of SKILL.md covers confidence, and on what line?"

required_tools  = ["outline"]              # must have RUN
forbidden_tools = ["bash", "write_file"]   # must not have
max_tokens      = 30000                    # cost ceiling
max_iterations  = 8                        # model round-trip ceiling
check           = "graders:names_a_line_number"   # optional, see below
min_pass_rate   = 0.7                      # "holds 7 runs in 10" -- see --repeat
```

Reach for `required_tools` / `forbidden_tools` first. They grade the
TRAJECTORY -- what the agent actually did -- which is usually the more
useful assertion: a wrong answer is one unlucky roll of the dice, but an
agent that answered without reading anything is broken and got lucky.

Keys that do NOT exist here, and why: `system` (the package's own prompt
is the thing under test -- a case that replaced it would grade some other
agent), `setup` (per-case Python wiring is a library feature, not a
package one), `repeat` (how many times the gate runs is paid for by
whoever runs it -- declare `min_pass_rate` and let them pass `--repeat N`).
Unknown keys are errors, so a typo can never quietly turn an assertion
into decoration.

## Asserting the tool LIST, for free

The keys above all need a run. These two do not:

```toml
[[case]]
id = "has-no-way-to-write"
description = "not 'it did not write' -- 'it cannot'"
lacks_tools = ["write_file", "edit_file", "bash", "browser_*"]
has_tools   = ["read_file", "glob", "outline"]
```

`has_tools` / `lacks_tools` grade the ROSTER: the tools the agent is
offered at all. Note what is missing from that case -- there is no
`user_message`, because there is nothing for a model to do. It costs zero
tokens and finishes in milliseconds, which makes it the one kind of eval
case a gate can afford to run on every push.

Use it for the claim `forbidden_tools` cannot make. A tool that was
available and went unused looks exactly like a tool that was never there,
so "it did not write" and "it cannot write" are two separate assertions
and a package usually wants both. Two rules worth knowing:

* **Patterns work here and nowhere else.** `lacks_tools = ["browser_*"]`
  is fnmatch, like `tools.allow`, because a roster is a set. A pattern in
  `required_tools` / `forbidden_tools` is refused at load time -- those
  grade a list of names that executed, so `write_*` would match nothing
  and pass silently.
* **A roster failure stops the case.** No request goes out, and nothing is
  spent: an agent with the wrong tool list is not the agent the case
  describes, so its trajectory would be about something else.

MCP tools ARE visible here: `--eval` starts the servers your manifest
declares, so `has_tools = ["mcp__docs__*"]` grades something real. A
declared server the gate cannot reach exits 2 rather than grading an
agent smaller than the one you ship; `--no-mcp` skips them for offline
CI and says loudly that it did.

What a roster check still cannot see: anything a prompt talks the model
into or out of -- that is what the trajectory keys are for.

## Asserting a DECLARED SUB-AGENT's list, also for free

The roster above is a CEILING over the whole package. A child declared
with `[[subagent]]` is built out of the parent's registry and cannot
exceed it, so `lacks_tools = ["bash"]` already covers every child. What
it cannot say is that a child was kept deliberately NARROWER than the
package around it -- and that is the line an author moves by accident:

```toml
[[case]]
id = "the-checker-stays-off-the-network"
description = "fact_checker reads what is here; it does not go and find more"
has_tools = ["fact_checker"]
subagent_has_tools   = { fact_checker = ["read_file", "outline"] }
subagent_lacks_tools = { "*" = ["web_*", "write_file", "edit_file", "bash"] }
```

Free, like its neighbours above: no `user_message`, no model, no key.
Three rules:

* **The key is a NAME, or `"*"` for every child the package declares.** A
  pattern key is refused at load time. `"*"` is the one worth reaching
  for, because it covers the sub-agent somebody adds next year without
  anybody remembering to update the case.
* **Naming a child that is not declared is a FAILURE.** The tempting
  reading -- a child that does not exist cannot use `web_fetch`, so the
  claim holds -- is a green case reporting on a typo. Rename a sub-agent
  and its assertions go red until you update them, which is the point.
* **Tool patterns work in the values,** exactly as in `lacks_tools`.

What it grades is the DECLARATION -- what a child WOULD be offered -- not
a child that ran. For that, put `forbidden_tools` on the case that
actually delegates. Reasoning in `notes/44-a-ceiling-and-a-floor.md`.

## Running a case more than once

A trajectory is a die roll. `--repeat N` runs every case N times and
judges it on the pass RATE:

```bash
uv run yantra --agent ./my-agent --eval --repeat 5
uv run yantra --agent ./my-agent --eval --repeat 10 --async 4   # concurrently
```

A case passes when its passes reach `min_pass_rate` of its runs, rounded
UP (0.7 of ten runs is seven, not six). The default is 1.0 -- every run
must pass -- so one run at the default is exactly the old behaviour.

Read the rate as a description, not a target. A case that passes seven
times in ten is sometimes a genuinely probabilistic behavior and is more
often a case whose instruction left the model two reasonable routes.
Lowering the threshold until a suite goes green is how a real regression
gets waved through; if you are tuning it downward, edit the case instead.

Roster-only cases are never repeated -- they reach no model, so n runs
would be n copies of one fact. And a run whose selected cases are ALL
roster ones resolves no provider at all: no key, no local server, zero
tokens and zero setup.

`--repeat` applies to every case, which is expensive when one case is
probabilistic and twenty are not. Point it:

```bash
uv run yantra --agent ./my-agent --eval --case "chooses-the-*" --repeat 10
```

`--case PATTERN` is fnmatch against case ids and repeatable. A filtered
run reports as **SUBSET GREEN**, never SUITE GREEN -- it is a claim about
the cases that ran, and it is not your package's gate.

## Comparing two runs

`--report FILE` writes a run down; `--against FILE` compares this one to
it. Useful the moment you are choosing between models:

```bash
uv run yantra --agent ./my-agent --eval --model qwen3.8:latest --report qwen.json
uv run yantra --agent ./my-agent --eval --model gemma4:12b --against qwen.json
```

The comparison is a report, not a gate: it changes no verdict and no exit
code. Read it as counts (`7/10 → 6/10`), not percentages, and expect
`added`/`gone` lines whenever the two runs graded different case sets --
which `--case` guarantees. `notes/42-two-runs-of-the-same-suite.md` has
the reasoning.

## Graders: checking the answer text

Only when a trajectory check will not do. Plain functions, one argument,
True or False:

```python
# evals/graders.py
def cites_a_file(answer):
    return "agent.toml" in answer.lower()
```

```toml
check = "graders:cites_a_file"
```

`graders` is the filename without `.py`; `cites_a_file` is the function.
Both are resolved when the file is READ, so a typo costs zero tokens:

```
error: .../evals/cases.toml: case 'cites-what-it-read'.check: graders.py
       defines no 'cites_a_url' (it defines: cites_a_file, names_a_line_number)
```

Keep them dumb. A grader decides whether other code may ship, so it
should be the kind of thing you can be sure of by reading it once. Grade
the SHAPE of an answer (a source is named, a line is given) rather than
exact prose, or the suite goes red every time a heading is renamed. For
genuinely subjective criteria, call `judge()` from `yantra.evals` inside
a grader -- and check first whether a substring would have done.

## Reading the output

```
  PASS  outlines-before-reading  18.6s · 9285 tok · 4 it · glob, outline, read_file
  FAIL  cites-what-it-read  25.5s · 6404 tok · 2 it · read_file
        required tool not used: web_fetch
        over token budget: 6404 > 2000

SUITE RED · 1/2 passed · 34887 tokens
```

Wall clock, tokens, model round-trips, then **the tools that actually
executed**. Failures accumulate rather than stopping at the first --
"it didn't use web_fetch" and "it cost three times its budget" are two
different bugs and you want both on one run.

Exit codes: `0` green, `1` red, `2` the suite itself is broken (bad TOML,
unresolvable grader, no suite there). So CI is one line:

```bash
uv run yantra --agent ./my-agent --eval || exit 1
```

## Flags worth knowing

| flag | effect |
| --- | --- |
| `--agent DIR` | which package (or `cd` in and omit it) |
| `--provider ollama` | local model instead of a cloud key; `--model TAG` picks one |
| `--yolo` | let the suite write files and run commands |
| `--cwd DIR` | run the cases somewhere other than the package folder |
| `--repeat N` | run every case N times, judge it on the pass rate |
| `--case PATTERN` | run only matching case ids (fnmatch, repeatable); reports as a SUBSET |
| `--async N` | N trajectories at once (default 4); identical grading |
| `--no-mcp` | do not start the package's declared servers (their tools are then absent) |
| `--report FILE` | write this run as JSON, green or red |
| `--against FILE` | print what moved since an earlier report; changes no verdict |

**About `--yolo`.** By default the suite auto-approves read-only tools
and REFUSES everything that writes or executes, because nobody is sitting
in front of an acceptance run to answer a prompt. If your agent genuinely
needs to write to do its job, its cases fail until you pass `--yolo`.
A package's own `permissions.mode` is ignored here on purpose -- opening
the gate is the operator's call, not the author's.

**About `--cwd`.** Cases run with the package directory as the working
directory, so a suite that reads files the package SHIPS answers the same
way on every machine. Point it wider only when the agent's job genuinely
needs a larger tree.

## Writing good cases

* **Read only what the package ships.** A case that reads `../../README.md`
  makes the verdict depend on the tree it was run in.
* **Set budgets from an observed run,** not from a guess. Run the case
  once, look at the token count, and leave headroom. That is what
  `case_from_trace()` does automatically for regressions (spend x 1.5).
* **Turn real failures into cases.** When the agent fails in front of
  someone, that failure becomes a `[[case]]` and the gate stops it coming
  back -- and it travels with the package to everyone else running it.
* **One behavior per case.** A case that asserts five things tells you
  five things; a case that asserts one thing badly tells you nothing.

## When the package has a `[budget]`

A ceiling in `agent.toml` applies to eval cases too -- each case is one
turn against a fresh agent built from the same spec. So a case whose
trajectory costs more than `max_usd_per_turn` does not fail its grader;
it never reaches one, and the suite reports it as a crash naming the
dollars:

```
FAIL  reads-before-answering
      crashed: RuntimeError: turn ended without a response (over_budget
      after 2 iterations): spent ~$0.6000 of the $0.50 ceiling for this turn
```

That is a real result and usually a real finding -- the agent took a more
expensive route than its author budgeted for. But check which thing broke
before editing the case: raising `max_usd_per_turn` to make a suite green
is how a cost regression gets waved through.

Against a local model the ceiling is inert (nothing is billed), so a
suite that runs green on Ollama tells you nothing about whether it fits
the package's budget on a metered one.

The heads-up that normally precedes a budget stop does not reach the
suite. It is advice to whoever is watching a turn, and nobody is watching
an eval case -- what lands in the report is the crash above. To find out
whether a case is running close to the ceiling rather than over it, run
that one prompt by hand with `--max-usd` and read the `· budget:` line.

**About `--async`.** Worth it against a metered provider, where the
concurrency belongs to somebody else's fleet. Close to a wash against one
local model on one GPU -- the card was the bottleneck, not the client --
and lines then land in completion order, which is harder to read. Measured
both ways in `notes/35-roster-and-pass-rates.md`.

## The limit to remember

`forbidden_tools` grades what EXECUTED, not what was AVAILABLE. Adding
`write_file` to a package's `tools.allow` does not necessarily turn a
"cannot write" case red -- if the prompt still tells the model it cannot
modify anything, the model will go along with it and the tool sits there
unused. Those cases pin BEHAVIOR.

For CONFIGURATION, write the roster assertion beside it: `lacks_tools =
["write_file"]` goes red the moment the key appears in `tools.allow`, with
no model involved. Keep both -- "it did not write" and "it cannot write"
are two claims, and a prompt regression breaks the first while a manifest
edit breaks the second.

The same split applies one level down. `subagent_lacks_tools` goes red
when a child's `tools` list grows; the delegation case's
`forbidden_tools` goes red when a child USES something it should not.

## Before you say you are done

* Run the suite for real and paste what came back -- a receipt beats a
  claim about what should happen. Against Ollama when you can: it is free
  and roughly half this project's readers run local models.
* Green once is not green. Evals are probabilistic, so confirm a new case
  with `--repeat 5` before trusting it. If it comes back four out of five,
  that is usually an under-specified case rather than a flaky model --
  rewrite the case before reaching for `min_pass_rate`.
* If you added a case for a bug, confirm it goes RED against the broken
  version before you call it a regression test.
