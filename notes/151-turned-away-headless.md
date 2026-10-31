# 151 — Turned away headless

You paste a grocery store's product page and ask the agent to tell you
when the price drops. It fetches the page: timeout. It opens it in the
browser: `net::ERR_HTTP2_PROTOCOL_ERROR`. It tries `curl` from a shell:
the stream closes before a single byte. So it tells you, with total
confidence, that the store is blocking this machine's network, and that
the page "opens fine on your side". Then you ask it to open the page in
a real Chrome window and it can't, because its hand-off button only
passes on a page that has already loaded.

Every word of that diagnosis was wrong. On the same machine, in the same
container, the same Chrome **with a window** loaded the page and showed
the price:

| How the page was opened | What came back |
|---|---|
| `web_fetch`, `curl` (HTTP/2 or 1.1) | dropped, or nothing |
| Chrome, headless | `ERR_HTTP2_PROTOCOL_ERROR` |
| the same Chrome, headed, on an invisible Xvfb | the page, `$6.19` |

The store's bot shield (it covers the parent company's other sites too)
doesn't show a robot check to a client it doesn't like. It ends the
connection. Nothing in the error says "bot", so a model reads it as a
network problem.

## One more try, with a window

When a headless load ends the way a shield ends it, `browser_open` opens
the page once more with a window, and says so above the page:

```
(this site turned the headless browser away, so it was opened again with a window -- on a screen nobody sees)

title: Mayfield Dairy Farms Premium Black Cherry Flavored Ice Cream 1.5 qt, 1.5 qt - Harris Teeter
```

**ONLY WHAT A SHIELD DOES.** These count: the connection dropped
mid-answer (`ERR_HTTP2_PROTOCOL_ERROR`, `ERR_CONNECTION_RESET`,
`ERR_CONNECTION_CLOSED`, `ERR_EMPTY_RESPONSE`), or a 403. A 401 is a
sign-in page, and no window changes that. A timeout is usually just a
slow site, and trying again would double a wait that's already long.

**ITS OWN SCREEN, EVEN ON A DESKTOP.** `YANTRA_BROWSER_HEADED=1` on a
desktop paints on the desktop, because you asked for a window. The retry
wasn't asked for, so it always gets an Xvfb of its own. A browser window
popping up in front of you in the middle of an answer would be its own
bug.

**ONCE, AND UNTIL IT CLOSES.** Turned away twice, it stops. The session
stays headed until the browser closes, so a click on the page the window
loaded doesn't fall back to headless and get dropped again. The next
browser starts headless.

**NOT A DISGUISE.** Nothing is spoofed. The window is real and Chrome is
Chrome; nobody is looking at the screen, that's all
([note 58](58-the-browser-you-already-have.md) argues why that is the
honest version). A site that refuses a headed browser too still refuses
it.

`YANTRA_BROWSER_HEADED=0` turns the retry off. Unset means "headless
while it works", `1` means headed always, and `0` means headless no
matter what.

## Saying what it means

Turned away both ways, the error now says what it means and what to do
next, so a model has no reason to invent a cause:

```
could not load https://...: Error: Page.goto: net::ERR_HTTP2_PROTOCOL_ERROR ...
-- it was tried again with a window and turned away again. The site is
refusing automated browsers; that is not this machine's network, and
trying again now will not change it. To give the person the page, call
browser_handoff with mode='finish' if you have it ...
```

A failed `web_fetch` (a timeout, a drop, a 403) adds one line: some
sites drop plain fetches and still load in a real browser, so try
`browser_open` before saying the site is down or blocking this machine.

## Handing over a page that never loaded

`browser_handoff` takes no address, and that's on purpose: a page that
talks the model into "sending you to" a lookalike sign-in would need one
([note 28](28-browser-tools.md)). It handed over the page the agent was
on. That meant a site that never loaded, which is exactly the one your
own browser should get, couldn't be handed over at all.

Now `mode='finish'` with no page open hands over **the address last
given to `browser_open`**. It still takes no address argument. You
already approved that address once, the approval card for the hand-off
names it, and it holds through a closed browser, so the next turn can
still pass it on. `mode='return'` still needs a live page, since its
whole point is to get one back.

## Watches

A watch's check is one tool call, run with no model
([note 98](98-one-call.md)). Prahari grants the watch its own call word
for word, so `browser_open` on a fixed address can be a check, just as
`web_fetch` can. A page that only a browser can read gets watched with
the browser, and the retry happens inside the look the same way.

## What was deliberately not built

**No stealth.** No user-agent rewriting, no patched `navigator`, no
fingerprint noise. Shields are updated all the time, and a disguise
that works this month fails silently next month, in exactly the way
this note started with.

**No remembered list of sites that need a window.** A failed headless
load costs about a second (the drop is quick), so paying it again on
the next visit is cheaper than keeping a list that goes stale.

**No phone browser fallback yet.** A connected Android phone has its
own network and a real mobile browser. It's a plausible last resort,
but opening tabs on a phone someone is holding is a different decision,
and it's left open.

## What the tests pin

`tests/test_browser_turned_away.py`: a drop or a 403 is tried again
headed and a 401 or a timeout is not; the retry always gets its own
Xvfb, even with `DISPLAY` set; it happens once; the session goes back
to headless when it closes; no Xvfb or `HEADED=0` means no retry; and
`finish` hands over a failed address even after the browser has closed,
while `return` still refuses.

## Receipt

From a desktop session, a throwaway profile, `YANTRA_BROWSER_EXECUTABLE=chrome`,
no model:

```
$ yantra --memory off --call browser_open --args '{"url": "https://www.harristeeter.com/p/mayfield-dairy-farms-premium-black-cherry-flavored-ice-cream-1-5-qt/0007524340131"}' --allow-tools 'browser_open(url=https://www.harristeeter.com/p/...)'
(this site turned the headless browser away, so it was opened again with a window -- on a screen nobody sees)

title: Mayfield Dairy Farms Premium Black Cherry Flavored Ice Cream 1.5 qt, 1.5 qt - Harris Teeter
...
Located in FROZEN
Low Stock
$6.19
$6.99
Sale: $6.19 discounted from $6.99
```

No window appeared on the desktop.
