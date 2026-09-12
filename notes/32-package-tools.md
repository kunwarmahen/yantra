# 32 · Tools from outside the tree — a package brings its own

[Note 31](31-agent-packages.md) made an agent a directory you can hand to
someone. It left one thing conspicuously undone, and you can state the gap
in a sentence:

> A package could only *narrow* the built-in tools. It could not add one.

`tools.allow` and `tools.deny` pick a subset of sixteen. Which means
"build your own agent with Yantra" meant "choose some of my tools" — a
configurable CLI, not a framework. Your agent could have its own name, its
own prompt, its own skills, its own model, and still not the one thing that
made it worth building: the tool that talks to *your* database, *your*
ticket system, *your* lab instrument.

So a package may now ship code:

```
researcher/
├── agent.toml       [tools] dirs = ["tools"]   ← or just have a tools/
├── prompt.md
├── tools/
│   ├── outline.py   → class Outline(Tool)
│   └── _shared.py   helpers; a leading underscore means "not a tool file"
└── skills/
```

That is the whole feature. The rest of this note is about the four things
that could go quietly wrong, because each of them is a security property
wearing an ergonomics costume — and about one thing this note refuses to
add, which is the part most likely to be argued with.

## The refusal: no `@tool` decorator

Every framework in this space offers this:

```python
@tool                                  # NOT how Yantra works
def search(query: str, limit: int = 10) -> str:
    """Search the archive."""
```

…and generates the JSON Schema from the type hints. It is genuinely
pleasant, and Yantra will not do it, for a reason the harness has been
making since [note 04](04-tools.md): **the schema is the prompt.** The
model does not see your function. It sees this:

```json
{"type": "object",
 "properties": {
   "query": {"type": "string", "description": "Search the archive."},
   "limit": {"type": "integer"}},
 "required": ["query"]}
```

Search *what* archive? Limit in what units, and what happens at the
default? A generated schema can only ever say as much as the signature
said, and a signature says almost nothing. Vague schemas produce vague
tool calls — the model guesses arguments, the tool errors, the loop burns
an iteration, and the author concludes the model is dim.

`tools/base.py` says, in as many words, that writing the schema by hand —
real `description` strings, a `required` list,
`additionalProperties: false` — *is the lesson*. Generating it from type
hints produces exactly the schema that module warns about, and would
delete the most useful thing this harness teaches somebody writing their
first tool. The sugar costs one minute of typing and buys a worse agent.

So a third-party tool is the same `Tool` subclass a built-in is. **Only
discovery is new.** Here is the whole of it, from the worked example
([`examples/agents/researcher/tools/outline.py`](../examples/agents/researcher/tools/outline.py)):

```python
from yantra.tools.base import Tool, ToolContext, require_str
from yantra.tools.fs import resolve_in_sandbox

class Outline(Tool):
    name = "outline"
    description = (
        "List the Markdown headings of a file with their line numbers... "
        "Use to decide WHETHER a document answers a question, and which "
        "part of it to read, before spending a read_file on the whole thing."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string",
                     "description": "Path to a Markdown file, relative to "
                                    "the working directory."},
            "depth": {"type": "integer",
                      "description": "Deepest heading level to include, 1-6. "
                                     "Defaults to 3; use 2 for a long document."},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    read_only = True

    def summary(self, args, ctx):
        return f"outline: {args.get('path', '?')}"

    def run(self, args, ctx):
        path = resolve_in_sandbox(ctx, require_str(args, "path"))
        ...
```

Note the second import. A package tool is not sandboxed away from the
harness — it can use the same helpers the built-ins use, and `outline`
uses the *same* sandbox resolver `read_file` does, so it cannot be talked
into reading `/etc/shadow` either. Writing a tool that respects the
working-directory fence is two lines, and the two lines are copy-paste.

## How a file becomes a tool

`yantra/tools/discover.py`, about a hundred lines of real code:

1. **Which files.** Every `*.py` directly inside the directory, sorted, so
   registration order matches the directory listing. Not recursive. Files
   starting with `_` are skipped — that is how you keep helpers, and
   `__init__.py`, next to your tools.
2. **Loaded by path**, with `importlib.util.spec_from_file_location`, under
   a private module name, and **`sys.path` is never touched**.
3. **Every concrete `Tool` subclass *defined* in the module** is
   instantiated with no arguments and registered.

