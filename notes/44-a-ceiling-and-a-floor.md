# 44 · A ceiling, and the floor it could not see

[Note 40](40-a-package-that-delegates.md) let a package declare its own
sub-agents: a `[[subagent]]` table in `agent.toml` names a child, writes
its instructions, and — the part that matters here — writes the list of
tools that child may use. The example package ships one, a `fact_checker`
that reads files and quotes what it found. Its list is deliberately
narrow:

```toml
[[subagent]]
name = "fact_checker"
# NARROWER THAN THE PARENT, on purpose. No web_fetch: checking a claim
# against the sources in front of you is a different job from going and
# finding new ones.
tools = ["read_file", "glob", "grep", "outline"]
```

That comment is the whole promise. And until this note, nothing checked
it. Note 40 said so in its own closing section, and the example package's
eval suite said so twice — once in a comment that has now been deleted:

> What it CANNOT see is the child's own tool list. Roster assertions grade
> the tools this agent is offered, and `fact_checker`'s narrower list (no
> `web_fetch`, on purpose) is a fact about a child that does not exist
> until somebody delegates. Widening it would go unnoticed here.

Adding one word to that list is a one-line diff. It passes every test in
the repo, passes the package's own acceptance gate, and changes what the
package can reach on the internet. Of all the ways this repo's boundaries
can move, it was the only one with no alarm on it at all.

## Why the existing assertion could not reach it

[Note 35](35-roster-and-pass-rates.md) gave an eval case two assertions
that cost nothing: `has_tools` and `lacks_tools` grade the ROSTER — the
tools the agent is offered at all — before any request goes out. "This
agent has no way to write to disk" is the claim, and it is free, because
a tool list is knowable the moment the agent is built.

The obvious question is why that did not already cover the child. The
answer turned out to be more interesting than "nobody wrote the code",
and it is worth stating precisely, because it is also the reason the new
assertion is a *separate* family rather than a widening of the old one.

**A child is built out of the parent's registry.** When somebody
delegates, the spawner walks the child's declared list and pulls each
tool from the parent's own registry; there is no second source. So a tool
the package excluded cannot reach a child by way of delegation — it is
not there to be pulled.

Which means `lacks_tools = ["bash"]` has always covered the whole
package, children included. That assertion was never as narrow as it
looked.

What it cannot say is anything about the other direction. The parent's
list is a **ceiling**, and every child sits somewhere underneath it. A
fact-checker deliberately kept off the network sits well *inside* a
ceiling that permits `web_fetch`, and moving it up to the ceiling moves
nothing the old assertion could see. There was no way to assert a
**floor**.

```
   parent roster:  read_file  glob  grep  outline  web_fetch   <- ceiling
                   ─────────────────────────────────────────
   fact_checker:   read_file  glob  grep  outline              <- the floor
                                                     ^
                                      widening moves this line, and
                                      the ceiling never notices
```

## The assertion

Two new keys in `cases.toml`, and they are the same two claims as before,
asked about a named child:

```toml
[[case]]
id = "the-checker-is-actually-on-the-roster"
has_tools = ["fact_checker"]
subagent_has_tools   = { fact_checker = ["read_file", "outline"] }
subagent_lacks_tools = { "*" = ["web_*", "write_file", "edit_file", "bash"] }
```

Free, like their neighbours: no `user_message`, no model, no API key, no
tokens. A declared child's tool list is a fact about a file, and reading
a file costs nothing.

Four decisions in that, and three of them are about one failure.

### A table, not a namespace

The cheap version of this feature is a prefix: `lacks_tools =
["fact_checker:web_fetch"]`, one key, no new vocabulary. It was tempting
for about an hour.

It fails on the question the feature exists to answer. `lacks_tools =
["bash"]` in that world is ambiguous — does it mean the agent, or
everything in the package? Both readings are defensible, the difference
is invisible in the file, and every existing case in the wild would have
to be re-read to find out which one its author meant. Two objects get two
keys.

### A CHILD NAMED BY AN ASSERTION MUST EXIST

This is the load-bearing rule, and it is a rule about a *green* suite
rather than a red one.

