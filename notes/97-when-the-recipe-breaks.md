# 97 — When the recipe breaks

A learned skill ([note 96](96-solve-it-once.md)) is a recipe written
down on the day it worked. The world it describes keeps moving. Home
Assistant renames a service, an API moves a field, a script's
assumption stops holding. The recipe itself doesn't change, and the
next run follows it straight into the wall.

Two things can go wrong from there. The first is quiet. The model
follows the recipe, it fails, and the model, being a model, works out
another way and finishes the task. The person gets their fan at 30%
and never learns that the saved recipe is now wrong. Next time the
model pays for the failure again, then for the rediscovery. The second
is loud. A recipe keeps failing and the model keeps loading it,
because the roster says it is the right tool for the job.

The counters from note 96 already see both: every use of a learned
skill is recorded as worked or failed. This note is about what happens
next.

## The failed turn is the repair

Look at what the quiet case leaves behind. A turn that loaded the
recipe, a step that failed, and then steps that worked — the way the
task gets done *now*. That is exactly what a repair needs, and it is
already in the history. So repair is not a second solve. It's the same
write-up as a new skill, pointed at a different question.

After a turn in which a learned skill was followed and counted as
failed, Yantra checks one more thing: did any call after the failure
work? If not, there is nothing to repair *from*. The task wasn't
finished another way, and nothing is offered. If one did, the
write-up runs:

* **One fresh, small call**, as in note 96: the task, the steps,
  clipped results, a line per failure. It also gets the saved skill as
  it is now, its `SKILL.md` and scripts, minus the counter line.
* **It decides first.** `VERDICT: skip` when the steps show the failure
  wasn't the recipe's fault: a server that was down, a mistake in what
  the person asked. The recipe is left alone, and the failure still
  counts. Otherwise `VERDICT: update`, and the same shape as a new
  skill, **under the same name**. A reply that renames it is overruled.
  An update keeps its name and its folder.
* **The same test, the same cap.** The new script runs once where the
  solve ran. It gets one fix if it fails, and is dropped if it still
  does.
* **The question is a diff.** A person reads an update as a change, so
  that's what they see. The first three lines below are from the
  receipt run. The question is what `--learn ask` puts after them (the
  receipt ran `--learn auto`, which saves without asking):

```
skill ha-fan-set-percentage: this use counted as failed (1 in a row; set aside at 3)
· ha-fan-set-percentage failed this time; seeing whether it needs updating
· testing scripts/set_fan.py (1 of 2)

Update this skill?
  ha-fan-set-percentage failed this time -- bash: ERROR: POST fan/set_percentage failed: HTTP Error 400: Bad Request
  the task was then finished another way; this is that way, written over the saved recipe
── changes ──────────────────────────────────────────────────────
-    req = Request(f"{ha_url}/api/services/fan/set_percentage", data=body, method="POST",
+    req = Request(f"{ha_url}/api/services/{service}", data=body, method="POST",
…
[s]ave  [e]dit first  [N]o >
```

The page shows the same diff, with added and removed lines coloured,
above both files in editable form.

**Saving an update keeps the record.** The date it was first learned,
the times it worked and failed, and the last good day all carry over.
Only the streak (below) goes back to zero. A recipe with a long good
history that broke once and was fixed should look like that in
`/skills`, not like something new.

## Three failures in a row: set aside

A recipe can also fail with nothing to repair from. The model follows
it, fails, and gives up, or the turn is cut off. One such failure is
noise, because a server can be down for an afternoon. Three in a row is
the world having changed.

So the counter line gained a streak:

```
learned: 2026-09-28 · worked 4 · failed 3 · last ok 2026-10-02 · failing 3
```

`failing` counts failures since the last time the recipe worked. Any
success resets it. At three, the skill is **set aside**:

* it leaves the roster, so the model no longer sees it as the way to do
  the job, and does the task with its own tools instead;
* `/skills` still lists it, marked `[stale]`, and the page marks it
  *stale*, so the person can see what happened and delete it or edit
  it by hand;
* a model that names it anyway (from memory of an earlier turn, say)
  is refused with a sentence telling it to do the task directly, not
  handed a recipe that failed three times.

Being set aside is how a stale recipe gets repaired. The model solves
the task fresh, and that fresh solve is a new learning candidate.
Before the write-up, Yantra lists the set-aside skills in its prompt:
*if this task is what one of them was for, use its NAME, so the new
recipe replaces it.* The new recipe then takes the old one's place
instead of sitting beside it, and the question says so.

The threshold is three, and it is a constant, not a setting. Changing
it means a different theory of when a recipe is broken, which is worth
arguing about. It isn't worth a flag.

