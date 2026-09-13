# 31 · Agent packages — an agent you can hand to someone

Everything up to here builds *a* harness. This note is where the harness
starts building *other people's agents*.

The gap is smaller than it sounds and more awkward than it looks. All the
machinery already exists — the loop, tools, skills, permissions, MCP,
sandboxing. What did not exist was **an artifact you can name, version,
share, or point anything at**. An agent was a particular arrangement of
command-line flags, living in one person's shell history.

So an agent becomes a directory:

```
researcher/
├── agent.toml      identity · model · tools · skills · servers · policy
├── prompt.md       the system prompt
├── tools/          your own Tool subclasses (notes/32)
├── skills/         procedures this agent knows (notes/30)
└── evals/          how you know it still works ([notes/33](33-evals-as-a-gate.md))
```

And the thing to clear up in the first paragraph, because everybody has
this confusion exactly once:

> A **skill** is a procedure an agent may load. A **package** is the whole
> agent. Packages contain skills. Never the reverse.

## The smallest package that works

```toml
[agent]
name = "tiny"
```

That is a complete, runnable agent package. So is an empty `agent.toml` —
the directory's own name fills in. Everything is optional, which is a
design decision doing real work; see "Why every field is optional" below.

Run one:

```bash
yantra --agent ./researcher "what changed in notes/30 recently?"
yantra --agent ./researcher --provider ollama    # against your own hardware
cd researcher && yantra                          # ./agent.toml is found
```

## The whole format

```toml
[agent]
name        = "researcher"
description = "Reads sources and answers with citations."
version     = "0.1.0"
prompt      = "prompt.md"       # optional: prompt.md is found anyway

[model]
provider       = "anthropic"    # omit to use whatever key you have
model          = "claude-sonnet-5"
max_tokens     = 16384
max_iterations = 20
context_window = 180000
cache          = true

[tools]
allow    = ["read_file", "glob", "grep", "web_fetch", "load_skill"]
deny     = ["browser_*"]
per_turn = 6                    # tool selection width (notes/17)
dirs     = ["tools"]            # your own Tool subclasses (notes/32)

[skills]
dirs     = ["skills"]           # optional: skills/ is found anyway
disabled = ["deploy-*"]
enabled  = true

[[mcp]]
name    = "docs"
url     = "https://example.invalid/mcp"
headers = { Authorization = "Bearer ${DOCS_TOKEN}" }

[permissions]
mode = "ask"                    # ask | yolo

[env]
context = "local"               # off | local | full  (notes/29)
```

TOML because `tomllib` has been in the standard library since 3.11. A
package format that dragged in a YAML parser would have been the first
crack in a project whose runtime dependencies are `httpx` and `rich`.

## Why every field is optional

Not minimalism for its own sake. It is what keeps a package **portable**,
and the resolution order is the mechanism:

```
command line   >   agent.toml   >   environment   >   built-in default
```

Consider the alternative. You write a package against Anthropic and put
`model = "claude-sonnet-5"` in it. I run a local model on a box in my
study. If your package's model were the final word, your agent would be
unrunnable for me — I would have to fork your file and maintain a diff
forever, to change a string.

Instead I run `yantra --agent ./researcher --provider ollama`, and my
`OLLAMA_MODEL` answers. Your package still says what *you* built it
against; my command line says what *I* have. Nobody edits anybody's file.

That is also why an `AgentSpec` records what was **asked for** rather than
what this machine happens to have: the environment and the built-in
defaults are consulted inside `build()`, at the last possible moment, so
the same spec is portable across machines that have different things in
them.

## Unknown keys are errors

```
$ yantra --agent ./broken
error: ./broken/agent.toml: unknown key(s) in [tools]: alow
       (known: allow, deny, dirs, per_turn)
```

A typo'd key that is quietly ignored is how a package comes to
"configure" something that never happens. The worst version of that is
specific enough to be worth naming: a misspelled `deny` leaving `bash`
armed in an agent whose author believes they switched it off. Every table
and key is checked against the known set, and the error names the file so
you can go fix it.