Step 3's "defined" does real work. A tool file must `import Tool` to
subclass it, so a naive sweep of the module's namespace registers `Tool`
itself — the abstract base, with no `name`, as a tool. The filter is
`value.__module__ == module.__name__`: what this file wrote, not what it
imported. `inspect.isabstract` catches the other half, a shared base class
living in the same file as a concrete tool.

"Instantiated with no arguments" is the one real constraint on a package
tool. Built-ins like `Bash(sandbox)` and `BashStart(jobs)` take their
collaborators from the host; a package tool has no host to ask, so
whatever it needs, it builds or reads inside `run()`. A tool whose
`__init__` demands an argument gets a `ConfigError` that says so, rather
than a `TypeError` from inside importlib.

## Two packages, one filename

This is the reason for "by path, never by `sys.path`":

```
alice/tools/search.py     →  class Search(Tool): name = "archive_search"
bob/tools/search.py       →  class Search(Tool): name = "ticket_search"
```

Both may be loaded in one process. If discovery appended directories to
`sys.path`, the second `search.py` would never load — `sys.modules` would
already hold the first under the name `search` — and Bob's agent would
silently run Alice's code. Worse, an agent package would have quietly
changed what `import search` means for *everything else in the process*,
including its host. An agent package does not get to redefine the host's
imports.

So each directory gets its own private parent module,
`yantra._package_tools.<dirname>_<digest of the path>`, and each file
loads as a child of it. Two side effects worth knowing:

* The same directory loaded twice returns the **same** classes, not a
  second identical set. Two agents built from one package share tool
  classes, as they should.
* The parent is a real package with a `__path__`, so `from . import
  _shared` works inside a tool file and resolves against *that* directory
  only.

## Collisions are loud

`ToolRegistry.register` already refuses a duplicate name; discovery turns
that into an error naming the file:

```
error: ./mypkg/tools/shell.py: tool 'bash' is already registered --
       a package tool may not shadow another tool (rename it, or
       exclude the original with tools.deny)
```

A package tool that silently replaced `bash` would be a security hole
dressed up as a convenience: every permission prompt would still say
`bash`, the operator would still recognize it, and the code behind the
name would be somebody else's. If you genuinely want to replace a
built-in, you say so — `deny = ["bash"]` in your own manifest — and then
your `bash` is the only one, on purpose and in writing.

A file that defines **no** tool is also an error. That sounds pedantic
until you meet the mistake it catches: a class that forgot to subclass
`Tool`, or a typo in the import. The alternative is an agent that quietly
lacks the tool its author is certain they shipped.

## The admission policy still governs — including your own code

A package tool goes through `register()` like everything else, and the
admission policy was installed before anything registered
([note 31](31-agent-packages.md)). So:

```toml
[tools]
allow = ["read_file", "glob", "grep"]     # your own tool is not in here
```

…means your own tool does not load into the agent. Not as a puzzle —
startup says so:

```
agent: researcher 0.1.0 -- examples/agents/researcher
package excludes: outline
```

This is deliberate and it is the interesting half of the design. A
complete whitelist is a *statement about what this agent may do*, and a
package that could smuggle its own code past its own declared tool list
would make that statement worthless to the person reading it. The rule
"the manifest is the truth about this agent's tools" only holds if the
manifest also binds the author.

The other direction is reported too, for the same reason in reverse:

```
package tools: outline
```

Loading that file **ran somebody's Python on your machine**. You are
entitled to see that it happened, and what it added.

## `read_only` is a declaration, and nothing can check it

`read_only = True` makes a tool auto-approve under the default gate — no
prompt, ever. For built-ins that flag was reviewed by whoever reviewed
this repo. For a package tool, it is the author's word.

An author who writes `read_only = True` on a tool that deletes files has
lied to the permission gate, and **the gate has no way to know.** There is
no static analysis here that would not be trivially defeated by
`getattr(os, "rem" + "ove")`. So the honest thing is to say it plainly
rather than imply a check exists.

Which is the same trust you extend to every dependency you install. The
mitigation is not a clever fence, it is the ordinary one: read the
`tools/` directory of a package before you run it. It is a folder of small
Python files with hand-written schemas — that is the *other* reason the
format has no code generation. A package you can review is a package you
can review.

## The security paragraph

The one part of this note that constrains what gets built next.

**Loading `tools/*.py` is arbitrary code execution as the invoking user,
at import time, before any permission gate exists.** That is fine and
normal for a package *you* chose to run — it is exactly what `pip install`
does. It stops being fine the moment a package path can come from
somewhere other than a human's hands:

