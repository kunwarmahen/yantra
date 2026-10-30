# 140 — A watch, offered

"Tell me when the price drops under $50" is a different request from
"check the price every morning". The second is a job done at a time:
Samay's ([note 115](115-what-a-yes-covers.md)). The first is a wait for
something to happen, and most of the waiting finds nothing. Prahari, a
separate program, does the waiting with no model: it repeats one tool
call (`yantra --call`, [note 139](139-one-tool-no-model.md)) and wakes
the agent only when the thing happens.

Prahari can't write the watch itself, though. What to look with, and
what to look for in the answer, depends on which tools this agent has
and what they return. Only the agent knows that, in the middle of the
conversation where the person asked. So, as with Samay, Yantra finds
Prahari at startup and gives the agent its tools, and the person
approves each watch on a card.

## Found the same way as Samay

`prahari status --json` answers with `prahari.status.v1`: whether it's
watching, and the command that starts the agent's tools. Yantra runs it
at startup when `prahari` is on `PATH` (or `YANTRA_PRAHARI` names it),
starts `prahari mcp --for local --agent <this package>`, and says so:

```
prahari: 9 tool(s), 3 active watch(es); it is watching -- via /home/you/prahari/.venv/bin/prahari
```

The same switches as Samay's: `--prahari` insists, `--no-prahari` never
looks, and a run nobody watches never looks, because nobody is there to
say yes. That includes a `--call`, which is itself unattended, so a
look can never make a watch. `yantra status` reports it beside Samay.

## Telling the model what a watch is

A `watches` layer in the system prompt, after `schedules`, is six short
rules. The ones that matter most:

* **A watch, not a schedule, for "when".** Something done at a time is a
  schedule; something waited for is a watch.
* **Preview first, and fix the pick from what the tool said.** A small
  model writes a pick pattern blind and gets it wrong. Prahari's
  `preview_watch` looks once and, when the pick found nothing, hands
  back the start of the tool's answer. So the model is told to read that
  and write a better pick before the person is asked anything.