The same bias runs through the rest of the loader. A *declared* path must
exist (`prompt = "persona.md"` with no such file is a typo, not a
preference), while a *conventional* one is used only if it happens to be
there. Types are checked. Enums are checked at load, not at use.

## Admission, not removal

`[tools] allow` and `deny` were the one part of this that turned out to
have a wrong version and a right version, and the wrong version looked
fine.

The wrong version: build the default registry, then unregister what the
package excluded. It works — for about four lines. Then the host registers
`ask_user` after construction, and skills register `load_skill`, and the
MCP block registers `mcp__docs__search`, all of them *after* the sweep
already ran. A package that said it does not get `bash` would still be
handed three tools it never approved.

So the registry carries a **standing admission policy** instead. `register`
consults it. There are now three distinct things, and the distinction is
worth holding on to:

| | what it does | when |
|---|---|---|
| `unregister` | removes what is present | now |
| `disable` | hides a registered tool, reversibly | mid-session, operator |
| `admit_only` | governs what may ever be registered | now **and later** |

Patterns are `fnmatch`, the same as `$YANTRA_DISABLED_TOOLS`, so
`browser_*` and `mcp__slack__*` work the way you would guess. And a
non-`None` `allow` is a **complete** whitelist, MCP tools included — a
package that both narrows its tools and declares a server has to say
`mcp__*` if it wants them. Guessing either way would be wrong half the
time.

Refusals are silent to the *model* and loud to the *operator*: hosts
register `ask_user` unconditionally and a package that excluded it wants
it absent rather than a crash, so the names are recorded and printed.
"Why is there no bash" always has an answer on screen.

## The incoherence the loader refuses

One case earned a hard error rather than a warning:

```
error: ./researcher/agent.toml: this package declares skills but
       tools.allow excludes load_skill, so nothing could load them;
       add "load_skill" to tools.allow (or drop the skills)
```

A package that ships `skills/` and then excludes the tool that opens them
would compose a roster of skills into the system prompt with no way to
reach any of them — advertising capabilities the model does not have,
which is a reliable way to make a model invent one. The manifest is the
only place worth saying this: by the time an agent exists, all anyone can
see is a list that does not work.

Two notes on the neighbourhood. `run_skill` is `load_skill`'s sibling, for
skills that run in a sub-agent instead of inline — a narrow `allow` needs
both. And a package's skills are **additive** to the project's: point a
package at a repo that has its own `skills/` and the agent gets the union,
which is what you want from something shareable. `[skills] disabled`
prunes.

## AgentSpec: the composer that owns assembly order

`Agent.__init__` takes seventeen keyword arguments, and knowing all
seventeen is still not enough, because the assembly has an **order**:

* the operator's system prompt must be captured as the `base` layer before
  `env_context` appends a fact sheet to it (notes/29);
* skills register before the tool catalog is built, or a pinned tool does
  not exist yet to be pinned (notes/17);
* the admission policy goes on before anything is registered, or an
  excluded tool is briefly present and reachable.

That order lived in exactly one place — `cli/main.py` — which was fine
right until something other than the CLI wanted an agent. Now it lives in
`AgentSpec.build()`, and the CLI is one of its callers.

This is the same move `SystemPrompt` made in notes/29: **the primitive
stays dumb and a composer owns assembly.** `Agent.__init__` did not change
at all.

`prompt.md` arrives as a new prompt layer, and the declared order grew to
match:

```python
LAYER_ORDER = ("agent", "base", "env", "skills")
```

`agent` renders first, `base` — the operator's `--system` — second. That
is the composition an operator expects: the package says what the agent
*is*, and whatever they typed refines it rather than being buried under
it.

### What build deliberately does not do

Three things a host keeps, because each needs state a spec cannot carry:

* **MCP connections.** `spec.mcp` carries the configs; opening them needs
  a live `MCPManager` whose sessions outlive the build and get closed on
  every exit path.
* **Tool selection.** `spec.tools_per_turn` carries the width, but the
  catalog must be built after *every* tool is registered — including MCP
  tools, which arrive after the build — so the host decides when.
