# 53 · A tool that arrives by pip

[notes/32](32-package-tools.md) let a package ship its own tools:

```
researcher/
|-- agent.toml
`-- tools/
    `-- outline.py   -> class Outline(Tool)
```

That works for anyone holding the directory, which is the limit the note
wrote down at the time:

> **pip-installable tool packs** via
> `[project.entry-points."yantra.tools"]` and `importlib.metadata` —
> about twenty lines, and the way a tool reaches people who are not
> sharing a directory with you.

A tool worth writing is usually worth writing once. A `tides` tool, a
wrapper around your company's ticket system, a client for an internal
service — none of those belong inside one agent package, and copying
`tools/tides.py` between six repositories is how six copies drift.

## The publishing side

A distribution says what it offers in its own `pyproject.toml`:

```toml
[project]
name = "tide-pack"

[project.entry-points."yantra.tools"]
tides = "tidepack.tools"
```

`pip install tide-pack`, and then:

```
$ yantra --provider ollama --tool-pack tide-pack "What are the tides at Whitby today?"
→ tides()
╭─ tides() ───────────────────────────────────────────────╮
│ { "harbour": "Whitby" }                                 │
│ Whitby: high 06:14, low 12:38                           │
╰─────────────────────────────────────────────────────────╯

Tides at Whitby today:
- **High tide:** 06:14
- **Low tide:** 12:38
```

A real run, against `qwen3.8:27b` on a local server, with a pack built
and installed for the purpose. In a package it is one line instead of a
flag:

```toml
[tools]
packs = ["tide-pack"]
```

## Named, never ambient

The obvious implementation loads *every* installed distribution that
publishes the group. `importlib.metadata.entry_points(group=...)` hands
them over; nothing else is needed; most plugin systems stop there.

That would make an agent's tool list **a fact about the virtualenv**.
The same package, handed to a colleague, would have different tools —
more on a machine with more installed, fewer on a fresh one — and
`agent.toml` would not say a word about why. Every other route into a
registry in this harness is written down somewhere a person can read:
built-ins are named in `tools.allow`, package tools live in a directory
the manifest points at, MCP servers are declared. A pack that appeared
because somebody once ran `pip install` for an unrelated reason would be
the only exception, and it would be the one nobody could see.

So a pack is loaded when it is **named**, one line, and that line is the
record. Discovery still exists — `entry_point_packs()` lists what is
installed without importing any of it, so a host can show an operator
what is available — but listing is not loading.

**A name nothing publishes is an error**, and the error says what *is*
installed:

```
error: tool pack 'no-such-pack' is not installed: nothing publishing
'yantra.tools' is called that (installed: tide-pack). Install it into the
same environment as the agent, or drop the line that asks for it
```

That is the same rule a `tools/` directory follows: an author who wrote
the line believes they shipped the tool, and an agent quietly missing it
is exactly the failure this area exists to prevent.

## Asking what is installed

Naming a pack assumes you know its name. `--packs` answers that from the
shell:

```
$ yantra --packs
tide-pack 0.2.0
  tides = tidepack.tools

1 pack(s) installed, none loaded. An agent gets one only by naming it:
--tool-pack NAME, or packs = ["NAME"] in agent.toml
```

It shows the entry points, not the tool names. **A LISTING IS NOT A
LOAD.** Nothing is imported, because finding out which tools a module
defines means running it, and asking what is installed should not run
anybody's code. That also means a pack that fails on import still shows
up in the list, which is the moment you most need to know it is there.
The entry point says where its tools live; `--tool-pack` is what loads
them.

## What an entry point may point at

Two shapes:

* **A `Tool` subclass** — `tides = "tidepack.tools:Tides"`.
* **A module** — `tides = "tidepack.tools"`, and every concrete `Tool`
  defined in it loads, exactly as a `tools/` directory works. This is the
  shape that makes a *pack* worth having: one line, several tools.

**A callable is not a shape here.** An entry point resolving to a factory
would run somebody's code, with arguments nobody can see, at a moment
nobody chose, to produce a tool list that is written down nowhere. Both
forms above can be read straight out of installed metadata; what a pack
ships is knowable before it does anything.

## Everything else is unchanged, deliberately

A pack tool is the same `Tool` subclass, with the same hand-written
schema. There is still no `@tool` decorator
([notes/32](32-package-tools.md) argues that at length, and pip does not
change the argument). Collisions still raise — a pack may not shadow
`bash` any more than a local file may. And the admission policy still
binds:

```toml
[tools]
allow = ["read_file", "glob"]      # tides is not in here
packs = ["tide-pack"]              # so tides does not load
```

…which shows up in `refused_names()` on startup, like everything else a
package turned away.

The trust model is the one from note 32, restated because pip widens it:
installing a pack is arbitrary code execution as you, at import time,
before any gate exists. That is what `pip install` always is. What
changes with a pack is only that the code arrives from an index rather
than from a directory you cloned, so the operator gets told it happened:

```
tool packs: tide-pack
```

## What is not here yet

* **Nothing pins a version.** `packs = ["tide-pack"]` names a
  distribution, not a release, and which version answers depends on the
  environment. Pinning belongs in the environment's own lockfile, which
  is where every other dependency's version lives — but it does mean a
  manifest cannot say "this agent was graded against tide-pack 0.2".
* **No namespacing.** A pack's tools land in the registry under their own
  names, so two packs that both ship `search` collide loudly and neither
  can be renamed from the manifest. A `mcp__server__tool`-style prefix
  ([notes/09](09-mcp.md)) would fix it and would make every eval case
  naming the tool longer.
* **The group is not versioned.** `yantra.tools` will mean whatever
  `Tool` means in whatever version is installed; a pack built against an
  older base class fails at registration rather than at install.
* ~~**Nothing lists packs from the command line.**~~ `yantra --packs`
  prints them; see [Asking what is installed](#asking-what-is-installed)
  above. Was: `entry_point_packs()` existed and no flag printed it, so
  "what could I load?" had an answer in Python and none in the shell.
