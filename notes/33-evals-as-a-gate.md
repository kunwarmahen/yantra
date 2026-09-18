# 33 · Evals as a gate — how someone else knows your agent works

[Note 31](31-agent-packages.md) made an agent a directory you can hand to
someone. [Note 32](32-package-tools.md) let that directory bring its own
Python. Put those two together and look at what you are actually asking
of the person on the other end:

> Run this folder. It has a prompt I wrote, a tool list I chose, and code
> that executes as you the moment you start it. It works — I promise.

Every other kind of software answers that with evidence. A library has a
test suite you can run yourself. What an agent package had was the
author's word, and an author's claim that their agent works is worth
about what a model's claim that the tests passed is worth. Builder mode
([notes/18](18-builder-mode.md)) already settled that argument for
generated code: **verify, don't trust** — run the checks yourself, and
believe the exit code rather than the summary. This note points the same
rule at the agent instead of the project.

So a package may carry its own evidence:

```
researcher/
├── agent.toml
├── prompt.md
├── tools/
├── skills/
└── evals/
    ├── cases.toml     the cases, declaratively
    └── graders.py     optional: plain functions, (str) -> bool
```

And one command turns it into a verdict:

```bash
yantra --agent examples/agents/researcher --eval
```

Non-zero on any failure, which is the entire interface CI needs.

## Tests and evals are different things, and this is the eval one

Worth restating because the two words get used interchangeably.
[Note 10](10-evals.md) drew the line: **tests** are binary, free,
deterministic, and run on every commit — they protect *mechanics*, using
scripted providers to prove the loop and the adapters. **Evals** run a
real model over a whole trajectory, cost real money, and come back with
a pass *rate* rather than a pass — they protect *behavior*.

The suite in `evals/` is the second kind. It does not check that
`outline` parses headings correctly; that is a unit test and it lives in
this repo. It checks that *this agent reaches for `outline` when asked
about a long document* — which is a claim about a prompt, a tool
description and a model, all three at once, and which no amount of unit
testing can reach.

That is also why the gate is a command you run, not a hook on every
commit. Probabilistic red does not belong on every push.

## The design problem: TOML cannot hold a function

`EvalCase` — the thing note 10 already built — has two callable fields.
`check_answer` grades the final answer, `setup` wires extra machinery
into the agent. Neither survives a trip through a config file.

The answer is the same shape as the manifest's, which is a good sign:
declarative keys for everything that can be declared, one narrow escape
hatch for the part that genuinely needs code.

```toml
[[case]]
id = "outlines-before-reading"
description = "the prompt says outline first; hold it to that"
user_message = """
Which section of skills/source-brief/SKILL.md covers how confident you
are in an answer? Name the section and the line it starts on.
"""
required_tools = ["outline"]
check = "graders:names_a_line_number"
max_tokens = 30000
max_iterations = 8
```

`check` is `module:function`, resolved against the package's own
`evals/` directory with the by-path loader note 32 built for `tools/`.
Same import discipline, because it is the same trust: nothing lands on
`sys.path`, two packages can both ship a `graders.py`, and the file runs
as you the moment you ask for the suite.

Most cases will not need a grader at all. `required_tools` and
`forbidden_tools` grade the *trajectory* — what the agent actually did —
and that is usually the more useful assertion anyway. A wrong answer is
one bad roll of the dice; an agent that answered without reading anything
is a broken agent that happened to get lucky.

## Three rules that are the whole of the design

**The suite grades THE PACKAGE.** This is the one that took the most
code and is easiest to get silently wrong. `EvalRunner` used to build its
agent by hand — `Agent(provider, model=..., tools=...)` — which has none
of the package's prompt, none of its skills, none of its own tools, and
none of its admission policy. A suite run that way would print a verdict
on an agent nobody ships. So the runner now takes the `AgentSpec`
([notes/31](31-agent-packages.md)) and calls `build()`, exactly as a real
session does, once per case. The ceilings in `agent.toml` are the
ceilings under test: an agent whose manifest says twenty iterations is
graded at twenty.

**A case may not replace the prompt.** `system` is a field on `EvalCase`
and deliberately *not* a key in `cases.toml`. A suite that swapped in its
own system prompt would be grading some other agent and reporting the
result as this one's. What a case gets to vary is the task and the
budget.

