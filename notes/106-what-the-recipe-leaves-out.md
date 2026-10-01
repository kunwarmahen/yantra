# 106 — What the recipe leaves out

A learned skill ([note 96](96-solve-it-once.md)) is not allowed to hold
anything about you. The bedroom fan's id, your Home Assistant's
address, the file your token is in: all of those are **inputs**. The
recipe names them and never writes them down. That rule is what keeps a
recipe correct after you move house, safe to read, and possible to
share.

But it leaves a gap. The first solve discovered that your bedroom fan
is `fan.master_bedroom_ceiling`, not the `switch.bedroom_fan_light` next
to it, and that took real steps. A recipe that asks *"which fan?"* every
time has only moved that discovery, not saved it. The next run lists
every entity again and works it out again.

Meanwhile Yantra already has a place for facts about you: memory
([note 100](100-what-it-knows-about-you.md)). And it has a place for
access to your accounts: Setu ([note 95](95-the-accounts-you-connected.md)).
This note joins the recipe to both. **The recipe keeps the way. Memory
keeps your values. Setu keeps your access.**

## One write-up, two kinds of thing learned

When a turn is worth keeping, one fresh, small model call writes the
recipe ([note 96](96-solve-it-once.md)). That call now also lists the
values it kept out, as facts, in the same shape the end-of-conversation
look back uses ([note 101](101-looking-back.md)):

```
=== FACTS ===
fact: Their bedroom fan is fan.master_bedroom_ceiling in Home Assistant.
fact: Their Home Assistant URL and token are in the file ha.env.
=== END ===
```

It's the same call, so learning the facts costs nothing extra. It's
also the moment that knows the most: the call is looking at the exact
steps where the id was found.

**THE LOOK BACK'S RULES, NOT LOOSER ONES.** These facts go through the
same checks as a look back's:

* The look back's own mode decides. `ask` offers them when the turn
  ends, after the skill's question. `auto` keeps them. `off` drops them.
* A fact that's already remembered, or that you dropped earlier in the
  session, isn't offered again.
* A fact containing a value that was scrubbed from the steps as a secret
  is dropped. That value was a token.

The facts are offered **whatever you say to the skill**. They're about
you, not about the recipe. You can say no to a recipe and still want
Yantra to remember which fan is the bedroom one.

**A FACT SAYS WHAT THE VALUE IS.** *"fan.master_bedroom_ceiling"* on its
own is useless next month. *"Their bedroom fan is
fan.master_bedroom_ceiling in Home Assistant"* can be found by someone
asking about the bedroom fan. That lesson came from
[note 105](105-a-rate-not-a-picture.md): a fact worded without its
meaning isn't found by a store that searches by meaning.

## The values come back when the recipe loads

Memory already fills a prompt layer at the start of each conversation.
But that layer holds twenty facts at most, and a small model often
misses the one that matters among them. The `recall_memory` tool exists
too, but in the recall trial ([note 103](103-said-once-found-later.md))
no local model ever called it.

So when `load_skill` hands over a learned recipe that has `inputs:`, it
searches memory itself. The query is the recipe's description, its
inputs, and what you just asked for. Up to five matches go right under
the steps:

```
What you remember about this person that may fill its inputs (use what
fits; what they asked for now wins):
- Their bedroom fan is `fan.master_bedroom_ceiling` in Home Assistant.
- Their Home Assistant instance runs at http://127.0.0.1:8765.
```

**CANDIDATES, NOT ORDERS.** *"What they asked for now wins"* is there
because you might have a new fan. Nothing is added when there's no
memory, when nothing matches, or when the store fails. A recipe loads
whatever memory is doing.

## A recipe says which account it needs

A recipe's `needs:` line may now name a Setu connection:
`needs: setu:gmail`. When the recipe loads, the model is told one of two
things:

```
Needs Gmail (setu:gmail): connected -- use its tools (mcp__gmail-personal__*).
```

```
Needs Outlook (setu:outlook), which is NOT connected: do not work around
it. Tell the person the recipe needs Outlook connected (they can run
`setu connect outlook`).
```

The second one matters more. A recipe followed into an account that
isn't there fails four steps in. The fix is yours to make (connect it),
so the model should say so, not improvise around it.

The write-up is given the connector ids Setu reports, so it can write
`setu:gmail` instead of *"Gmail access"*. The save question says whether
each one is connected:

```
  needs:  setu:gmail
          Gmail: connected
```

Anything else in `needs:` stays plain words for a person. Without Setu,
the write-up hears nothing about it.

## Receipt

`qwen3.8:latest` through Ollama, one-shot CLI, `--yolo`,
`--env-context local`, `--learn auto --reflect auto`, local memory, and
a throwaway home and state folder. The fake Home Assistant is a REST API
with a bearer token, ten entities including three fans and the decoy
`switch.bedroom_fan_light`, and a call log.

**The solve.** *"Set my bedroom fan to 60%. My Home Assistant URL and
token are in ha.env."* Six calls, 17,356 in / 793 out tokens. The
recipe `ha-set-fan-percentage` was saved, and in the same write-up
three facts were kept:

```
Their bedroom fan is entity fan.master_bedroom_ceiling in Home Assistant.
Their Home Assistant URL and token are in the file ha.env (relative to the working directory).
Their Home Assistant instance runs at http://127.0.0.1:8765.
```

