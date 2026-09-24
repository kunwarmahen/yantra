# 77 — A mark taken back, and a button for it

[Note 74](74-a-verdict-you-write-down.md) gave a person somewhere to
write their verdict on a recorded turn: `--mark ID good|bad`. It left
two gaps, and each one made a mark harder to trust.

**A mark could not be taken back.** You could change a mark to the
other verdict, but you could not remove it. A mark made on the wrong id,
or one made before somebody read the turn properly, stayed in the file
as a verdict. Worse, a mark on a suite's turn **replaced** the grader's
verdict, and the grader's verdict was gone. After a mistaken `good` on a
turn the grader had failed, there was no way to get the grader's "no"
back except by running the case again.

**The browser had no way to mark.** The web UI records its turns
([notes/63](63-the-whole-turn-written-down.md)), and you read the answer
in the page, but to judge it you had to go to a terminal, run
`--turns`, find the id and type it. You have just read the answer and
know what you think of it, but the page offered nowhere to record that.

## `--mark ID clear`

```
yantra --mark 614fd76f clear --trace runs/today.jsonl
```

This removes the person's verdict and their reason. What is left
depends on the turn:

* **A turn somebody typed** has no verdict again. It reads exactly as it
  did before it was marked.
* **A turn a suite recorded** gets its grader's verdict back.

**REPLACED, NOT LOST.** When a mark replaces a grader's verdict, the
grader's verdict is now kept in the line under `graded`, so clearing
the mark can put it back. A second mark keeps the first mark's `graded`,
so however many times the turn is re-marked, clearing it gives back
what the suite said. A line written before note 74 with a `passed` and
no `judged_by` got that verdict from a suite, so it counts as the
grader's.

The command says what is left, so you do not have to list the file to
find out:

```
614fd76f mark cleared, back to its grader's pass (Which section of ...)
f0325287 mark cleared, unjudged (Which tools is this agent allowed ...)
```

`--why` with `clear` is an error. A reason explains a verdict, and
`clear` removes one.

## A button in the page

With `--web --trace FILE`, each turn now ends with a small row under
the answer:

```
was this turn right?   (✓ good)  (✗ bad)                       f0325287
```

**Good** writes the mark. **Bad** first asks *what was wrong?*. The
answer is optional, and it becomes the case's description if the turn
is ever turned into a regression case with `--fossil`. Once marked, the
row says so, with your reason, and offers **take it back**, which is
`clear`. The id on the right is the one `--turns` and `--fossil` use.

**THE BUTTON WRITES THE TERMINAL'S LINE.** The page calls the same
`TrajectoryLog.mark` that `--mark` calls. A second copy of the rules,
written in JavaScript, would slowly drift from the first. With one, a
mark from the page and a mark from the terminal cannot differ, and the
tests check that.

**OFFERED ONLY FOR A TURN THE FILE ALREADY HOLDS.** The page learns a
turn's id from a new `recorded` message, sent after the line is on
disk and before the turn is finished. So the row never appears for a
turn that has not been written, or one that was not recorded at all.
Without `--trace` there is no row, and `/api/mark` says the server is
not recording.

**NOT DURING A TURN.** A mark rewrites the file in place
([notes/67](67-the-agent-or-the-vendor.md)), and the recorder appends
to it when a turn ends. Like the page's other controls that change
things, `/api/mark` refuses while a turn is running rather than
rewriting the file while something is being added to it.

## Both roads

Nothing here touches a model. The receipts below are turns from
`qwen3.8:latest`; a cloud turn is marked the same way.

## What was deliberately not built

**No mark buttons on a reloaded transcript.** When a page reloads, it
rebuilds the conversation from the agent's history, and that history
does not know which trace line each turn became. Guessing by position
would work until the first time it did not, and then it would silently
write a person's verdict against a different turn. A turn from before
the reload can still be marked with `--mark` in the terminal.

**No confirmation dialog for a mark.** A mark can now be taken back, so
asking "are you sure?" first would be friction with nothing to protect.

## What is not here yet

* **The page cannot show old marks.** Only turns recorded while the page
  was open get the row. Showing `--turns` inside the page would need a
  panel of its own.

## Receipt

A turn in the browser, against `examples/agents/researcher` on
`qwen3.8:latest`, sent to the running server with a small websocket
client. The `recorded` message arrives before `turn_done`:

```
{'type': 'tool_result', 'name': 'outline'}
{'type': 'tool_result', 'name': 'read_file'}
{'type': 'turn_end', 'reason': 'end_turn', 'text': 'According to prompt.md, this agent is a read-only research assistant whose only named tool is `outline` (line 19), along with the `read_file` tool ...'}
{'type': 'recorded', 'id': 'f0325287f898497f9381e82910ddc78c'}
{'type': 'turn_done'}
```

The same three requests the buttons send:

```
POST /api/mark {"id":"f0325287","verdict":"bad","why":"called outline the only tool, then listed two more"}
{"id":"f0325287f898497f9381e82910ddc78c","passed":false,"judged_by":"person","why":"called outline the only tool, then listed two more"}
POST /api/mark {"id":"f0325287","verdict":"clear"}
{"id":"f0325287f898497f9381e82910ddc78c","passed":null,"judged_by":null,"why":null}
POST /api/mark {"id":"f0325287","verdict":"good"}
{"id":"f0325287f898497f9381e82910ddc78c","passed":true,"judged_by":"person","why":null}

$ yantra --turns --trace runs/web.jsonl
✓ f0325287  2026-09-24T21:02:45Z   2 tool(s)  Which tools is this agent allowed to use? ...
$ yantra --mark f0325287 clear --trace runs/web.jsonl
f0325287 mark cleared, unjudged (Which tools is this agent allowed to use? Answer from prompt)
```

A suite's turn, where the grader's verdict comes back. The mark below
**demonstrates the command**; it is not a judgement of the answer:

```
$ yantra --eval --case outlines-before-reading --repeat 2 --trace runs/suite.jsonl
  PASS  outlines-before-reading  ✓✓ 2/2 runs · 0.34-1.00 at 95% · 13.8s · 14487 tok
$ yantra --mark 614fd76f bad --why "demonstration mark for this note" --trace runs/suite.jsonl
614fd76f marked bad: demonstration mark for this note (Which section of sources/rate-limiting.md covers what a clie)
$ yantra --turns failed --trace runs/suite.jsonl
✗ 614fd76f  2026-09-24T21:03:28Z   1 tool(s)  Which section of sources/rate-limiting.md covers what a c... case outlines-before-reading
             marked bad by a person: demonstration mark for this note
$ yantra --mark 614fd76f clear --trace runs/suite.jsonl
614fd76f mark cleared, back to its grader's pass (Which section of sources/rate-limiting.md covers what a clie)
```

and the line afterwards: `passed: true, judged_by: "grader"`, with no
`why` and no `graded` left behind.

`1840 passed, 1 skipped` (was 1826).