**A suite never asks, and a package cannot open its own gate.** Nobody
is sitting in front of an acceptance run, so a permission prompt would
hang it forever. By default the gate auto-approves read-only tools and
refuses everything else; `--yolo` opens it. The package's own
`permissions.mode` is ignored here on purpose — otherwise an author
could ship `mode = "yolo"` and have their own gate graded with the safety
off. Opening it is the operator's decision, made on their command line.

## Failing before the money is spent

A suite is the one place in this harness where a typo has a *price*. Nine
cases against a cloud model is real spend, and discovering on case seven
that `check = "graders:cites_a_sorce"` resolves to nothing means six
cases' worth of tokens bought you a spelling correction.

So everything checkable is checked while the file is being read, before a
single request goes out. The grader module is imported, the function is
looked up, and its signature is bound against one string argument:

```
$ yantra --agent ./researcher --eval
error: .../evals/cases.toml: case 'cites-what-it-read'.check: graders.py
       defines no 'cites_a_url' (it defines: cites_a_file,
       names_a_line_number)
```

Zero tokens. Same principle as `agent.toml` refusing unknown keys
instead of ignoring them: the failures you can find for free, you find
for free.

The unknown-key rule carries over for the same reason it existed there.
A misspelled `forbiden_tools` that gets quietly ignored does not produce
a broken suite — it produces a *green* one, which is strictly worse,
because someone will trust it. An empty `cases.toml` is refused outright
on the same logic: a gate with no cases passes everything forever.

## Reproducibility: whose working directory?

A suite whose verdict depends on which folder the operator was standing
in is not a gate, it is a mood ring. So `--eval` runs with the **package
directory** as the working directory by default, and the researcher's
cases read only files the package itself ships — its own `agent.toml`,
its own `SKILL.md`. The suite answers the same way on your machine as on
mine. `--cwd` points it at a wider tree when a package genuinely needs
one.

## Live receipt

The researcher package, its own three cases, on local hardware. The suite
carries a fourth case as well — it asserts the tool *list* rather than a
behavior, costs nothing to grade, and is argued in
[notes/35](35-roster-and-pass-rates.md) — so a run of it prints one more
row than this receipt:

```
$ yantra --agent examples/agents/researcher --eval \
         --provider ollama --model qwen3.8-64k:latest
eval researcher 0.1.0 · 3 case(s) · ollama · qwen3.8-64k:latest
cwd: /home/…/yantra/examples/agents/researcher
gate: read-only tools only; writes and commands are refused (--yolo opens it)

  PASS  outlines-before-reading  18.6s · 9285 tok · 4 it · glob, outline, read_file
  PASS  cites-what-it-read  13.6s · 7783 tok · 3 it · list_dir, glob, read_file
  PASS  cannot-write-even-when-asked  26.8s · 7776 tok · 3 it · glob, read_file

SUITE GREEN · 3/3 passed · 24844 tokens
```

A 27B model on one desktop, one minute, 25k tokens, no API key. The
`outline` in the first row is the package's own tool from note 32 being
recorded as it executed — which is what makes this a verdict on the
package rather than on the harness.

And red, with a budget tightened to 2000 tokens and a `web_fetch` added
to one case's `required_tools`:

```
  FAIL  outlines-before-reading  25.5s · 6404 tok · 2 it · read_file
        required tool not used: outline
        over token budget: 6404 > 2000
  FAIL  cites-what-it-read  64.5s · 14610 tok · 4 it · list_dir, read_file, …
        required tool not used: web_fetch
  PASS  cannot-write-even-when-asked  80.6s · 13873 tok · 4 it · list_dir, glob

SUITE RED · 1/3 passed · 34887 tokens
$ echo $?
1
```

Note the first row failing *twice*. Failures accumulate rather than
short-circuiting, because "it didn't use outline" and "it cost three
times its budget" are two different bugs and you want both on the first
run ([notes/10](10-evals.md) argued this at length).

## What a trajectory assertion cannot see

This one is worth a section because I got it wrong first and the receipt
corrected me.

The researcher's third case asks the agent, in plain language, to edit
`prompt.md`. I wrote it believing it pinned the *admission policy* — add
`write_file` to `tools.allow` and the suite goes red. So I copied the
package, added `write_file` and `edit_file` to the allow list, ran the
suite with `--yolo`, and watched it come back green.

It was right to. `forbidden_tools` grades what **executed**, and nothing
executed: the model still had `prompt.md` telling it "you cannot modify
anything", so it described the change and declined to make it. The tool
was available and went unused.