## What an update writes down

An update records the solve. It doesn't record what the person meant.
In the receipt below, the recovering solve read the fan's
`percentage_step` (33.3 for that fan) and chose to send **33**, not the
30 that was asked for. The update wrote that choice into the script,
as a "snap to the nearest step". That is faithful to what happened,
and it is also a change of behaviour a person might not want. This is
the case for asking first, made again. With `--learn ask` the snap is
in the diff, and the person can edit it out before saying yes. With
`--learn auto`, which the receipt used, nobody read it.

## Found along the way

The recovering model tried to open the saved script with `read_file`
and was refused: *path escapes sandbox*. A skill in `~/.yantra/` is
outside the working folder, and `read_file` only reads inside it. The
model fell back to `bash`, which cost a wasted call. But `load_skill`'s
own delivery text says to read bundled files with `read_file`, and for
a skill in the person's home folder that advice is wrong. This
predates learned skills: every skill in `~/.yantra/skills/` had the
same problem.

~~It is a separate fix.~~ Fixed, in two parts. First, **read-only tools
may read inside a skill's own folder.** `read_file`, `list_dir`, `glob`,
`grep` and `read_image` can now read the folder of each skill that is
switched on, even when it is outside the working folder. The folder
opens only for reading, never for `write_file` or `edit_file`. It is
one folder per skill, never `~/.yantra` as a whole, which holds other
things. A skill that is off or set aside shares nothing, and a symlink
inside the folder that points out of it is still refused. Sub-agents
get the same folders as their parent.

Second, **`load_skill` names each bundled file by its full path**, so
the model copies one exact string instead of building it. With only the
first part, `qwen3.8:latest` still retyped a long home path four ways
before one worked: 6 calls, 21,350 tokens, 44 s to read one script.
With both, the same question took `load_skill` and one `read_file`: 2
calls, 10,990 tokens, 18 s.

## What was deliberately not built

* **Repair without a person.** `--learn auto` saves updates unread, as
  it saves new skills. The snap-to-33 above is the argument against
  running unattended for long.
* **Undo.** An update replaces the saved folder. The old version is in
  the diff the person read, not on disk. If updates turn out to go
  wrong in practice, keeping one previous version is the obvious next
  step.
* **Repairing a hand-written skill.** Only learned skills are counted,
  set aside or updated. A skill a person wrote is theirs to change, and
  a failure of one is reported by nothing but the turn itself.
* **Promotion to a tool.** Still the next step ([note 96](96-solve-it-once.md)
  has the argument): a recipe that has worked many times could become
  one structured call.

## Receipt

`qwen3.8:latest` through Ollama, `--yolo`, `--learn auto`, the fake
Home Assistant from note 96 **after an upgrade**: `fan.set_percentage`
is gone (400, *service not found*), and `fan.turn_on` accepts a
percentage. The saved skill was the `ha-fan-set-percentage` recipe
from note 96, with a record of `worked 3 · failed 0`.

| | tool calls | tokens | seconds | server log |
|---|---|---|---|---|
| "living room fan to 30%": the recipe fails, then another way works | 11 | 49,137 | — | `set_percentage` 400 ×2, `set_speed` 400 ×2, then `turn_on` 200 |
| its update: written and tested | 1 test run | 10,143 | 68 | one `turn_on` 200 |
| "office fan to 40%" with the updated recipe | 3 | 11,904 | 29 | list, `turn_on` 200, read back |

The record through the three steps:

```
learned: 2026-09-28 · worked 3 · failed 0 · last ok 2026-09-28               before
learned: 2026-09-28 · worked 3 · failed 1 · last ok 2026-09-28 · failing 1   after the failed use
learned: 2026-09-28 · worked 3 · failed 1 · last ok 2026-09-28               update saved: streak cleared
learned: 2026-09-28 · worked 4 · failed 1 · last ok 2026-09-29               next use
```

The update cost more than a new save (10k tokens against 4–6k),
because it carries the old skill and describes an eleven-call turn.
That is still 26 times cheaper than the old in-session write-up. The
saved folder held no token, no server address and no entity id.

`2258 passed, 1 skipped` (was 2248). The new tests are in
`tests/test_learned_skills.py` (`TestRepair`, `TestStale`). They check
that:

* an update carries the saved script and never the counters;
* a reply cannot rename what it updates;
* saving keeps the record and clears the streak;
* a failure the model calls not the recipe's fault leaves it alone;
* no model call is made when nothing worked after the failure;
* a working reuse is still not relearned;
* the third failure in a row takes a skill out of the roster;
* a fresh solve is told the set-aside names, so it replaces them.
