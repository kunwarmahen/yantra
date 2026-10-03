# 96 — Solve it once

Ask an agent to *"turn the bedroom fan to 60%"* and the first time is
real work. It has to find Home Assistant, learn how its API wants to be
called, list thirty-odd devices, work out that the bedroom fan is
`fan.master_bedroom_ceiling` and not the `switch.bedroom_fan_light`
sitting next to it, and get the request body right. On
`qwen3.8:latest` that took eight tool calls and four failed requests.

The second time should take two. The agent has done this. Nothing it
learned was secret or lucky: a URL pattern, a service name, a way to
find a fan by the name a person says. It just had nowhere to put it,
so the next session starts from nothing and pays for the discovery
again.

Yantra already has a place for procedures: a skill ([note 30](30-skills.md)),
a folder with a `SKILL.md` the model loads only when a task matches. So
the question was never *where* to keep a solved task. It was how to get
from a finished turn to a good skill without paying more for the
write-up than the task cost, and without saving anything nobody read.

## The obvious way costs ten times the task

The obvious way is to ask. After the fan turn, say *"save what you
learned as a skill"*, and the agent has every tool it needs to write
one. Measured on `qwen3.8:latest`, against a fake Home Assistant with
three fans and a decoy:

| | tool calls | tokens | seconds |
|---|---|---|---|
| the solve | 8 | 27,385 | 53 |
| "save that as a skill", in the same session | 16 | **263,811** | 306 |

The skill it wrote worked: the next "living room fan to 30%" took three
or four steps instead of six to nine. But at 263k tokens, the save
only pays for itself after about twenty reuses. Two things made it
expensive, and both follow from asking inside the session:

* **Every step of the write-up re-sent the whole conversation.** The
  history was 15–20k tokens by then, and all of it went out again with
  each of the sixteen calls.
* **The model tested its script until it got bored.** Nobody said when
  to stop, so it ran it about ten times.

And the write-up had a third problem that money doesn't measure. Its
`SKILL.md` had a section of *pitfalls learned the hard way* that never
happened. It said `set_percentage` "returns 501" (it had returned 400,
for a bad body), and the script handled "HA custom codes" in the 1000
and 3000 ranges that the server never sent.

## What happens instead

When a turn ends, Yantra looks at it. Four steps, and only the second
asks a model anything:

```
notice   the turn finished, took 4+ tool calls, followed no skill    counted, free
distil   one fresh, small model call: decide, then write              1 call
test     run the script where the solve ran; one retry               at most 2 runs
ask      the person sees every line and says save, edit, or no       the terminal or the page
```

**Noticing is counted, not asked.** "Did it take real effort" is a
count: a turn with fewer than four tool calls had nothing to discover.
Weather in Raleigh takes two calls cold, because the model already
knows Open-Meteo, and a recipe would only restate it. A turn that
loaded a skill is reuse, not learning, and a turn that was cut off or
refused did not finish. None of these cost a token.

**One call decides and writes.** The other two questions, *did it
work* and *will it come up again with different inputs*, need
judgement, so they go to the session's own model. They go in the same
call that writes the skill: the reply starts `VERDICT: save` or
`VERDICT: skip`, so a turn that doesn't qualify costs one short
answer, not a judgement call plus a write-up.

**The call is fresh and small.** It gets one message and no tools, and
never sees the conversation. The message holds the task, each tool
call with its arguments, each successful result clipped to 1,200
characters, one line for each failed call, and the final answer. On
the fan runs below, the whole write-up call, prompt included, was
1,500–3,100 tokens in. The turns it described had sent 13–40k.

**The test has a cap.** If the draft has a script, it runs once. If it
fails, the model gets one chance to fix it, seeing only the script,
the command and the output, and it runs once more. Then Yantra stops.
A draft that still fails is **not offered**: the person is told why,
and nothing is left behind.

**The person decides.** In the terminal:

