# 119 — A phone, and what asks

Sparsh is a separate program that works an Android phone, or the
emulator, through `adb`. The model gets the screen as numbered lines,
read from the phone's own accessibility description:

```
4 field "running late, be there at 7" [tap, type, focused]
6 image "Send SMS" [tap]
```

It answers with a number. No picture is involved, so a local model can
do it ([Sparsh's note 01](https://github.com/kunwarmahen/sparsh/blob/main/notes/01-a-list-not-a-picture.md)).
Sparsh ships the agent's tools as an MCP server. `sparsh_link.py` finds
it, starts it, and settles a question Yantra's permission model has
answered three ways before: who decides what asks?

## Three answers so far

* **The tool's own word.** A built-in says `read_only`, and an MCP
  server's `readOnlyHint` is honoured, with anything unmarked assumed to
  change things ([note 09](09-mcp.md)). Every change asks.
* **The manifest's.** Setu classes each connector's tools as read, write
  or spend, and `setu_link` overrides the server's hints with that
  ([note 95](95-the-accounts-you-connected.md)). A read runs; a spend
  asks every time.
* **The card's.** Samay's writes ask as before, but the host writes
  what the card says ([note 115](115-what-a-yes-covers.md)).

None of them fits a phone. Every tap changes something, so the first
answer asks nine times to send one text message. A person stops reading
by the third card, and the ninth, the one on Send, gets the same glance
as "Use Messages without an account". The second needs a manifest, and
there's no manifest for whatever happens to be on the screen. Whether
a tap matters depends on what it's *on*, and only Sparsh can see that.

## The fourth: the phone's rules

**THE PHONE'S RULES DECIDE WHAT ASKS, NOT THE SERVER'S HINTS.** Sparsh
names three kinds of tool in `sparsh status --json`, and `sparsh_link`
sets each wrapped tool by its kind:

| kind | tools | here |
|---|---|---|
| read | `look`, `list_apps`, `describe_hold` | never asked about |
| act | `tap`, `type_text`, `scroll`, `press_key`, `open_app` | not asked about |
| confirm | `confirm` | `always_ask`: asked every time, even under `--yolo` |

An act runs unasked *because* Sparsh holds the risky ones itself: a tap
on Send, Pay, Buy, Delete, Allow and the like, typing into a password
field, Enter while such a button is on screen. A held step isn't done,
and the model is told so:

```
NOT DONE -- this needs the person's yes: tap image "Send SMS" in
com.google.android.apps.messaging -- held because it says "send". Call
confirm with hold "h68873c" now, in this same answer: that is how they
are asked. Do not ask them in words first, and do not try another way
round it.
```

`confirm` is the only way through. Setting `read_only` on an act is a
statement about asking, not about side effects, the same way
`setu_link` sets it for a manifest's reads. The comment at the
assignment says so.

**THE CARD IS SPARSH'S WORDS FOR THE STEP.** `confirm`'s raw call is a
hold id, and nobody can say yes to `{"hold": "h68873c"}` with their eyes
open. Its `explain` hook asks the same server's `describe_hold` and
shows that: the step, why it was held, and the screen it was held on,
including the text in the message box. If Sparsh can't describe the
hold, the card says so in capitals instead of falling back to the id.

A tool Sparsh doesn't name keeps the server's hint, which errs toward
asking.

## Found, and when not

`YANTRA_SPARSH=auto|on|off|PATH`, `--sparsh [PATH]` and `--no-sparsh`
work like their Samay twins, with one difference. **IN AUTO, NO PHONE
MEANS NO TOOLS.** Sparsh on `PATH` with nothing attached would put nine
tools and a prompt layer into every session, for a phone that isn't
there. So auto stays silent until `adb` sees a phone that is ready
(`device`, not `unauthorized` or `offline`). `--sparsh` connects anyway,
and the `phone` prompt layer tells the model to say the phone needs
plugging in. `yantra status` reports Sparsh as found even with no phone,
because "found, no phone attached" is the line a person setting it up
needs to read.

**NOT WITH NOBODY WATCHING.** An `--unattended` run gets no phone. With
nobody watching, everything an act may do by itself would be done on
a phone nobody is looking at, and every held step could only be
refused.

The `phone` prompt layer sits between `schedules` and `skills`. It
says: act by number, read the screen each action returns instead of
looking again, and on "NOT DONE" say in a sentence what will happen
and call `confirm`, never another way round. When to use the phone is
the model's call.

## The panel peeks

The browser UI has a **phone** chip, shown when Sparsh is linked. Its
panel lists the phones `adb` sees (ready, or waiting for the USB
debugging yes), the rules in force with the path of the file that holds
them, and on **see the screen** a screenshot beside the lines the agent
reads, exactly as it reads them.

**THE PAGE PEEKS, IT DOESN'T LOOK.** Sparsh remembers each look as the
screen the agent's numbers refer to, and checks every tap against it
(Sparsh's note 01). A panel that did an ordinary look mid-turn would
renumber the screen under the agent: the agent read "7 is Send SMS",
the person glanced at the phone, and the agent's tap 7 is checked
against the person's 7. So the panel runs `sparsh look --peek`, a
separate program run that reads the screen and leaves the last look as
it was. That makes watching the agent work, the panel's main use, safe.

The panel shows everything, apps on the `never` list included. It is
the person's own eyes, and nothing it reads reaches the model. It
doesn't act on the phone either: the person's own hands are `sparsh` in
a terminal, or the phone itself.

## A phone plugged in later

In auto mode, no phone at the start means no tools. That's right for the
start, but wrong for the person who plugs the phone in five minutes
later and finds the agent can't see it until a restart.

**DORMANT, NOT ABSENT.** With Sparsh found but no phone ready, the
session still says nothing, registers no tools and writes no prompt
layer. It keeps the Sparsh handle, though, unconnected. The page shows
the phone chip as "no phone", then "phone · not in use" once one is
attached. The panel's **use this phone** (POST `/api/phone/use`, only
between turns, because the tools and the prompt change) or `/phone use`
in the terminal asks Sparsh again and starts the tools. **Nothing
happens by itself**: a phone appearing doesn't give the agent new hands
in the middle of a conversation until the person says so.

The panel's **what was done** reads Sparsh's step log (`sparsh log
--json`): each act, by the agent or by the person, however it ended.

Live, in a browser against the page: Yantra started with no emulator
running. Its startup was silent, with 24 tools and no phone tools; the
chip read "no phone". After the emulator booted, refreshing the panel
gave "phone · not in use". **Use this phone** turned it to "phone", and
*"On my phone, open Settings and tell me what the Battery row says"*
was answered "Battery — Charged" in two calls (31 s). **What was done**
then listed `done · the agent: open_app name="settings"`.

## Live receipt

The Android 15 emulator, booted clean; `qwen3.8:latest`; Setu, Samay and
memory off. Sending a text took nine model calls, and the one card came
on Send:

```
sparsh: 9 tool(s); phone emulator-5554 (sdk_gphone64_x86_64) -- via .../sparsh
→ mcp__sparsh__open_app {"name": "messages"}
→ mcp__sparsh__tap {"n": 2}          Use Messages without an account
→ mcp__sparsh__tap {"n": 6}          Start chat
→ mcp__sparsh__type_text {"into": 4, "text": "5554"}
→ mcp__sparsh__tap {"n": 5}          Send to 5554 — 5554
→ mcp__sparsh__type_text {"into": 5, "text": "running late, be there at 7"}
→ mcp__sparsh__tap {"n": 6}          [error] NOT DONE ... "Send SMS" ...
→ mcp__sparsh__confirm {"hold": "h68873c"}
╭─ approve mcp__sparsh__confirm()? ──────────────────────────────╮
│ Do this on the phone?                                          │
│ On the phone emulator-5554: tap image "Send SMS" in            │
│ com.google.android.apps.messaging -- held because it says      │
│ "send".                                                        │
│ The screen when it was asked for:                              │
│ ...                                                            │
│ 4 field "running late, be there at 7"                          │
│ ...                                                            │
╰────────────────────────────────────────────────────────────────╯
run it? [y/n/e/s] (n): y
Done -- the text "running late, be there at 7" was sent to 5554 (SMS).
── end_turn · 31395 in / 504 out · 9 iteration(s)
```

Answered `n` instead, on a second message, the model stopped:
"you declined the send, so I left the message ready but unsent". The
phone's message store held only the first message. Turning Airplane
mode on took six calls and no cards at all.

The first attempt at the SMS failed, and what it found was fixed in
Sparsh before this commit. The model typed with `ref` (Yantra's
browser tools' word), and the words were lost. Unknown arguments are
now refused with the right name. Sparsh's
[note 02](https://github.com/kunwarmahen/sparsh/blob/main/notes/02-held-for-a-yes.md)
has the rest.

## What is not here yet

* ~~**A yes asked in words.**~~ A model asked "shall I send it?" instead
  of calling `confirm`; the hold was gone by the reply. The card for a
  hold that is gone is now never shown
  ([note 124](124-a-question-with-no-step-behind-it.md)).

* A phone for scheduled runs, which needs an answer to "held, with
  nobody to ask" better than refusing.
* ~~The page: Yantra's browser UI shows the confirm card like any other,
  but has no phone panel (which phone, the rules in force).~~ Done:
  *The panel peeks*, above. Live on the emulator, a peek with its
  screenshot took 2.4 s.
* ~~The trial: these tasks and more on `qwen3.8:latest`, `gemma4:12b` and
  a frontier cloud model, counted.~~ Done for two local models:
  [note 120](120-thirteen-tasks-on-a-phone.md), 12 of 13 each, every
  send held. The cloud model is parked.
* An iPhone, run for real. Sparsh drives one through WebDriverAgent
  ([its note 03](https://github.com/kunwarmahen/sparsh/blob/main/notes/03-an-iphone-through-a-mac.md));
  here the model is told it's an iPhone (Back is a swipe from the
  edge), and the startup line says when the iPhone's 7-day signature is
  two days from running out. Not yet run on a real iPhone.
