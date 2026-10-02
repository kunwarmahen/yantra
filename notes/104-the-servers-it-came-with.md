# 104 — The servers it came with

An agent package can list the MCP servers it needs, in `agent.toml`
([note 31](31-agent-packages.md), [note 09](09-mcp.md)):

```toml
[[mcp]]
name    = "docs"
url     = "https://example.invalid/mcp"
headers = { Authorization = "Bearer ${DOCS_TOKEN}" }
```

For a long time that line did something only when the package was being
*tested*. `yantra --eval` started the declared servers
([note 41](41-a-gate-you-can-point.md)), so the suite graded the agent
with its tools. An ordinary session, in the terminal or the page, did
not. The package's prompt might say *"search the docs first"*, and the
model would find no `mcp__docs__search` to call. The agent looked broken
in exactly the setting people use it in, and passed its own tests.

One server was the exception. A package whose memory lives behind an
MCP server ([note 102](102-kept-somewhere-else.md)) had that one server
connected, as a special case inside the memory binding. Everything else
it declared stayed off.

Now every session starts what the package declares.

## After everything you set up yourself

A session collects servers from four places, in this order:

1. `--mcp-config FILE` on the command line;
2. servers you saved earlier with "remember this server" (`.yantra/mcp.json`);
3. your connected accounts, through Setu ([note 95](95-the-accounts-you-connected.md));
4. the package's `[[mcp]]`.

**THE FIRST ONE TO CLAIM A NAME KEEPS IT.** The package goes last, so if
you already have a server called `docs` (your own copy, a different
account, a local mirror), yours is used and the package's is skipped,
with a line saying so:

```
mcp 'docs' (agent.toml): already connected from your own setup; using that
```

The person running the agent knows things about their machine that the
author could not. The tradeoff: a package author can't force their exact
server on anyone. The package's copy only fills a gap.

## A dead server is a warning here, and red under --eval

In a session, a declared server that won't start gets one yellow line,
and the session carries on without it. That is how every other server in
this CLI behaves, and a missing optional integration should not stop
someone from using the rest of the agent.

Under `--eval` the same server is still a red suite (exit 2). A test run
that quietly graded a smaller agent than the one that ships would answer
the wrong question. Same declaration, two rules, because a person and a
test are asking different things.

## Live receipt

A scratch package declaring two servers: `tiny` (the fixture server in
[`examples/tiny_mcp_server.py`](../examples/tiny_mcp_server.py)) and
`docs`, whose command doesn't exist. A normal one-shot run on
`gemma4:12b` through Ollama, no `--mcp-config`:

```
mcp 'tiny' (agent.toml): 2 tool(s) -- mcp__tiny__echo, mcp__tiny__add
mcp 'docs' (agent.toml) unavailable: cannot spawn mcp server 'docs'
→ mcp__tiny__add()
│ { "a": 17, "b": 25 }
│ 42
42
── end_turn · 3406 in / 59 out · 2 iteration(s)
```

Before this change the same run had no `mcp__tiny__add` to call.

## What else moved

* **Memory lost its special case.** `bind_memory_server` no longer
  connects anything. The session has already connected the package's
  servers by the time memory binds, so binding only binds. If the memory
  server is still missing, it was never declared, or it failed and
  already said why.
* **`--mcp-login NAME` finds a package's server too.** A package server
  behind an OAuth login had no way to be signed in to, because the login
  looked only at `--mcp-config` and remembered servers. It now searches
  the package's `[[mcp]]` last, in the same order a session connects.
* **The package's tool policy still applies.** A package that narrows
  its tools with `[tools] allow` must name `mcp__*` to keep a server's
  tools ([note 31](31-agent-packages.md)). Admission is checked when a
  tool registers, so servers that connect after the build are held to
  it the same way.

`tests/test_mcp.py` pins it: declared servers connect and are
announced; a name you connected yourself is not connected twice; one
dead server doesn't stop the next; the allow list still refuses what it
doesn't name; and `--mcp-login` finds a package's server.

## What was deliberately not built

* ~~**Marking a package's servers in `/mcp` and the page's panel.** The
  startup line says `(agent.toml)`, and the listing shows them like any
  other server. Removing one in a session removes it for that session;
  it comes back next launch, since it's in the package.~~ Marked now.
  `/mcp` says `· from the package` (or `· from Setu` for a connected
  account), the page's row carries a **package** or **setu** badge, and
  removing one says it's back next launch instead of "saved entry
  forgotten":

  ```
  > /mcp
  tiny (stdio) · from the package — …/python …/examples/tiny_mcp_server.py — 2 tool(s)
  > /mcp remove tiny
  disconnected 'tiny' -- 2 tool(s) removed, back next launch -- the package declares it
  ```
* **Asking before starting a package's stdio server.** Declaring a
  `command` means running it, the same as the package's own `tools/`
  code runs. Choosing to run a package is when that trust is given. A
  prompt per server would be asking the same question twice.
