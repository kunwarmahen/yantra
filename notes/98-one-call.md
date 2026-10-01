# 98 — One call

A learned skill ([note 96](96-solve-it-once.md)) turns an eight-step
discovery into a recipe. The recipe still costs steps. The model loads
it, reads the instructions, and writes the shell command that runs its
script. With a fan to set, that is two or three tool calls and about
12,000 tokens, every time. The script itself doesn't change between
runs, and neither does the way it's called. Only the fan's name and the
percentage do.

So a recipe that has proved itself can become a **tool**. It's the same
script, offered to the model as one structured call:

```
ha_fan_set_percentage(fan_name="living room", percentage=30)
```

That's the cheapest shape there is. It's also the shape small local
models get right most often. Filling in two named fields is easier than
reading a page of steps and writing a shell command from them.

## Suggested, never done for you

Yantra doesn't promote anything on its own. It watches the counter line
Yantra already keeps for each learned skill and adds one number to it:
**successes in a row**. When a skill with a script has worked five
times in a row, `/skills` says so:

```
   ha-fan-set-percentage [learned-user] -- Set a Home Assistant fan entity to a
given percentage via the REST API, … Use when asked to turn a fan on/off or
change its speed on a Home Assistant instance.
     learned 2026-09-28 · worked 9 · failed 1 · last ok 2026-09-29 · in a row 5
     worked 5 times in a row -- make it a tool? /skills tool ha-fan-set-percentage
```

The turn that reaches five prints the same suggestion once. On the web
page the skill's row gets a *make it a tool?* button.

It counts in a row, not in total, on purpose. A recipe that was just
repaired ([note 97](97-when-the-recipe-breaks.md)) has to prove the new
version. It doesn't get to coast on the old one's record.

## Making one

`/skills tool NAME` runs the same pattern as a save. It's one fresh,
small model call, followed by a test and a question:

1. **The call** sees the recipe, its script, and the commands that ran
   the script in this session, if there were any. It never sees the
   conversation. It writes the tool as JSON: a name, a description, the
   parameters, and an **argv template**. The template says which
   parameter goes where on the script's command line. An optional one
   sits in a *group* with its flag (`["--env", "{env_file}"]`), so
   leaving it out drops the flag too.
2. **The rules are checked**, and a definition that breaks one gets one
   more try, with the rule it broke. Every parameter must reach the
   script. No placeholder may name a parameter that doesn't exist. An
   optional parameter must be in a group. And **no parameter may carry a
   secret.** This last rule came from the first live run. The fan script
   accepts `--token`, so the first definition offered an `ha_token`
   parameter. That would have made the model write the token into every
   call, and into the history. The script already reads the token from
   the file the person named. Now a parameter whose name looks like a
   credential is refused.
3. **The test runs once, through the new tool**, with arguments picked
   from a use the person already made. It goes through the permission
   gate like any call. A refused test means nothing is offered.
4. **The question.** Nothing is written until the person says yes:

```
Make this a tool?
  ha_fan_set_percentage -- Set a Home Assistant fan entity to a given percentage
via the REST API, auto-discovering the entity by name and snapping to the fan's
valid speed steps. Use when asked to turn a fan on/off or change its speed on a
Home Assistant instance.
  from:   the skill ha-fan-set-percentage
  fan_name: string, required -- Fan's friendly name or full entity_id to target
  percentage: integer, required -- Target speed percentage (0 to 100)
  env_file: string, optional -- Path to env file containing HA_URL and HA_TOKEN
  runs:   python3 …/ha-fan-set-percentage/scripts/set_fan.py 'living room' 30
  tested: passed -- {"fan_name": "living room", "percentage": 30}
Note: snapped 30% to nearest speed step 33%
fan.living_room_ceiling (Living Room Fan) → on at 33%
  cost:   2,291 in / 1,828 out tokens to write it
  each call still asks first, like bash
[m]ake it a tool  [N]o >
```

A yes writes a `tool.json` beside the script, and adds one line to
`SKILL.md`:

```
tool: ha_fan_set_percentage scripts/set_fan.py
```

The skill stays a skill. Its line in the prompt gains
`[tool: ha_fan_set_percentage]`, so the model knows it can skip the
recipe. Its counters keep counting, because a call to the tool counts
as a use.

## What a tool call may and may not do

**No shell, ever.** The script runs as a list of words
(`python3 <script> <arg> <arg>`), not as a command line a shell would
read. A fan name of `office; rm -rf ~` reaches the script as one odd
fan name. It never becomes a second command. The one thing a list of
words still allows is an option the script never meant to expose. A
fan called `--help` would do that, so a text argument may not start
with `-`.

**It asks, like bash.** The person has read the script twice by now, at
the save and at the promotion. But a script can do anything bash can,
so each call goes through the ordinary permission prompt. `--yolo`
covers it exactly as it covers bash. So does a confining `--sandbox`,
under the same rule that auto-approves bash only while it is really
contained ([note 16](16-sandboxing.md)). A skill whose `needs` line
talks about money asks every time, even under `--yolo`.

