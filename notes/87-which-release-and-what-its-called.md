# 87 — Which release, and what it's called

A **tool pack** is a Python distribution you install with pip that
brings tools with it ([note 53](53-a-tool-that-arrives-by-pip.md)). An
agent package names the packs it uses in `agent.toml`:

```toml
[tools]
packs = ["tide-pack"]
```

Note 53 left three gaps, and they turned out to want three different
answers:

1. **Nothing says which release.** `tide-pack` is a name, not a version.
   You grade the agent against tide-pack 0.2.1 on Monday. Somebody
   upgrades the environment on Tuesday, and the same manifest now runs
   against 0.3.0 without anyone noticing.
2. **Nothing checks the pack fits this Yantra.** A pack written for a
   newer Yantra loaded fine and then failed with a confusing error when
   its tools were registered.
3. **Two packs that both ship `search` cannot both be used.** The
   collision is loud, and it should be. But the only way out was to fork
   one of the packs and rename its tool.

## 1. The manifest checks the release, the lockfile chooses it

```toml
[tools]
packs = ["tide-pack==0.2.1"]
```

If a different release is installed, the agent refuses to start and says
which one to install:

```
error: tool pack tide-pack: this agent names release 0.2.0 and release
0.2.1 is installed. Install the one it was written against
(pip install tide-pack==0.2.0), or change the line if the agent has been
checked against 0.2.1
```

**THE MANIFEST CHECKS, THE LOCKFILE PINS.** Note 53 was right that
choosing a version is the environment's job. `uv.lock` or
`requirements.txt` decides what gets installed, as it does for every
other dependency. The manifest does something different: it states the
release the agent was written and graded against, and it refuses to run
when the environment disagrees. It never installs anything.

**EXACT RELEASES ONLY.** `tide-pack>=0.2` is an error, against the file,
as soon as the manifest is read:

```
error: harbour/agent.toml: tool pack 'tide-pack>=0.2': a pack is a name,
or a name and the exact release it was written against
(tide-pack==0.2.1); ranges belong in the environment's lockfile, which is
what chooses the version installed
```

There are two reasons. A range is a question for a dependency resolver,
and answering it here would need a full version parser or a new
dependency. Yantra's core depends on two libraries, on purpose. And an
exact release is the honest claim: the agent was graded against *one*
release, not a range of them.

The check happens **before the pack is imported**. A pack's code starts
running the moment it is imported. A pack that is the wrong release
should be refused without its code getting that chance.

A bare name still works exactly as before. `--tool-pack NAME==1.2.3`
works on the command line too.

## 2. A pack says what Yantra it needs, and is refused at the door

A pack author already writes their Yantra requirement in their own
`pyproject.toml`, the same way they list any other dependency:

```toml
dependencies = ["yantra>=0.4"]
```

pip normally enforces that when it installs. But a pack can still end
up in an environment that does not meet it: installed with `--no-deps`,
or Yantra changed after the pack was installed. So Yantra now reads the
pack's own requirement when it loads the pack:

```
error: tool pack future-pack requires yantra>=0.4, and this is yantra
0.1.0; upgrade yantra, or install a release of future-pack built for
this one
```

That pack's import code exits the whole process. The error above is all
that happened, so its code never ran.

**A CHECK THAT CANNOT BE SURE STAYS QUIET.** `>=`, `>`, `<=`, `<`, `==`
and `!=` against plain release numbers are compared. Anything else is
left alone rather than guessed at: `~=`, wildcards, pre-releases like
`1.0rc1`, and lines that only apply under a condition (`; extra ==
"web"`). A wrong refusal of a pack that would have worked is worse than
the confusing error it replaces. A pack that declares no Yantra
requirement loads as it always did.

**NOT A VERSIONED GROUP NAME.** The alternative was a new entry-point
group per base-class version, `yantra.tools.v2`. Nobody can move to a
group name like that gradually: every pack would have to republish on
the same day.

## 3. A prefix, when you ask for one

```toml
[tools]
packs = ["tide-pack==0.2.1", "sea-pack==1.0.0"]

[tools.prefix]
"tide-pack" = "tide"       # search -> tide_search
"sea-pack" = "sea"         # search -> sea_search
```

**NO PREFIX BY DEFAULT.** Most packs never collide with anything. A
mandatory prefix would make every tool name longer, in every prompt and
every eval case, to solve a problem most agents do not have. The
collision error now points at the way out:

```
error: tool pack sea-pack: tool 'search' is already registered -- a pack
may not shadow another tool (exclude the original with tools.deny, or
give one pack a prefix in [tools.prefix])
```

**THE PREFIXED NAME IS THE ONLY NAME.** `tools.allow`, `tools.deny`,
eval cases and the model itself all see `tide_search`. There is no
second, "real" name hiding underneath that some part of the system
still uses.

