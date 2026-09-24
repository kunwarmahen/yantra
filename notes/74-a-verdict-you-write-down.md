# 74 — A verdict you write down

Since [note 57](57-a-turn-written-down.md), one rule has held: **Yantra
does not decide whether a recorded turn was a failure.** A turn can end
cleanly, with every tool working, and still give a wrong answer, and
only a person reading it can tell. [Note 70](70-a-list-to-choose-from.md)
kept that rule and added `--turns`, a list with the signals Yantra *can*
see: the turn ended badly, a tool failed, or a suite's own grader said
no.

What was missing was the other half. When a person *did* decide, the
decision had nowhere to go. They read a turn, saw it was wrong, and
either turned it into a case immediately or forgot about it. The next
time anyone listed that recording, the turn looked exactly as it did
before somebody had judged it.

## `--mark`

```
yantra --mark 3f9c21ab bad --why "cited a file it never opened" --trace runs/today.jsonl
yantra --mark 7aa97a4c good --trace runs/today.jsonl
```

This writes the verdict into that turn's line: `"passed"`,
`"judged_by": "person"`, and the reason if one was given. Yantra still
decides nothing. It keeps what the person decided.

**A PERSON'S MARK OUTRANKS EVERYTHING ELSE.** `--turns` flags a turn a
person marked bad, with their words. A turn a person marked **good** is
not flagged, even if a tool failed inside it or its case's grader said
no. The person read the answer. The cheap filter and the grader only
checked its shape. The listing shows it with a `✓`.

**THE REASON BECOMES THE CASE'S DESCRIPTION.** `--fossil` on a marked
turn starts the case's `description` with "marked bad: …", so the
regression case says why it exists in the words of the person who
decided it should.

**THE LINE IS REWRITTEN, NOT APPENDED TO.** Appending a second "verdict"
line would be simpler. But an older version of Yantra reading the file
would not understand that line, would count it as unreadable, and would
say so, which reads like corruption. So the turn's own line is rewritten
in place, using the same safe swap as `--trace-prune`
([notes/67](67-the-agent-or-the-vendor.md)): the new file is written
beside the old one, anything appended in the meantime is copied across,
and one rename puts it in place.

A suite's turns now say who judged them as well: `"judged_by":
"grader"` beside the `passed` that note 70 added.

## Both roads

Nothing here touches a model.

## What was deliberately not built

**No verdict without a person.** There is still no rule, heuristic or
model deciding what failed. `--mark` needs an id and a word typed by
someone.

**No `--mark` from the browser.** The web UI records turns
([notes/63](63-the-whole-turn-written-down.md)) but had no button to
judge one. Since [note 77](77-a-mark-taken-back.md) it does: a good/bad
row under each recorded turn, writing the same line `--mark` writes.

## What is not here yet

* ~~**A mark cannot be taken back**~~ Shipped in
  [note 77](77-a-mark-taken-back.md): `--mark ID clear` removes it, and
  a suite's turn gets its grader's verdict back. Was: except by marking
  again. There is no "unmark"; marking good or bad overwrites the
  previous mark.

## Receipt

The recording from [note 70](70-a-list-to-choose-from.md)'s receipt, on
`qwen3.8:latest`. The two marks below are **demonstrations of the
command**, not judgements of those answers (a SHAPE recording does not
keep the answer to read).

```
$ yantra --mark af32c7da bad --why "demonstration mark for this note" --trace runs/marked.jsonl
af32c7da marked bad: demonstration mark for this note (Which tools is this agent allowed to use? Answer from the pa)
$ yantra --mark f8d557f7 good --trace runs/marked.jsonl
f8d557f7 marked good (Which file in sources/ mentions 'token bucket'? Answer with )

$ yantra --turns --trace runs/marked.jsonl
...
✓ f8d557f7  2026-09-24T15:04:55Z   2 tool(s)  Which file in sources/ mentions 'token bucket'? Answer wi... case outlines-a-one-word-lookup
✗ 28a0f12c  2026-09-24T15:05:03Z   2 tool(s)  Which file in sources/ mentions 'token bucket'? Answer wi... case outlines-a-one-word-lookup
             red in its case -- the grader said no

10 turn(s), 2 flagged -- by the cheap filter, a case's own grader, or a person's --mark; an unflagged turn can still be wrong. ...

$ yantra --fossil af32c7da --trace runs/marked.jsonl
[[case]]
id = "trace-af32c7da"
description = "marked bad: demonstration mark for this note (recorded 2026-09-24T15:02:07Z on ollama/qwen3.8:latest; ended end_turn)"
```

`f8d557f7` was red in its case and had a failed `read_file`, and was
flagged by both in note 70. Marked good, it is no longer flagged.

`1803 passed, 1 skipped` (was 1796).
