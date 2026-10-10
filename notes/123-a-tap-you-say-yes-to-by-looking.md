# 123 — A tap you say yes to by looking

[Note 121](121-a-picture-where-the-list-has-nothing.md) let a local
model that can see read a phone screen the list can't describe. It
couldn't act there: Settings' About page shows "Device name", and with
no number to tap, "rename my phone" was out of reach. Sparsh now taps
by position on such a screen, and holds every one of those taps for the
person (Sparsh's note 06). This note is Yantra's half: what the person
is shown when asked.

## The card carries a picture

A held tap reaches the person as `confirm`'s card, which is Sparsh's
own account of the step (`describe_hold`, note 119). For a tap on
"Send" that account is enough: the words say what will happen. For a
tap at a spot it isn't. "x 222, y 368 of 1000" means nothing to a
person, so `describe_hold` also returns the screenshot with the spot
ringed, and the card shows it.

**FOR THE PERSON'S EYES, NEVER THE MODEL'S.** `PermissionRequest` gains
a `picture` (an `ImageBlock`, None for every card that is words alone).
A tool offers one through a `card_picture` hook, set by whoever wired
it, never by the server, the same way `explain` gives a card its words
(note 115). The agent loops ask for it beside the summary, and a broken
one is None rather than a card that never shows. The picture goes to
the gate and nowhere else: not into the transcript, not into the
model's next request. A cloud model kept from phone screenshots
(`YANTRA_PHONE_SHOTS`) stays kept from them, and the person still sees
where the tap will land.

Where it shows:

* **the page:** above the card's words, in the approval modal and in a
  held turn's panel, scaled to fit;
* **the terminal:** it can't draw one, so the picture is saved to a
  file and the path is printed under the card (`the screen, the spot
  ringed: /tmp/yantra-card-….png`);
* **an edited card** drops it: the picture is of the call as asked, not
  as amended.

## Live, on the emulator

`examples/phone_trial.py --shots --only rename_phone --repeat 3`,
`qwen3.8:latest` on Ollama. The trial gained `spots` (taps by
position) and `--cards DIR`, which keeps each card's picture, because
the trial's yes doesn't look and a person should:

```
qwen3.8:latest (ollama) -- 1 tasks x 3
  done                         3/3 (44-100%)
  screenshots shown            16
  taps by position (held)      10
  went round the phone         0
  errors                       0
```

The phone's name, read over adb, was "Trial Phone" each time. All
thirteen cards ringed what the model meant. Note 121's `gemma4:26b`
runs did this task 0 of 4.

Two failures on the way, both fixed in Sparsh and both worth knowing
about from this side:

* **A confirm that outran the call.** Reading a screen that never goes
  still costs about twelve seconds a try. A confirm that read it before
  and after the tap took about fifty, and Yantra's MCP call gives up at
  thirty. The tap was done, and the model was told "timed out". One
  run took 695 seconds. Sparsh now checks the screen against the
  picture instead of reading it, and tries a restless screen once.
* **A yes in words only.** `confirm` returned text, so the model worked
  blind after the first yes, and once ringed empty space below the
  dialog it meant to press OK in. The trial said yes, the phone wasn't
  renamed, and the model said it was. The card showed exactly where
  the tap would land, which is what a person needs to say no. `confirm`
  now returns the screen and its picture, like any act.

**Through the page.** The same task typed into the browser UI, with
only the phone connected (`--no-setu --no-samay --memory off`), every
phone card approved by a script and every other card denied. The
emulator was "Trial Phone" after 966 seconds: four phone cards, each
with its picture, and one `bash` card in words, denied. One card's
ring sat between Cancel and OK, a little low: the tap landed on OK,
but a careful person could fairly have said no, and that would have
been right. That is the case for the picture coming first. It sat
under Sparsh's whole account of the step, out of sight until scrolled
to; now it is the first thing on the card, with the words under it.

## What the tests hold

`tests/test_sparsh_link.py`: a tap by position's card says where the
ring is and its picture comes through `card_picture`, while a card in
words has none; the picture reaches the gate and not the tool result.
`tests/test_web_server.py`: a card's picture rides in the
`permission_request` envelope. 2733 tests before, 2736 after (and 1 skipped, as before).

## Not here yet

* ~~Dvara. A person asked in Telegram gets the card's words, not its
  picture.~~ Dvara sends the photo first and the buttons under it
  (Dvara's notes/32).
* ~~A picture only for a tap by position.~~ Every held step now comes
  with the screen it was held on, what it would tap ringed, and the
  words filled in on it -- not the model's numbered list, which on a
  real phone was forty-one lines of a dialler's keys (Sparsh's
  notes/10).
* The cloud road, with pictures to the model, untried like the rest of
  the cloud trial.
