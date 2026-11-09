# 156 — each phone its own way

*[Note 133](133-taught-once-for-both-phones.md) made a skill taught on
one phone open its app on either, by name. It left the steps inside the
app as the taught phone's: a menu the other phone keeps somewhere else
was left to a line under the title. This note is about the first run on
that other phone, and what it writes down.*

## The problem, as you would meet it

You teach the agent to check your battery on your iPhone: Settings,
**Battery**, read the percentage. Then you ask it with the Nexus plugged
in. Android's Settings has no **Battery** row at the top. It has
**Battery and device care**, and **Battery** one screen further in. The
agent finds its way, and you get your answer.

What happens to the skill next is the trouble. The repair of
[note 97](97-when-the-recipe-breaks.md) is built for exactly this, a
recipe whose step failed while the task got finished another way, and
it writes the way that worked *over* the old one. So the skill becomes
the Android skill. The next time you ask on the iPhone, *Battery and
device care* isn't there, the iPhone's way gets written back, and the
two phones undo each other's fixes for as long as you use both. And if
the Android run didn't fail at all (the model found the row without a
failed call), nothing is written, so the next Android run finds its way
from scratch again.

## A section for each phone

**A PHONE SKILL KEEPS EACH PHONE'S WAY.** A skill taught on one phone
and first run on the other is written up once more, as that phone's
section:

```markdown
# Check my battery

Taught on an iPhone; on an Android phone the screens may differ.

1. Open the **Settings** app (open_app "Settings").
2. Tap **Battery**.
3. Read the percentage.

## On an Android phone

Replaces step 2 with two taps:

1. From the Settings home screen, tap **Battery and device care** …
2. In the Device care screen that opens, tap **Battery**.

Step 3 (read the percentage) is unchanged.
```

The taught steps are copied word for word. The write-up is told so in
capitals, and the offer is a diff, so the person sees a section added
and nothing above it changed.

**WHICH PHONES IT COVERS IS READ FROM ITS OWN TEXT.** The *Taught on an
iPhone* line names one phone, and each `## On an Android phone` heading
names another (`phone_kinds`). The phone a run used is the one its phone
steps named, or the one phone the agent works now (`run_phone`, from the
phone layer, as note 133's lesson does). A run on a phone the skill
already covers writes nothing. Nothing is kept in a hidden record, so
the skill says which phones it knows, both to the person and to the next
run.

**ONCE PER PHONE, WORKED OR NOT.** The first run on the other phone is
written up in either case:

* **it worked**: the write-up is told it ran on a phone it had no steps
  for yet, and it worked. If every step went as written, the section
  says that in one line (*"The same steps work as written."*), and the
  model is told never to skip it for being the same, because the section
  is how the next run knows the phone was checked;
* **a step failed, and the task was finished another way**: it's the
  repair of note 97, told the same thing. The fix goes in the phone's
  section, and the taught phone's steps stay.

A run that failed and wasn't finished writes nothing, as before, because
there is nothing to write from.

**THE NEXT RUN FOLLOWS THE SECTION.** The skills layer, where it tells
the model to follow the person's own skills, now says one more thing: a
phone skill may have a section for the other kind of phone, and on that
phone its steps take the place of the ones they name. The phone layer
already says which phone it is.

## Receipt

The write-up on `qwen3.8:latest` through Ollama, from a recorded run of
the iPhone-taught battery skill on an Android phone: Settings opened,
*Battery and device care*, then *Battery*, 81%. It answered in 23
seconds:

```
say: check-battery ran on the Android phone for the first time; writing down that phone's way
 1. Open the **Settings** app (open_app "Settings").
 2. Tap **Battery**.
 3. Read the percentage.
+
+## On an Android phone
+
+Replaces step 2 with two taps:
+
+1. From the Settings home screen, tap **Battery and device care** (it is listed
+   alongside Search, Connections, Sounds and vibration, Apps — there is no
+   "Battery" item at this level).
+2. In the Device care screen that opens (showing Battery, Storage, Memory), tap
+   **Battery**.
+
+Step 3 (read the percentage) is unchanged.
```

The iPhone's three steps were left exactly as they were. The run's
phone steps were stand-ins that printed what Sparsh would have said.
Nobody has yet taught a skill on the real iPhone and run it on the
Nexus.

Tests: `tests/test_phone_variants.py` (7). The phones a skill covers are
read from its text. A first run on the other phone that worked writes
that phone's section, with the taught steps kept. Once the section is
there, nothing more is written, and the same goes for a run on the
taught phone. A failure on the other phone is repaired as its section.
A skill not taught on a phone is left alone. The skills layer tells the
model to follow the section.

## What is not here yet

* **A third kind of phone.** Two kinds are told apart, iPhone and
  Android. An Android phone from another maker (Samsung's *Device
  care*, Pixel's *Battery*) is the same kind here, so the first Android
  phone's section stands for all of them.
* **Skills taught before *Taught on* was written.** With no line naming
  the phone, a skill isn't known to be a phone skill, and the repair of
  note 97 applies to it as before.

Continues [note 133](133-taught-once-for-both-phones.md) (taught once,
for both phones) and [note 97](97-when-the-recipe-breaks.md) (when the
recipe breaks).