1. A package path comes from **the operator's command line**, or from a
   root the owner configured. Never from a trigger payload, a chat
   message, a webhook body, or a string a model produced.
2. A service (the always-on `dvara` sitting behind this) resolves packages
   from an **owner-controlled directory only**. A Telegram message that
   can name a package path is remote code execution with the harness's own
   credentials behind it. Write it down now, while it costs nothing;
   discover it later and it costs everything.
3. Loading a package **is not sandboxed and will not pretend to be**. The
   sandbox ([note 16](16-sandboxing.md)) confines the *agent's* tool calls.
   It has nothing to say about a module's import-time side effects.

One design consequence follows immediately, and it is why the code is
split the way it is: **`load_package` never imports anything.** Reading a
manifest is a pure read — `tools/` is *named* by `package.py` and *loaded*
by `spec.build()`. Anything that wants to inspect a package (a listing, a
registry UI, a service deciding whether to offer it) can parse every
manifest it can see without executing a line of anyone's Python. Code runs
at the moment a human asked for this agent, and not before.

## The format

```toml
[tools]
dirs = ["tools"]        # optional: ./tools is picked up when it exists
```

Paths are relative to the package root, a declared one must exist, and
several are allowed (`dirs = ["tools", "../shared-tools"]`). Same
convention-with-an-escape-hatch as `prompt.md` and `skills/`: the common
package declares nothing at all.

Embedding, for hosts that build their own registry rather than a package:

```python
from yantra import discover_tools
from yantra.tools import default_registry, register_tool_dirs

registry = default_registry()
register_tool_dirs(registry, ["./my-tools"])       # honors the policy
tools = discover_tools("./my-tools")               # or just instantiate them
```

## What the tests pin

[`tests/test_tool_dirs.py`](../tests/test_tool_dirs.py), 24 tests:

* a package tool registers and runs, and the imported `Tool` base and an
  abstract shared base are *not* registered;
* `_shared.py` is skipped as a tool file and still importable as
  `from . import _shared` — with the directory never on `sys.path` and
  `_shared` never in global `sys.modules`;
* two packages both shipping `tools/search.py` coexist, each running its
  own code, and the same directory loaded twice yields the same classes;
* a package tool named `bash` raises, and the real `bash` survives;
* an import error, a file defining no tool, a tool needing constructor
  arguments, a tool with no `name`, and a missing directory each raise with
  the file named;
* `load_package` on a package whose `tools/bomb.py` raises at import
  **succeeds** — only `build()` explodes;
* the admission policy refuses a package tool the manifest did not allow,
  and it lands in `refused_names()`;
* `read_only` reaches the gate: through a real agent loop under
  `allow_read_only`, the tool declaring `True` runs and the one declaring
  `False` is denied as data.

## Live receipt

The researcher package, its own tool, and a local model:

```
$ yantra --agent examples/agents/researcher --provider ollama \
         --model qwen3.8:latest "outline notes/30-skills.md at depth 1"
agent: researcher 0.1.0 -- examples/agents/researcher
package tools: outline
skills: 4 loaded -- new-tool, notes-entry, repo-survey, source-brief

→ outline()
╭─ outline() ───────────────────────────────────────────────────╮
│ {"path": "notes/30-skills.md", "depth": 1}                    │
│                                                               │
│ 30 · Skills — teaching the agent your procedures  (line 1)    │
╰───────────────────────────────────────────────────────────────╯
── end_turn · 2427 in / 61 out · 2 iteration(s)
```

No `--yolo`, and no permission prompt: `outline` declared `read_only`, and
the gate believed it. That one line is the whole trust model of package
tools, working exactly as designed and exactly as far as the author's
honesty goes.

## What is not here yet

* **pip-installable tool packs** via `[project.entry-points."yantra.tools"]`
  and `importlib.metadata` — about twenty lines, and the way a tool reaches
  people who are not sharing a directory with you. Left out until somebody
  wants it: a folder and a git URL is still enough.
* **Recursive discovery.** `tools/a/b/c.py` is not loaded. Flat is legible,
  and a tool directory large enough to need subfolders is probably a
  library that should be `pip install`ed and imported.
* **Hot reload.** A tool file edited mid-session is not re-read. The module
  cache is what makes "same package, same classes" true, and giving that up
  for an editing convenience is a bad trade.
