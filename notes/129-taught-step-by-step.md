# 129 — taught step by step

*[Note 96](96-solve-it-once.md) writes a solved turn down as a skill,
and `/learn` asks for that by hand. This note is about a job that isn't
solved in one turn: one the person walks the agent through, a message
at a time.*

## The problem, as you would meet it

Some jobs are easier to show than to describe. A WhatsApp message on
your phone: open WhatsApp, tap Mom, type the words, tap Send. You walk
the agent through it, one message per step, and send `/learn` at the
end.

The skill that comes back is "type a message". `/learn` reads the last
turn: from the last thing you typed to the answer after it. Each step
you gave was a turn of its own, so the walk-through was learned as its
last step.

Two other things go wrong on the way. A capable model told "open
WhatsApp" opens it, then goes on to finish the job its own way, which
is exactly the job you didn't show it. And the look after each turn
([note 113](113-after-the-answer.md)) offers "tap Mom" as a skill to
keep, with nothing before or after it.

## A lesson

```
/learn start find the iPhone's iOS version in Settings
open the Settings app
tap General
tap About
/learn done
```

`/learn start` opens a **lesson** (`skills/lesson.py`). What follows it
is what the job is for. The lesson holds every turn from then on: the
person's words, then the calls that came after them. `/learn done`
hands the whole of it to the same one-call write-up note 96 built, and
the offer is the usual save question. `/learn stop` drops it, and plain
`/learn` still keeps the last turn.

THE AGENT IS TOLD IT IS BEING TAUGHT, in the conversation itself. The
lesson's first turn is an opening sent as the person's message: *do ONLY
the step I tell you, nothing more; after each step, tell me what is on
the screen now and ask me what's next; don't finish the job your own
way.* It's a message rather than a system prompt layer, so it's in the
history every later turn reads, and nothing outside the conversation
has to be switched back off when the lesson ends.

NOTHING IS OFFERED HALFWAY. While a lesson is open, hosts skip the look
after a turn: the terminal's save question, the page's tray, Dvara's
offer. One step of a walk, offered on its own, is worse than nothing.

THE STEPS ARE KEPT IN THE LESSON, NOT ONLY IN THE HISTORY. A lesson on a
phone is a dozen screens of text, and on a small context window
compaction folds part of it away before the person says done. So each
turn is copied into the lesson as it ends, each result clipped to what
the write-up would see anyway (`MAX_RESULT_CHARS`). The lesson
round-trips a dict, so a host that builds its agent per message (Dvara)
keeps it on disk.

## What the write-up sees

The lesson becomes one `Turn`: the task the person named, every step in
order, and `said`, the person's words with the step each came before.
The digest prints them where they were said:

```
TASK: find the iPhone's iOS version in Settings

STEPS:
PERSON: open the Settings app
1. mcp__sparsh__open_app {"name": "Settings"}
   -> ok: App: com.apple.Preferences ...
PERSON: tap General
2. mcp__sparsh__look {}
...
```

and the decide section is the lesson's own (`DECIDE_LESSON`). The
person's steps are the recipe, in their order. What in their words will
differ next time (who, what to type, which item) is an input. On a
phone, a tap is named by the words on what was tapped and the app it is
in, with what the screen should show after it, so the next run can
check it's on the right screen. A line number, a position or
coordinates is never written down, because they change every time the
screen is read. A step that waited for a yes still waits next time. And
no script is written, because a phone is worked one step at a time.

The checks before the write-up change for a lesson too. It's the person
asking, so one step is enough. A skill followed along the way is one of
the steps they showed, not a recipe to repair. And a lesson where
nothing was done says so (*the lesson took 0 tool call(s)*).

## Shown, not told

`/learn watch <what>` is the same lesson with the person's own hand on
the phone. Its opening tells the agent not to touch the phone: call
`watch_phone` once, then `watched` each time the person says next, and
say in one line what it thinks they did, so they can correct it.
Sparsh's two read tools do the seeing (Sparsh's notes/17). Android
reads the finger against the screen as it was ("tapped 4 item 'Mom'").
An iPhone can't show where it was touched, so it hands over the screen
before and after. The calls that read them are the lesson's steps like
any other, so the write-up needs nothing new.

## Everywhere `/learn` is

| where | how |
|---|---|
| terminal | `/learn start WHAT`, `/learn watch WHAT`, `/learn done`, `/learn stop` |
| Yantra's page | the same words typed in the chat; `state.lesson` says how far it got, a banner answers the words; *save last turn* is still plain `/learn` |
| Dvara (Telegram, its web road, Sarathi's home chat) | the same words; the lesson kept in `state/skills/<person>/<agent>.lesson.json` (Dvara's notes/49) |

## Receipt

A real iPhone 17 through Sparsh and WebDriverAgent, Yantra's terminal,
`qwen3.8-64k` with a 65,536-token window, the words piped in:

```
> /learn start find the iPhone's iOS version in Settings
  -> "Ready" (1 iteration)
> open the Settings app
  -> open_app "Settings"; "The Settings app is open on its main screen
     ... What's the next step?"                       (2 iterations)
> tap General
  -> look, scroll, look, look, look, tap {"element": "General"} (refused:
     "use `n`"), tap {"n": 8}; "I'm now on the General screen ... What's
     the next step?"                                   (8 iterations)
> tap About
  -> tap {"n": 5}; "I'm now on the About screen -- it shows ... iOS
     Version (27.0.1) ... What's next?"                (2 iterations)
> /learn done
· writing down what you taught me

Save this as a skill?
  find-ios-version -- Opens iPhone Settings and navigates to General ->
  About to read the iOS version. Use when someone asks what iOS version
  the phone is running.
  inputs: none (the target is always "the phone's own iOS version")
  tested: no script, so nothing to run
  cost:   2,929 in / 843 out tokens to write and test

1. In the sparsh tool, open the Settings app (open_app with name "Settings").
   - The screen should show the Settings list with items like "Airplane
     Mode", "Wi-Fi", "Bluetooth", etc.
2. ... scroll down in the settings list until the "General" button is
   visible.
3. ... tap the "General" button (use tap with n set to its position
   number on screen).
   - The screen should now show the General page with items: "About",
     "Software Update", "iPhone Storage", "AirDrop", etc.
4. ... tap the "About" button ...
5. Read the value next to "iOS Version" on the screen and report it.
```

What it shows:

* **It did only the step it was told, every time,** and stopped to ask.
  At no turn did it go on to About by itself.
* **Every step of the walk reached the write-up.** All four of the
  person's steps are in the skill, in their order. The old `/learn` here
  would have read only the last turn: one tap on About.
* **Taps are named by their words, and each step says what the screen
  should show.** The step numbers on the screen (8 for General, 5 for
  About) aren't in it: "n set to its position number" is the tool's
  argument, read fresh each run.
* **No personal facts.** The phone's name, serial number and warranty
  were on the About screen; none of them is in the skill.
* **Its five looks at General are gone.** The dead ends were dropped;
  the scroll that found General was kept.

The save question was answered no (the piped answer was a stray `p`,
which isn't one of `s/e/c`, and the default is no), so nothing was
written to disk. Writing it is the same `learner.save` every other offer
uses.

## What is not here yet

* **Watching, run for real.** `/learn watch` is built on both phones and
  tested on Sparsh's saved screens, not yet run with a person's finger
  on the Nexus or the iPhone.
* **A Teach button on the page.** The words work there; a button beside
  *save last turn* would start a lesson without typing one.
* **One skill for both phones.** A lesson is taught on one phone, and its
  app names (a package, a bundle id) are that phone's. Teaching it on
  each is the way for now.
