# 86 — Names on a list

[Note 79](79-scrubbed-before-it-is-written.md) taught the recorder to
scrub before it writes. `--trace-redact email` turns every address into
`[redacted]`, and `--trace-redact token` does the same for API keys. It
also takes any regular expression you like, such as `'ACME-\d+'` for
ticket numbers.

Note 79 also said what patterns cannot do:

> **Names, addresses and other free text.** A regular expression cannot
> find "the customer's name". That needs a model or a list, and either
> one is a different kind of feature.

An email address has a shape: letters, an `@`, a dot. A name does not.
"Ana Lima" looks like any other two words, and no pattern can tell it
apart from "Release owner". So a support agent recorded at `--trace-full`
kept every customer name it read, and the redaction flag could not help.

## The operator already has the list

The people who run an agent like that usually know exactly whose names
might turn up. They have a customer table, a staff directory, a list of
patients. So you can now give the recorder that list:

```
$ cat staff.txt
# staff directory export
Ana Lima
Bo Chen
Acme Inc.

$ yantra --trace run.jsonl --trace-full --trace-redact-words staff.txt
```

One name or phrase per line. Lines starting with `#` and blank lines are
skipped, so the file can say where it came from. The flag can be given
more than once, and it works alongside `--trace-redact`.

**A LIST IS THE OPERATOR'S KNOWLEDGE; A MODEL IS A GUESS.** The other
road note 79 named was a model: ask one to find the names. A model would
catch names nobody listed, and that is a real advantage. But it fails in
a way nobody can predict: on one line in a thousand, silently, and
maybe the line that mattered. A list fails in a way you *can* predict:
a name not on the list goes through. You can check a list. You cannot
check a guess until after the file has been shared. So the list comes
first. A model may follow, if it can show on real data that it misses
almost nothing, and if it scrubs *everything* when it breaks, never
nothing.

## How a name is matched

**WHOLE WORDS, ANY CASE, LONGEST FIRST.** Each entry is matched as a
whole word or phrase, ignoring case. `Bo` on the list does not touch
"Bob", and `Ana` does not touch "Banana". When two entries could match
at the same spot, the longer one wins. With both `Ana` and `Ana Lima` on
the list, "Ana Lima" becomes one `[redacted]`. Otherwise it would come
out as `[redacted] [redacted]`: two words, which is half the name given
back.

**A SPACE MATCHES ANY SPACING.** A name broken across two lines of a
file, or typed with two spaces, is still one match.

**ENTRIES ARE LITERAL.** `A.B` means those three characters, not "A,
anything, B". Nobody writing a customer list should have to know which
characters are special in a regular expression.

**ENDS THAT ARE NOT LETTERS WORK TOO.** "Acme Inc." ends in a full stop.
The usual regex word boundary (`\b`) needs a letter on one side of it,
so it would never match that entry. The check here asks the simpler
question instead: is there a letter or digit *just outside* the entry?

## What gets refused

**AN EMPTY LIST.** A file that holds only comments, or nothing at all,
is an error before anything runs:

```
error: --trace-redact-words empty.txt has no entries (blank lines and
# comments do not count), so it would scrub nothing
```

Whoever passed that file believes they are covered. The one thing the
flag must never do is let them go on believing it.

**A ONE-LETTER ENTRY.** "a" as a whole word appears in every other
sentence. The error names the file and the line number.

**AN ENTRY OVER 200 CHARACTERS.** That is a paragraph pasted into the
wrong file, not a name.

## Only the count is shown

The list is the most private file in the room, so nothing that is
displayed is built from it. The web page's `rec` chip says *1 redaction
pattern(s) and 3 listed word(s) scrubbed*. The server banner and
`--eval` say `full, redacting 1 pattern(s) and 3 word(s)`. The names
themselves never appear on screen, and a recorded line only carries the
`redacted` count it always carried.

## Twenty thousand names