**The reuses**, each in a fresh session:

| run | ask | calls | tokens (in + out) | entity looked up? | Home Assistant calls |
|---|---|---|---|---|---|
| solve | bedroom fan to 60% | 6 | 18,149 | yes: `GET /api/states`, one failed POST | 3 |
| reuse 1 | bedroom fan to 40% | 4 | 17,680 | no | 1 (plus the repair's test) |
| reuse 2 | bedroom fan to 25% | 4 | 16,745 | no | 1 |

Neither reuse listed entities or guessed at the fan. Each one went
straight to `fan.master_bedroom_ceiling` with the env file the fact
named.

The token savings are smaller than [note 96](96-solve-it-once.md)'s
because both reuses lost steps elsewhere, and both losses are recorded
here:

* **Reuse 1 hit a bad recipe.** The write-up saved a *bash* script, but
  its steps said `python3 "$SKILL_DIR/scripts/set_fan.sh"`. The
  template the write-up follows showed only a `python3` line. The model
  read the file and ran it with bash. Repair
  ([note 97](97-when-the-recipe-breaks.md)) then rewrote the step to
  `bash …`. The template now says *the same program as TEST, python3
  for a Python script, bash for a shell one*.
* **Reuse 2 went looking for `ha.env`.** The fact said *relative to the
  working directory*. The model wondered whether that meant the skill's
  folder, ran an `ls` that failed, then a `find` that worked.

An earlier pair of runs, before the write-up was told that *the file
holding their credentials* is a value too, kept only the fan and the
address. The reuse then spent a step grepping the whole home folder for
`HA_TOKEN`. That's why the example in the prompt names a file.

**Found: the counter is too strict.** Both reuses above, and that
earlier one, **counted as failed**, even though each set the fan
correctly. A learned skill's use fails when any call after the load
fails, and a look-around command that exits non-zero (`ls` on a missing
path, `grep` over folders it can't read) is a failed call. Three such
uses in a row would set a working recipe aside. Fixed afterwards: see
the next section.

**Setu's half** has no live receipt: there's no Home Assistant connector
in Setu yet, and the Gmail connector's recipes need a real inbox. It's
covered by tests against a Setu status report (connected, not connected,
unknown connector, no Setu at all), through `load_skill`, the write-up's
prompt, the terminal question and the page's.

`2452 passed, 1 skipped` (was 2434). The new tests are in
`tests/test_learned_skills.py`. They check that:

* the facts section is neither script nor instructions;
* under `ask` facts wait for a yes, under `auto` they're kept, and under
  `off` they're dropped;
* known and dropped facts aren't offered again;
* a fact carrying a scrubbed secret is never offered;
* with no memory the skill is still offered;
* the terminal and the page ask about facts after the skill;
* matching facts come under a learned recipe's steps, and a failing
  store still delivers the recipe;
* `setu:<id>` resolves to connected, not connected, or unknown, and
  plain words add nothing.

## Judged on its own steps

A use is now judged on **the recipe's own calls**: its promoted tool,
or a command that runs something from the skill's folder. A look-around
`ls` that exits 1 isn't the recipe failing, so it doesn't count. The
script failing still does, even if the model then works around it. The
`python3`-on-a-bash-script reuse above is still a failure, and is
still repaired. Repair uses the same rule: the recipe's own first
failure, then anything at all that worked after it. The one line the
update question shows is the script's error, not the `ls`.

A recipe with no script has no calls of its own to look at, and neither
does a turn that never ran the script. Those are judged the old way, on
every call after the load.

The script is recognised by its full folder path in the command, which
is how `load_skill` hands it over. Three more reuses on `qwen3.8:latest`,
with the same recipe:

| reuse | calls | tokens (in + out) | failed calls | counted |
|---|---|---|---|---|
| fan to 60% | 2 | 9,503 | 0 | worked |
| fan to 70% | 2 | 9,532 | 0 | worked |
| fan to 80% | 6 | 25,974 | 1 (`bash home/.yantra/…`, exit 127) | worked |

In the third, the model first typed the script's path relative to the
wrong folder, then ran it by its full path, and it worked. Under the
old rule that reuse would have been the recipe's second failure in a
row. The record now reads `worked 3 · failed 2 · in a row 3`, and each
of the three set the fan in one call, according to the server's log.

`2455 passed, 1 skipped` (was 2452).

## What was deliberately not built

* ~~**A fairer counter.** The fix is probably to count a use as failed
  only when the recipe's own script (or its promoted tool) fails, not
  any command after the load. That changes what triggers a repair,
  which is a decision of its own, so it isn't made here.~~ Built: see
  *Judged on its own steps* above.
* ~~**Facts for a promoted tool.** A skill promoted to a tool
  ([note 98](98-one-call.md)) can be called without `load_skill`, so its
  inputs aren't looked up. The prompt layer still carries the facts.~~
  Built: the facts ride in the tool's description
  ([note 108](108-where-the-model-reads.md)).
* **Facts when the write-up skips.** A turn judged not worth a recipe
  writes no facts. The end-of-conversation look back still sees it.
* **A Home Assistant connector.** `needs: setu:homeassistant` works as
  soon as Setu has one. Until then a Home Assistant recipe's access is
  still a file you named.
