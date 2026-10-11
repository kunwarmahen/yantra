# 125 — The answer comes back here

[Note 115](115-what-a-yes-covers.md) gave a new schedule an approval
card in words, and a Schedules panel on the page. Both were about
*making* a schedule. Neither said what happens to what it finds.

## A timer that answered nobody

A person on this page asked for a check every morning. They said yes on
the card, and the schedule ran on time. Then nothing happened. The
answer never came to this page, and it never came to Telegram. It was
on Samay's page, if they thought to look there.

The reason is in how the schedule runs. A schedule made at this computer
goes on Samay's direct road: Samay starts a Yantra turn with nobody
attached, and writes down what it said. Samay sends answers through
Dvara only for a schedule that Dvara made, for a person Dvara knows.
This one had nobody.

## The reply is already written down

Every run's reply is in Samay's record, whether or not it was sent
anywhere. So the page doesn't need anything sent to it. It reads.

`Samay.answers()` asks Samay for its latest runs (`samay runs --json`)
and keeps those that belong to this session's person and have something
to read: a reply, a failure, something only the person can fix, a held
step, a run refused because nothing allowed what the job needed. A quiet run (*nothing new*), a skipped one and a missed one are
left out, since there's nothing in them to read. `GET
/api/schedules/answers` hands the list to the page.

**THE PAGE REMEMBERS WHAT YOU'VE SEEN, NOT THE SERVER.** An **answers**
chip appears when there's a reply newer than the last one this browser
showed you: *1 new answer*. Click it and the Schedules panel opens with
**what they said** at the top, newest first, the new ones outlined. The
reply is drawn as the chat draws one. Opening the panel marks them seen,
and the chip goes away. That fact lives in the browser, because reading
an answer changes nothing anywhere else, and a second browser should
still see it as new. The page asks again once a minute while it's open.

## And it goes on to you

Sarathi tells Samay's clock who the person at this computer is
(`SAMAY_DVARA_ACTOR`, Sarathi's note 12). With that set, a schedule
made here still runs on the direct road, but its answer also goes
through Dvara: to Telegram, and to the chat on Sarathi's home page.
Samay's clock writes where it sends into `serve.json`, and `samay
status` reports it as `local_to`. That matters because Yantra runs
`samay status` with its own environment, not the clock's.

## The card, laid out like the form

The old card was right but hard to follow: every fact on one indented
line, the prompt last. Samay's own page has a **New schedule** form
whose headings a person already reads easily. The card now uses them,
in the form's order:

```
New schedule. At each time it runs with nobody watching.

What it does each time
  Check whether https://example.com is up: use web_fetch to load …
When
  every day at 07:30 -- next: Sun 11 Oct 07:30, Mon 12 Oct 07:30, …
Tell me
  after every run -- on Yantra's page (its answers chip)
May use without asking
  web_fetch

Anything else that changes something is refused while nobody is there.
```

**IT IS STILL TEXT.** The same card goes to a terminal, and to Telegram
through Dvara, so the layout lives in the words, not in markup.

**A LONG PROMPT IS CUT.** Note 115 put the prompt last so that a long
one couldn't push the line that matters off the bottom. With the prompt
first, the card shows its first 240 characters and says *all of it under
raw arguments*. What the run may do unasked is always on the card.

**TELL ME SAYS WHERE.** Here it's *on Yantra's page (its answers chip)*.
Once Samay's clock sends through Dvara it adds *and to you through Dvara
(Telegram, Sarathi's page)*. Behind Dvara, for a person's own turn, it
says *in your chat*. A schedule set to `never` says only *it is kept for
you to read here*.

## Asked, not assumed

On this page the card also has a **Tell me** list: *only when there is
something new*, *after every run*, *never — keep it here for me to
read*. It starts at what the model chose. Approving with a different
choice is an edit: the card comes back with the new words, and the next
approve is the yes. That is the same "what you approve is what runs"
rule as every edited card.

## Receipt

A scratch Samay state and `yantra --web` on `qwen3.8:latest` through
Ollama, with memory off, driven in Chromium.

*"Every day at 07:30, check https://example.com is up and tell me."*
The card is the one above. The model had chosen *after every run*, and
approve saved it. `samay run-now` ran it on the direct road:

```
Status check complete:
**https://example.com is up and responding normally.** ✅
```

Back on the page, with no reload trick: the chip read *1 new answer* at
1200 and at 390 pixels wide. The panel showed the reply under **what
they said**, outlined, and opening it hid the chip.

*"Every Monday at 09:00, check https://example.org is up and tell me
only if something changed."* The card said *only when there is
something new -- on Yantra's page (its answers chip)*. Changing **Tell
me** to *never* and pressing approve brought the card back with
*never -- it is kept for you to read here*, and the select set to
`never`. The second approve saved it, and `samay list --json` showed
`notify: never`.

## What was deliberately not built

* **A notice in the page's own transcript.** An answer is not a turn of
  this conversation, and drawing it among the turns would make it look
  like one the model could continue. It sits in the panel instead.
* **"Seen" kept by the server.** Above: that is the browser's business.
* **Choosing the channel.** Where an answer goes is the clock's wiring
  (Sarathi sets it), not a per-schedule choice. The card says where;
  *Tell me* says whether.
