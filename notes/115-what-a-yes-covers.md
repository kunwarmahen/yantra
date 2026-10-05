# 115 — What a yes covers

A person says "check my mail every two hours and tell me if anything
needs me". The agent can't do that in one turn: it needs a clock that
keeps running while the person is somewhere else. Samay is that clock, a
separate program next to Yantra ([note 114](114-nobody-watching.md)
covers how Yantra behaves when Samay starts it with nobody watching). It
ships the agent's tools as an MCP server: `preview_schedule`,
`create_schedule` and a few more.

Until now a person had to connect that server by hand with
`--mcp-config`. That was fine as a step, but it left two problems.

The first was a chore: every session that should offer schedules needed
the config file.

The second was worse. When the agent made a schedule, the approval card
showed the raw call:

```
mcp__samay__create_schedule({"prompt": "Check the person's inbox ...",
  "when": {"every": "2h"}, "notify": "when_new",
  "allow_tools": ["mcp__gmail__*"]}) -- mcp server 'samay'
```

Read that card as somebody who isn't a programmer. A JSON `when`. A
glob. Nothing saying that `mcp__gmail__*` includes `send_message`, or
that it covers *both* of your Gmail accounts. Nothing saying that every
account you connected through Setu ([note 95](95-the-accounts-you-connected.md))
can be read by this run with nobody watching. People approve a card like
that without reading it. A schedule then spends money and reaches
accounts every two hours for weeks, on the strength of that one click.

## Found like Setu

`samay_link.py` follows the same pattern as `setu_link.py`. At startup
Yantra runs `samay status --json`, checks that its `format` is
`samay.status.v1` (an unknown format is refused, never guessed at), and
starts the MCP server Samay names, with two words added:

```
samay --state ~/.samay mcp --for local --agent ~/agents/mail
```

* `--for local` is the person this agent serves. A session at this
  computer is `local`. The model has no argument with which to name
  anybody else.
* `--agent` is the package this session runs. A schedule made while you
  talk to your mail agent runs your mail agent. Plain Yantra leaves it
  out.

`YANTRA_SAMAY=auto|on|off|PATH`, `--samay [PATH]` and `--no-samay` work
exactly like their Setu twins: in auto, a missing Samay is silence, and
one you asked for that can't be found stops the start. Whatever is found
is announced in one line, including whether its clock is running:

```
samay: 7 tool(s), 0 active schedule(s); its clock is NOT running --
nothing runs on time until `samay serve` is -- via .../samay
```

There's one difference from Setu: there is no import road. Samay's
report needs its own state folder and store opened first, which is
Samay's business, so Yantra always runs the command.

**A run nobody watches gets no Samay.** Each scheduled run is itself a
`yantra --unattended`. It can't be said yes to, so it gets no tools for
offering and no prompt telling it to offer.

## The card says it in words

`MCPToolWrapper` grew one hook, `explain`. It holds plain words for the
permission card, set by the host that knows what a server's calls mean.
A server can't set it: a server's description is its own claim about
itself, and the card is the host's job. `samay_link` sets it for the
four write tools. The card for `create_schedule`, from a live run:

```
Save a schedule. At each time this agent runs it with NOBODY watching.
  when:      every 2 hours -- next: Mon 5 Oct 14:26, 16:26, 18:26 (America/New_York)
  tells you: only when there is something new
  without asking, it may also use:
    mcp__gmail__*: mcp__gmail__create_draft, mcp__gmail__get_message, ... +1 more
    THIS CHANGES THINGS IN YOUR Gmail (you@gmail.com; also.you@gmail.com)
    WITHOUT ASKING: create_draft, send_message
  through Setu, it can read without asking: Amazon (amazon.com); Gmail (...);
    Home Assistant (...); Linkedin (...); Slack (...); Whatsapp (...); X (x.com); Yahoo (...)
  Anything else that changes something is refused while nobody is there.
  does:      Check the person's mail in both Gmail accounts ...
```

Each line answers a question the raw call left open:

* **When.** Samay's own sentence, from the server's own
  `preview_schedule`, so the card and the schedule can't disagree about
  what "every 2h" means. A `when` Samay can't read is shown in capitals
  with Samay's reason.
