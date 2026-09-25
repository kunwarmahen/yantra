# 90 — What it said

[Note 81](81-the-turns-the-page-never-saw.md) opened the recording in the
browser. Click the `rec` chip and every recorded turn is listed, newest
first, each with **good** and **bad** buttons. A row showed the task,
the tools, and any reason it was flagged.

It did not show the answer. You were being asked *was this turn right?*
while looking at the question and not the reply. For a turn you watched
a minute ago, you might remember. For a turn from yesterday, you would
be marking a guess about the task, not a judgement of what the agent
said.

For many turns the answer was already sitting in the file. A recording
made with `--trace-full` keeps what the model finally said
([note 57](57-a-turn-written-down.md)). The page just never read it.

## The answer sits under the task

```
recorded turns
2 turn(s) in turns.jsonl, 0 flagged — an unflagged turn can still be
wrong. Answers are kept only by --trace-full

247bb85b  2026-09-25T20:39:34Z  2 tool(s)
  How many lines are in notes.txt?
  was this turn right?  [good] [bad]

98de40de  2026-09-25T20:38:43Z  1 tool(s)
  What does notes.txt say?
  │ notes.txt contains a two-item to-do list:
  │
  │ 1. buy milk
  │ 2. call the plumber
  was this turn right?  [good] [bad]
```

A long answer starts folded to four lines, and a click unfolds it. The
panel is for scanning many turns, and one long reply should not push
every other row off the screen.

## What the page shows is what the file holds

**THE PAGE SHOWS THE ANSWER AS IT WAS WRITTEN.** It is not fetched
again or re-rendered from the conversation. The line in the file is
what `--fossil` would turn into a test case and what `--mark` writes a
verdict into, so it is the thing being judged. It was clipped to 2,000
characters when it was recorded, and scrubbed by `--trace-redact`
([note 79](79-scrubbed-before-it-is-written.md)) before that, so the
page cannot show anything the file does not already hold.

A line whose contents were withheld, because the local name reader
failed on it ([note 89](89-names-nobody-listed.md)), shows why instead
of the `[withheld]` placeholder. `--turns` in the terminal already said
this, and the page now says it too.

A turn that stopped without an answer, such as one held for an approval
([note 88](88-not-yet.md)), says how it stopped.

## Turns recorded without the answer

The default recording keeps the shape of a turn — which tools, how
many, how it ended — and not what anybody said. That is deliberate
(note 57): a shape-only file is one you can keep for a year without
worrying about what is in it.

Those rows show no answer, and the page does not pretend otherwise. The
note at the top of the panel says *Answers are kept only by
--trace-full* whenever any row lacks one. It says so once, not on every
row. In a file of a hundred shape-only turns, a hundred copies of "not
kept" would bury the rows.

## Both roads

The panel reads a file, so which model wrote it makes no difference.
The receipt is `qwen3.8:latest`.

## What was deliberately not built

**No answers for shape-only turns.** A recording that chose not to keep
answers keeps none. Going back to the saved session to find the reply
would pull in words the person chose not to record, and would get it
wrong once the conversation has been compacted.

**No sub-agent answers.** A child's task and answer are kept at
`--trace-full` too. The panel shows the turn's own answer, which is what
a person marks. `--fossil` shows the children.

**No rendering of Markdown.** The answer is shown as plain text with its
line breaks kept. The panel is for checking what was said, and plain
text shows exactly that.

## Receipt

Two turns on `qwen3.8:latest`, into one file. The first was recorded
with `--trace-full`. The server was then restarted without it, and the
second was recorded as shape only. The recording as the page reads it:

```
$ curl -s localhost:8391/api/turns
  "task": "What does notes.txt say?",
  "answer": "notes.txt contains a two-item to-do list:\n\n1. buy milk\n2. call the plumber",
  "withheld": null,
  "outcome": "end_turn"
```

A new page in headless Chrome, which had seen neither turn. The `rec`
chip was clicked and the rows were read back:

```
note: 2 turn(s) in turns.jsonl, 0 flagged — an unflagged turn can still be wrong. Answers are kept only by --trace-full
row 1: turn-row-head :: 247bb85b 2026-09-25T20:39:34Z 2 tool(s)
       turn-row-task :: How many lines are in notes.txt?
       turn-mark :: was this turn right? good bad
row 2: turn-row-head :: 98de40de 2026-09-25T20:38:43Z 1 tool(s)
       turn-row-task :: What does notes.txt say?
       turn-row-answer is-folded :: notes.txt contains a two-item to-do list: 1. buy milk 2. call the plumber
       turn-mark :: was this turn right? good bad
```

The shape-only turn has no answer row. The full one does, folded.

`2100 passed, 1 skipped` (was 2098).
