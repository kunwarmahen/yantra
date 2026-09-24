# 81 — The turns the page never saw

[Note 77](77-a-mark-taken-back.md) put **good** and **bad** buttons under
each recorded turn in the browser, so you could judge an answer in the
place you read it. The buttons appear when a turn is recorded, and only
then. Reload the page and they are gone. Open a second tab and it never
had them. Come back tomorrow to a server that has been recording all
week, and the page has nothing to mark.

The turns themselves are all still in the file. The terminal can list
them with `--turns`. To mark one from the browser, though, you had to
leave it, run `--turns` in a terminal, find the right id, and type
`--mark`. Note 77 put that in its "not here yet" list: *showing `--turns`
inside the page would need a panel of its own.*

## The `rec` chip opens it

While `--trace` is recording, the header already shows a `rec` chip.
Clicking it now opens **recorded turns**: the recording, newest first,
one row per turn, each with the same buttons as the row under a live
turn.

```
recorded turns
2 turn(s) in …/turns.jsonl, 1 flagged — an unflagged turn can still be wrong

☐ flagged only

✗ a69ee9d9  2026-09-24T23:41:03Z  1 tool(s)
  Read missing.txt and tell me what it says.
  read_file (failed)
  was this turn right?  [good] [bad]

✓ cb79b4b8  2026-09-24T23:41:01Z  1 tool(s)
  What does notes.txt say?
  you marked this good  [take it back]
```

A row shows the same things as a `--turns` line: the id `--fossil` takes,
when, how many tools ran, the task, the case for a suite's turn, and
every reason a flagged turn is flagged. **Flagged only** does what
`--turns failed` does.

## One rule, in one place

**THE PAGE AND THE TERMINAL FLAG THE SAME TURNS.** Deciding that a turn
is flagged is not simple. A tool failed, or a sub-agent did, or the turn
ended badly, or a suite's grader said no, and a person's mark outranks
all of those ([note 74](74-a-verdict-you-write-down.md)). That rule used
to live inside the command-line code. It now lives in `trace.py` as
`flagged()` and `why_flagged()`, and `--turns` and the page both call it.
A second copy written in JavaScript would drift the first time either
one changed, and a person would see a turn flagged in one place and
clean in the other.

For the same reason, the page does not work out a new flag after you
mark a row. It asks the server for the list again. The server knows the
rule, and the page does not need to.

**THE BUTTONS ARE THE SAME BUTTONS.** A row in the panel and the row
under a live turn share one piece of code, and both send the same request
note 77 built. That request calls the same `TrajectoryLog.mark` as
`--mark`. A mark made in the panel is indistinguishable from one made in
the terminal. It is also still refused while a turn is running, for
note 77's reason: rewriting the file while the recorder appends to it
could lose a line.

## What it reads

`GET /api/turns` reads the file `--trace` names, the same file `--turns`
reads. Reading is allowed mid-turn, because the recorder writes each line
whole and a reader cannot catch half of one. A file that does not exist
yet, before the first turn, is an empty list rather than an error.

The list is **newest first and capped at 100**. The turn someone wants to
judge is nearly always a recent one, and a recording kept for a year
should not become a page thousands of rows long. The heading says how
many there are in total, so nobody mistakes the newest hundred for all
of them.

An open panel refreshes itself when a new turn is recorded.

## Both roads

The panel reads a file. Which model wrote it makes no difference. The
receipt below is from `qwen3.8:latest`.

## What was deliberately not built

**No `--fossil` button.** Turning a turn into a regression case produces
TOML that belongs in a package's `evals/cases.toml`, and that is a
decision about someone's repository. The panel shows the id, and
`--fossil ID` is one command away.

**No paging past the newest hundred.** `--turns` in a terminal lists all
of them, and `--turns failed` narrows them. A second way to page through
a year of turns would add a lot of page for a rare need.

**No search.** Same reason, and `grep` on a JSONL file already exists.

## What is not here yet

* **A turn shows its task, not its answer.** A recording made at shape
  level (the default) does not keep the answer
  ([note 57](57-a-turn-written-down.md)), so the panel cannot show you
  what you are judging for a turn you did not watch. With `--trace-full`
  the answer is in the file, and the panel could show it. It does not
  yet.

## Receipt

Two turns recorded by the browser on `qwen3.8:latest` with
`--trace turns.jsonl`. One read `notes.txt`; the other asked for a file
that does not exist. A new page was then opened in headless Chrome. It
had never seen either turn. The `rec` chip was clicked, and the rows
were read back:

```
rows: ['✗ | a69ee9d9 | 2026-09-24T23:41:03Z | 1 tool(s) | Read missing.txt and tell me what it says. | read_file (failed) | was this turn right? | good | bad',
       'cb79b4b8 | 2026-09-24T23:41:01Z | 1 tool(s) | What does notes.txt say? | was this turn right? | good | bad']
note: 2 turn(s) in …/live3/turns.jsonl, 1 flagged — an unflagged turn can still be wrong
```

**good** clicked on the second row, in the panel:

```
after mark: ['turn-row is-flagged :: ✗ | a69ee9d9 | … | read_file (failed) | was this turn right? | good | bad',
             'turn-row is-good :: ✓ | cb79b4b8 | … | What does notes.txt say? | you marked this good | take it back']
flagged only: ['✗']
```

and the terminal, reading the same file afterwards:

```
$ yantra --turns --trace turns.jsonl
✓ cb79b4b8  2026-09-24T23:41:01Z   1 tool(s)  What does notes.txt say?
✗ a69ee9d9  2026-09-24T23:41:03Z   1 tool(s)  Read missing.txt and tell me what it says.
             read_file (failed)
```

`1900 passed, 1 skipped` (was 1894).
