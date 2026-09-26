# 92 — The window that stayed open

One ordinary question showed three problems at once:

> Hey can you find me fligth to Detroit on 5th Oct

It was asked in the browser UI on a local model (`qwen3.8:latest`),
with the browser tools installed and a visible Chrome
(`YANTRA_BROWSER_HEADED=1`, [note 58](58-the-browser-you-already-have.md)).
The answer did arrive, a table of nonstop flights. But:

1. before touching the browser, the model went looking for a flight
   **skill** that does not exist;
2. the Chrome window it opened was **still on screen** after the
   answer was written;
3. the table arrived as rows of literal pipes. That one was the page's
   markdown renderer, fixed on its own
   ([note 22](22-web-ui.md#rendering-the-models-prose)).

This note is about the first two. They turned out to be the same kind
of mistake: something the model could not see, or could not be relied
on to do, left to the model anyway.

## Why it went looking for a skill

Yantra sends each turn only the tools that look relevant
([note 17](17-tool-selection.md)). Some are always sent (read and write
files, `bash`, `load_skill`, the `list_available_tools` hatch) and the
rest are chosen by matching words in the conversation against each
tool's name and description.

For "find me fligth to Detroit" the match picked `read_image` and
`glob` (both are about *finding* files), `web_fetch`, and
`browser_fill`, whose description mentions running searches. It did
not pick `browser_open`, and `browser_open` is the only browser tool
that can start anything. The model was handed a tool for typing into a
page with no way to open one.

So it reasoned the way it was set up to. From a traced run:

```
· thinking
The user wants to find flights to Detroit on October 5th. [...] This
requires a flight search tool. Let me check if there are available
tools for flights. [...] maybe there's a travel/flight tool from a
skill or a separate tool.
→ list_available_tools()
```

With `load_skill` in front of it and nothing that looked like a way to
search the web, a skill was the obvious place to look.

## A tool can name the tool it needs

**A VERB ARRIVES WITH ITS OPENER.** A tool can now say which tools it
is useless without:

```python
class BrowserFill(_BrowserTool):
    name = "browser_fill"
    requires = ("browser_open",)  # no page without it
```

When selection picks a tool, it brings along what that tool
`requires`. The prerequisite takes the place of the lowest-ranked
retrieved tool, so the number of tools sent does not grow, and it never
displaces a pinned tool. `browser_click` and `browser_fill` declare
`browser_open`; nothing else declares anything yet.

This is the second time the browser family has hit the same problem.
[Note 60](60-the-tool-that-explained-itself.md) fixed the ranking that
put browser_open's thorough description below its terse siblings. That
fix was about how tools *score*. This one covers queries where the
entry point scores nothing at all. Note that "fligth" matches nothing,
spelled that way. Scoring cannot rescue a word it has never seen; a
declared prerequisite does not depend on the words.

`browser_open`'s description also now says what the tool is *for* —
live information a site shows or searches for you: flights, prices,
schedules, availability. That is for the model reading it as much as
for the ranker. A correctly spelled "find me flights" now retrieves it
directly.

**THE SKILL LIST SAYS IT IS THE WHOLE LIST.** The skill roster already
told the model to load a skill "when a task matches one". It now also
says the listed skills are the only ones, that when none fits it should
not call `load_skill` at all, and that an unlisted name does not exist.
A capable model infers that from the list. A small one needs it said.

## Why the window stayed open

The browser closed only when the model called `browser_close`. A model
that has its answer writes the answer and stops; tidying up is not part
of answering. In the terminal that mattered little, because the process
exits and takes Chrome with it. The browser UI is one long-running
server, so the window stayed up until someone stopped the server.

It cost more than screen space. The profile directory is locked while a
Chrome holds it, so a later session that tried to browse found the
profile locked. In one reproduction, run with `--yolo`, the model
diagnosed the lock itself and deleted Chrome's lock files with `bash`:
five steps of cleaning up after the previous turn before it could
start on the question it was asked.

**A TURN THAT IS OVER LETS GO.** Every tool now has a `turn_ended()`
hook, which does nothing by default. The loop calls it on every tool
when a turn ends, and the browser tools use it to close the session.
Three rules decide when "ends" applies:

* **Every way out counts.** An answer, the iteration cap, the budget,
  an error from the provider, and a consumer that closes the stream
  mid-turn all release. The release runs *before* the `TurnEnd` event
  is handed out, not in a `finally`, because `Agent.run()` returns as
  soon as it sees `TurnEnd` and never resumes the generator. A release
  that waited for the generator to finish would wait for garbage
  collection, which is the bug again.
* **A held turn keeps everything.** A turn paused for approval
  ([note 88](88-not-yet.md)) has not ended. When the person
  approves, the resumed turn clicks on the page it was looking at, so
  the page has to still be there. The release happens when the resumed
  turn ends.
* **A sub-agent never releases.** A child is given its parent's tool
  objects, not copies ([note 08](08-sub-agents.md)). A child finishing
  is the middle of its parent's turn, and closing the browser there
  would pull the page out from under the parent. A child is recognised
  the same way its approval clock is, by the parent turn stamped on it
  ([note 91](91-one-clock-for-the-child.md)).

A tool whose `turn_ended` raises is ignored: tidying up must never cost
the answer it follows.

## The tradeoff

A follow-up turn now starts with no page. "Click the cheapest one"
after the answer means opening the results again. The model still has
the URL in its history, and the error it gets without a page says why:

```
no page open -- the browser closes when a turn ends, and refs from an
earlier turn are gone; browser_open(url) first
```

That costs a page load per follow-up. The alternative, a window that
stays until an idle timer fires, leaves the exact thing the person
complained about on screen for minutes after every answer, and still
holds the profile lock the whole time. Logins are not affected: they
live in the profile on disk ([note 28](28-browser-tools.md)), not in
the open window.

## What was deliberately not built

* **No idle timer.** It adds a thread, a clock and a race with the next
  turn, all to keep open a window people want closed.
* **No generic "session" object for tools.** One hook on `Tool` covers
  the browser, and a background job or an open connection could use it
  later. A lifecycle framework with one user is a framework waiting for
  a reason.
* **No automatic cleanup of a stale profile lock.** Deleting Chrome's
  lock files is safe only when no Chrome holds the profile, and
  checking that reliably is exactly where the model's own attempt went
  wrong (`pkill -f` matched its own shell). With turns releasing, the
  stale lock should not be created in the first place.

## What is not here yet

* Only the browser declares `requires`. An MCP server whose tools need
  a `login` call first could use the same field, but nothing reads MCP
  schemas for it.

## Receipt

The same question on `qwen3.8:latest`, headless, from the terminal,
with the departure city added so a one-shot run does not stop to ask:

```
$ uv run yantra --provider ollama --yolo \
    "Hey can you find me fligth to Detroit on 5th Oct from Raleigh"
skills: 4 loaded -- eval-suite, new-tool, notes-entry, repo-survey
→ browser_open()
→ browser_click()
→ browser_click()
→ browser_fill()
→ browser_click()
→ browser_open()
Here are flights from Raleigh (RDU) to Detroit (DTW) on Monday, Oct 5,
2026 (one-way, per Google Flights):
[...]
── end_turn · 12672 in / 453 out · 7 iteration(s)
```

The first call is `browser_open`, with no `list_available_tools` and
no `load_skill`. Before the change, the same question spent its first
iteration on `list_available_tools`, and in the reproduction that met a
leftover lock, five more on `bash` clearing the profile.

`2125 passed, 1 skipped` (was 2106). The new tests are in
`tests/test_turn_release.py`: every way a turn ends releases, and a
held turn and a child do not. There is also a test that the release
has already happened when `TurnEnd` is seen. `tests/test_selector.py`
covers prerequisites, including that a pin is never traded away.