**A SEPARATE TABLE, NOT A SECOND SYNTAX.** The prefix could have been
squeezed into the `packs` line (`"tide-pack as tide"`), or `packs` could
have held tables instead of strings. Both would give one list two
shapes. A small table of *pack = prefix* keeps `packs` a list of strings
and puts the new idea on its own line. A prefix for a pack that `packs`
does not load is an error, because it is a typo and the collision it was
meant to settle is still there.

The separator is one underscore. MCP tools use two (`mcp__server__tool`,
[note 09](09-mcp.md)), so you can tell at a glance which way a tool
arrived.

**THE DESCRIPTION IS NOT REWRITTEN.** If a pack's description says "use
search to find a port", the model now reads about a tool called
`search` while holding one called `tide_search`. Local models mostly
cope, as the receipt shows, but it is a real wart. The fix belongs to
the pack author: a description should say what the tool does, not what
it is called. Rewriting someone else's prose by pattern-matching would
be worse than one stale word.

## The report says which pack moved

[Note 68](68-the-version-nobody-bumped.md) already put each pack's
installed release into the package **fingerprint**, the short hash an
eval report uses to tell whether the agent changed between runs. A moved
pack changed the fingerprint, but the report could only say *something*
changed. Now each report records the release of every pack by name:

```json
"packs": {"tide-pack": "0.3.0", "sea-pack": "1.0.0"}
```

`--against` names the one that moved, the same way it names a moved
price ([note 62](62-the-reports-you-already-have.md)):

```
tool pack tide-pack: 0.2.1 → 0.3.0, so what moved below may be the pack
```

## Both roads

None of this depends on the model. Releases are checked and names are
prefixed before any model is called, so a cloud run and a local one
refuse the same things with the same errors. The receipt uses
`qwen3.8:latest`.

## What was deliberately not built

**Installing the named release.** Yantra refuses and says which release
to install. It never runs pip. An agent that installed its own
dependencies at startup would be changing the operator's environment
behind their back.

**A prefix from the command line.** `--tool-pack` loads a pack for a
quick try. Settling a collision is a decision about the agent, and it
belongs in the file that describes the agent.

**Rewriting descriptions.** See above.

## Receipt

Three real distributions, built and installed into a scratch
virtualenv: `tide-pack` and `sea-pack` each ship a tool called `search`,
and `future-pack` requires `yantra>=0.4` and exits the process if it is
ever imported. pip itself refused `future-pack` (*"only yantra<0.4 is
available"*), so it went in with `--no-deps`: the case the check
exists for.

```
$ yantra --packs
future-pack 2.0.0
  tools = futurepack.tools
sea-pack 1.0.0
  tools = seapack.tools
tide-pack 0.2.1
  tools = tidepack.tools
```

The four refusals above came from these four `packs` lines, in that
order: `"tide-pack==0.2.0"`, `"tide-pack>=0.2"`, `"future-pack"`, and
`"tide-pack==0.2.1", "sea-pack"` with no prefixes. Then the `[tools.prefix]`
table from section 3, with `qwen3.8:latest`:

```
$ yantra --agent harbour --model qwen3.8:latest \
    --prompt "When is high water at Falmouth today, and what is the forecast for sea area Plymouth?"
agent: harbour 0.1.0 -- harbour
tool packs: tide-pack==0.2.1, sea-pack==1.0.0
→ tide_search()
→ sea_search()
╭─ tide_search() ─────────────────────────────────────────────╮
│ {"port": "Falmouth"}                                        │
│ Falmouth: high water 06:42 and 19:03, low water 00:31 and 12:55
╰─────────────────────────────────────────────────────────────╯
╭─ sea_search() ──────────────────────────────────────────────╮
│ {"area": "Plymouth"}                                        │
│ Plymouth: southwest 5 to 7, moderate or rough, good         │
╰─────────────────────────────────────────────────────────────╯
**High water at Falmouth (UK local time, BST):** 06:42 (morning), 19:03 (evening)
**Sea area Plymouth forecast:** Force 5–7, southwest; moderate to rough; good visibility
```

Both descriptions still say only "Search the …", and the model picked
the right `search` for each question on its first try.

A one-case suite, run, then `tide-pack` upgraded to 0.3.0 and the pin
moved to match:

```
$ yantra --agent harbour --model qwen3.8:latest --eval --report r2.json --against r1.json
against harbour 0.1.0 on ollama/qwen3.8:latest, 2026-09-25T01:55:03Z (1/1 passed)
tool pack tide-pack: 0.2.1 → 0.3.0, so what moved below may be the pack
same version, different package: 4af295a3737f → 22886ddffa1c -- the agent was
edited without a version bump, so what moved below may be the edit
  no case changed verdict or pass count
```

Both warnings are true. The pack moved, and so did the manifest (its
pin line changed). Before this change, only the second one could be
printed.

`2035 passed, 1 skipped` (was 1991).
