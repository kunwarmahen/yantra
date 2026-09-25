# 89 — Names nobody listed

[Note 86](86-names-on-a-list.md) gave the recorder a list of names to
scrub: `--trace-redact-words staff.txt`. A name on the list becomes
`[redacted]` before a line is written. A name that is not on the list
goes through, and that is the gap. A support agent recorded at
`--trace-full` reads a new customer's ticket, and the new customer is
not in anybody's file yet.

Note 86 designed the next step and refused to build it:

> **A model as a second reader.** Designed above, not built: local only,
> adding to the list and never removing from it, and falling back to
> writing no contents at all when the model fails. It needs a
> measurement first: how many names does `qwen3.8:latest` miss on a
> labelled set? If it misses more than a few in a hundred, the flag
> would give false comfort.

This note is the measurement, and what was built because of it.

## The measurement

`examples/name_recall_trial.py` reads a labelled set of texts, asks the
model for the names in each, and scores the answer **the way the
scrubber would use it**. What the model returns is compiled with the same
matcher as `--trace-redact-words`. A labelled name counts as caught only
when every letter of it would have been scrubbed, so "Ana" returned for
"Ana Lima" is a miss, because "Lima" would still be in the file.

The set, `examples/name_recall_cases.jsonl`, is 165 texts with 1,493
names, shaped like what tools return. It came in two halves, because the
first half turned out to be too easy.

**The easy half** is 112 short texts: handover notes, git logs, support
tickets, CSV rows, email, meeting minutes, chat, JSON, first names that
are also words ("Grace", "Will") beside those words used as words, titles
("Dr. Byrne"), sixteen texts with no people at all, and decoys (vans
from Ford, a warehouse near Washington, orders via Alexa). The result
was 0 missed out of 378.

A perfect score on a templated set is exactly the false comfort note 86
warned about. So **the hard half** went after what real tool results do
and templates do not: CSV exports up to 150 rows, 120-line application
logs, 40-message support threads, lower-case names mid-sentence, names
in file paths and git remotes (`/home/rafael/`, `@anand.boateng`),
initials ("B.A. Okafor"), hyphenated first names, Cyrillic, Arabic,
Devanagari, Greek, Hebrew, Hangul and Vietnamese names, and word-names
standing alone ("Ask Will about the rota").

`qwen3.8:latest`, temperature 0, one run:

```
kind                 texts  names missed  over failed
handover                10     40      0     0      0
git-log                 10     30      0     0      0
ticket                  10     40      0     0      0
csv                     10     40      0     0      0
email                   10     70      0     0      0
minutes                 10     50      0     0      0
chat                    10     40      0     0      0
json                     8     24      0     0      0
wordish                 10     20      0     0      0
titles                   8     24      0     0      0
decoy                   10      0      0     2      0
none                     6      0      0     0      0
hard-long-csv            6    550      0     0      0
hard-long-log            5    148      0     0      0
hard-lowercase-prose     8     32      0     0      0
hard-paths               8     24      0     0      0
hard-initials            8     32      0     0      0
hard-non-latin           6     18      0     0      0
hard-word-alone          8     27      0     0      0
hard-long-thread         4    284      5     0      0

165 texts, 1493 names in the texts that answered: 5 missed (0.3 in a hundred), 2 phrase(s) over-scrubbed, 0 call(s) failed
```

Five misses, all in one 40-message thread, and all the same mistake:

```
found: [..., 'Dmitri Nkosi', ..., 'Lucía Vega', ...]
[02] Dmitri Nkosi: Rafael, can you check the Globex account?
[09] Lucía Vega: looping in Dmitri for the billing side
[28] Lucía Vega: Lucía, can you check the Northwind account?
```

The model listed each person once, by full name, and did not list the
bare "Dmitri" that appears further down. That is not a random miss. It
is a pattern, and a pattern has a mechanical fix.