Read `subagent_lacks_tools = { fact_checkr = ["web_fetch"] }` — note the
typo — the way a naive implementation would. There is no child by that
name. A child that does not exist cannot use `web_fetch`. The claim
holds! The case passes, the suite is green, and it checked nothing.

So naming a child that is not there is a failure, in *both* directions —
`has` and `lacks` alike. Renaming a sub-agent turns its assertions red
until somebody updates them, which is exactly the alarm this note exists
to install. A gate that goes quiet when you rename something is a gate
that goes quiet at precisely the moment you were changing the thing it
guards.

The same rule is why a child the package's admission policy turned away
does not count, and neither does one the operator disabled this session.
Both are lines in a file describing a delegation that cannot happen. An
author told "your gate covers that child" would be wrong.

### The key is a name, or `*`, and nothing in between

Tool patterns work in the values — `["web_*"]` is fnmatch, exactly as in
`lacks_tools`, because "nothing that reaches the network" is a shape
rather than a list. The key is not a pattern, and refuses to be one:

```
case 'c'.subagent_lacks_tools takes a sub-agent NAME, not a pattern
(fact_*): a key matching no child would assert nothing. Use "*" for
every declared sub-agent, or name them one at a time
```

A pattern key is the vacuous pass wearing a disguise: `fact_*` after a
rename to `verifier` matches nobody, and silently grades air.

`*` is the one generalisation worth having, because it is the only form
that covers children *added after the case was written*. "No child of
this package may write to disk" is the assertion an author actually
means, and it should hold for the second sub-agent somebody adds next
year without anybody remembering to update a list. It carries the same
must-exist rule: `*` in a package that declares no children is a case
that checked nothing, and says so.

### A child that cannot be built is reported as broken, not graded

If a child declares a tool the session cannot supply — unknown, or
disabled by the operator — its roster is a fiction. Nothing on it will
ever reach a model, because the spawn is going to refuse. Grading the
rest would produce "it lacks `web_fetch`", which is true, useless, and
green.

```
fact_checker cannot be built: it declares note, which this agent does not offer
```

## What it caught on the way in

Two things, both of which were already broken before this note was
written.

**The recording proxy swallowed the children.** Every tool in a suite's
registry is wrapped in a `_RecordingTool` so the runner can see what
executed ([note 10](10-evals.md)). The first version of the new check
looked for `DeclaredSubagent` instances in the registry — which works
perfectly in a unit test against a plain registry, and finds *nothing*
inside a real suite run. The failure mode is the worst one available: it
reports that the package declares no sub-agents, which is indistinguishable
from a package that declares none, and a `lacks_` assertion about a child
sitting right there would have gone green.

Twenty-seven unit tests passed. The receipt did not:

```
  FAIL  the-checker-is-actually-on-the-roster  roster failed · no model call · 0 tok
        no sub-agent called 'fact_checker' is declared (declared: nothing)
```

This is the whole argument for the "run it for real" step. A bug that
only appears in the one code path every real run uses is not going to be
found by a test that builds its own registry.

**A disabled tool crashed a permission check.** `DeclaredSubagent` has a
`read_only` property — [note 40](40-a-package-that-delegates.md)'s nicest
detail, the one that means a fact-checker which can only read files never
makes anybody approve anything. It asked the registry two questions: is
this name known, and is that tool read-only. The first used `names()`,
which lists disabled tools; the second used `get()`, which *raises* for
them. So an operator disabling a tool that a declared child happened to
use turned the next permission check into a `KeyError` — an exception
about configuration, thrown from inside the gate, mid-turn.

The fix is one function, and it exists because writing the third copy of
"what tools would this child actually get?" was the moment it became
obvious there should not have been a first and second:

```python
def resolve_child_tools(registry, tools) -> tuple[list[str], list[str]]:
    """A child's declared tool list, split into (offered, unreachable)."""
```

