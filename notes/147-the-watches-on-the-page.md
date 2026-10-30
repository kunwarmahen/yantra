# 147 — The watches on the page

A watch ([note 140](140-a-watch-offered.md)) is made in a conversation on
Yantra's page: "tell me when a mail from the bank arrives", a card, a yes.
Then it went somewhere else. To see it again, pause it, or read what it
saw, the person had to open Prahari's own page — a second address, with
its own token — or a terminal. A schedule made on the same page had a
panel right there ([note 115](115-what-a-yes-covers.md)) and a chip that
lit when it said something ([note 125](125-the-answer-comes-back-here.md)).
The two "later" things the agent offers were seen in two different places.

## What it does

An **eye** chip in the header, `2 watching`, opens a **Watches** panel:

* each watch with what you asked for, in your words (*You asked: "tell
  me if bitcoin is under a million"*), and in its own sentence
  (Prahari's: *every hour, looks at api.coinbase.com/v2/prices (with
  web_fetch); tells you when BTC's price is below 1,000,000*), what happens
  when it fires, its id, *listens for whatsapp* or *woken by gmail-mail*,
  *for asha* when it is somebody else's, how many times it fired today,
  when it looks next, why it paused, and its last look;
* **looks** (every look, newest first: how it ended, how it came — *looked
  now*, *heard at the inlet*, *woken by a knock* — and what it saw),
  **look now**, **pause** / **resume**, **delete** (asked once, in a
  dialog, because a watch's memory of what it has seen goes with it);
* on each watch that looks, a box with how often (*30m*, *3h*, *1d*) and
  **set**: `prahari edit ID --every STEP`, which keeps what it saw and
  brings the next look nearer when the step got shorter. The box takes a
  number and a word and nothing else, so nothing typed there reaches
  Prahari's command line as a flag;
* a warning at the top when `prahari serve` isn't running, and where news
  goes (*to Yantra's page (its Watches panel) and Prahari's own*, or *to
  mahen's chat through Dvara, and to Yantra's page*).

A second chip, `2 seen`, appears when a watch has fired since you last
opened the panel, and the panel opens with **what they saw** at the top.

## Why it is drawn from the program

THE PANEL RUNS PRAHARI'S OWN COMMANDS, AS THE SCHEDULES PANEL RUNS
SAMAY'S. `prahari list --json`, `looks`, `pause`, `resume`, `rm` work
whether `prahari serve` is up or not, need no token, and print the same
JSON Prahari's page is drawn from. So Yantra holds no copy of anything and
learns no key: the inlet's key stays Prahari's to show. An id that isn't
an id (`--help`) is refused before any program runs.

LOOK NOW IS NOT WAITED FOR. A look is a whole `yantra --call` — a few
seconds for an API, most of a minute for WhatsApp Web. The page starts
`prahari look ID` and lets it run; the look shows in the watch's looks
when it is done, as a run started from the Schedules panel does.

THE LOOK GETS THIS YANTRA. Prahari finds the Yantra that looks by
`PRAHARI_YANTRA`, which `prahari serve` is given and the page's own
process often isn't. The first live try from the page failed with
*cannot find yantra: set PRAHARI_YANTRA …*. Now a look started from the
page, when nothing names a Yantra, is given the one the page runs (the
`yantra` beside its Python, started where the page was, so the same
`.env` and accounts). One already named is left alone.

ONLY WHAT MATTERED LIGHTS THE CHIP. *Seen* is this person's looks that
**fired**, were **limited** (it mattered, but the watch had fired its
most for the day) or **need the person** (a sign-in wall). A quiet look,
the first look that only learns, and a broken one don't: a watch that
breaks often pauses itself, and its row says why. Somebody else's watch
(a Dvara person's) is listed, tagged *for asha*, but never lights your
chip. Which ones you've seen is the browser's to remember, as for
answers; reading them changes nothing anywhere else.

A count that moved in the turn is right when the turn ends: each of
Prahari's write tools is followed by a fresh `prahari status --json`,
as Samay's are.

## Live receipt

A scratch Prahari state, the real `prahari` program, no model anywhere:

```
$ prahari add "BTC under a million" --check web_fetch \
    --args '{"url":"https://api.coinbase.com/v2/prices/BTC-USD/spot"}' \
    --pick 'json:data.amount' --when 'below 1000000' --every 1h ...
$ prahari look e73212c6        # fired      80,340.52
$ prahari look e73212c6        # quiet      80,345.38
$ prahari hear whatsapp '{"title":"Mum","text":"Dinner at 8?"}'   # fired: Mum
$ PRAHARI_STATE=/tmp/pr-scratch yantra --web --port 8329 --prahari .../prahari
```

The page's header said `2 watching` and `2 seen`; opening the panel
showed *what they saw* (Mum on WhatsApp, *fired 18:12 · heard at the
inlet*, *title: Mum · text: Dinner at 8?*; BTC, *fired · looked now*),
both watches, and BTC's looks; the *seen* chip went away. Through the
page's API:

```
POST /api/watches/e73212c6/look    -> {"started": "e73212c6"}   (then: quiet now 80,345.38)
POST /api/watches/7925f9e9/pause   -> state paused; /resume -> active
POST /api/watches/nope/pause       -> 404 "prahari: no watch nope"
```

## Not here

- **Making or changing a watch from the panel.** A watch is a recipe the
  agent writes against a tool's real answer; the person's part is the
  yes. Prahari's own page has a **New watch** form for anyone who wants
  to write one by hand.
- **The inlet's address and key.** They open a door from outside; they
  stay on Prahari's page and `prahari inlet`.
- **Mailboxes.** Added at the computer (`prahari mailbox add`); listed on
  Prahari's page.
