# 135 — Setu's own page, inside the panel

[Note 99](99-the-connections-page.md) put a Connections panel in Yantra's
page: cards for the accounts you connected, cards for the connectors
you could connect, and Yantra drawing all of them from Setu's report.
Setu then got a page of its own (`setu serve`). It shows the same
connections and more: servers of your own, what each connection did,
people's pages, certifiers, settings. That left two drawings of one thing,
and the copy in Yantra fell behind each time Setu's page learned
something. A server you added on Setu's page showed in Yantra's panel as
a bare card with no way to add another.

Now the panel shows Setu's page itself, in a frame, and Yantra draws only
what is Yantra's.

## What is whose

Setu's page, below: every connection, connect, change a level,
disconnect, *What it did*, add a site or a server of your own, the
catalog, certifiers, settings.

Yantra's strip, above it:

* a line saying what the page below is, with **open in its own tab**;
* a sign-in Yantra itself is running (one started from its own cards),
  while it runs;
* for a package, what it asked for (**allow** / **not now**) and what it
  was allowed (**forget**) — Setu doesn't know which agent asked;
* **recipes**, folded: which recipes on this computer need which account,
  and the catalog's recipes you could install. Recipes are Yantra's
  learned skills, not Setu's.

## Its own copy, started when first wanted

The first time a tab on this computer opens the panel, Yantra runs

```
setu serve --json --port 0 --embed http://127.0.0.1:8811 --embed http://localhost:8811
```

in this session's Setu folder (a person's, when the session is theirs).
`--port 0` takes any free port. `--embed` names the one page that may
frame Setu's, and any other site that tries to gets an empty frame.
`--json` prints a single line, the address with its key, and nothing
else:

```
{"format": "setu-page/1", "url": "http://127.0.0.1:40651/",
 "page_url": "http://127.0.0.1:40651/#token=…", "home": "/home/you/.local/state/setu",
 "embed": ["http://127.0.0.1:8811", "http://localhost:8811"]}
```

**A PAGE FOUND RUNNING IS NOT USED** unless the house names it (below).
Something already answering on 8775
looks like the obvious thing to frame. On a computer running Sarathi,
though, 8775 is Setu's container, which serves another folder and names
no Yantra as its embedder. Yantra would show the wrong accounts, or an
empty frame. Its own copy costs one small process, started only if the
panel is opened.