* **When you hear.** `when_new` in words.
* **What each glob reaches, in this agent.** Each entry in `allow_tools`
  is matched against the tools this session has. A name that matches
  nothing is said in capitals (`NO TOOL BY THIS NAME`), because small
  models make tool names up: in Samay's own trial, qwen3.8 asked for
  `browser_navigate`, which doesn't exist. A tool that spends money is
  said to be refused, since it's asked about every time and nobody will
  be there to answer.
* **Whose account.** A matched tool that changes something and belongs
  to a Setu connection is grouped under the account it acts on. A
  connector with two accounts shares one set of tools (`mcp__gmail__*`),
  so its line names both addresses.
* **What it reads unasked.** Every Setu connection this agent has, in a
  package session only the ones that package was allowed. Reads run
  unattended without being allowed ahead of time; note 114 found a
  scheduled run reading a real X timeline that way.

**The risky part comes before the prompt.** The first version put the
model's prompt second. A model writes a long prompt (this one was 700
characters), and on the page the `send_message` line ended up below the
fold, under the buttons. Now the prompt goes last.

Pause, resume and delete get short cards too: *"Delete schedule
9994f642 AND its history, for good"*, followed by the schedule's line
from `list_schedules`.

## The model is told, briefly

A `schedules` prompt layer, added after `connections`, appears only when
Samay is linked. It says the tools exist, to preview first and say the
sentence, to create only after a yes, and to name only tools the agent
really has in `allow_tools`. When Samay's clock isn't running it adds
one more line: say so when you make a schedule. When to offer is left to
the model. This is plumbing, and the agent decides.

## A Schedules panel on the page

`yantra --web` gets a clock chip in the header (`N scheduled`), shown
only when Samay is linked, and a panel like Memory and Connections.
Each schedule shows its sentence, its prompt, who hears and when, the
tools it may use, its next times on a 24-hour clock, and its last run
coloured by outcome. Its buttons are *runs* (the history, with what a
run needed from you and what it was refused), *run now*, *pause* or
*resume*, and *delete* (which asks first). If the clock isn't running,
the panel says so at the top.

**The panel talks to Samay's program, not to Samay's page.** `samay
list --json` and its neighbours work whether or not `samay serve` is
running, need no token, and return the same JSON Samay's own page is
drawn from. Its HTTP API would need the token Samay keeps out of
`status --json` on purpose, and would only answer while `serve` runs.
*Run now* starts `samay run-now` and doesn't wait: a run is an agent
turn, minutes long, and it shows up in the history when it's done.

A schedule id goes into a command line as one word, so one that starts
with `-` is refused before anything runs.

## Receipt

`yantra --web` on `qwen3.8:latest` through Ollama, memory off, scratch
browser profile, a fresh Samay state, Setu on with nine real
connections. Two messages: *"check my mail every 2 hours and tell me if
anything needs me"*, then *"yes, set that up"*.

Turn 1: the model called `preview_schedule`, said the sentence, asked
*"Want me to create it?"*, and added a heads-up that the schedule won't
run until `samay serve` is started. Turn 2: the card above, then
approve. The schedule appeared on the panel and in `samay list`:

```
$ samay list
2836225d  active  every 2 hours -- next: Mon 5 Oct 14:26, 16:26, 18:26 (America/New_York)
          Check the person's mail in both Gmail accounts (account: "mine" = …
```

**What the card caught.** In both runs, the model wrote
"read only" into its own prompt and still asked for `mcp__gmail__*`,
which includes `send_message`. Nothing stops that: the model writes the
arguments, and a model that over-grants is a model doing its job
badly, not a bug in the plumbing. The card is where a person sees it.
Without it, that was a glob inside a JSON blob.

## What was deliberately not built

* **The Dvara road.** The person on the other end of a Telegram chat
  needs `samay mcp --for <their id>`, and Dvara builds its agents
  without any MCP servers today. `Samay(person=...)` is the seam;
  wiring Dvara to it is Dvara's own change.
* **Narrowing what the model asked for.** The card could offer to drop
  `send_message` from the glob. That's approve-with-edits, which the
  page already has (*edit* on the card). A second, schedule-only way to
  do the same thing would be a dialect of its own.
* **A guarantee that the run has the same tools as the card.** The card
  matches globs against *this* session. The scheduled run is a fresh
  process with the same environment and the same package, so it
  normally has the same tools. A Setu connection made or dropped
  between now and 08:00 changes what it reaches, and the card can't
  know that.
* **The 24-hour strip of Samay's own page.** The panel lists the next
  times instead. The strip is one click away on Samay's page, whose
  address the panel shows while the clock is running.
