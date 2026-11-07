# 148 — A source you trust, and one yes for the task

[Note 142](142-outside-text-cannot-give-orders.md) made a rule: once a
turn has read text you didn't write, every change asks. It held, and it
held too often. Through Dvara, the house's runs showed it asking after
your agent had read things you *did* write: your own schedules from
Samay, your own lights from Home Assistant. And a five-step job on one
web page asked five times about one page you had already looked at.

A question you have learned to say yes to without reading protects
nobody. The rule only works if you still read the card. So this note
makes two ways to be asked less, and neither of them is "asked never".

## A source you trust

Some servers answer with your own words. Samay's list is the schedules
you made. Home Assistant's states are your own house. Reading them
doesn't bring anybody else's orders into the turn.

So a host can name trusted sources, and a read from one of them is not
noted as outside text at all:

```python
agent.trusted_reads = ("samay", "homeassistant-home")
```

Each entry is a whole MCP server by its name (`samay` covers every
`mcp__samay__…` tool), or one tool by its full name
(`mcp__prahari__list_watches`). A sub-agent shares its parent's list,
the same way it shares the list of what was read.

Three limits, each one a way trust could go wider than you meant:

* **No wildcards.** `mcp__samay__*` is refused, with the fix in the
  error. Dvara's `allow` rules take no wildcards for the same reason:
  trust that's too wide costs a page nobody looked at, and a pattern
  can't say in advance which tools a server will grow.
* **Only a server's tools.** `web_fetch`, the browser and the site tools
  read the open web, which is never anyone's own words. Naming one does
  nothing (`outside.trusted` only matches `mcp__…` names), and an entry
  that looks like a tool but isn't a server's (`tradingview__open`) is
  refused.
* **The server name must match exactly.** `sam` doesn't cover `samay`.

Trusting your Gmail would be a mistake: an email is the exact thing note
142 was written about. Nothing stops an owner from doing it, but that's
the owner's call to make, and the docs say so.

In Dvara the list is per person, `trusted = [...]` in `actors.toml`,
and Sarathi's home page has a box for it.

## One yes for the task

The second way doesn't touch what counts as outside. It changes what a
yes can cover.

When a change asks only because the turn read something, the loop now
says so on the request: `only_after_reading` is True. A host can then
offer a third answer on the card: *approve all for this task*. Dvara
does. One yes then covers every later after-reading question in the
same turn.

It covers only what you saw. The yes remembers what the turn had read
when you gave it. If the agent then reads something new (another page,
an email), the next change asks again, and the card names the new read.

A spend never comes under it. A tool that always asks for itself
(`always_ask`: a purchase, the phone's `confirm`) keeps
`only_after_reading` False even after a read, so no "for this task"
covers it. The card still says what was read.

How the yes is kept is each host's business. Dvara keeps it in its gate
and ask desk; Yantra's own page and terminal keep it in `TurnYes`
(permissions.py), one per gate: the turn it was given in and what had
been read by then. On the page the card has a **yes to all for this
task** button beside **approve**, only on a card that asks because of a
read; at the terminal the prompt offers `t` beside `y/n/e/s`, said on a
line above it (*t = yes, and the rest of this task (until it reads
something new)*). A `t` on a card that didn't offer it can't be typed;
the page's button sent for a card that didn't offer it is a plain yes.
The step it covers says who answered: *your yes for this task, on the
page* (or *in the terminal*).

## Live receipt

A scratch Dvara on `qwen3.8:latest`, with an agent holding `web_fetch`
and `write_file`. The question was: "Fetch https://example.com. Then
write its page title to title.txt. Then, as a separate step, write the
first sentence of its body to first.txt." Every question was answered
over HTTP, and the first one that offered it was answered "for this
task":

```
ASKED web_fetch for_turn=None
  fetch https://example.com
  -> approved (plain yes)
ASKED write_file for_turn=True
  Asked because this turn read text you didn't write (https://example.com (web_fetch)). ...
  -> approved (approve all for this task)
REPLY: Done. ... title.txt — "Example Domain" ... first.txt — "This domain is for use in
documentation examples without needing permission."
questions put: 2
```

That's two questions where there had been three. The run's record names
what settled each call:

```
[["web_fetch", null, "asked:http"], ["write_file", null, "asked:http"],
 ["write_file", null, "for_turn"]]
```

The same question on Yantra's own page (`yantra --web` on
`qwen3.8-64k`, driven in headless Chrome, each card answered as it
came up):

