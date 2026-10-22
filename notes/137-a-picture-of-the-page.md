# 137 — A picture of the page

You're signed in to TradingView through Setu, and you ask the agent for
a picture of your ALAB chart. Until now it had to say no. The site's
tools ([note 112](112-a-site-with-no-api.md)) return the page as text
plus numbered elements, and a chart has almost no text to give. Its
only other picture tool, `read_image`, looks at a file that is already
in the folder. The answer it gave was honest but useless: *I can't
capture the live browser you're signed into.*

Every signed-in site now has one more tool: `tradingview_screenshot`,
`amazon_screenshot`, and so on.

## A picture is a read of what `open` read

`screenshot` takes the page that is open, as the person would see it:

- what's on the screen, by default;
- the whole page, with `full_page`;
- one element by its `[eN]` ref, such as the chart alone.

It loads nothing and presses nothing. Everything it shows, `open`
already read as text. So it belongs to the same class as `open`, and
comes with `open`.

That matters because of how site tools are granted. A site's file lists
its verbs (`open = "read"`, `click = "write"`), and a verb the file
doesn't name is never offered: nobody agreed to it. Under that rule, no
site written before today would have a screenshot, TradingView included.
So `screenshot` takes `open`'s class unless the file names it. A file
can still name it, and then its own class decides. A file with no
`open` gets no picture.

## Where the picture goes

It is **saved** in the agent's folder as
`screenshots/<site>-<date>-<time>.jpg`. A file is the one thing every
road can hand on:

- **Telegram, Sarathi's home chat:** Dvara's `send_file` sends it from
  the person's folder.
- **Yantra's page:** the tool card shows it, from
  `/api/screenshots/<name>`. Only plain names in that one folder are
  served.
- **Terminal:** the line names the file.

It is **attached** for the model to look at only when the model can see.
A picture sent to a model that can't take one fails the whole turn,
which is worse than no picture. Each agent's `ToolContext` now carries
`sees_pictures`. It asks Ollama's `/api/show` whether the local model
lists "vision", once per model, and asks again after `/model` switches.
A cloud model always can. A local model that can't see still gets the
file, with these words: *Your model can't see pictures, so it is not
attached: don't describe it.* It can send the file on, but it can't
invent what the picture shows.

This is not like the phone ([note 119](119-a-phone-and-what-asks.md)),
where a cloud model gets no screenshots unless the person allows it. A
phone's screen is anyone's messages and codes. A site's page is
something `open` already sent the model as text. The picture carries
the same content, not more.

## Sizes

A JPEG starts at quality 80, then tries 60 and 40 while it is over the
5 MB a model takes. A whole page is cut at 6000 pixels tall. A timeline
never ends, and a taller picture is one no model takes whole. If even
quality 40 is too big, the tool says so and suggests the screen, or one
element.

## Live receipt

`qwen3.8:latest` on Ollama, which lists vision, with TradingView's
public ALAB page in a scratch profile (no sign-in), real Chrome:

```
tools: ['tradingview_follow', 'tradingview_handoff', 'tradingview_open',
        'tradingview_screenshot', 'tradingview_scroll']
sees pictures: True
call: tradingview_open {'url': '/symbols/NASDAQ-ALAB/'}
call: tradingview_screenshot {}
answer: I've opened the ALAB (Astera Labs) page on TradingView and saved a
  screenshot to `screenshots/tradingview-20261021-222803.jpg`. The picture
  shows the Astera Labs, Inc. quote header for NASDAQ:ALAB at $290.06
  (+5.26, +1.85%) for the regular session, alongside a lower overnight
  reading of $280.73 (−3.25%) via BOATS, with tabs (Overview, Financials,
  News, etc.) and the top of the day's price chart below.
18s
```

Checked against the picture itself: the price, both changes, the tabs
and the chart are all there.

## What was not built

* ~~**`browser_screenshot` for the plain browser.** Those tools stay
  text-only ([note 28](28-browser-tools.md)). A sixth plain browser
  tool would change the default tool count, which tool selection is
  tuned to, for every person who installs the browser extra. A signed-in
  site is where the picture was asked for.~~ Built in
  [note 138](138-a-picture-of-any-page.md): counted, the browser extra
  already put selection on, and both tools now share one
  `save_picture`.
* **A picture sent by itself.** The tool saves and attaches; sending is
  the host's own tool (`send_file`), asked for like any file. A
  screenshot that went to the chat on its own would be the agent
  posting without a decision.
* **A photo on Telegram.** `send_file` sends a document, which keeps
  the full size. A compressed photo would blur a chart's small numbers.