* **The permission gate.** Passed in, because what asks a human depends
  entirely on which human: a terminal `y/n/e`, a browser modal, a policy
  file with nobody home. `permissions_mode` says only which mode the
  host's gate should start in — and `--yolo` wins the merge if given, so
  one attribute answers for both.

### One more thing build does not do

`env_context = None` means **do not attach**, which is deliberately not
the same as `"off"`. At `"full"`, attaching costs a network call for the
geo lookup. A library that builds an agent must not reach the network
because it forgot to say no. The CLI always passes a level; a bare
`AgentSpec().build()` stays offline.

## Merging, and the asymmetry in it

`merge` means "the other spec wins wherever it said something", and "said
something" means "differs from the field's default". That is the entire
reason unset is `None` rather than a real value — a host can merge its
flags in unconditionally and an all-default override is a no-op.

The consequence is worth stating out loud, because it is a restriction
people will meet: **an override cannot clear a value back to empty.**
`tool_deny = []` on the command line does not undo a package's deny list.
Lifting a restriction takes an explicit flag, not an empty one. A CLI that
passed `[]` for every unmentioned list would silently disarm every package
it ran, and that failure would be invisible.

## What the tests pin

`tests/test_spec.py` (28) and `tests/test_package.py` (42):

* an override wins only where it said something, empty lists cannot clear
  a restriction, and merging mutates neither operand;
* the admission policy **still applies after build** — the test registers a
  tool afterwards and watches it get refused, which is the whole reason
  the policy exists;
* `allow` is a whitelist, `deny` takes patterns, no policy admits
  everything;
* the `agent` layer renders before the operator's `base`, and no prompt at
  all still sends `system=None` — byte-identical to the pre-package wire
  shape;
* skills are skipped when `load_skill` is excluded, and a *package* that
  does that is rejected outright;
* unknown tables, unknown keys, wrong types, invalid TOML and bad enums
  all fail with the file named;
* `${VAR}` in an MCP header stays unexpanded on disk — a package is a file
  people commit, and it must be impossible to do that correctly and still
  leak a token;
* through the CLI: the package configures the session, a flag overrides
  the package, `./agent.toml` is found without a flag, nothing is searched
  for *upward*, and a broken manifest exits 2.

## Live receipt

```
$ yantra --agent examples/agents/researcher --provider ollama --model nope "hi"
agent: researcher 0.1.0 -- examples/agents/researcher
skills: 4 loaded -- new-tool, notes-entry, repo-survey, source-brief

404: not_found_error: model 'nope' not found
```

Four skills from a package that ships one: its own `source-brief` plus the
repo's three, the union being the point. The 404 is the receipt that
everything before the model call was already wired — package found, tools
admitted, prompt composed, skills attached — and that the model slug is
the only thing this invocation got wrong.

```
$ yantra --agent ./broken
error: ./broken/agent.toml: unknown key(s) in [tools]: alow
       (known: allow, deny, dirs, per_turn)
```

## What is not here yet

Named, so the format's refusals are as legible as its features:

* ~~**`tools.dirs`**~~ — shipped in [notes/32](32-package-tools.md): your
  own `Tool` subclasses, loaded from the package rather than from this
  tree. Schemas stayed hand-written (notes/04 explains why at length);
  only discovery was new.
* ~~**`evals/`**~~ — shipped in [notes/33](33-evals-as-a-gate.md): the
  package carries the evidence that it works, and `--eval` turns it into
  an exit code. The format is this one's shape again — declarative keys,
  one narrow hatch to Python, unknown keys refused.
* ~~**`[budget]`**~~ — shipped in [notes/34](34-budgets.md) as
  `max_usd_per_turn`, once there was a loop hook to make it bite. Per
  TURN rather than per run, because a turn is the only unit a package
  author can honestly estimate; a stop rather than a cap, because the
  price of a model call is knowable only after making it.
* **Package registries, publishing, `extends`, version constraints.** A
  package is a folder and a git URL. That is enough for now.
* **`[[subagent]]`** — declarable sub-agents. `subagent.py` exists and this
  is tempting; deferring it may well turn out to be the wrong call.
