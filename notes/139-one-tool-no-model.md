# 139 — One tool, no model

"Tell me when the price drops under $50." "Tell me when a mail from the
bank arrives." Answering either means looking, again and again, until
something changes. A scheduler could do the looking with an ordinary
turn — `yantra --json --unattended --prompt "check the price"` — but
that asks a model every time. Ninety-nine looks in a hundred find
nothing, so ninety-nine model calls are spent to learn nothing. A small
local model may also misread the number, and "under $50" decided by a
model that copied 47.10 as 4710 is a watch that never fires.

The look itself is one tool call: open the product page, search the
inbox. The model is only needed when that call shows something. So
Yantra gets a way to make the call with no model at all:

```bash
yantra --call TOOL --args JSON [--json]
```

The agent is built exactly as for a turn: the package if one is named,
Setu's accounts, MCP servers, signed-in sites. Then the named tool runs
once, with the arguments given, and Yantra exits. With `--json`, stdout
is one object, `yantra.call.v1`:

```json
{"format": "yantra.call.v1", "ok": true, "tool": "web_fetch",
 "text": "title: Example Domain\n...", "detail": "", "images": 0,
 "needs_person": [], "busy": []}
```

`ok` is false when the tool failed, and `detail` says why in the tool's
own words. `images` counts pictures a screenshot made (the file is
saved where the tool saves it; the pixels are not printed).
`needs_person` holds a sign-in wall, as for `--json` on a turn
([note 114](114-nobody-watching.md)). Without `--json` the text alone is
printed. Prahari, the program that watches for things, runs every check
this way.

## Only what reads, or one call said yes to

A call has nobody to approve it and no model deciding it is wanted. So
the rule is narrower than an unattended turn's.

A tool marked read-only runs: reading a file, searching mail, a site's
`open`, a screenshot. Anything else is refused, whatever `--allow-tools`
says, with one exception. A grant that names this exact tool, no glob,
and fixes EVERY argument passed:

```bash
yantra --call web_fetch --args '{"url": "https://example.com"}' \
       --allow-tools 'web_fetch(url=https://example.com)'
```

`web_fetch` is not read-only on purpose. It changes nothing here, but
an address can carry data out of the machine, so a person approves each
one ([note 25](25-web-fetch.md)). A
person who accepted "fetch this address every three hours" approved
exactly that address. The grant form already existed for the stairs
light ([note 126](126-a-yes-that-covers-the-job.md)); here it has to fix
every argument, not just the dangerous ones. A bare `web_fetch`, a
`web_*`, or `web_fetch(url=...)` with a second argument left free does
not cover a call. A tool that asks every time (a purchase) never runs
this way.

Tested both ways live, against the real network and no model:

```
$ yantra --json --call web_fetch --args '{"url":"https://example.com"}' \
         --allow-tools 'web_fetch(url=https://example.com)'
{"format": "yantra.call.v1", "ok": true, "tool": "web_fetch",
 "text": "title: Example Domain\nsource: https://example.com\n\nThis domain is for use in ...", ...}

$ yantra --json --call web_fetch --args '{"url":"https://example.com"}'
{"format": "yantra.call.v1", "ok": false, "tool": "web_fetch", "text": "",
 "detail": "web_fetch is not read-only; --call runs only tools that read, or one call
 granted word for word: --allow-tools 'web_fetch(key=value, ...)' fixing every argument", ...}
```

## No key needed

A call asks no model, so a missing API key is no reason to refuse one.
When the provider can't be set up (no `ANTHROPIC_API_KEY`, say), the
agent is built around a local provider that is never called. A test
holds this down: its scripted provider has no script at all, so any
model call would fail the test.

## A call is unattended

Nobody is in front of a call, so `--call` turns on `--unattended`:
nothing it starts waits for a keystroke, a browser handoff opens no
window, and Samay's offers are not made. `--call` takes no prompt, and
`--args` without `--call` is an error.

## Live receipt: a real inbox

The person's Gmail, connected through Setu, searched with no model:

```
$ yantra --json --call mcp__gmail__search_threads \
         --args '{"account":"personal","query":"newer_than:2d","max_results":3}'
ok: True
thread 1a16384849b1c8ec · Thu, 22 Oct 2026 12:09:51 +0000 (GMT) · from Apple Developer
  <developer@insideapple.apple.com> · UNREAD
  subject: Upcoming events: Explore iPhone Duo in new cities.
```

Each thread starts with `thread <id>`, and that id is what a watch for
"new mail from X" remembers, so the same mail never fires twice.

The first try named the account `gmail:personal` and the tool said
*no account 'gmail:personal'; choose one of mine, personal*. A failed
call returns the tool's own error like that, so the agent writing a
check can fix it.

## Not here

- **No list of tools to call.** The agent writing a check already has
  the tool names in its conversation; `--call NAME` with a near miss
  answers *did you mean …*.
- **No phone.** A call gets Sparsh's tools only as an unattended turn
  would: with a Dvara to ask through and a phone run named. Reading the
  phone's notifications on a timer needs neither a model nor a person,
  but it wakes the phone every time; a phone that tells you itself is
  the better road (Prahari's inlet, its README).