**EACH WORD OF A NAME IS SCRUBBED TOO.** Every word of a multi-word name
the model returns is added beside it: "Dmitri Nkosi" also scrubs
"Dmitri" and "Nkosi". The trial scores that too:

```
165 texts, 1493 names in the texts that answered: 0 missed (0.0 in a hundred), 10 phrase(s) over-scrubbed, 0 call(s) failed
```

The price is the over-scrubbed column: 2 phrases became 10. The two
from before were "Hilton" and "Wayne" in the decoy texts, and the new
ones are ordinary words that are also first names, hidden because a
person with that name was mentioned: "will" beside "Will Turner",
"mark", "grace", "Hope". Eight of the ten are in the set built to trap
exactly that. An extra scrub costs a word of evidence, and a miss costs
somebody's privacy, so the trade goes that way.

## What the numbers do not say

**THE SET IS SYNTHETIC.** Note 86 asked for a model that "can show on
real data that it misses almost nothing", and there is no real data
here. The texts were generated from name lists and templates, so the
names sit in places a generator put them. Real files have names in
places nobody thought to test. The hard half narrows that gap; it does
not close it.

So the trial is part of the feature. `--cases` takes any labelled set,
one JSON object a line:

```json
{"text": "Backup: bo chen, on call weekends", "names": ["bo chen"]}
```

Label fifty of your own records before trusting the flag with the rest.
If your data breaks it, the trial will say where, the way it said
"Dmitri" here.

**ONE MODEL, ONE RUN.** At temperature 0 a local model answers the same
way each time, so a second run of the same set would say little. A
different model is a different measurement. `--model` is there to take
it.

## What was built

```bash
yantra --trace run.jsonl --trace-full --trace-redact-reader qwen3.8:latest
```

For every recorded turn, the reader is asked for the names in the
line's contents: the task, the tool arguments and results, the answer,
and all the same fields of any sub-agents. Each name it returns, and
each word of each name, becomes `[redacted]`. The rules from note 86
hold, and each is enforced rather than promised:

**LOCAL ONLY.** The text being scrubbed is the text that must not leave
the machine. The flag takes an Ollama model tag, and there is no flag for
any other provider. This holds whichever road the agent itself is on: a
turn answered by a frontier cloud model is still read for names on your
own machine.

**IT ONLY ADDS.** `--trace-redact` and `--trace-redact-words` run first,
exactly as they do without a reader. The model is shown what they left,
and whatever it finds is scrubbed after. The first version ran the model
first, and the receipt below caught it: the reader hid "ana" inside
`ana@example.com`, and the `email` pattern then saw
`[redacted]@example.com`, which is not an address, and left the domain
in the file.

**A READER THAT FAILS WRITES NOTHING, NEVER EVERYTHING.** If the model
errors, or its answer is cut off by the token limit, or it answers with
prose instead of a list, the turn's contents are **withheld**. The line
is still written, with the tools, counts and outcome, and a note saying
why. `--turns` shows it:

```
  351ad195  2026-09-25T03:12:12Z   3 tool(s)  [withheld]
             contents withheld: the name reader failed: ProviderError: 404:
not_found_error: model 'no-such-model:latest' not found
```

**NO MORE TEXT AT ONCE THAN WAS MEASURED.** The longest text in the set
was 6.6 KB. A line with more content than 8 KB is asked about in pieces,
cut at line breaks, and one piece failing fails the whole line.

**ONLY THE MODEL IS NAMED.** The banner says `full, redacting names read
by qwen3.8:latest`, and the page's `rec` chip says the same. What the
model found is never shown anywhere, for note 86's reason: those names
are exactly what the flag exists to hide.

## What it costs

**ONE LOCAL MODEL CALL PER RECORDED TURN.** In the trial, a short text
took 2 to 12 seconds on `qwen3.8:latest` and a 150-row export took 33
seconds, most of it the model thinking. The line is written when the
turn ends, so at the terminal your next prompt waits while the reader
reads. That is the price of reading before writing, and it is why this
is a flag rather than a default.

## Both roads

