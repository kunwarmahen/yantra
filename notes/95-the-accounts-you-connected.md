# 95 — The accounts you connected

Setu is a separate project that keeps a person's sign-ins — Gmail first
— and starts a small MCP server for each one (`setu run gmail:personal`)
that never holds the key. Any MCP client can use it: paste that command
into a config ([note 09](09-mcp.md)) and the Gmail tools appear.

That works, and it leaves three things wrong.

1. **You configure it by hand**, in every project folder, and a
   reconnect or a second account means editing it again.
2. **The model is not told what it has.** Asked "anything in my
   Outlook?", it sees no Outlook tools and has to guess whether that
   means "not connected" or "try harder".
3. **The server decides what needs asking.** Yantra lets a tool run
   without a prompt when its MCP server says `readOnlyHint: true`. That
   is the server's claim about itself. A connector that marked
   `send_message` read-only — by mistake or otherwise — would send mail
   without anybody being asked.

Setu already knows all of it: which accounts, at which level of access,
and a class for every tool its connector offers, written in a manifest
the person could read before signing in. So Yantra asks.

## Found at start, and said out loud

At startup Yantra looks for Setu and, when it is there, connects every
connection as an MCP server, named as Setu names it (`gmail-personal`).
Whatever it connects is announced in one line:

```
setu: gmail-personal (7 tool(s)) -- via /home/you/setu/.venv/bin/setu
```

so a connection never becomes tools the person did not know the agent
had. Three ways to steer it, one setting underneath:

| | |
|---|---|
| `YANTRA_SETU=auto` (default) | use Setu if it can be found; say nothing if not |
| `--setu`, `YANTRA_SETU=on` | use it, and refuse to start if it cannot be found |
| `--setu PATH`, `YANTRA_SETU=PATH` | the setu program to run (which also means on) |
| `--no-setu`, `YANTRA_SETU=off` | never look |

A flag outranks the setting. In `auto`, a missing Setu is silence and a
broken one is a warning: the session does not depend on it. Asked for
and missing, it is an error, because then the person is expecting their
mail and would otherwise get an agent that quietly has none.

A server of the same name that the person configured themselves —
`--mcp-config`, or the page's remembered list — wins. If it runs the
same command it is recognised as the same connection (and the rules
below still apply to it); if it runs something else it is left alone,
and the start says so.

## Two roads to one answer

Setu's answer is a function, `report()`, and a command that prints it,
`setu status --json`. When Setu is installed in Yantra's own Python
environment the function is called directly; otherwise the command is
run and its JSON read. In practice the command is the usual road — Setu
lives in its own environment — and `YANTRA_SETU=PATH` names it when it
is not on `PATH`.

**THE JSON IS THE CONTRACT.** Neither project imports the other's
internals. The report carries a `format` field (`setu.status.v1`), and a
format Yantra does not know is refused, never guessed at — the failure a
guess produces is the one nobody notices: tools classed by a field that
moved.

The report never contains a key. It is built from what `setu list` may
show, so Yantra can log it, and put parts of it in the prompt.

## The manifest decides

Every tool a Setu connector offers has a class in its manifest — read,
write or spend — and that class, not the server's hint, is what Yantra
goes by:

* **read** may run without asking, like any read-only tool;
* **write** is always asked about, whatever the server claims;
* **spend** is asked about every time, and no blanket approval covers
  it: not `--yolo`, not the page's yolo mode;
* a tool the manifest does not list **is not registered at all**, and
  the start names it. Nobody agreed to it.

One exception, and it is the manifest's to make, not the server's. A
bridge to a server whose tool names are its own — Home Assistant's MCP
server renames its Assist tools between versions, `GetDateTime` becoming
`llm__GetDateTime` — may say `"*" = "write"`. Every tool it doesn't
name then counts as write: asked about, never dropped, never run unasked.
A `"*"` of read is ignored, because "anything else only reads" is a claim
nobody can check. Against a real Home Assistant, the bridge's manifest
named three read tools of the twenty-eight it offered; the other
twenty-five arrived as writes.