```
CARD: APPROVAL NEEDED | web_fetch() | fetch https://example.com | RAW ARGUMENTS | edit | deny | approve ⏎
  -> approve
CARD: APPROVAL NEEDED | write_file() | Asked because this turn read text you didn't write (https://example.com (web_fetch)). Check this is what you asked for. | NEW FILE title.txt (1 lines) | RAW ARGUMENTS | edit | deny | yes to all for this task
  -> yes to all for this task
CARDS SHOWN: 2
$ cat title.txt first.txt
Example Domain
This domain is for use in documentation examples without needing permission.
```

The web_fetch card had no such button (it asks for itself), and the
second write ran on the yes given for the task.

The trusted-source half was not run against a live Samay. Its tests
drive both loops with a stand-in `mcp__samay__list_schedules`.

## What is deliberately not here

* **No "yes for today", no "yes for this agent".** One turn is what you
  asked for, and it's what the card is about. A wider yes is a standing
  rule, and a standing rule can't answer these questions at all.
* **No trust for the open web**, however a host words it.
* ~~**Yantra's own page and terminal don't offer "for this task" yet.**~~
  They do (`TurnYes`, above; receipt below).
* ~~**A held turn's answers** (the page's resume, notes/88) are one per
  waiting call, with no "for this task".~~ When every waiting call asked
  only because of what the turn read, the held card offers **yes to all
  for this task** too (`TurnYes.held_offers`). The resumed turn is a new
  turn with a new id, so the yes is `carry`-ed: its first question asked
  for the same reason takes it, read as the held card named it, and the
  page `drop`s it when that turn ends. A held call's reading is saved in
  the checkpoint (`after_reading`, `only_after_reading`), so a page
  opened after a restart still offers it.

  Live, on `qwen3.8:latest` (`yantra --web --wait-budget 20
  --on-timeout hold`), asked to fetch example.com and then write its
  title, its first sentence and the date to three files, one at a time.
  The fetch was approved; the first write was left to run out of time:

  ```
  ASK web_fetch for_task=False   -> approved
  ASK write_file for_task=True   -> left unanswered
  held: {"for_task": true, "calls": [{"tool_name": "write_file", "summary":
    "Asked because this turn read text you didn't write (https://example.com
    (web_fetch)). ... NEW FILE title.txt (1 lines)"}]}
  resume for_task: {"ok":true}
  tool_result write_file title.txt
  tool_result write_file first.txt
  tool_result write_file date.txt
  held: null
  ```

  The held card showed **yes to all for this task** beside **carry on**,
  and after it the other two writes asked nothing.

## Tests

`tests/test_yes_for_this_task.py`: the yes covers the turn's later
after-reading cards while nothing new is read, never the next turn,
never a card that asks for itself; the terminal offers `t` only after a
read and asks again when something new was read; the page's card offers
the button only after a read, and the button on a spend is a plain yes;
a held card offers it only when every waiting call asked because of a
read, its yes is taken by the resumed turn's first card, never after
`drop`, never by a card that asks for itself, and a held call's reading
survives the checkpoint. `tests/test_web_hold.py`: `for_task` on a hold
that didn't offer it is a plain yes, and a non-boolean is a 400.

`tests/test_outside.py` (28, up from 16): a trusted server's answer is
not outside text; one tool can be trusted by its full name; a read from
an untrusted server still marks the change; `sam` doesn't cover `samay`;
this program's own tools are never trusted; a wildcard and a non-server
name are refused with the fix; a sub-agent and the async twin share the
trust; a send after a read asks only because of the read; a spend after
a read asks for itself.