```
Save this as a skill?
  ha-set-fan-percentage -- Turn a Home Assistant fan entity on at a given percentage
  by looking up the matching entity and calling the fan/turn_on service. Use when …
  scope:  you, in every project -> ~/.yantra/skills/learned/ha-set-fan-percentage
  needs:  network access to the Home Assistant instance named in the env file
  inputs: env file path … fan search term (e.g. `office`) … percentage (0–100)
  tested: passed (1 run) -- $ python3 "$SKILL_DIR/scripts/ha_set_fan.py" ha.env office 50
  cost:   3,112 in / 2,950 out tokens to write and test
── SKILL.md ──────────── … every line that would be saved …
── scripts/ha_set_fan.py ──── … the whole script …
[s]ave  [e]dit first  [c]hange scope to project  [N]o >
```

The default is no. *Edit first* opens both files in `$EDITOR`, then
asks again. On the web page the offer waits in the *to keep* tray
instead of asking ([note 113](113-after-the-answer.md)), and its review
dialog has both files as editable text. An edit that breaks a rule (a renamed skill, a
thin description) comes back as the same question, with the error on
top and the edit kept.

## Only what was observed

The invented pitfalls came from a model that had seen the whole messy
transcript and was asked to be helpful about it. The fix has three
parts.

* **A failed call is one line.** The distil call sees that a request
  failed and the first line of why. It doesn't see a stack of 404
  bodies to build a story from.
* **The prompt says it.** *Write ONLY what the steps below show. No
  tips, pitfalls, error codes, retries or fallbacks that did not happen
  here.*
* **The person reads it before it exists.** The save question shows
  everything that would be written, not a summary.

The terminal run above is the hard case. Its solve went badly: nine
model calls and eight failed requests to paths Home Assistant doesn't
have. The model's own thinking noted the failures were *"worth jotting
down"*. The skill it wrote holds none of them, only the path that
worked: list the fans, match the name, `POST /api/services/fan/turn_on`,
read the state back.

## Never a secret, never a personal fact

A skill is a file that lives for years and may one day be shared. It
must not hold the person's token, and it shouldn't hold their fan's id
either: that is a fact about their house, and the skill should still
work after they rename the fan. Three layers keep both out.

1. **The model never sees a secret.** The distil message is scrubbed
   before it is sent. `HA_TOKEN=…` lines, `Authorization:` header
   values, `"password": "…"` fields and the token shapes the trace
   scrubber already knows ([note 79](79-scrubbed-before-it-is-written.md))
   become `[redacted]`. A value caught once is caught everywhere: the
   token found in `ha.env` is also removed from the curl command that
   used it.
2. **The prompt makes personal facts inputs.** *This person's server
   addresses, device names and ids, places and account names are
   INPUTS the skill asks for or looks up.*
3. **A draft that still carries one is dropped.** If the draft contains
   any value the scrubber removed, or anything shaped like a token, it
   is not offered, and the person is told why.

The check is narrow on purpose. A script that reads
`os.environ["HA_TOKEN"]`, or sends `f"Bearer {token}"`, is doing the
right thing, and a check that flagged it would teach people to ignore
the check.

In every run below, a search of the saved folder for the token, the
server's address and the bedroom fan's entity id found nothing. The
scripts find a fan by the name the person says.

## The test runs where the solve ran

A script the agent just wrote is code nobody has read. So its test
gets no looser rule than the turn that produced it. It runs through the
session's own `bash` tool, through the session's own permission gate,
in the session's own sandbox ([note 16](16-sandboxing.md)). Under
`--yolo` it just runs. Under the default gate, the person is asked
before it runs, like any other command, and a refusal means the draft
is not offered. There is nothing for a repair to fix in a script that
never ran, so a refused test is not counted as a failed one.

The test runs from the working folder, as the solve did, with
`$SKILL_DIR` pointing at the staged draft
(`.yantra/learning/NAME/`, under the gitignored folder).

## Where they live

| | |
|---|---|
| `~/.yantra/skills/learned/` | "you": every project |
| `.yantra/skills/learned/` | "this project": private, never committed |

The model suggests one. Anything about the person's world goes to
*you*, and anything about the code in this folder goes to *this
project*. The person can switch it in the same question. Nothing is
ever saved into the committed `skills/` folder. That would put
something the agent wrote into a repository, and only a person should
do that.

Both folders are searched **after** every hand-written root, so a skill
a person wrote always wins its name. If a draft picks a name a
hand-written skill already has, it is renamed (`-learned`) and the
question says so. If the name belongs to an earlier learned skill, the
question says the new one **replaces** it.

A learned skill is the ordinary format with a few more header keys:

