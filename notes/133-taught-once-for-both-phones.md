# 133 — taught once, for both phones

*[Note 129](129-taught-step-by-step.md) teaches the agent a job one step
at a time, by telling it or showing it on the phone. This note is about
what that lesson leaves behind: a skill that only works on the phone it
was taught on, a lesson you can start only by typing, and one word
people reach for that wasn't there.*

## The problem, as you would meet it

You teach the agent to read your Coinbase balance on your iPhone. You
open the Finance folder on the home screen, tap Coinbase Advanced, tap
Portfolio, and say done. The skill it keeps reads:

```
inputs: App: the Coinbase Advanced app (com.vilcsak.bitcoin2)
1. ... tap the finance folder (16 apps) icon on the home screen, then
   tap the Coinbase Advanced icon inside.
```

Every word of that is true of your iPhone and wrong for the Android
phone in the same house. Its home screen has no Finance folder, and the
app is a different package there. Ask the agent the same question with
the Nexus plugged in, and the skill sends it looking for a folder that
isn't there.

None of this comes from the lesson itself. What the agent saw were the
iPhone's screens, so the write-up described them faithfully.

## The app by its name

Sparsh's `open_app` already takes the app's everyday name. On Android it
matches the name against the installed packages, word by word. On an
iPhone it does the same against the bundle ids, and failing that it
searches Spotlight the way a person would (Sparsh's notes/16). So
"Coinbase Advanced" opens the app on either phone, and neither the
folder nor the bundle id is needed to get there.

So the lesson's write-up is told, in the rules it gets
(`DECIDE_LESSON` in `skills/learn.py`):

* open an app with `open_app` and its everyday name, the one under its
  icon, *even where the person opened it by tapping through a home
  screen or a folder*;
* never name it by its package or bundle id, or by the folder it sits in;
* if it is told which phone the lesson was taught on, say so in one line
  under the title, and name a step that is that phone's own (an iPhone
  swiping back, an Android Back key).

The steps inside the app are still the ones seen. An app is often laid
out alike on both phones, and where a screen does differ, the line under
the title is the next run's warning to look.

## Which phone it was

The write-up can only say "taught on an iPhone" if something tells it.
The screens don't: Sparsh reads an iPhone's elements into the same kinds
an Android screen has, so a list of buttons looks the same from either.

The agent knows, though. Its phone layer (`sparsh_link.prompt_text`)
says *"You can work the person's iPhone (...)"*, written from Sparsh's
status when the phone was connected. `sparsh_link.phone_kind(agent)`
reads that line back, and a lesson keeps what it says (`Lesson.phone`)
each time a turn with steps is added. A phone swapped halfway is the one
the later steps were seen on, so the latest one wins. It goes to disk
with the rest of the lesson, so Dvara's rebuilt agents keep it too. The
write-up sees one more line at the top of what it reads:

```
TASK: check my Coinbase portfolio value
TAUGHT ON: the person's iPhone
```

With no phone, or with both kinds at once, the line is left out rather
than guessed.

## A Teach button

A lesson used to start only by typing `/learn start …` or
`/learn watch …`, and the page gave no sign one was open. Now the skills
panel on Yantra's page has **teach** beside *save last turn*. It opens a
small dialog: *tell it* or *show it*, and what it's for. Start types the
same words a person would, through the same road, so the chat shows
`/learn watch check my battery level` either way.

While a lesson is open, a bar sits above the message box: *"Showing:
check my battery level — 2 step(s) so far; do the next step on the
phone, then say next."*, with **done** and **stop**. It comes from the
page's state, which now carries the lesson as a dict (`task`,
`watching`, `told`, `steps`, `phone`, `status`) rather than one line of
text. That state is sent when the lesson opens, not only when its first
turn ends, so the bar is there before the agent answers.

Sarathi's home chat has the same: a **Teach** button in its row of
words, the same dialog, and a bar with **Done** and **Stop** for the
agent picked below. Dvara's web look now lists each open lesson
(`lessons`), only the person's own, so the bar shows a lesson started on
Telegram too. It is one lesson per person per agent, wherever it was
opened.

## `/learn clear`

`/clear` empties a conversation, so `/learn clear` is the word some
people send to drop a lesson. It was answered with the usage text. Now
`lesson.parse` reads `clear` as `stop`, and the hosts never see it as
anything else: terminal, page, Telegram and Sarathi's chat all drop the
lesson, and the help texts name it.

## Receipt

The write-up on `qwen3.8:latest` through Ollama, given a recorded
`watch` lesson shaped like the real one above: home screen, the Finance
folder, Coinbase Advanced, Portfolio, the Assets line, taught on an
iPhone. It answered in 22 seconds:

```
OFFER: check-coinbase-portfolio
...
inputs: The app to open (here "Coinbase Advanced"); the phone it is on
  (the person's iPhone)
---

# Check Coinbase Portfolio Value

Taught on an iPhone; on an Android phone the screens may differ (e.g.
navigation back uses the Back key instead of swiping).

1. Open the **Coinbase Advanced** app (open_app "Coinbase Advanced").
   - Screen should show ... a row of buttons: **Spot**, **Portfolio**,
     **History**.
2. Tap the **Portfolio** button.
   - Screen should change to show a "Portfolio" heading, "Total
     portfolio value", "Assets", and a dollar amount.
3. Read the dollar amount shown next to / under **"Assets"** ...
```

The folder and the bundle id are gone. The steps inside the app are the
ones it was shown.

The pages, in headless Chrome at 1280 px and 390 px. On Yantra's page
(`gemma4:12b`), **teach** → *show it* → "check my battery level" →
start. The bar was up before the agent answered, and **stop** took it
away. Sarathi's home chat did the same, with Dvara's answers stood in
for: Teach sent `/learn watch check my battery level`, the bar showed
*"Showing minder: check my battery level. 0 step(s) so far"*, Stop sent
`/learn stop` and the bar went, and Escape on the dialog sent nothing.

Tests: `tests/test_lesson.py` (the phone layer read back, a lesson
keeping its phone through a dict, the `TAUGHT ON` line and its absence,
the rules the write-up gets, `clear` as `stop` in the terminal and on
the page, the bar's state sent when a lesson opens and when it clears);
Dvara's `tests/test_lessons.py` (`/learn clear`, the web look's lessons,
one person's only).

## What is not here yet

* **A skill taught on one phone, run on the other, for real.** The
  write-up now writes for both. Nobody has yet taught a skill on the
  iPhone and asked for it with the Nexus plugged in.
* **Steps that differ.** Only opening the app is made phone-free. A
  screen that differs inside the app (a menu in another place) is left
  to the line under the title and the next run's check of each screen.
* ~~**Both phones at once.**~~ With both ready, the lesson takes the
  phone its steps named, and the layer's list says its kind
  ([note 134](134-several-phones-by-name.md)).
* **Skills already kept.** A skill taught before this keeps its folder
  and bundle id until it is taught again, or fails and is rewritten.
