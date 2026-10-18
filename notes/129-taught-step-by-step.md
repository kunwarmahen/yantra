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

### Shown, on a real Nexus 6P

`/learn watch search my Gmail for an email`, `qwen3.8-64k`, the person
doing each step and saying next. The agent called `watch_phone` by
itself and never touched the phone. On the first next, Sparsh named
the Google folder and said the tap inside it couldn't be named. The
agent guessed *"you opened Gmail and started typing 'uni'"*: wrong, the
word was left from an earlier test. The person corrected it, and the
agent took the correction (*"Got it -- you opened the Google folder and
then Gmail, and 'uni' was pre-existing"*). On the second, Sparsh named
Clear text and counted four keys, and the field said "unit". `/learn
done`, then save:

```
gmail-search -- Search for a term in Gmail on the phone by clearing any
stale search text, typing the query, and letting results filter live.
  inputs: search_term; folder_name (default: "Google")
1. Home screen ... Tap the item labelled Folder: <folder_name>.
   Check: the screen now shows the folder's contents, including Gmail.
2. Open Gmail. Tap Gmail.
3. Clear stale text. IF the search field already contains text, tap the
   Clear text button.
4. Tap the search field.
5. Type the full search_term.
6. Done. Tell the person what matched.
```

On a real iPhone 17, `/learn watch check the battery percentage in
Settings` went through on the two screens alone. The agent said *"I
think you opened the Settings app"*, then *"I think you tapped
Battery"*, both right, and `/learn done` wrote `check-battery-settings`
(Sparsh's notes/17).

The clearing step came out conditional with nobody asking for it. The
corrections in the lesson were enough.

Next session, asked *search my Gmail for amazon*: `load_skill
gmail-search` first, then one look, which found Gmail already open with
"unit" in the box. So it went straight to the skill's conditional step
(Clear text), typed "amazon", and answered with the one match, asking
before opening it. Seven model calls. Yantra counted the use as
**failed**, though, because one call was `type_text {"content": ...}`, a
wrong argument name that the next call fixed. A recipe with no script is
judged on every call after its load ([note 96](96-solve-it-once.md)),
so the model's slip counted against a recipe that worked. Now a call
refused for how it was called, and then made right, isn't counted
([note 106](106-what-the-recipe-leaves-out.md)). Two Sparsh bugs were found on the
way and fixed (Sparsh's notes/17): getevent's lines sat in a buffer when
piped, and the touch reader swallowed the agent's own requests.

## Found in use: telling when you meant showing

The owner's first lesson of their own, on Telegram, about a Coinbase
portfolio on the iPhone, came back as *the lesson took 0 tool call(s)*.
Three things had joined up:

1. A lesson was already open, from a first try. The second `/learn
   start` was answered with that one's status (*You're teaching me how
   to … 0 step(s) so far*), which reads like a confirmation of the new
   one.
2. `/new` came between them. It took away the conversation, and the
   opening that told the agent it was being taught went with it, but
   the lesson stayed open.
3. The steps were done by hand on the iPhone, in a `start` lesson,
   which learns only the steps it's told. Showing is `/learn watch`.

So: a lesson nothing was taught in (`Lesson.empty`: the opening and no
step) is replaced by a new start, and the status now ends *or /learn
stop, then start again* when it isn't empty. `/learn done` on an empty
lesson doesn't look. It says what each word is for (`NOTHING_YET`) and
keeps the lesson open. `/clear` here, and `/new` and `/chats N` in
Dvara, drop an open lesson and say so (`DROPPED`). The tutorials now
say it at the point of choosing: telling is typing, showing is
`watch`.

## Found in use: a skill of theirs, passed over

Taught on Telegram by showing (`/learn watch check my Coinbase portfolio
value`, on the iPhone), kept as `check-coinbase-portfolio`. Asked after
`/new`, *"hey can you find what my current coinbase portfolio looks
like"*, the agent opened coinbase.com in the browser. It loaded the
skill only when pushed (*"did you check all skills"*), and then worked
the phone to the answer.

The skill had been in the roster all along. Two things outweighed it.
The package's own prompt starts *"You check web pages for people"* and
sends the person to the phone tools only *"if they ask for something
done on their phone"*. And the skill's line (*"Open the Coinbase
Advanced app and read the total portfolio value…"*) said neither that
it was the phone nor that it was the person's.

* THE PERSON'S OWN SKILL IS MARKED. A skill found in a `learned/` folder
  (`Skill.is_theirs`, never a package's, whatever its `origin` says) is
  `[yours]` in the roster, under one line: *how THIS person does that
  job … load it and follow it instead of finding another way (a website,
  a search, other tools) — even when they don't say where or how*.
* A LESSON'S DESCRIPTION SAYS WHERE AND HOW THEY ASK. The lesson's
  decide section asks for the place (the phone, the app) and, after
  "Use when", the ways the person might put it, in their own words.

The kept skill's description was rewritten that way by hand. Asked the
same words again, on the host with the minder package and
`qwen3.8-64k`: *"There's a skill called check-coinbase-portfolio. Let's
load that."* It opened the app and stopped at Coinbase's own Face ID,
asking the person to look at the phone. Rebuilt and asked through
Telegram, the owner's own agent used the skill without a push. The roster is still frozen per
session, with no per-message ranking: the mark is one more word on a
line that was already there.

## What is not here yet

* ~~**A model's slip counted against the recipe.**~~ A call refused for
  how it was called, then made right, no longer counts
  ([note 106](106-what-the-recipe-leaves-out.md)).
* **A Teach button on the page.** The words work there; a button beside
  *save last turn* would start a lesson without typing one, choosing
  telling or showing, with **Done** and **Stop** while it's open. The
  same on Sarathi's home chat.
* **One skill for both phones.** A lesson is taught on one phone, and its
  app names (a package, a bundle id, the iPhone's "finance folder") are
  that phone's. A skill that opened the app by its everyday name with
  `open_app` would work on either. Teaching it on each is the way for
  now.
* **`/learn clear`.** `/learn stop` is the word that drops an open
  lesson; `clear` is the one some people reach for first.
