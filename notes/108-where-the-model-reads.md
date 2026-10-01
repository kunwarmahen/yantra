# 108 — Where the model reads

A learned recipe never holds your values. The fan's id is an **input**:
the recipe names it, and memory keeps it
([note 106](106-what-the-recipe-leaves-out.md)). When `load_skill`
delivers the recipe, Yantra searches memory for facts that fit its
inputs and puts them right under the steps that need them.

A recipe promoted to a tool ([note 98](98-one-call.md)) skips that
step. The model sees `fan_control(fan, state)` in its tool list, fills
in `fan`, and calls it. Nothing gets loaded, so nothing gets searched.
The model has to fill `fan` from whatever it already has in front of
it.

Sometimes the memory layer covers this. It's the twenty or so facts put
in the system prompt at the start of a conversation
([note 100](100-what-it-knows-about-you.md)). But that layer is ranked
by your first message, and *"I'm roasting at my desk, cool me down"*
shares no words with *"Their office fan's Home Assistant entity id is
fan.office_ceiling_2."* With thirty newer memories on top, the fact
isn't in the prompt at all. The model then guesses a name.

## The facts go where the model reads

When the model fills a tool's arguments, it reads that tool's
description. So that's where the facts go:

```
Turn a Home Assistant fan on or off through its REST API.

What you remember about this person that may fill its arguments (use
what fits; what they asked for now wins):
- Their office fan's Home Assistant entity id is fan.office_ceiling_2.
```

The search is the same one `load_skill` runs: the recipe's description,
its inputs, and your request, with at most five results. One function,
`remembered_inputs`, serves both, so the two can't drift apart.

**ONCE PER CONVERSATION, LIKE THE LAYER.** The lookup runs when memory
fills its layer: on a conversation's first message, and again after a
`/clear`. A recipe promoted partway through a conversation gets its
lookup right away, rather than waiting for the next one. Tool
descriptions are rebuilt on every request, so the change reaches the
model on its next call.

**CANDIDATES, NOT ORDERS.** The wording is the same as `load_skill`'s:
what you ask for now wins. *"Switch the bedroom fan on"* still means the
bedroom fan, even when the office fan is the one remembered.

**NOTHING WHEN THERE IS NOTHING.** A recipe that names no inputs, no
memory, no match, or a store that fails: the description stays exactly
as `tool.json` wrote it, and the tool works as before.

## Receipt

A fan recipe promoted to `fan_control`; the entity fact kept, then
thirty newer memories on the `local` store; the request *"I am roasting
at my desk, cool me down please."* On the same Ollama, five runs per
model, with the lookup switched off and then on. The fact was in the
memory layer in none of the twenty runs.

```
qwen3.8:latest
off   [recall_memory 'fan', fan_control fan.office_ceiling_2]
off   [recall_memory 'fan', fan_control fan.office_ceiling_2]
off   [recall_memory 'fan Home Assistant entity room desk', fan_control fan.office_ceiling_2]
off   [fan_control fan.dining_room, fan_control fan.office]
off   [recall_memory 'fan Home Assistant entity room desk', fan_control fan.office_ceiling_2]
fixed [fan_control fan.office_ceiling_2]   (x5)

gemma4:12b
off   []
off   [fan_control desk_fan]
off   []
off   [fan_control desk_fan]
off   [recall_memory 'fan', fan_control fan.office_ceiling_2]
fixed [fan_control fan.office_ceiling_2]   (x5)
```

| | off | on |
|---|---|---|
| qwen3.8, right fan | 4/5, each after a `recall_memory` | **5/5, one call** |
| gemma4:12b, right fan | 1/5 | **5/5, one call** |

Without the lookup, qwen does what note 105 saw it do when a fact was
buried: it goes looking, and it finds the fan four times in five, at the
cost of an extra call each time. When it doesn't look, it guesses two
entities that don't exist. gemma mostly guesses (`desk_fan`) or does
nothing. With the lookup, both models call the right fan first time, in
all ten runs.

An earlier qwen run cut off after four runs, with the lookup off. In
two of those runs qwen loaded the recipe first (and so got the fact),
in one it guessed `living_room_fan`, and in one it made no call.

`2463 passed, 1 skipped` (was 2459). The tests are in
`tests/test_promote.py` (`TestInputsFromMemory`).

## What was deliberately not built

* **A lookup on every call.** Each call could search memory again with
  the arguments in hand. But by then the model has already filled
  `fan`. The search has to happen before the call, so it lives in the
  description.
* **A lookup on every turn.** Searching again for each new request
  would follow a conversation that changes subject. It would also
  change the tool list mid-conversation and cost one search per
  promoted tool per turn. Once per conversation matches the memory
  layer, and `load_skill` still searches on every load.
* **Leaving out what the layer already has.** A fact can appear in both
  the layer and a tool's description. That costs a line, and the layer
  is exactly what loses a fact the request never names.
