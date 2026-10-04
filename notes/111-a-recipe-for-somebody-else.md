# 111 — A recipe for somebody else

When Yantra solves something the slow way, it writes the working path
down as a recipe, a learned skill, and follows that next time
([note 96](96-solve-it-once.md)). The recipe was meant to hold steps,
never your values. Your fan's entity id goes in memory, and the recipe
names it as an input
([note 106](106-what-the-recipe-leaves-out.md)). So in principle a
recipe can be handed to somebody else with the same fan and the same
problem.

In practice, "meant to" isn't enough. The write-up was told to keep
your values out, and you read it before it was saved. But a recipe gets
repaired after a failure ([note 97](97-when-the-recipe-breaks.md)),
edited by hand, and promoted to a tool
([note 98](98-one-call.md)). Nobody re-reads it with sharing in mind.
Copying the folder to a friend is how your Home Assistant token, or the
IP of your house, ends up in their chat.

## Read again, every file, before it leaves

```
yantra --skill-share home-fan        # or, in a session: /skills share home-fan
```

No model runs and no key is needed. Every file in the recipe's folder is
read for:

| what | how it's found |
|---|---|
| a secret | the same scrubber the write-up uses: tokens, `KEY=value` lines, Authorization headers |
| your redaction | the trace's own `--trace-redact` patterns and `--trace-redact-words` names ([note 79](79-scrubbed-before-it-is-written.md)) |
| an address | any email address |
| something you told it | a value from your memory: anything with a digit or a `. _ @ / :` in it (an entity id, an IP, a port) |
| where you live | your home folder's path, your login name |
| an unknown account | a `needs: setu:<id>` Setu has no connector for |

**ANY FINDING STOPS IT.** Nothing is written, and each problem names
the file, the line and the reason:

```
not shared -- 1 problem(s):
  scripts/fan.py:2: a value you told it: 'fan.office_ceiling_2' (from memory:
  "Their office fan's Home Assistant entity id is fan.office_ceiling_2.") -- make it an input
```

The fix is yours: edit the file, or turn the value into an input. A
checker that quietly cut the value out would hand somebody a recipe
with a hole in it, one that fails on their machine and that nobody
reviewed in its changed form.

**WORDS AREN'T VALUES.** Memory says *"Their office fan's Home
Assistant entity id is …"*, and every fan recipe says "Home Assistant".
Only tokens with a digit or a joiner in them count as yours. A personal
name is what the trace's name list and name reader are for, and those
are checked too.

**WHAT ISN'T THE RECIPE'S STAYS BEHIND.** The `learned:` counters are
how it did for *you*. A `tool:` line is a promotion you chose, so the
person who installs it decides that for themselves. The copy keeps the
steps, the inputs, the needs and the script.

## Where it goes

`recipes/<name>/` under the current folder, with a hash over its files:

```
written to ./recipes/home-fan
  sha256:db0bc1ae674dd27dc00d8ab5f4628d64d9532441660b796653bf848a4722749b
```

Zip the folder and send it, or offer it to the Setu catalog:

```
yantra --skill-share home-fan --submit --author priya
```

`--submit` runs the same checks, writes the same folder, and then hands
it to Setu's catalog server for review (`setu catalog submit`). That
only happens when you ask: sharing alone never sends anything anywhere.
If you don't give `--author`, git's `user.name` is used.

## From the catalog

A recipe the catalog lists is installed by name:

```
yantra --skill-install catalog:ha-fan-speed
```

Setu fetches it, checks it against the hash in the signed catalog, and
writes it to a temporary folder. From there it's the install below:
every file shown, then the question. On the Connections page, a listed
recipe has an **install** button under the connector it needs. It opens
the same thing as a dialog, with every file, and installs only on your
click.

## Who vouches for it

A catalog recipe arrives with what can be known about that exact
version, and the install dialog puts it first:

- **Signed by its author.** Optional for a recipe: `--skill-share NAME
  --submit --sign-with KEY`.
- **Certified.** Independent people ran this version on their own
  servers and signed for it, each with their own key. You choose whose
  word counts (`setu certify trust THEIR.pub`), and a card reads
  *"certified by 1 you trust (Acme Labs) + 2 others"*. A withdrawal
  shows in amber: *"withdrawn by Acme Labs"*.
- **Worked.** How often this version did its job elsewhere. Each time a
  recipe you installed from the catalog is counted, worked or failed,
  that's reported anonymously, under Setu's `share-installs` switch.
- **Where its scripts run here.** Said as it is: *"scripts run here in
  bubblewrap: no network, the host read-only"*, or *"UNCONFINED… start
  Yantra with its bubblewrap sandbox to contain them"*.

None of these proves the absence of an attack, and the dialog doesn't
claim to. Every file is still shown before the question. How to become
a certifier is in Setu's README, under "Who vouches for it".

## Installing one somebody shared

```
yantra --skill-install ./recipes/home-fan
```

The danger runs the other way now: a script somebody else wrote. So
every file is printed in full, with the hash, and you're asked:

```
install home-fan? Its script runs in your sessions, in the sandbox, when a task calls for it [y/N]
```

Without a terminal to ask in, nothing is installed. On a yes, the recipe
goes into your own learned folder with fresh counters and a line saying
where it came from:

```
shared: sha256:db0bc1ae…
learned: 2026-10-01
```

From then on it's yours. `/skills` marks it `[shared]`. It's counted,
repaired and set aside when it goes stale exactly like a recipe Yantra
learned for you, and its script runs in the same sandbox, through the
same approvals. Installing never overwrites. Installing the same recipe
twice does nothing, and a different recipe with the name of one you
already have is refused.

## Receipt

A scratch project with a learned fan recipe whose script hard-codes
`"fan.office_ceiling_2"`, and a memory that says it. Run through the
real CLI, no model involved: the share above refused, naming the line.
After the value became `sys.argv[1]` it was written, with the hash
above. Then, as another person (a different `HOME`), `--skill-install`
printed SKILL.md and `scripts/fan.py` in full and asked. On `y` the
recipe landed in that person's `~/.yantra/skills/learned/home-fan/`,
with `shared:` carrying the same hash and `tool:` gone.

`tests/test_skill_share.py` pins it. A clean recipe leaves without
your counters or `tool:`. A token, an email, your home folder, your
login, a redaction match or a value from memory each stop it, and
nothing is written. "Home Assistant" is not a value. Needs are checked
when Setu is there and noted when it isn't. Installing shows and asks,
gets fresh counters and the hash, treats the same recipe again as a
no-op, and refuses a different one with the same name.

## What was deliberately not built

* ~~**The catalog's side.**~~ Built: `--submit` sends a checked recipe
  for review, and `--skill-install catalog:NAME` and the page's
  **install** button take a listed one, checked by hash and counted
  anonymously. See *From the catalog* above, and the Setu README's
  catalog server.
* **Running the recipe's test before it leaves.** The test command
  lives in the draft while saving and isn't kept in the folder.
  Re-running a recipe against a fake connector is the catalog's CI,
  where the network is off.
* **Checking a personal name.** That's the trace's name list
  (`--trace-redact-words`) and name reader, which share reads when you
  pass them. Guessing names from capital letters flagged "Home
  Assistant".
* **Sharing from the page.** The terminal only, for now. A button in the
  skills panel is the same call and a different screen.
