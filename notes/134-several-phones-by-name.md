# Several phones, by name

Sparsh can now be given several phones, each with a name
(`SPARSH_PHONES="nexus=…,iphone=…"`;
[its note 18](https://github.com/kunwarmahen/sparsh/blob/main/notes/18-several-phones.md)).
They are served by one server, so the tools keep their names:
`mcp__sparsh__tap` is still `mcp__sparsh__tap`. Everything
[note 119](119-a-phone-and-what-asks.md) built on those names stays as
it was. `act` still runs unasked, `confirm` still asks every time, and
the card is still Sparsh's own description of the held step. Yantra
needed to change in only two places: what the model is told, and what
the person sees.

## Telling the model which is which

With two phones, every tool takes `phone` (an enum of the names), and
every answer from Sparsh starts `Phone: NAME`. That alone would let a
model work both. What it can't know is that the two lists look alike
and that their numbers don't carry over. **THE PROMPT SAYS IT.**
`sparsh status --json` now has a `named` list, `{name, where, kind}`
for each phone, and the `phone` layer reads it. When more than one
named phone is ready, the layer adds this line:

> There are 2 phones: nexus -- Android; iphone -- iPhone. Every tool
> but `confirm` and `describe_hold` says which with `phone`, and every
> answer starts "Phone: NAME". If the person didn't say which phone,
> ask -- or use the one the task's app is on. Numbers from one phone's
> screen mean nothing on another.

The iPhone line (Back is a swipe from the edge) is kept whenever an
iPhone is among them. When the phones aren't named, nothing changes.

## What the person sees

* The startup line names each phone: *"sparsh: 12 tool(s); phone
  nexus: 84B7N16128001616 (Nexus_6P), pixel: emulator-5554
  (sdk_gphone64_x86_64)"*.
* `/phone` puts the name in front of each phone. A named phone that
  isn't answering is listed as `not found`, so a phone in a drawer
  isn't silently missing.
* `/phone log NAME` shows the steps on that phone. `/phone use NAME`
  starts the tools with that phone only (`sparsh mcp --phone NAME`).
  This is for a session that should never touch the other phone, such
  as the person's daily phone while the agent works the spare.
* The page's phone panel shows each phone's name in bold before its
  model. Its **see the screen** and **what was done** buttons were
  already per phone, by serial.

Who owns which phone isn't decided here. Yantra is one person's
session, so every phone it's given is theirs. Ownership is Dvara's
business: it starts each person's tools with only their phones.

## LIVE RECEIPT

A Nexus 6P on the cable and the Android 15 emulator, both named, with
`qwen3.8-64k:latest` on Ollama:

```
$ export SPARSH_PHONES="nexus=84B7N16128001616,pixel=emulator-5554"
$ uv run yantra --provider ollama --model qwen3.8-64k:latest \
    "On the pixel phone, open Settings and tell me the first three items in its list. Don't touch the nexus."
sparsh: 12 tool(s); phone nexus: 84B7N16128001616 (Nexus_6P), pixel:
emulator-5554 (sdk_gphone64_x86_64); screenshots on (local model) -- via ...
...
│ Phone: pixel
│ App: com.android.settings
│ 1 list "settings homepage container" [scroll]
...
│ 6 item "Network & internet — Mobile, Wi‑Fi, hotspot" [tap]
│ 7 item "Connected devices — Bluetooth, pairing" [tap]
...
Settings is open on the pixel. The first three items in its menu are:
1. **Network & internet** — Mobile, Wi‑Fi, hotspot
2. **Connected devices** — Bluetooth, pairing
3. **Apps** — Assistant, recent apps, default apps
── end_turn · 9095 in / 978 out · 2 iteration(s)
```

The model called `open_app` with `phone: "pixel"` and read the list it
got back, without a second look. `sparsh log --phone nexus` showed no
new step. Asked *"which phones can you work?"*, it answered *"two
Android phones: **nexus** (Nexus 6P) and **pixel** (emulator)"*.

## What is not here yet

* A real iPhone and a real Android in one session. The iPhone line in
  the prompt is the same one a lone iPhone gets; it has only been tested
  in `tests/test_sparsh_link.py`.
* A lesson taught with two phones attached. `phone_kind`
  ([note 133](133-taught-once-for-both-phones.md)) reads one phone's
  kind back from the layer and says "" for two, so such a skill doesn't
  record which phone it was taught on. The `phone` argument of
  `watch_phone` would say.
* Choosing a phone by name from the page. `/phone use NAME` is
  terminal-only. The panel's **use this phone** starts all of them.
