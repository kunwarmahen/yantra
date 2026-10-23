# 146 — A host's layer, and a prompt that names only what can be called

Two small changes to the system prompt's layers ([note 31](31-agent-packages.md)),
both made for a host that serves a household (Dvara) and both plumbing:
Yantra still decides nothing about what goes in them.

## `household`: a layer the host writes

The `memory` layer is the person's own: what *they* told an agent,
searched and topped up per conversation ([note 100](100-what-it-knows-about-you.md)).
A house has a second kind of fact: the wifi, bin day, where the spare key
is. Those are true for everyone the agent serves, written on purpose by
whoever runs the house, and few.

So `LAYER_ORDER` gains `household`, after `memory` and before
`connections`. Yantra never fills it. A host sets it per turn with
`attach_prompt(agent).set("household", text)` and `apply()`, the same
way every other owner of a layer does. Dvara fills it from the owner's
list and shows the whole list in every turn of every agent (its note
62). Shown, not searched, because there are few of these facts and a
missed one is the whole failure.

It comes after `memory` because the person's own words should be read
first, and a household fact is background to them. It comes before
`connections` so it isn't buried under tool advice.

## A layer that names only what the model can call

Sparsh gained `phone_status`: the battery and the new missed calls,
read with no screen. A morning brief admits that tool and none of the
screen's, so it can run on a schedule without waiting for the phone or
asking for it to be unlocked.

The `phone` layer then described the other ten tools anyway: look,
tap BY NUMBER, confirm a hold. Those are tools the model can't call.
On a small local model that is exactly the invitation not to send. A
prompt that lists "mail, calendar, money" made `gemma4:12b` call
`mcp__mail_query` and `mcp__portfolio_summary`, tools it had never been
given (Dvara's note 63).

**The layer follows what was admitted.** `Sparsh.connect` already knows
which of Sparsh's tools survived the package's allow list. When
`mcp__sparsh__look` is not among them, the layer is `status_text`: two
sentences on `phone_status` and that this is all of the phone the agent
has. The full layer also gained a line for `phone_status`, so an agent
with every tool knows it can read the battery without opening Settings.

## Receipt

`tests/test_prompt.py` checks the order, `household` included.
`tests/test_sparsh_link.py::test_an_agent_with_only_the_battery_is_told_only_the_battery`
checks the short layer. Through Dvara, on the Android emulator with a
missed call placed and the battery at 23%, a brief that admits only
`phone_status`, sent as a schedule, on `qwen3.8-64k:latest`, three runs:

```
mcp__sparsh__phone_status
**Phone**
- Battery at 23% and not charging overnight — plug it in.
- 1 missed call from 5550100.
```

The system prompt it ran with had `phone_status` in it and no `BY
NUMBER`.

## What was deliberately not built

* **Filling `household` from Yantra itself.** At a terminal there is one
  person, and their own memory is the place for their facts. A house
  is a host's idea, so the host fills the layer.
* **Trimming every layer to the admitted tools.** The `schedules` and
  `watches` layers describe tool families that are admitted whole or
  not at all. The phone was the one family a package could admit one
  tool of.
