# 94 — A form the way a person fills it

Asked to find the cheapest nonstop from Raleigh to Detroit, a local
model (`qwen3.8:latest`) spent all 25 of its iterations on Google
Flights' airport box and finished with nothing. It typed "Detroit".
The page showed a list of airports underneath, which a person would
click. The model could not see that list, and there was no Enter key to
press. So it tried to build Google's encoded search address by hand,
decoding base64 with `bash`, until it ran out of turns.

Working out why turned up five gaps in the browser verbs
([note 28](28-browser-tools.md)). Each one is small, and together they
made an ordinary search form impossible to fill. Each is fixed here,
and each was checked against the live page before the model was
pointed at it.

## 1. Suggestions are options

An autocomplete's suggestions are `<li role="option">`. The snapshot
collected links, buttons, inputs and a handful of ARIA roles, but not
`option`, so the list was invisible. It now also collects `option`,
`menuitem`, `tab`, `radio` and `switch`. Typing "Detroit" into the
live page now gives back:

```
[e20] option Detroit, Michigan
[e22] option Detroit Metropolitan Wayne County Airport (DTW)
[e23] option Detroit Lakes, Minnesota
```

and the model clicks the one it means. `browser_fill`'s description
says this happens: suggestions appear as `option` refs, click the
right one rather than guessing.

## 2. Enter

`browser_fill` takes `enter: true` and presses Enter after typing. It
is what submits a search box with no button, and it makes most
autocompletes take their top suggestion. It is a flag on the fill
rather than a new tool, because every tool competes for a place in the
set sent each turn ([note 17](17-tool-selection.md)), and pressing
Enter on its own, after something other than typing, is rare.

## 3. One ref, one element

Refs were written onto the page as a `data-yantra-ref` attribute and
never removed. An element tagged `e18` in one snapshot kept the tag
after it was hidden. The next snapshot handed `e18` to something new,
and a fill on `e18` failed:

```
fill on e18 failed: Error: Locator.fill: Error: strict mode violation:
locator("[data-yantra-ref=\"e18\"]") resolved to 3 elements
```

**REFS ARE PER SNAPSHOT.** Every snapshot now removes all old tags
before writing new ones. This bug predates everything else here; it
went unnoticed because simple pages rarely hide one element and show
another in its place, and a search form does that at every step.

## 4. The dialog first, and its way out

Opening the date picker produced 416 clickable elements, and a
snapshot lists 60. They were listed in page order: menu, logo, tabs,
form, and then the calendar. The listing ran out around September,
and every day it did show read `button 27` with no month. The model
clicked a date, the page said October 6, and it spent the rest of its
reply budget on weekday arithmetic.

**AN OPEN DIALOG IS LISTED FIRST.** When a dialog is open, a date
picker or a cookie banner, it is where a person's attention is. Its
elements now come first, and the snapshot says so:
`(a dialog is open -- its elements are listed first)`.

**A SHORT LABEL BORROWS ITS CHILD'S.** A calendar day's own text is
"5", and the date it stands for is in a child's `aria-label`. When an
element's label is three characters or fewer, the snapshot uses a
labelled child instead: `[e14] button Monday, October 5, 2026`.

**A DIALOG TOO BIG TO LIST KEEPS ITS TAIL.** A calendar is hundreds of
days and then the one button that closes it. Listing only the first
sixty days would hide **Done**, and with the dialog listed first, the
page's Search button behind it too. When the dialog alone overflows
the cap, its last `DIALOG_TAIL` elements are kept and the gap is
marked:

```
[e14] button Monday, October 5, 2026
[e15] button Tuesday, October 6, 2026
...
[... more in the dialog ...]
[e334] button Next
[e335] button Done.
```

## 5. A covered element is clicked the way its page would

Playwright will not click an element that another element covers. That
is the right check for a stray overlay and the wrong one for how rich
pages are built. A Google Flights result is a link whose own contents
are drawn on top of it, so clicking the $225 row failed every time:

```
<div ...>1 hr 42 min</div> from <div ...> subtree intercepts pointer events
```

A person's click on the same row lands fine. **ON THAT ONE FAILURE,
THE CLICK GOES THROUGH THE PAGE'S SCRIPT**: `el.click()` fires the same
click event on the element itself. The first try is now 3 seconds, not
10, because a covering element does not move. Any other failure is
reported as before. The result says what happened:

```
(another element covers e39, so it was clicked through the page's script)
```

It is said because the model and whoever reads the transcript should
know the click did not come the ordinary way.

And a dropdown that the page draws itself (`role="combobox"` on a
`<div>`, like Google Flights' "Round trip") refuses `browser_fill`,
because it is not a real `<select>`. The error now says what to do:
`browser_click e12 to open it, then browser_click the option 'One way'`.

## What was deliberately not built

* **Site-specific code.** Nothing here knows about Google Flights. The
  options are ARIA roles, the dialog is `role="dialog"`, the labels
  come from `aria-label`, and each rule is one any accessible site
  follows.
* **Script clicks in general.** Only the covered-element failure
  falls back. A disabled button, a detached element or a slow page
  still fails the way it did, because clicking those through script
  would do something a person could not.
* **A larger cap.** Sixty elements is what a small model can read
  without losing the question. Putting the right sixty first was the
  fix, not listing more.

## Receipt

`qwen3.8:latest`, headless, a fresh profile,
`YANTRA_BROWSER_HANDOFF=link`, told to use the form rather than find
its own way round it:

```
$ uv run yantra --provider ollama --yolo "Open https://www.google.com/
  travel/flights, set it to one way, type Raleigh into the From box and
  Detroit into the To box (pick the suggestions), pick October 5 in the
  date picker, search, then hand me the cheapest nonstop so I can book it"
```

What it did, from its own narration:

```
One-way mode has been enabled. [after one failed fill on the drawn dropdown]
The suggestions are shown. I should pick "Raleigh, North Carolina"
Select "Detroit, Michigan" (the city). This covers DTW.
October 5th is selected [...] Next, click Done (e337) and then click "Search".
The results are in. The cheapest nonstop (direct) flight is Frontier
Airlines 10:13 AM–11:55 AM, $225 [...]
│ (another element covers e38, so it was clicked through the page's script)
It's about to spend money—hand over to the person in 'finish' mode.
── end_turn · 24726 in / 458 out · 16 iteration(s)
```

It ended on Frontier's booking page, handed over as a link. The one
error in the run was the drawn dropdown, which the model recovered from
by clicking it, and whose message now tells it to. Before these
changes, the same site took all 25 iterations and did not get past the
airport box.

Without the instruction to use the form, the model often skips it
altogether. It puts the question into a Google search URL and reaches
the same results in three iterations. Both ways are fine; the form now
works too.

`2171 passed, 1 skipped` (was 2159).