```markdown
---
name: ha-fan-set-percentage
description: Set a Home Assistant fan entity to a given percentage via
  the REST API, auto-discovering the entity by name. Use when asked to
  turn a fan on/off or change its speed on a Home Assistant instance.
needs: Network access to the Home Assistant REST API; read access to the
  env file
inputs: - Fan name or entity_id (e.g. "bedroom") — from the user's
  request - Target percentage (e.g. 60) — from the user's request -
  HA_URL and HA_TOKEN — from a file the user named (here ha.env) or from
  environment variables of the same name
origin: learned
learned: 2026-09-28 · worked 3 · failed 0 · last ok 2026-09-28
---
```

The `learned:` line belongs to Yantra. An edit can't set it (saving
rewrites it), and the model never writes it.

## Counting, honestly

After every turn, Yantra checks for learned skills the turn loaded.
Each one gets a result: **worked** if the turn finished and no call
after the load failed, **failed** otherwise. A shell command that
exited non-zero counts as failed, even though the `bash` tool reports
it as data rather than as an error. A call the person refused doesn't
count against the recipe. For a recipe with a script, only the
recipe's own calls count, not the commands the model ran to look
around ([note 106](106-what-the-recipe-leaves-out.md)).

That rule is crude, and it says so. But it is what the harness saw,
not a grade the model gave itself, and it has already paid its way.
In the first reuse after the first save, the counter recorded
**failed**, even though the fan was set correctly. The trace showed
why: the recipe said to run the script "from this skill's folder", so
the model `cd`'d into it, and `ha.env`, a path relative to the working
folder, was gone. It recovered on the next call. The flaw was in the
recipe, and it came from the write-up prompt, so the prompt changed.
Recipes now run their script from the working folder as
`"$SKILL_DIR/scripts/…"`, the same form their test used. `load_skill`
fills in the real folder when it hands a learned recipe over. After
that change, every reuse counted as worked.

`/skills` shows the counters under each learned skill. The page shows
them in its skills panel.

## Using it

```bash
yantra                   # ask (default): offers after a solved turn
/learn                   # save the last turn now -- also works with learning off
yantra --learn off       # never offer; /learn still saves by hand
yantra --learn auto      # save a draft that passed its test WITHOUT asking
YANTRA_LEARN=off         # same as the flag
```

*Ask* needs somebody to ask, so a piped one-shot run (`yantra "…" |
tee log`) never offers, rather than stop and wait for an answer that
can't come. *Auto* is for unattended runs only. It says so at start,
because it writes files nobody read.

The page looks after it has handed the input back, not before. What
it finds waits in the *to keep* tray, a message sent meanwhile stops
the look, and a test that needs your yes waits for you to run it
([note 113](113-after-the-answer.md)). Its **save last turn** button
asks straight away, because you asked: while that question is up, the
input stays busy, and Stop means no.

Both roads work the same way. The write-up uses the session's own
provider and model, and its tokens go on the session's meter, so
`/usage` and the page's cost line include them. On a local model they
cost time. On a cloud model they cost money, and the save question
shows how much. On a small local model the whole feature is worth
more: solve a task once, with some trial and error, and every later
run follows a recipe known to work. Following a recipe is something
small models do well. Rediscovering an API is something they do badly.

## What was deliberately not built

* ~~**Promoting a script to a tool.** A recipe whose script has worked
  many times could become one structured tool call, which is cheaper
  still and easier for small models.~~ Built, and the person decides:
  [note 98](98-one-call.md).
* ~~**Repair.** When a saved recipe fails, the agent could solve the
  task fresh and offer the fix as a diff.~~ Built:
  [note 97](97-when-the-recipe-breaks.md).
* ~~**Going stale.** A recipe that fails several times in a row should
  stop being offered until it is fixed.~~ Built, alongside repair:
  [note 97](97-when-the-recipe-breaks.md).
* ~~**Inputs from memory, needs from connections.** `inputs:` and
  `needs:` are words for now. Filling "which fan" from what the agent
  remembers, and "Home Assistant" from a Setu connection
  ([note 95](95-the-accounts-you-connected.md)), comes when memory and
  a Home Assistant connector exist.~~ Built: the write-up hands the
  values it left out to memory, `load_skill` brings them back, and
  `needs: setu:<id>` is checked against Setu
  ([note 106](106-what-the-recipe-leaves-out.md)).