A real customer list can have thousands of entries. The obvious way to
match them is one long regular expression, "this name, or that one, or
that one...", but that tries every name at every position in the text.
With twenty thousand names it took half a second to scan 18 KB, and a
full recording scans every tool result of every turn.

So the list is compiled into a **prefix tree** instead. "Ana", "Ana
Lima" and "Anand" share their first three letters, so those letters are
checked once, not three times. The same 18 KB scans in about two
milliseconds. A test checks that twenty thousand names record a turn in
well under half a second. The one cost is paid once, at startup:
building the tree for twenty thousand names takes about a second.

## Both roads

Scrubbing happens on your machine, after the model has answered, and
does not depend on which model it was. A cloud model and a local one
see the same unscrubbed text during the turn. The list only decides
what reaches the file. If the names must not reach a cloud model at
all, that is a reason to run the local road, not something a recording
flag can promise.

## What was deliberately not built

**Scrubbing the answer on screen.** The answer you are reading is
untouched, as in note 79. The flag decides what reaches the file.

**A package key.** Whose names to hide depends on who runs the agent,
on whose data. That makes it the operator's flag, not the package
author's. The same rule `--trace-redact` follows.

**Fuzzy matching.** "Ana Lim" or "A. Lima" get through. A matcher that
guessed at near-misses would start scrubbing ordinary words that happen
to be close to a name, and people switch off a scrubber that mangles
their evidence. Put the variants you know about on the list.

**Scripts written without spaces.** In Chinese or Japanese running
text, a name has letters on both sides, so it never counts as a whole
word and is not matched. For those, pass the name to `--trace-redact`
as a pattern.

## What is not here yet

* ~~**A model as a second reader.**~~ Measured and built in
  [note 89](89-names-nobody-listed.md): `--trace-redact-reader
  qwen3.8:latest`. On a labelled set of 1,493 names it missed 5, all the
  same way (a bare first name after the full one), and none once each
  word of a returned name is scrubbed too. The set is synthetic, so the
  trial ships with the flag. Was: designed above, not built: local only,
  adding to the list and never removing from it, and falling back to
  writing no contents at all when the model fails. It needed a
  measurement first.

## Receipt

A handover file with three names in it, one of them in lower case, read
by `qwen3.8:latest` in the terminal:

```
$ cat handover.txt
Release owner: Ana Lima (ana@example.com)
Backup: bo chen, on call weekends
Vendor contact: Acme Inc. support desk

$ yantra --provider ollama --model qwen3.8:latest --trace run.jsonl \
    --trace-full --trace-redact email --trace-redact-words staff.txt \
    --prompt "Read handover.txt. Who owns the release, who is the backup, \
and who is the vendor? Quote the names exactly."
From handover.txt:
- **Release owner:** "Ana Lima (ana@example.com)"
- **Backup:** "bo chen" (on call weekends)
- **Vendor:** "Acme Inc. support desk"
── end_turn · 2853 in / 59 out · 2 iteration(s)
```

The answer on screen is untouched. What went to disk:

```json
"steps": [{
    "name": "read_file",
    "arguments": {"path": "handover.txt"},
    "result": "     1\tRelease owner: [redacted] ([redacted])\n     2\tBackup: [redacted], on call weekends\n     3\tVendor contact: [redacted] support desk"
}],
"answer": "From handover.txt:\n\n- **Release owner:** \"[redacted] ([redacted])\"\n- **Backup:** \"[redacted]\" (on call weekends)\n- **Vendor:** \"[redacted] support desk\"",
"redacted": 8
```

Eight replacements. Each of the three names, and the email address,
appears twice: once in what the tool read and once in the answer. "bo
chen" was on the list as "Bo Chen" and was caught anyway. "support desk"
was not on the list, and it stayed.

The same server started with `--web` reports what it is scrubbing,
without naming anyone:

```
{'path': 'run3.jsonl', 'detail': 'full', 'redacting': 1, 'redacting_words': 3}
```

`1991 passed, 1 skipped` (was 1970).
