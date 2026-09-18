# 50 · The rest of what a child is

[notes/44](44-a-ceiling-and-a-floor.md) gave a declared sub-agent's tool
list a floor as well as a ceiling: `subagent_has_tools` and
`subagent_lacks_tools`, keyed by the child's name, so that widening a
fact-checker by one word turns a gate red in under a second with no API
key.

It also said what it had not done:

> **No assertion about a child's PROMPT.** Its tool list is now guarded;
> its instructions are a file that can be rewritten freely […]
> **A child's iteration cap and model are ungraded.** `max_iterations = 12`
> and a `model` slug are both in the same table as the tool list, both
> editable in the same one-line diff, and neither has an assertion.

Here is that table, from `examples/agents/researcher/agent.toml`:

```toml
[[subagent]]
name = "fact_checker"
prompt = "subagents/fact_checker.md"
tools = ["read_file", "glob", "grep", "outline"]
max_iterations = 12
# model = "..."
```

Four things a package decided about the child it delegates to. Until now
exactly one of them could be held by a case, and the reason it went
first was never that it mattered most — it was that it changes what the
package can *reach*. The other three change what it *costs*, how long it
may go on, and what it was told to do.

## The three assertions

```toml
subagent_prompt_contains = { fact_checker = ["Quote the evidence", "file and line"] }
subagent_model = { fact_checker = "" }
subagent_iterations_at_most = { fact_checker = 12 }
```

All three are free — lines in a file, graded before any request, in a
case that needs no `user_message` and no key. All three follow note 44's
rules unchanged: the key is a NAME or `*`, never a pattern, and A CHILD
NAMED BY AN ASSERTION MUST EXIST in both directions.

Each one exists for a specific edit.

**The prompt.** The case above this one in the same suite asks the
researcher to delegate a claim and grades the answer with
`check = "graders:cites_a_file"`. That grader only works because the
child's prompt tells it to quote a file and a line. Rewrite the prompt,
drop that instruction, and the delegation case goes red *for a reason
that reads like a model problem* — somebody spends an afternoon on the
model before opening the markdown file. Now:

```
  FAIL  the-checker-is-actually-on-the-roster  roster failed · no model call · 0 tok
        fact_checker's prompt does not mention 'Quote the evidence'
```

That is a real run against the example package with one word changed in
`subagents/fact_checker.md`. No model, no key, under a second.

**The model and the cap.** Both widened at once, both named:

```
  FAIL  the-checker-is-actually-on-the-roster  roster failed · no model call · 0 tok
        fact_checker declares its own model (gemma4:26b); the case says it should run on the parent's
        fact_checker may run 50 iterations, and the case allows at most 12
```

`""` is the assertion that the child names **no model of its own** — it
runs on whatever the operator chose for the parent. That is the claim
worth pinning, because a child quietly pointed at a dearer slug is a
package whose cost changed without its behaviour changing, and the
delegation still works perfectly, which is what makes it hard to notice.

## Prose is a substring, never a pattern

The values in `subagent_has_tools` are fnmatch patterns, because a
roster is a set and "nothing that writes" is a shape. The values in
`subagent_prompt_contains` are **case-insensitive substrings**, and the
difference is deliberate.

A prompt is written for a model to read. A key that accepted
`*quote*line*` would put authors in a regex debugger with an instruction
file open beside it, chasing a metacharacter through prose that was
never written to be matched. Worse, a pattern that matches nothing looks
identical to a claim that holds — the same vacuous green note 44 refused
for keys.

So the assertion says one honest thing: *this phrase appears in that
file.* The failure says it back the same way: `does not mention`.

## A ceiling, not an equality

`subagent_iterations_at_most = { fact_checker = 12 }` passes at 12, at 4,
at 1. It fails at 50.

An equality would have been easier to write and worse to live with. The
dangerous edit to a cap is *upward* — a child that may run 50 rounds is
a child that can burn a turn's budget on its own — and a case that went
red because somebody **lowered** a cap is a case whose author deletes it
the second time it happens. A gate nobody keeps is not a gate.

Worth knowing about the neighbouring ceiling: the manifest already
refuses a cap outside 1–50, package-wide, in `package.py`. That is the
harness saying what is structurally sane. This assertion is the
*package's own* claim about one child, which is a narrower and more
specific thing — 12 because checking is short work, and a checker still
looking after twelve rounds has not found the file and is not about to.

## What is not here yet

* **Nothing grades the child's `description`**, which is the text the
  MODEL reads when deciding whether to delegate at all. It is prose in
  the same table and would take the same substring treatment; it is left
  out because a description that drifts shows up as a delegation case
  failing, which is the honest signal rather than a proxy for it.
* **Nothing grades `output_format`.** Same argument, one step weaker:
  the format is what the child's answer is supposed to look like, and
  the case that reads the answer is a better judge of it than a
  substring is.
* **A prompt assertion cannot say "still roughly this".** A phrase
  either appears or does not. A prompt rewritten completely while
  keeping the three phrases a case names passes, and nothing short of a
  model reading both versions would catch that — which is a grader over
  the child's instructions, and a much bigger idea than a key.
* **The parent's own prompt is still ungraded.** Everything here is
  about a declared child; `prompt.md` at the top of the package is a
  file that can be rewritten just as freely, and nothing holds a phrase
  in it. The same key would work; nobody has wanted it yet, because the
  parent's prompt failing shows up in every trajectory case at once.