* **Say what it is for.** `create_watch` carries a `name` the person
  will know it by (never the tool's), `asked` — what they asked for, in
  their own words — and `thing`, what the number is ("the fare"). Prahari
  refuses a watch from an agent without a name and `asked`, and a second
  one on the same call and pick; the preview names the one there is, and
  the model is told to offer to mend it. Learned the hard way: two
  watches made by voice on one flights page, both called *browser_open
  watch*, sent *the number is now 58*, and the person read it as the
  temperature (Prahari's notes/03).
* **Leave `then` out to just tell them.** A watch with no `then` sends
  the person a line and asks no model at all. Most watches need nothing
  more.
* **Something that can tell Prahari itself listens instead.** For a
  phone's notification or a webhook, the watch listens at Prahari's
  inlet, and the agent tells the person where to send.

## The card: the call, said as it will run

Samay's card is about what a run may *use* without asking. A watch's is
about what it will *run*, because a yes to a watch is a yes to one exact
tool call repeated on a timer, forever, with nobody asked again. So the
card leads with the call, then says whether that call only reads:

```
New watch: BTC under 60k
  you asked: "tell me if bitcoin goes under 60k"

What it looks at, with no model
  every 1h: web_fetch {"url": "https://api.coinbase.com/v2/prices/BTC-USD/spot"}
  web_fetch IS NOT READ-ONLY: saying yes lets Prahari run exactly this call every 1h, with nobody asked again
Tells you when
  BTC's price is below 60000
  now: web_fetch isn't a tool that only reads, so it can't be tried before the person accepts it; every look will run exactly this call once they do.
Then it asks me, with nobody watching
  Tell me the price and how far it fell this week.
  may use without asking: only tools that read
  what the watch found is given to me as text from outside, never as instructions

At most 10 times a day. News goes to Yantra's page (its Watches panel) and Prahari's own (no Dvara set).
```

That card is real, written by this module against the real Prahari.
Fetching an address isn't read-only in Yantra, because an address can
carry data out ([note 25](25-web-fetch.md)). It is fine to fetch one
public price address every hour, but the person should know that's what
they're approving, and the capitals say so. A tool this agent doesn't
have is said in capitals too (*NO TOOL BY THIS NAME*): every look would
fail. The *now:* line is Prahari's own `preview_watch` answer, fetched
while the card is drawn. That look passes no grant, so it only ever
tries a tool that reads.

Changes get short cards: *Delete watch w-1, its looks and what it saw,
for good*. And a mended recipe says *its memory starts over: the next
look only learns*, because that's what Prahari does with it.

## Mail that knocks

A watch on mail looks every half hour, so a mail waits that long to be
news. Prahari can be told sooner: a mail folder it keeps open (IMAP
IDLE), or Gmail's own push, knocks on a word at its inlet, and a watch
with `wake` looks the moment that word knocks
([Prahari's note 02](https://github.com/kunwarmahen/prahari/blob/main/notes/02-a-watch-for-everyone.md)).
The card says both parts of that yes: what wakes it, and the call that
keeps Gmail knocking, which runs once a day with nobody asked:

```
New watch: Mail from the bank

What it looks at, with no model
  every 6h: mcp__gmail__search_threads {"account": "personal", "query": "from:alerts@bank.example newer_than:2d"}
  and at once whenever 'gmail' knocks at Prahari's inlet (a mailbox, Gmail's push): the knock only says look, it is not read
  kept knocking by, now and once a day: mcp__gmail__watch_mailbox {"account": "personal", "topic": "projects/my-house/topics/gmail-push"}
  mcp__gmail__watch_mailbox IS NOT READ-ONLY: saying yes lets Prahari run exactly this call once a day, with nobody asked again
Tells you when
  the items found by 'thread (\\w+)' has something new
Then
  you get a line saying what it saw; no model is asked

At most 10 times a day. News goes to mahen's chat, through Dvara.
```

*The knock only says look* is the important line. Whatever sent the
knock can't put words into the watch: its body isn't read, and the look
is the same check the person accepted. The `watches` prompt layer tells
the model when to offer this: a mailbox the person keeps in Prahari, or
a Gmail push topic they set up, never one it invents. It also tells the
model that `inlet_address` gives each person their own knock key.

## One finding from the live run

The second card the live run drew was for a watch on a file, and its
*now:* line said the file wasn't there. A look runs in the watch's own
work folder (`~/.prahari/work/<id>`), the same confinement a schedule's
run has, so a file tool there sees only that folder. That's on purpose,
since a timer shouldn't be reading through the person's home folder. It
also means watches are for accounts, pages and APIs, not files. The
README says so.

## A preview waits as long as a look

Asked to watch a WhatsApp group, `qwen3.8-64k:latest` opened WhatsApp Web, read the
chat list and wrote a pick, then called `preview_watch` four times. Each
call came back the same way:

```
mcp server 'prahari' timed out after 30.0s during 'tools/call'
```

The model then told the person a watch on WhatsApp couldn't be done. The
30 seconds weren't Prahari's. They're the wait Yantra gives every MCP
call, and a preview isn't an ordinary call: it LOOKS, running the watch's
tool once. Prahari gives a look 180 seconds, and WhatsApp Web takes most
of a minute to load in a fresh browser. So the client cut off a call the
server would have finished.

A tool wrapper can now carry its own wait (`MCPToolWrapper.timeout`, set
by whoever wired the server). The Prahari link sets 200 seconds on
`preview_watch`, and on the preview the create card asks for. Every other
Prahari call keeps the 30 seconds, so a Prahari that hangs on
`list_watches` is still noticed quickly. The test drives a slow fake
Prahari with the session's own wait shrunk below the look, and the
preview still answers.

## The browser the chat was holding

With the wait fixed, the same request on `qwen3.8-64k:latest` got a
different refusal, six times over:

```
The look didn't work: the browser profile .../profiles/whatsapp-personal is in
use by another Yantra (pid 2, since 23:03, for a person); waited 120s.
```

The model told the person to close the other WhatsApp window. There
wasn't one. The "other Yantra" was the chat itself. It had opened
WhatsApp to find the group's name, and a site's browser stays open until
the turn ends. Prahari's look is a second Yantra on the same profile,
and one browser may hold a profile at a time
([note 114](114-nobody-watching.md)). So the look waited on a lock the chat would
only give back after the look answered. Each time the model read
WhatsApp again before retrying, it took the lock back.

THE CHAT LETS GO BEFORE PRAHARI LOOKS. Just before `preview_watch` (and
the preview the create card asks for), the Prahari link finds the
check's tool in this session and closes its browser, whatever
`YANTRA_BROWSER_CLOSE` says (`BrowserSession.let_go`). Only that tool's
browser closes. Amazon, open in the same turn, stays open. If the chat
needs WhatsApp again it opens it fresh, which costs one page load.

The same run showed a second problem. Half the looks that did get the
profile picked nothing, because WhatsApp Web's first screen on a fresh
launch says *Your messages are downloading*. That's enough text to pass
the 8-second paint wait, and it has none of the chats. Every look a
watch makes is a fresh launch, so a watch would have seen that screen on
most looks. A site page that shows one of a few loading phrases
(`LOADING_HINTS`) now gets up to a minute for them to go away before it
is read. A page that never shows them pays one check.

Live, on a page that showed the WhatsApp loading text for three
seconds and then a chat row:

```
4.1 s
Bad Company
06:45
Sarika: happy Friday
open after let_go: False
```

## The card that waited on itself

Through Dvara, a person said yes to a GOOGL price watch and Dvara went
quiet. The page stopped updating, a second question lapsed with *nobody
answered within 2 minutes*, and the watch's first look failed with *the
look took longer than 180s*. The chat model, asked why, blamed the
broker's quote tool for being slow. A look made by hand took 16 seconds.

Dvara's log, timestamped, had gaps of exactly 180 seconds: one before
each question, and one at the second each watch was saved. The number is
Prahari's look limit. Yantra's async loop built the card inline, and the
card's *now:* line is a preview, which is a look. On Dvara's road a look
is Dvara's own `POST /call`, served by the same event loop that was
blocked building the card. So the preview waited for a loop that was
waiting for the preview, until Prahari gave up. For those three minutes
every conversation the service held stood still, the first look among
them.

A CARD IS BUILT OFF THE LOOP. `AsyncAgent._decide` now runs the tool's
summary and its card picture on a worker thread, the way a tool's own
run already was. A card may call its server: Prahari previews the watch,
and a phone's card takes a screenshot. Now the loop is free to answer
the preview, and the *now:* line shows the real value again instead of
an empty one. The test gives a tool a card that can only finish when
something else on the loop answers it. Built inline, the card comes back
*starved*.

Dvara had the same freeze a second time, in its own code: the step line
it shows on the page asked for the same summary after the call ran. It
now says such steps by their call ([Dvara's note
56](https://github.com/kunwarmahen/dvara/blob/main/notes/56-a-look-as-them.md)).

## Not here

- ~~**A Watches panel on the page.**~~ Built: an eye chip opens the
  watches, drawn from Prahari's own commands, with looks, look now,
  pause and delete ([note 147](147-the-watches-on-the-page.md)).
- ~~**The answers chip for a watch.**~~ Built: a *seen* chip lights when
  a watch of yours fired since you last looked
  ([note 147](147-the-watches-on-the-page.md)).
- **Live offers measured on local models.** Note 116 counted Samay's
  offers on qwen and gemma. The same count for watches waits for a free
  GPU; the layer was written with that note's findings in hand.
