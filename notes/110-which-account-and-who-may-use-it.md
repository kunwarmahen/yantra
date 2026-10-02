# 110 — Which account, and who may use it

[Note 95](95-the-accounts-you-connected.md) connected your accounts
through Setu: each one is its own small MCP server, and its tools show
up in every session. Two things about that were wrong for real use.

**Two accounts doubled the tools.** Sign in to Gmail as `personal` and
again as `work`, and the model got `mcp__gmail-personal__search_threads`
*and* `mcp__gmail-work__search_threads`, and so on for every tool. Local
models choose worse from long tool lists, and a third account made it
worse again.

**Every agent saw every account.** An agent package is something
somebody else wrote and you downloaded
([note 31](31-agent-packages.md)). If you'd signed in to Gmail, a
package you ran for something else entirely got your mail tools too.
Memory already refused this: a package gets none of your memories unless
it asks ([note 100](100-what-it-knows-about-you.md)). Accounts didn't.

## Several accounts, one set of tools

Each account still runs as its own server, so one process only ever
holds one account's token. What changes is what the model sees. When a
connector has two or more accounts, Yantra merges their tools into one
set, named for the connector, and adds an `account` argument:

```
mcp__gmail__search_threads(account: "personal" | "work" | "all", ...)
mcp__gmail__send_message(account: "personal" | "work", ...)   # required
```

The `account` choices come from your own labels, and the description
pairs each with the address you signed in as. So *"my work mail"* maps
to `work` without guessing. The connections prompt layer says the same:

```
- Gmail, 2 accounts (`personal` as me@example.com: Read, draft and send;
  `work` as me@work.example: Read, draft and send). Its tools are
  `mcp__gmail__*` and take `account`: name it for anything that sends or
  changes; a read may leave it out to use every account.
```

**A READ MAY ASK EVERY ACCOUNT.** Leave `account` out, or say `all`, and
each account is asked in turn. The answers come back labelled:

```
## personal (me@example.com)
…
## work (me@work.example)
…
```

**A WRITE HAS NO DEFAULT.** Sending from the wrong address is a mistake
you can't take back. So a tool the manifest classes as write or spend
requires `account`. A call without one fails with *"say which account to
use (personal, work)"*, and the model has to choose. The approval
question then starts with the account:

```
From: work (me@work.example) -- mcp__gmail-work__send_message({"to": …})
```

**ONE ACCOUNT CHANGES NOTHING.** Most people have one account per
service. They keep `mcp__gmail-personal__…`, with no `account` argument
to fill.

Merging happens on every sync, at startup and after a sign-in or a
disconnect from the Connections page
([note 99](99-the-connections-page.md)). Each sync first puts every
account's own tools back, then merges again. So disconnecting one of two
accounts leaves the other with its plain tools, and adding a second
merges them without a restart.

## A package gets what it asked for, and what you allowed

A package names the accounts it needs in `agent.toml`:

```toml
[connections]
needs = ["gmail:read"]       # connector, then read | write | spend
```

**NOTHING UNLESS IT ASKS.** A package without `[connections]` gets none
of your accounts, and the startup line says so:

```
setu: mail-helper asks for none of your connected accounts, so it gets none ([connections] needs in agent.toml)
```

**ONCE, PER PACKAGE AND LEVEL.** The first time a package asks, the
terminal puts the question:

```
mail-helper wants to read your Gmail (me@example.com, me@work.example). Allow? [y/N]
```

A yes is saved in `~/.local/state/yantra/connections-approved.json`,
keyed by the package's name *and* where it lives on disk. A package that
borrows a trusted package's name from somewhere else gets asked again. A
no isn't saved, so the next launch asks again, which is the cheap kind
of mistake. A package asking for `write` after you allowed `read` is
asked again, because that's a new question.

**THE LEVEL IS A CEILING.** `read` keeps only the tools Setu's manifest
classes as read. `write` adds writes, and `spend` everything. With
`gmail:read`, the package above sees `mcp__gmail__search_threads` and no
way to send.

**NOBODY TO ASK MEANS NOT YET.** Without a terminal (stdin is not a
TTY), nothing is asked and nothing is granted:

```
setu: mail-helper wants to read your Gmail (…). Allow? -- not answered yet; start it once in a terminal to say yes
setu: none of your connections for this agent
```

**YOUR OWN SESSIONS ARE UNCHANGED.** Running `yantra` without
`--agent` still sees every account you connected. They're yours.

## Receipt

A scratch Setu reporting two Gmail accounts, each with its own fake
connector process, and `qwen3.8:latest` on Ollama. First the person's
own session:

```
$ yantra --setu ./setu --provider ollama --model qwen3.8:latest \
    "Search my Gmail for threads about invoices."
setu: gmail-personal (3 tool(s)), gmail-work (3 tool(s)) -- via ./setu
→ mcp__gmail__search_threads()   { "account": "personal" }   ran search_threads as personal
→ mcp__gmail__search_threads()   { "account": "work" }       ran search_threads as work
I searched both of your Gmail accounts (personal: me@example.com and
work: me@work.example) …
```

Then a package needing `gmail:read`, answered with `y` in a terminal:

```
mail-helper wants to read your Gmail (me@example.com, me@work.example). Allow? y
setu: gmail-personal (1 tool(s)), gmail-work (1 tool(s)) -- via ./setu
> Search my work Gmail for invoices.
→ mcp__gmail__search_threads()   { "account": "work" }   ran search_threads as work
```

Only the read tool reached it, one per account, merged into one.

`tests/test_setu_link.py` pins both halves. Two accounts merge, and a
read without `account` asks both. A write without one fails, and its
approval names the account. Dropping back to one account restores the
plain tools. A package that asked for nothing gets nothing, a yes is
remembered for that package and place only, a no isn't, and an account
that isn't connected is reported, not asked about.

## What was deliberately not built

* **Asking on the page.** A package started with `--web` asks in the
  terminal it was launched from, before the page opens. A question in
  the page itself is a later step.
* **Asking mid-session.** An account you connect from the page while a
  package is running isn't offered to it until the next launch, when
  it's asked about. A package's access shouldn't widen while it runs.
* **Forgetting a yes from inside Yantra.** Delete the line from
  `connections-approved.json` for now.
* **Tool names by connector for one account.** Renaming
  `mcp__gmail-personal__…` to `mcp__gmail__…` even with one account
  would keep names stable when a second arrives. It would also rename
  every one-account setup that works today, recipes included.
