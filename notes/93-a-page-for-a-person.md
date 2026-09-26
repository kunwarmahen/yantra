# 93 — A page for a person

The browser tools ([note 28](28-browser-tools.md)) let the agent do the
web by itself. Two kinds of step should not be done by the agent at
all, and until now it had no way to say so:

* **Steps only a person can do.** A sign-in with 2FA on the person's
  phone, a captcha, "is this you?" from the bank. `--browse-login` beats
  these, but only before the agent starts ([note 58](58-the-browser-you-already-have.md)),
  and only if the person saw the wall coming.
* **Steps only a person should do.** Paying for the flight. Typing a
  card number, a passport number, an address. The agent can find the
  flight and compare the fares; buying it is the person's decision, and
  their card details have no business passing through a model.

`browser_handoff` is how the agent gives the page to the person.

## Two directions

```python
browser_handoff(mode="finish", reason="Book the 10:13 Frontier nonstop")
browser_handoff(mode="return", reason="Sign in to Gmail, then close the window")
```

**`finish`: the rest is theirs.** The page opens in the person's
**own** browser, the one with their saved cards and passwords, not in
the agent's. The agent's browser closes, and the result tells the model
its part is done: say what is on the page and what is left, and stop.
This is the flight case.

**`return`: help me, then I carry on.** The agent's browser closes,
and a visible window opens on the agent's profile at the same page. It
is the same window `--browse-login` opens, including the plain,
unautomated Chrome that Google's sign-in accepts. The person signs in
and closes it. The agent reopens the page, now signed in, and gets back
a snapshot of what it shows. This is the Gmail case.

Why `finish` does not reuse the agent's profile: the agent's profile
has the agent's logins and no saved cards. The person's own browser is
where they are already signed in to the airline and where their
password manager lives. And once a page is handed over for good, the
agent has no reason to see what happens on it next.

## The rules

**NO ADDRESS ARGUMENT.** The tool hands over the page the agent is on,
never an address it was given. The approval prompt names that page:

```
hand the browser to you (the rest is yours):
https://www.google.com/travel/flights/booking?tfs=… -- Book the 10:13 Frontier nonstop
```

A web page can contain text written to steer the model. With a `url`
argument, a page that says "send the user to this sign-in page" is one
tool call from showing the person a convincing fake. Without one, what
the person approves is what opens.

**A WINDOW NOBODY CAN SEE IS REFUSED, NOT WAITED ON.** `return` opens a
window on the machine Yantra runs on. On a desktop that is the person.
In the container, or on a server reached from a phone, it is nobody,
and the turn would wait ten minutes for no one. So `return` checks for
a screen first. Without one it refuses and tells the model to use
`finish`, which becomes a link, or to ask the person to run
`--browse-login` themselves.

**A HELPED PAGE NEEDS A PROFILE.** Without `YANTRA_BROWSER_PROFILE`, the
agent's browser keeps nothing when it closes, so a sign-in done in a
handoff window would be gone before the agent reopened the page.
`return` refuses and says so, rather than letting the person sign in
for nothing.

**TEN MINUTES, THEN IT CLOSES FOR THEM.** A handed-back window that is
still open after `HANDOFF_WAIT_SECONDS` is asked to close the way
Ctrl-C asks the login window: one SIGINT, which makes Chrome write its
cookies on the way out ([note 61](61-the-signal-that-saves.md)). The
model is told the window was closed for them, and that what they did
may be unfinished.

## Where the page goes: `YANTRA_BROWSER_HANDOFF`

| value | `finish` | `return` |
|---|---|---|
| `window` | opens in the person's default browser | a window on this machine |
| `link` | the address goes in the answer | refused |
| `off` | the tool is not offered | |

Unset, it follows the machine: `window` when a screen is attached,
`link` when not. In the web UI a link in the answer is clickable, and
that is the one road that works when the person is not at the machine
Yantra runs on. If the person's browser will not open for some reason,
`finish` falls back to the link rather than claiming it opened.

## What was deliberately not built

* **The agent filling in payment or personal details.** The tool
  description tells the model to hand those off instead. The rule is
  written in the description, not enforced by the harness, and it is
  not meant to be: an approval prompt still stands between a model and
  `browser_fill`, and the person reading it is the enforcement.
* **Watching what the person does in the window.** The agent learns
  the result by reopening the page, not by recording the person's
  clicks or keystrokes.
* **Carrying half-filled forms across.** The window opens at the page's
  address. What is in the address survives (a Google Flights search is
  all in the URL); what was only typed into the agent's page does not.
  Moving live page state between two browsers is a much larger feature
  than the cases here need.
* **A handoff the web UI can cancel.** A `return` window blocks the
  turn until it is closed or the ten minutes run out, and the page's
  Stop button does not reach into it yet.

## Receipt

`qwen3.8:latest`, headless, `YANTRA_BROWSER_HANDOFF=link`:

```
$ uv run yantra --provider ollama --yolo "Hey can you find me fligth to
  Detroit on 5th Oct from Raleigh. Pick the best nonstop and hand it over
  to me so I can book it"
→ browser_open()
→ browser_click()
[... eight more browser_* calls ...]
→ browser_handoff()
╭─ browser_handoff() ──────────────────────────────────────────╮
│   "mode": "finish",                                          │
│   "reason": "Handing off the Frontier RDU→DTW Oct 5 nonstop  │
│ ($225) book…"                                                │
│                                                              │
│ Nothing can open a window from here, so the page goes to the │
│ person as a link. Put this address in your answer, [...]     │
│ https://www.google.com/travel/flights/booking?tfs=…          │
╰──────────────────────────────────────────────────────────────╯
Done! Here's the booking page for your flight — it was handed to
you as a link:

**Frontier F9 4937** — Mon, October 5
- RDU → DTW nonstop
- Departs 10:13 AM, arrives 11:55 AM (1 hr 42 min)
- **$225** one-way

🔗 [Open the booking page](https://www.google.com/travel/flights/booking?tfs=…)

What you'll find there: [...] You'll need to enter your passenger
details, select any baggage you need (Frontier's basic fare includes no
bags [...]), and complete payment.
── end_turn · 19869 in / 400 out · 12 iteration(s)
```

The model found the flight, chose `finish` by itself, and stopped at
the booking page. It left passenger details and payment to the person
and never touched the checkout.

An earlier run of a differently worded question used all 25
iterations on Google Flights' airport field. The suggestions under it
are not among the elements the snapshot collects, and there is no
Enter key to press, so the model tried to build Google's encoded search
address by hand with `bash`. That is a gap in the browser verbs
([note 28](28-browser-tools.md)), not in handing off, and it is listed
below.

## What is not here yet

* ~~**Autocomplete dropdowns.** The snapshot collects links, buttons,
  inputs and a few ARIA roles, but not `role="option"`, and there is no
  verb for pressing Enter.~~ Suggestions are clickable options and
  `browser_fill` can press Enter ([note 94](94-a-form-the-way-a-person-fills-it.md)).
* **Stop from the web UI** reaching a `return` window, as above.

`2159 passed, 1 skipped` (was 2139). The tests in
`tests/test_browser_handoff.py` pin that the tool takes no address,
that `return` refuses without a screen or a profile before anything
closes, that the agent's browser steps aside before the person's window
opens, and that a window nobody closed is asked to close, not killed.
