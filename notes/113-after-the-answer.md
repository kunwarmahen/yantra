# 113 — After the answer

When a turn on the page took real work, two things happen after the
answer. Yantra looks at whether the way it found is worth keeping as a
skill ([note 96](96-solve-it-once.md)), and it may find facts about you
worth remembering ([note 101](101-looking-back.md)). Both used to
happen *before* the page handed the input back. The answer was on
screen, but you couldn't type until the look was done, and then two
dialogs came up, one after the other, asking about it.

On a cloud model that look takes a few seconds. On a local one it's a
whole extra model call, and it can take longer than the answer did.
In the receipt below, `qwen3.8:latest` answered in 36.6 seconds and
then spent another 28.6 writing and testing a recipe. That's half a
minute of a locked input box, for a question you might not care about
right now.

So the look now waits its turn. The answer comes back, the input is
free, and the look runs behind it. Whatever it finds waits in one
place, a **to keep** chip in the header with a count, until you choose
to look.

## The input comes back first

The turn ends the way it always did: counters for any learned skill it
followed are updated (no model call, so it's instant), and the page
gets `turn_done`. Only then does the look start, on its own thread.

While it runs, the chip says *looking…* and pulses. When it's done,
one of three things has happened:

* it found nothing worth keeping, and the chip goes quiet again. The
  reason is kept as the panel's *last look* line, not printed in the
  conversation, where it would land in the middle of whatever you
  asked next;
* it found a recipe, tested it, and put it in the tray, with a short
  toast: *found something worth keeping — it waits under "to keep"*;
* under `--learn auto`, it saved the recipe, and says so.

Facts about you that the write-up found go to the same tray. So do the
ones the end-of-conversation look back finds when you clear or restore
a conversation, and the ones found just before compaction. Nothing on
the page asks about them in a dialog any more.

## A new message stops the look

If you send something while the look is still going, it stops. The
person in front of the page matters more than a write-up about the
last turn.

Stopping is real, not just ignoring the answer. The write-up is
streamed, and the stream is closed as soon as you send. A local model
like Ollama's stops generating when the connection closes, so your new
message doesn't queue behind a write-up nobody will read. Anything the
look had staged is removed.

The tradeoff: in a fast back-and-forth, a turn followed straight away
by another message never gets looked at. *save last turn* (`/learn`)
is still there for when you know you want one kept.

## Nothing is asked from behind

The draft's test runs through the session's own `bash` tool and
permission gate ([note 96](96-solve-it-once.md)). In `ask` mode that
gate would put an approval dialog on screen, which is exactly the
pop-up this change exists to remove, and it would appear at a moment
you didn't choose.

So the look never asks. If the test would need your yes, the page's
gate refuses it without asking, and the offer goes to the tray marked
*its test waits for your yes*. It can't be saved yet. **run the test**
runs it as a short turn of its own, the approval comes up then (when
you clicked for it), and a passing test makes it saveable. A failing
one drops it, with the output.

That only applies to a look in the background. In the open, an
unattended refusal still means *not offered*, so `--learn auto` can
never save a script whose test didn't run.

## The tray

The chip shows only when something is waiting or a look is running.
Its panel has three lists:

* **ways that worked**: each recipe with its name, whether its test
  passed, and what it's for. *review* opens the same save dialog as
  before, with both files editable, the scope choice, the test output
  and the token cost. Its buttons are *later* (close it and leave it
  waiting), *drop*, and *save skill*. A save that breaks a rule comes
  back as the same dialog, with the error on top and your edit kept.
* **about you**: the facts, each unticked. *keep ticked* keeps the
  ticked ones and drops the rest, and *drop all* drops everything.
  These follow the same rules as before: a dropped fact isn't offered
  again this session.
* **site guides**: for a site you added by its address, where things
  are on it, as a turn that used it found them, in a box you can edit.
  *keep* has Setu save it as the site's guide, and the agent reads it
  before using the site from the next turn on. *drop* writes nothing
  ([note 112](112-a-site-with-no-api.md)).

Offers stay until you answer them. A recipe found three turns ago can
still be saved. If a newer look drafts a recipe with the same name,
the newer one replaces it, because the two share a staging folder.

## The terminal

The terminal still asks right after the turn, as before. It has no
place for a question to wait. Text printed while you type would end
up in the middle of your next prompt. The REPL asks once, and Enter
means no.

## The tray, on disk

Waiting offers used to live only in the server's memory, so restarting
`--web` lost them all, while a recipe's staged files sat in
`.yantra/learning/` with nothing pointing at them. Now the tray is
written to a file on every change and read back when the page's session
starts.

The file sits in Yantra's state directory, beside memory
(`~/.local/state/yantra/tray/`), not in the project folder, because
facts about you wait there too. Each project folder has its own tray, as
it has its own staged drafts. An offer can't come back if it can no
longer be saved: if its staged folder is gone, or the skill it would
update is gone, it's dropped, and the panel's *last look* line says so.
A recipe's Setu needs are checked again, since what's connected may
have changed. A file that can't be read is renamed `.bad`, and the tray
starts empty.

The offers were about turns that no longer exist, which is why this was
left out at first. But an offer stands on its own: the recipe, its
test, its output. Nothing in it points back at the turn.

## A turn cut in two

On a small context window, compaction can run in the middle of a turn.
`qwen3.8:latest` reports 8192 tokens, so a long solve fills it before
the answer. Compaction folds the older messages, your question and the
first steps among them, into one summary. Read afterwards, the turn
seemed to start at that summary: a five-call solve looked like three,
with the summary as its task. The look said "fewer than 4 calls,
nothing to discover", so the long turns most worth keeping were exactly
the ones never looked at.

The summary is a user message with text, which is why it was taken for
your question. Now it carries a fixed marker and is never read as one.
That alone isn't enough, though, because the first steps are gone by
the time the look runs. So just before compaction, Yantra keeps the
turn so far, and the look joins it to whatever came after (by call id,
so no step counts twice). Two compactions in one turn join the same
way. When you type your next message, the kept half is done with.

The steps kept from before compaction are the full ones. Compaction
also shortens old tool results to stubs, and the write-up is better
from what the commands actually printed.

Checked on `qwen3.8:latest` with the window cut to 3,000 tokens
(`OLLAMA_CONTEXT_WINDOW=3000`), on the CSV task from the receipt below.
Compaction ran mid-turn: 8 tool results were cut to stubs, and a summary
sat right after the question. The turn took 260 seconds and 8 tool calls,
and 88 seconds later the tray held `csv-to-json`, tested and passing
(`Wrote 40 records to people.json` / `records: 40`). Before this change,
that summary was the turn's "question" and only the steps after it
were counted.

## Receipt

`qwen3.8:latest` through Ollama, `yantra --web --yolo --learn ask
--memory off`, a scratch working folder and home, and a script driving
the page's own HTTP and websocket. The times are seconds since the
message was sent.

**A recipe, found behind the answer:**

```
   0.1s turn_started
   3.2s tool: bash          (5 bash calls and a write_file)
  36.6s turn_done
  36.6s INPUT FREE
  36.6s look: looking at what worked, to see if it is worth keeping
  65.1s look: testing scripts/csv2json.py (1 of 2)
  65.2s kept: {'recipes': ['csv-to-json'], 'looking': False,
               'last': 'found csv-to-json'}
```

The test passed in one run (`Wrote 5 records to people.json` /
`count: 5`). The write-up and test cost 2,025 tokens in and 1,819 out.
Before this change, the input would have been busy until 65.2s.

**A message sent while the look ran:**

```
  60.9s turn_done
  60.9s INPUT FREE
  60.9s look: looking at what worked, to see if it is worth keeping
  60.9s send while looking: thanks — one line: which city has the most people?
  60.9s accepted: 200
  60.9s status: stopping the look at what worked
  63.5s kept: {'recipes': [], 'last': 'stopped: a new message came first'}
  82.9s turn_done
```

The look gave way in 2.6 seconds, which is how long the model took to
produce its first token of the write-up. Nothing was left staged.

**A turn not worth keeping:** input free at 36.8s, and the look
finished at 48.7s with *not worth keeping: This is a one-off query
about a specific file's contents*. That reason went to the panel's
*last look* line and nowhere else.

## What was deliberately not built

* ~~**A tray that outlives the server.**~~ Built: see *The tray, on
  disk* above.
* **Waiting in the terminal.** See above. A terminal tray would be a
  `/kept` command, and it can be added the day someone wants one.
* **Looking at every turn in a fast exchange.** A look stopped by your
  next message isn't retried later. Retrying would mean deciding which
  past turn to look at once the history has moved on.