Spend needed something new, because "asked about every time" was not a
thing a tool could say. `Tool.always_ask` is that: the agent copies it
onto the permission request, the `yolo` gate refuses such a call with
the code `needs_person` and a sentence saying why, and a `SwitchableGate`
in yolo mode asks anyway. It is general — any tool can set it — and Setu
connectors are the first to. (Gmail has no spend tools; buying and
paying arrive with connectors that do.)

The test for all of this uses a fake connector that **lies**: its search
tool claims side effects, its send tool claims to be read-only, and it
offers a tool no manifest lists. Search runs, send asks, the extra tool
never appears.

## The model is told

A new prompt layer, `connections`, sits between `env` and `skills`
([note 31](31-agent-packages.md) introduced the layers; the order grew
again):

```python
LAYER_ORDER = ("agent", "base", "env", "memory", "connections", "skills")
```

It lists each connection with its account and level, the connectors
installed but not connected, and one instruction: an account that is not
listed is not connected — say so rather than guessing. It is set at
start, like the tool list it describes, and rewritten whenever the
Connections page changes that list ([note 99](99-the-connections-page.md)).

A learned recipe can name the connection it needs (`needs: setu:gmail`).
When it loads, the model is told which server's tools that is, or that
it's not connected and the person has to connect it
([note 106](106-what-the-recipe-leaves-out.md)).

## What was deliberately not built

* ~~**A Connections page.** Connecting, changing a level and
  disconnecting stay in Setu's own commands for now.~~ Built: the page
  asks Setu to sign in and relays its address, and the tools follow
  without a restart ([note 99](99-the-connections-page.md)).
* ~~**One tool set for several accounts.** Two Gmail accounts are two
  servers with their own tools (`mcp__gmail-personal__…`,
  `mcp__gmail-work__…`). Merging them behind an `account` argument keeps
  the tool list short for local models, and is its own change.~~ Built:
  one `mcp__gmail__…` set with `account`, required for anything that
  writes ([note 110](110-which-account-and-who-may-use-it.md)).
* ~~**An agent package asking for a connection.** A package that needs
  Gmail cannot yet say so in `agent.toml`; it gets whatever the person
  has connected, like any session.~~ Built: `[connections] needs`,
  allowed once per package, and nothing for a package that didn't ask
  ([note 110](110-which-account-and-who-may-use-it.md)).
* **A way for spend to be pre-approved.** Not even a separate flag.
  When a connector that can pay exists, whether that should ever be
  possible is a decision for then.

## Receipt

Yantra on `qwen3.8:latest`, a real Setu with one Gmail connection at the
send level. Every Gmail tool was then disabled
(`YANTRA_DISABLED_TOOLS='mcp__*'`), so the only thing the model could
answer from was the new prompt layer (address shortened):

```
setu: gmail-personal (7 tool(s)) -- via /home/you/setu/.venv/bin/setu
disabled 14 tool(s): bash, browser_click, …, mcp__gmail-personal__search_threads,
mcp__gmail-personal__send_message, …

> Which of my accounts are connected, and what can you do with each?
> Is Outlook connected? Answer only from what you were told.

**Gmail** (`gmail-personal`) — connected as you@gmail.com. What I can do with it: …
**Outlook** — not connected. No Outlook account or tools are listed.
That's the only connected account. If you'd like Outlook, it would need to be
signed in through Setu.
── end_turn · 3208 in / 284 out · 1 iteration(s)
```

No `mcp.json` was involved: the folder's remembered list was empty, and
the Gmail server was started from what Setu reported.

`2199 passed, 1 skipped` (was 2184). The new tests are in
`tests/test_setu_link.py`; each of these breaks turns one of them red:
trusting the server's hint over the manifest, `yolo` approving a spend,
the yolo mode approving a spend, keeping a tool the manifest does not
list.