**The sandbox is bash's.** Whatever contains the session's bash
contains the script, and it runs from the working folder, as the recipe
did. Under bubblewrap, the skill's own folder is mounted read-only, so
the script can be found. Nothing else outside the working folder
appears.

**It follows its skill.** If the skill is switched off, the tool goes
with it. If the skill is set aside after three failures, the tool is
set aside too. If `tool.json` is broken, the tool isn't offered, and
the recipe still works as a recipe. `/skills` says why.

## When the recipe is repaired

A repair ([note 97](97-when-the-recipe-breaks.md)) rewrites the script.
The tool's definition was written for the old one. So the update
question now says what happens to the tool. The repair has its own
test command, for example `python3 "$SKILL_DIR/scripts/set_fan.py"
office 40`. That command's words are read back through the tool's argv
template:

* **They fit.** The fixed script takes the same arguments. The tool
  stays, and the update copies `tool.json` across.
* **They don't.** A new argument appeared, one went away, or the script
  has a new name. Yantra drops the tool, and the skill is a recipe
  again. Because the repair clears the success streak, the suggestion
  comes back after five fresh successes.

This check is plain string matching, not a model call. It can't guess.
When it can't read the test back into arguments, it says the shape
changed, and the tool goes.

## Receipt

`qwen3.8:latest` through Ollama, `--yolo`, and the fake Home Assistant
from note 97, with fan speeds in steps. The skill was the
`ha-fan-set-percentage` recipe that note 97 repaired, with its record
set to five successes in a row.

**Making the tool**, four runs from the same starting point:

| run | tokens (in / out) | seconds | first try passed its rules | parameters |
|---|---|---|---|---|
| 1 | 2,248 / 968 | 22 | yes | fan, percentage, env_file, ha_url, **ha_token** |
| 2 | 2,291 / 1,646 | 25 | yes | fan_name, percentage, env_file, ha_url |
| 3 | 2,291 / 1,451 | 24 | yes | fan, percentage |
| 4 | 2,291 / 1,828 | 28 | yes | fan_name, percentage, env_file |

Run 1 happened before the secret rule existed, and it is the reason for
that rule. After the rule was added, one more run broke a different
rule: it put an optional `env_file` outside a group. It was refused,
and that refusal is why a broken definition now gets one more try.
Every test run set the living room fan, with one `turn_on` on the
server each time.

**Using it**, with the run 4 tool:

| request | tool calls | tokens | before (recipe, note 97) |
|---|---|---|---|
| "set the office fan to 40%" | 2: `ha_fan_set_percentage` ×2 | 7,825 | 3 calls, 11,904 |
| "turn the bedroom fan to 50 percent" | 2: `ha_fan_set_percentage` ×2 | 7,915 | — |
| "set the living room fan to 60%" | 2: `load_skill`, `ha_fan_set_percentage` | 9,841 | — |

It isn't one call yet, and the reason is honest and not the tool's.
The saved script matches by substring, and "office fan" isn't a
substring of "Office Tower Fan". The first call fails, and the script's
error lists the fans. The second call, with `Office Tower Fan`, works.
A recipe that matched names word by word would make it one call. That
is a fix for the script, and a repair can deliver it. In the third run,
the model loaded the recipe first to read how the env file is used,
then made one correct call. It was counted as a use, and the turn
wasn't offered for saving again:

```
skill ha-fan-set-percentage: this use counted as worked
learned: 2026-09-28 · worked 10 · failed 1 · last ok 2026-09-29 · in a row 6
```

Even with the extra call, a request cost about 8,000 tokens with the
tool, against about 12,000 with the recipe. On a cloud model the same
saving is money. On a local one it's also time and fewer places for a
small model to go wrong.

`2313 passed, 1 skipped` (was 2265). The new tests are in
`tests/test_promote.py`. They check that:

* the suggestion waits for five in a row;
* a model-written value is one argument and never a second command;
* an option can't be smuggled in as a value;
* every call, including the promotion's own test, goes through the gate;
* a pulled or stale skill takes its tool with it;
* a broken `tool.json` leaves the recipe working;
* a tool name that's already taken is never registered over;
* a secret-shaped parameter is refused;
* a broken definition gets exactly one more try;
* a repair keeps the tool when the arguments fit, and drops it when
  they don't.

A promoted tool is called without `load_skill`, so the facts that fill
its recipe's inputs reach it another way: through its description
([note 108](108-where-the-model-reads.md)).

## What was deliberately not built

* **Promoting without asking.** Five in a row makes the suggestion.
  Promoting is still the person's call.
* **Several scripts, lists, objects.** A tool runs one script. Its
  parameters are text, whole numbers, numbers and yes/no, because each
  one becomes a single word on a command line. A recipe with two
  scripts, or one that takes a list, stays a recipe.
* **Asking less.** A promoted tool could be trusted like a read-only
  one after enough uses. It isn't. A script that sets a fan today is a
  script that could do anything tomorrow, if an update changed it.
* **Sharing a tool.** `tool.json` holds no data, and the test arguments
  are never stored in it. But sharing learned skills at all needs its
  own checks first ([note 96](96-solve-it-once.md)).