* ~~**Sharing.** A learned recipe holds no data, so it could be shared.
  But that needs its own checks before anything leaves the machine.~~
  Built: `--skill-share` reads every file for secrets, addresses, your
  paths and values from memory, and stops on any; `--skill-install`
  shows every file and asks ([note 111](111-a-recipe-for-somebody-else.md)).
* **Choosing which learned skills to show.** Every learned skill's
  description is in the roster, like a hand-written one. At two
  hundred skills, that's about 5,000 tokens a session. Picking learned
  skills by relevance, the way tools are picked
  ([note 17](17-tool-selection.md)), is the answer, once someone has
  enough skills to need it.
* **The async agent.** `AsyncAgent` hosts have no learner. The terminal
  and the page run the synchronous loop, and those are where there is
  somebody to ask.

## Receipt

`qwen3.8:latest` through Ollama, one-shot CLI, `--yolo`,
`--env-context local`, a fresh working folder per run, and a throwaway
home folder so user-scope saves could be inspected. The fake Home
Assistant is a REST API with a bearer token and 31 entities, including
three fans and a decoy switch, so the answer has to be discovered.
Token counts are every model call the process made, the write-up
included. Each run's end state was checked against the fake server's
call log.

**The save:**

| | solve (in + out) | save (in + out) | save time | test runs |
|---|---|---|---|---|
| asked inside the session (before) | 27,385 | **263,811** | 306 s | ~10 |
| bedroom fan, `--learn auto` | 27,740 | **4,195** | 35 s | 1 |
| bedroom fan, `--learn auto`, after the path fix | 13,593 | **4,623** | 47 s | 1 |
| office fan, terminal, answered `s` | 40,045 | **6,062** | — | 1 |

That's 43 to 63 times cheaper than asking inside the session, and 15
to 34 percent of what the solve cost. Every test passed on its first
run.

**The reuse** (the second save, then *"set the living room fan to
30%"* three times in fresh sessions):

| | tool calls | tokens | seconds | failed calls | counter after |
|---|---|---|---|---|---|
| reuse 1 | 3 | 11,734 | 15 | 0 | worked 1 · failed 0 |
| reuse 2 | 2 | 8,693 | 12 | 0 | worked 2 · failed 0 |
| reuse 3 | 3 | 12,058 | 19 | 0 | worked 3 · failed 0 |
| without a skill (before) | 6–9 | 22,801–29,690 | 33–56 | 0–3 | — |

The skill saves about 15k tokens a run. Before, the save only paid
for itself after about twenty reuses. Now it pays for itself on the
first one. Each reuse hit `fan.living_room_ceiling`, with one list, one
`set_percentage` and one read-back in the server's log.

**On ordinary work.** The check runs after every turn that passes the
count, so its cost on work that is *not* a recipe matters as much as
its cost on work that is. Six everyday tasks in a small Python project,
`--learn auto` so anything judged worth keeping would have been saved:

| task | turn (tokens) | decision | deciding cost |
|---|---|---|---|
| fix a failing test | 14,061 | skip — "a one-off bug fix" | 2,156 · 10 s |
| explain where approval is decided | 55,186 | skip — "a fixed fact about this repo" | 3,879 · 9 s |
| three largest files | 7,049 | skip by count (2 calls) | 0 |
| summarise the last commits | 4,742 | skip by count (1 call) | 0 |
| list every TODO | 11,885 | skip — "a single grep, not a recipe" | 2,792 · 19 s |
| add a function with a test | 49,595 | skip — "a one-off feature" | 3,866 · 10 s |

Six right answers and no false offers. The price is real, though:
7 to 23 percent more tokens on a turn that passes the count, about ten
seconds on a local model. On a cloud model that is money spent on
turns that save nothing, and `--learn off` is the answer for a session
of pure code work.

`2248 passed, 1 skipped` (was 2199). The new tests are in
`tests/test_learned_skills.py`, and each one guards against one of the
failures above. The write-up sees one message and no tools. A secret
the turn read never reaches it. A draft carrying one is dropped. A
failing script is tried at most twice and never offered. A refused
test is not repaired. A non-zero exit counts as a failed step. Enter
at the question saves nothing.