Four callers now: the registry a spawn builds, the friendly error the
model reads when a spawn cannot proceed, the `read_only` property a
permission gate consults, and the new assertion. **UNREACHABLE IS NOT
ONLY "NO SUCH TOOL"** — a disabled tool is registered, is listed, and is
still unreachable, and that is the answer all four callers wanted.

## The receipt

The package's own gate, pointed at the one case that grades the roster.
No key, no provider, no tokens:

```
$ yantra --agent examples/agents/researcher --eval --case "the-checker-*"
eval researcher 0.1.0 · 1 case(s) · 1 roster-only · none · (no model needed)
no provider resolved: every selected case grades the roster, so this run makes
no request and needs no key

  PASS  the-checker-is-actually-on-the-roster  roster only · no model call · 0 tok

SUBSET GREEN · 1/1 passed · 5 case(s) not run · 0 tokens · 1 case(s) cost nothing
```

Now the one-word diff this note is about — `web_fetch` appended to the
child's `tools` in `agent.toml`, and nothing else touched:

```
  FAIL  the-checker-is-actually-on-the-roster  roster failed · no model call · 0 tok
        on fact_checker's roster and should not be: web_* matches web_fetch

SUBSET RED · 0/1 passed · 5 case(s) not run · 0 tokens · 1 case(s) cost nothing
```

Exit 1, in under a second, with no API key and no model of any kind. The
failure names the child, the pattern, and the tool that matched it — the
three things somebody needs to find the line in the manifest.

And the full suite, against a local model, so the free cases sit beside
the ones that cost something:

```
$ yantra --agent examples/agents/researcher --eval --provider ollama
eval researcher 0.1.0 · 6 case(s) · 2 roster-only · ollama · qwen3.8:latest
budget: $0.50 per turn -- inert here, a local model bills nothing

  PASS  outlines-before-reading  13.3s · 5631 tok · 2 it · outline
  PASS  cites-what-it-read  20.1s · 10532 tok · 3 it · list_dir, read_file
  PASS  cannot-write-even-when-asked  28.3s · 9208 tok · 3 it · outline, read_file
  PASS  has-no-way-to-write  roster only · no model call · 0 tok
  PASS  delegation-works-end-to-end  19.7s · 5653 tok · 2 it · fact_checker, glob, glob, outline, grep, read_file
  PASS  the-checker-is-actually-on-the-roster  roster only · no model call · 0 tok

SUITE GREEN · 6/6 passed · 31024 tokens · 2 case(s) cost nothing
```

Two of six cases cost nothing, and between them they now pin both the
ceiling and the floor.

## The tradeoff

**This grades a declaration, not a child.** The assertion reads
`agent.toml` by way of the built parent, and answers what a child *would*
be offered if somebody delegated. It never spawns one. A bug in the
spawner that handed a child more than its list says would not be caught
here — that is a trajectory question, and `forbidden_tools` on the
delegation case is the assertion that reaches it.

That is the right trade for a check that costs zero tokens and runs
without a key, and it is the same trade [note 35](35-roster-and-pass-rates.md)
made for the parent's roster. A free assertion about configuration and a
paid assertion about behaviour answer different questions, and the
cheapest way to lose both is to pretend either one covers the other.

## What is not here yet

* **No assertion about a child's PROMPT.** Its tool list is now guarded;
  its instructions are a file that can be rewritten freely, and "the
  fact-checker's prompt still tells it to quote a line number" is a claim
  no roster check can make. A grader over the child's `instructions`
  would be the cheap version, and a case that delegates and reads the
  result is the honest one.
* **A child's iteration cap and model are ungraded.** `max_iterations = 12`
  and a `model` slug are both in the same table as the tool list, both
  editable in the same one-line diff, and neither has an assertion. The
  tool list came first because it is the one that changes what a package
  can *reach*.
* **Nothing grades the freeform `spawn_subagent`.** There is nothing to
  grade: the model writes that child's tool list at call time, which is
  the entire reason it is opt-in behind a flag
  ([note 08](08-sub-agents.md)). A trajectory check is the only instrument
  that reaches it.
* **One level deep, one level of assertion.** Children cannot have
  children, so there is no nesting to express — and if that rule ever
  bends, this format bends with it.
