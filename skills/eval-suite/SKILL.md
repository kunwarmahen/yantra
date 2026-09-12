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
argues this surface.

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
```

Reach for `required_tools` / `forbidden_tools` first. They grade the
TRAJECTORY -- what the agent actually did -- which is usually the more
useful assertion: a wrong answer is one unlucky roll of the dice, but an
agent that answered without reading anything is broken and got lucky.

Keys that do NOT exist here, and why: `system` (the package's own prompt
is the thing under test -- a case that replaced it would grade some other
agent), `setup` (per-case Python wiring is a library feature, not a
package one). Unknown keys are errors, so a typo can never quietly turn
an assertion into decoration.

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

## The limit to remember

`forbidden_tools` grades what EXECUTED, not what was AVAILABLE. Adding
`write_file` to a package's `tools.allow` does not necessarily turn a
"cannot write" case red -- if the prompt still tells the model it cannot
modify anything, the model will go along with it and the tool sits there
unused. These cases pin BEHAVIOR, not CONFIGURATION. For configuration,
read `agent.toml` -- `tools.allow` is a complete whitelist and says so
plainly.

## Before you say you are done

* Run the suite for real and paste what came back -- a receipt beats a
  claim about what should happen. Against Ollama when you can: it is free
  and roughly half this project's readers run local models.
* Green once is not green. Evals are probabilistic; a case that passes
  four times out of five is an under-specified case, not a flaky model.
* If you added a case for a bug, confirm it goes RED against the broken
  version before you call it a regression test.
