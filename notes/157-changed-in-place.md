# 157 — Changed in place

A schedule that should run at 8 instead of 7, a watch that should wait
for 80 instead of 70: both are a change to something the person already
has. The page had no way to make the first one. Its Schedules panel
could run, pause and delete, and the agent's only road was delete and
make again, which lost the schedule's runs and asked again for the tools
it had been allowed. The second one could be made, but it left the
watch's name saying 70.

Samay's notes/17 (`samay edit`, `change_schedule`) and Prahari's
notes/13 (a name that says its line) argue the programs' side. This is
Yantra's side: the panel, the card and the prompt.

## The Schedules panel

**change** sits beside *run now*. It opens what the schedule does, when,
when it tells you, and which phone (for one that works a phone) in
boxes. A new *when* is said back by Samay as it's typed, through `samay
preview`, so the sentence under the box is the one Samay will keep.
**save** sends only what differs, through `samay edit`, with each box as
one `--flag=value` word, so a prompt starting with dashes is still a
prompt. Samay's refusal comes back in its words.

## The card, and the prompt

`change_schedule` is marked as changing, so it arrives as a card:

```
Change schedule s-abc (its runs and allowed tools stay)
  now: s-abc [active] every day at 07:00

It will do, each time:
  brief and weather

When: every day at 08:00 -- next: Wed 11 Nov 08:00
Tells you: after every run
```

A *when* Samay can't read is said in capitals on the card (*SAMAY CAN'T
READ THIS*), the way the create card does. The `schedules` prompt layer
gains one rule: to change a schedule the person has, call
`change_schedule` with only what changes, and never delete it and make
it again.

## The Watches panel

The watch form gains *the thing it watches*. When it's opened to edit
a watch, its name, *what it's for* and the thing follow the test and
pick boxes as they're typed: `above 70` to `above 80` turns every 70 in
those boxes into 80, in front of the person, before save. A box they
typed in themselves is left alone. What Prahari says back (a word still
stale, say) is added to the toast. The `watches` prompt layer tells the
agent that a new line goes with its name, asked and thing in the same
`update_watch` call.

## Receipt

`tests/test_samay_link.py`: the panel's change goes through `samay edit`
with only the changed boxes, refuses an empty change and an id that
looks like an option, and previews through `samay preview`. The change
card says each change and a when Samay can't read. The prompt names
`change_schedule`. `tests/test_prahari_link.py`: a new line and its
words go in one `prahari edit`. The prompt says to change them together.
The programs' own pages and commands ran for real (Samay's notes/17 and
Prahari's notes/13). This panel's forms have not been clicked through in
a browser yet.
