# 138 — A picture of any page

[Note 137](137-a-picture-of-the-page.md) gave every signed-in site a
`screenshot`. The agent's own browser, the one with no sign-in, still
had none. Ask it to open a public chart and send you a picture, and it
could read you the page's words but had no way to show you the page.

`browser_screenshot` closes that gap. It works the same way as a
site's picture, on the page `browser_open` has open:

- what's on the screen, by default;
- the whole page, with `full_page` (cut at 6000 pixels tall);
- one element by its `[eN]` ref, such as a chart.

## The same picture, from the same place

Both tools hand the picture to `save_picture` in `tools/browser.py`.
The file is saved as `screenshots/browser-<date>-<time>.jpg` in the
agent's folder. It is attached for the model only when the model can
see (`ToolContext.sees_pictures`). A model that can't see gets the file
name and is told not to describe the picture. A chat's `send_file`
finds the file there, and Yantra's page shows it under the tool card,
as with a site's picture. One function means the two pictures can't
drift apart: same sizes, same folder, same words to the model.

## It does not ask again

Every other `browser_*` tool asks before it runs. They reach the
network from outside every sandbox, and a click or a fill can change
something on a site. A screenshot loads nothing and presses nothing.
It shows the page `browser_open` already brought, and that page went
through its own approval. So `browser_screenshot` is read-only and
runs without asking, the same rule note 137 gave a site's `screenshot`
(it takes `open`'s class).

It still needs `browser_open` first (`requires`). With no page open,
or after the browser has closed at the end of a turn, it says so.

## The tool count

Note 137 left this tool out for one reason: a sixth plain browser tool
would change the default tool count, which tool selection is tuned to.
Counted, that worry mostly falls away. Tool selection switches on past
20 tools ([note 17](17-tool-selection.md)). The sixteen built-ins plus
the five browser tools already made 21, so anyone with the browser
extra already had selection on. Twenty-two changes nothing for them.

One setup does change. With `YANTRA_BROWSER_HANDOFF=off` there were
exactly 20 tools, and now there are 21, so selection switches on. A
person who wants every tool sent anyway still has `--tool-select 0`.
Without the browser extra nothing moves: sixteen tools, as before.

With selection on, a picture has to be found from plain words. Nobody
types `browser_screenshot`; they say "picture" or "screenshot". The
description uses both words, and a test pins that, on the default
catalog, *"take a screenshot of the chart"* and *"open tradingview
ALAB and send me a picture"* both put `browser_screenshot` first, with
`browser_open` beside it.

## Live receipt

Real Chrome (`/usr/bin/google-chrome`, headless) on a scratch profile,
the tools driven directly, TradingView's public ALAB page:

```
tools: ['browser_click', 'browser_close', 'browser_fill', 'browser_handoff',
        'browser_open', 'browser_screenshot']
title: ALAB Stock Price and Chart — NASDAQ:ALAB — TradingView | ...
read_only: True | asks: picture of the screen on https://www.tradingview.com/symbols/NASDAQ-ALAB/
picture of https://www.tradingview.com/symbols/NASDAQ-ALAB/ saved to
  screenshots/browser-20261022-073501.jpg (52 KB) -- it is attached to this result. ...
attached: 1
picture of https://www.tradingview.com/symbols/NASDAQ-ALAB/ saved to
  screenshots/browser-20261022-073501-2.jpg (556 KB) -- it is attached to this result. ...
```

The first file shows the page as a person sees it: the Astera Labs
header, the tabs, and the top of the day's price chart. The second,
two pictures in one second, got its own name and did not overwrite the
first.

The model's half (looking at the attached picture and describing it)
is note 137's `save_picture`, unchanged, which `qwen3.8:latest` ran
live on the same page.

## What was not built

* **A picture sent by itself.** As in note 137, the tool saves and
  attaches; sending is the host's `send_file`.
* **A picture with every page.** `browser_open` still returns text
  only. Text is what local models work from best, and a picture on
  every step would fill a small model's window with images it didn't
  ask for.
