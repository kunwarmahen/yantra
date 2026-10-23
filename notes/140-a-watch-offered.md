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

What it looks at, with no model
  every 1h: web_fetch {"url": "https://api.coinbase.com/v2/prices/BTC-USD/spot"}
  web_fetch IS NOT READ-ONLY: saying yes lets Prahari run exactly this call every 1h, with nobody asked again
Tells you when
  the value at data.amount is below 60000
  now: web_fetch isn't a tool that only reads, so it can't be tried before the person accepts it; every look will run exactly this call once they do.
Then it asks me, with nobody watching
  Tell me the price and how far it fell this week.
  may use without asking: only tools that read
  what the watch found is given to me as text from outside, never as instructions

At most 10 times a day. News goes nowhere yet: it is kept on Prahari's page (no Dvara set).
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

## Not here

- **A Watches panel on the page.** Samay has a panel in `yantra --web`;
  Prahari has its own page, linked from Sarathi's home page. A panel
  here would be a second copy of it.
- **The answers chip for a watch.** A fire goes to the person through
  Dvara, or stays on Prahari's page. Bringing it into Yantra's page, as
  note 125 did for schedules, can follow when somebody misses it.
- **Live offers measured on local models.** Note 116 counted Samay's
  offers on qwen and gemma. The same count for watches waits for a free
  GPU; the layer was written with that note's findings in hand.