The reader is always the local road; that is the point of it. The agent
being recorded can be on either. The receipt below uses `qwen3.8:latest`
for both, and a cloud-model turn is read the same way on your machine.

## What was deliberately not built

**A cloud reader.** See above. Sending the text to a hosted model to
learn what must not leave the machine would defeat the flag.

**Letting the model remove names from the list.** It is a second reader,
not a judge of the first. A name the operator listed is scrubbed whatever
the model thinks of it.

**Fuzzy matching.** "A. Lima" is caught only if the model returns it,
and here it did, because the prompt asks for names "copied exactly as
written". The matcher still matches literally, for note 86's reason.

## What is not here yet

* **A measurement on real data.** Above. The trial takes your labelled
  records; nobody else's can stand in for them.
* **Reading in the background.** The next prompt waits for the reader.
  A recorder that read after returning control would need somewhere to
  hold an unscrubbed line in the meantime, which is the thing this
  exists to avoid.

## Receipt

The handover file from note 86, with **no word list at all**, read by
`qwen3.8:latest` in the terminal and read for names by the same model:

```
$ cat handover.txt
Release owner: Ana Lima (ana@example.com)
Backup: bo chen, on call weekends
Vendor contact: Acme Inc. support desk

$ yantra --provider ollama --model qwen3.8:latest --trace run.jsonl \
    --trace-full --trace-redact email --trace-redact-reader qwen3.8:latest \
    --prompt "Read handover.txt. Who owns the release, who is the backup, \
and who is the vendor? Quote the names exactly."
Here are the names exactly as written in handover.txt:

- **Release owner:** `Ana Lima` (ana@example.com)
- **Backup:** `bo chen` (on call weekends)
- **Vendor contact:** `Acme Inc. support desk`
── end_turn · 2920 in / 76 out · 3 iteration(s)
```

What went to disk (the model read the file twice this time):

```json
"steps": [{"name": "read_file", "arguments": {"path": "handover.txt"},
           "result": "     1\tRelease owner: [redacted] ([redacted])\n     2\tBackup: [redacted], on call weekends\n     3\tVendor contact: Acme Inc. support desk"},
          {"name": "read_file", "arguments": {"path": "handover.txt"},
           "result": "     1\tRelease owner: [redacted] ([redacted])\n     2\tBackup: [redacted], on call weekends\n     3\tVendor contact: Acme Inc. support desk"}],
"answer": "Here are the names exactly as written in handover.txt:\n\n- **Release owner:** `[redacted]` ([redacted])\n- **Backup:** `[redacted]` (on call weekends)\n- **Vendor contact:** `Acme Inc. support desk`",
"redacted": 9
```

Nine replacements: the address three times by the `email` pattern, and
the two names three times each by the reader. Nobody had written "bo
chen" down anywhere. "Acme Inc." is a company and stayed.

The same turn with a reader that cannot answer (a model tag Ollama does
not have), so the line is written with its contents withheld:

```
$ yantra ... --trace bad.jsonl --trace-full --trace-redact-reader no-such-model:latest \
    --prompt "Read handover.txt and name the backup."
The backup named in handover.txt is **Bo Chen** (on call weekends).

"task": "[withheld]",
"steps": [{"name": "glob", "arguments": "[withheld]", "result": "[withheld]"},
          {"name": "list_dir", "arguments": "[withheld]", "result": "[withheld]"},
          {"name": "read_file", "arguments": "[withheld]", "result": "[withheld]"}],
"answer": "[withheld]",
"withheld": "the name reader failed: ProviderError: 404: not_found_error: model 'no-such-model:latest' not found"
```

The answer on screen is untouched, as in notes 79 and 86. The flag
decides what reaches the file.

The measurement itself:

```
$ uv run python examples/name_recall_trial.py --provider ollama --model qwen3.8:latest --out qwen.jsonl
```

The tables above are its output, from the easy and hard halves run
separately and rescored together with `--rescore`.

`2097 passed, 1 skipped` (was 2076).