**IT ENDS WITH YANTRA.** Yantra holds the child's stdin, and `setu serve
--json` stops when its stdin closes. A Yantra stopped with Ctrl-C, killed,
or crashed takes its Setu page with it, without a shutdown hook that a
crash would skip.

**A NEW ADDRESS MEANS A NEW SETU PAGE.** The embedders are fixed when
Setu's page starts. Open Yantra as `localhost` after `127.0.0.1` and
Setu's page is started again, naming both.

## The tools follow

Framed, Setu's page tells its parent when the connections or connectors
it shows change:

```js
window.parent.postMessage({ type: "setu-changed", what: "connections" }, "*")
```

**THAT IT CHANGED, NEVER WHAT.** No name and no account is in the
message, so it can be said to any parent. Yantra listens only to its own
frame (`e.source`) at Setu's address (`e.origin`), and then asks Setu
again (`/api/connections/refresh`), so a connection made on Setu's page
has its tools on the next message. Mid-turn that ask is refused, and the
watcher from [note 127](127-a-sign-in-that-ran-out.md) catches up when
the turn ends. The message only makes it sooner.

## Where Yantra's own cards stay

Setu's page listens on this computer only. A Yantra tab on another
device, such as a phone on the house network, would load `127.0.0.1`
from the phone itself. That tab gets Yantra's own cards, as before, and
the endpoint refuses it plainly:

```
403  Setu's page opens on the computer running Yantra; this tab shows Yantra's own view of it
```

A Setu with no `serve --json` gets the cards too, with one line above
them: *Setu's own page can't be shown here (this Setu is too old to show
its page here …), so this is Yantra's view of your connections.*

## Receipt

A scratch Yantra on port 8811 (Setu found on PATH, 12 connections in its
folder), opened in headless Chrome on a throwaway profile:

```
cards in frame: 12 src: http://127.0.0.1:40651/
strip: Setu's own page, below: connect, change a level, disconnect, see what
it did. The agent's tools follow what you do there. | open in its own tab |
recipes | 9 on this computer — what this agent has learned to do with each account
```

The message, sent first by the top window itself and then by Setu's frame:

```
after a message from elsewhere: []
after Setu's frame said so: ['http://127.0.0.1:8811/api/connections/refresh']
frame src kept: http://127.0.0.1:40651/
```

The page reloaded and the panel opened again, on the same Setu page:
one child, kept. Yantra was stopped with Ctrl-C:

```
$ pgrep -af "setu serve --json" || echo "setu page gone with Yantra"
setu page gone with Yantra
```

Tests: `tests/test_connections_page.py` (a tab here gets the page started
once for its own address, a tab elsewhere doesn't, a new address starts
it again naming both, a Setu too old says so, the panel hears only its
own frame); Setu's `tests/test_page.py` (the one line, `--port 0`, the
embedder in the CSP, stdin closing ends it, the message carries no
name).

## On Sarathi's road: the house's own page

In Sarathi's containers none of the above could work. Yantra's page
answers a browser on this computer through Podman's bridge, so the tab
reads as another device (`local: False`), and a Setu page Yantra started
inside its container would listen where no browser can reach. So that
road kept the cards.

There, though, Setu's page on 8775 serves the *same* folder: Sarathi
gives Yantra's container and Setu's one Setu folder. So Sarathi says
where it is, and Yantra frames that one instead of starting its own:

```
Environment=YANTRA_SETU_PAGE=http://127.0.0.1:8775/                        (Yantra's)
Environment=SETU_PAGE_EMBED=http://127.0.0.1:8321 http://localhost:8321 \
                            http://linux-al-ml-beast.singhs:8321           (Setu's)
```

**THE KEY IS READ, NEVER HANDED OVER.** Setu keeps its page's key in its
folder (`page.token`), and Yantra reads it there, as Setu would
(`$SETU_HOME`, then the default). Nothing secret is in either
container's environment. With no key yet (Setu's page still starting),
the panel says so and keeps the cards.

**A TAB ON THE HOUSE'S NETWORK GETS IT UNDER THE SAME NAME.** Given as
`127.0.0.1`, the address is rewritten to the name the tab reached Yantra
by: a phone that opened `linux-al-ml-beast.singhs:8321` frames
`linux-al-ml-beast.singhs:8775`, which Sarathi already publishes there
for people's links. Setu's page names the house's address for Yantra as
an embedder, so it allows the frame.

Run on scratch ports (a Setu page on 8812 for a throwaway folder, a
Yantra page on 8811 told `YANTRA_SETU_PAGE=http://127.0.0.1:8812/`):
headless Chrome opened the panel and framed `http://127.0.0.1:8812/`,
showing *Your connections* for that folder. No `setu serve --json` of
Yantra's own was started. The first try found that Yantra looked for
the key in the default folder and ignored `$SETU_HOME`. It now follows
Setu's own order.

## What was deliberately not built

* ~~**Framing Setu's page for a tab on another device.**~~ On Sarathi's
  road, where it is the same folder: the house's own page, under the
  name the tab used (above). A plain `yantra --web` tab elsewhere still
  gets the cards.
* **Removing Yantra's cards.** They are what a tab elsewhere sees. They
  stay, and they may fall behind Setu's page again. That is the cost of
  keeping the other-device tab working.
* **Theming Setu's page to match Yantra's.** It is Setu's page, looking
  like Setu's. When framed it already drops its own brand line.