That is a real limit, and it is the honest description of what any
trajectory check can offer. The case still earns its place — it pins the
*behavior*, and it would catch a prompt regression that made this agent
start editing people's files. It just does not pin the *configuration*,
and a comment claiming it did would have been the exact kind of decorative
assertion this note is against.

Pinning the configuration takes a different kind of assertion — one about
the tool *list* rather than the trajectory — and
[notes/35](35-roster-and-pass-rates.md) is that assertion. It sits beside
this case in the researcher's suite rather than replacing it, because "it
did not write" and "it cannot write" are two claims and an agent should
have to keep both.

## Fossils close the loop

`case_from_trace(trace_id, reason, message, tokens_used=…)` has been in
`evals.py` since note 10: it turns an observed failure into a regression
case whose budget is the failing run's own spend × 1.5 — the fix must not
cost more than the bug. Until now there was nowhere to *put* the fossil
except a script in this repo.

Now there is. The loop closes end to end: the agent fails in front of a
real user → the failure becomes a `[[case]]` in the package → the
package's own gate stops it coming back → and it travels with the
package to everyone else running it. A service can hand a failed run
straight to `case_from_trace`, which is why a run record carries its
token usage at all.

The last step of that needed one more thing, and where it belongs was
the whole argument. `case_from_trace` returns an `EvalCase` *object*, and
what a host actually has to produce is TOML — which means knowing when a
string needs escaping, when a multi-line literal reads better, and what
happens to a description ending in a quote. Every one of those is a fact
about a format whose reader is a hundred lines up this same file. A host
that wrote its own would be a second implementation of `cases.toml`, and
the two would part company on the first thing that needed quoting.

So `render_case(case)` lives beside `load_cases`, and its tests are
round trips rather than string comparisons: render it, load it back with
the parser that will grade it, compare. A block that *looks* right and
does not parse is the only failure that would actually reach somebody's
package.

It refuses one thing. A case carrying `check_answer` holds a resolved
callable, and a callable cannot be turned back into the
`"graders:name"` reference it was loaded from — so rendering one raises
instead of quietly dropping the assertion. Silently writing out a case
that checks less than its author believed is the failure this entire
format exists against, and it would be a strange place to start making
an exception.

What had nowhere to live, until [note 57](57-a-turn-written-down.md),
was the TRAJECTORY. [Note 42](42-two-runs-of-the-same-suite.md) gave a
suite run a file to be written to, and that file holds results —
verdicts, counts, failure lines, tokens — not the transcripts behind
them. The privacy question attached to keeping transcripts turned out to
be the answer rather than the obstacle: what a recorded turn keeps by
default is its SHAPE — the task, the tool names, the counts — and a
fossil never needed more than that, because a case asserting on the
contents of a file goes red the day somebody edits that file.

## What is not here yet

* ~~**Roster assertions.** Nothing checks the tool *list* — only what
  ran.~~ Shipped as `has_tools` / `lacks_tools`
  ([notes/35](35-roster-and-pass-rates.md)). The section above was the
  evidence that this was a gap and not a taste: a case could not say
  "this agent has no way to write to disk", which is the assertion its
  author actually wanted. It turned out to be cheaper than expected —
  zero tokens, no model, and a case that asserts nothing else needs no
  `user_message` at all.
* ~~**`--async`.**~~ Shipped ([notes/35](35-roster-and-pass-rates.md)),
  with a receipt that argues against using it on local hardware: on one
  desktop GPU the suite got *slower*, because concurrency does not
  create hardware. It is a flag for metered providers.
* ~~**Pass rates over repeated runs.** Each case runs once.~~ Shipped as
  a `min_pass_rate` key and a `--repeat N` flag
  ([notes/35](35-roster-and-pass-rates.md)) — the author declares the
  rate, the operator buys the runs. The caveat below the bullet stood up:
  a flaky case is usually an under-specified case, and the new key makes
  that easier to ignore rather than less true.
* **A judge in `cases.toml`.** `judge()` exists and a grader can call it
  in two lines, which is the right amount of friction: a deterministic
  substring beats an LLM's opinion whenever it will do, and reaching for
  a judge should feel like reaching for something.
* **`yantra eval ./pkg` as a subcommand.** This CLI is flags all the way
  down and a shipped tutorial ([notes/06](06-cli.md)) describes it that
  way. `--eval` composes with `--agent`, `--provider` and `--yolo` for
  free today; subcommands are a 1.0 restructure with the flags aliased.
