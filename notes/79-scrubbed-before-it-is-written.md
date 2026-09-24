# 79 — Scrubbed before it is written

[Note 57](57-a-turn-written-down.md) made privacy the whole design of
`--trace`. By default a recording keeps a turn's **shape**: the task,
which tools ran and whether each worked, and the counts. It keeps
nothing the agent read. `--trace-full` adds the arguments, the results
and the answer, and every line says which level wrote it.

That leaves two gaps, and the second is the larger one.

* **The task is kept at every level.** Without it there is no case.
  But the task is whatever a person typed, and people paste things into
  a prompt: an email address, a customer's name, a key they want the
  agent to use.
* **A full recording keeps whatever the agent read.** That is the point
  of `--trace-full`, and it is also why a full file cannot be handed to
  anyone. Read one config file that contains an API key, and the key is
  now in `runs/today.jsonl` as well.

Note 57's answer was that a service wanting "drop anything matching
this pattern" could write it in ten lines against JSONL. That is true
for a service. It does nothing for somebody running the terminal or the
browser, who would have to write those ten lines themselves and run
them after every session, and whose file is unscrubbed in the meantime.

## `--trace-redact`

```
yantra --trace runs/today.jsonl --trace-full --trace-redact email --trace-redact token
yantra --trace runs/today.jsonl --trace-redact 'ACME-\d+'
```

Every match becomes `[redacted]` **before the line is written**. The
value never reaches the disk.

The pattern is a regular expression, and two are built in because
nearly everyone needs them:

* **`email`**: email addresses.
* **`token`**: the credentials that are recognisable from their shape.
  That is `sk-…` API keys (OpenAI, Anthropic), GitHub (`ghp_…`,
  `github_pat_…`), Slack (`xox…-`), AWS access key ids (`AKIA…`),
  Google API keys (`AIza…`), JWTs, and `Bearer …` headers.

**ONLY CONTENT IS SCRUBBED.** The patterns are applied to the task, a
tool's arguments (nested values included), its result, the answer, and
a sub-agent's task and answer. They are **not** applied to the id, the
timestamp, the model, the tool names, the refusal codes or the counts.
Those are shape. `--turns` and `--fossil` read them, and a pattern that
scrubbed a tool name would break the recording for the one purpose it
has.

**THE LINE SAYS HOW MANY.** Every line written with patterns carries
`"redacted": N`, even when N is 0. The reason is the same as for note
57's `detail`: a reader can tell a scrubbed file from one nobody
scrubbed without reading either. A line written without patterns has no
count at all, so "none found" and "nobody looked" read differently.

**A PATTERN THAT WOULD DO NOTHING IS REFUSED AT STARTUP.** A pattern
that is not a valid regular expression is an error before the session
starts, not a crash at the end of the first turn. So is one that matches
the empty string: `a*` would "redact" between every pair of characters
and scrub nothing. Discovering either one in the file would mean finding
out after the file had already been shared.

**THE TURN KEEPS WHAT IT RAN WITH.** Scrubbing works on a copy. The
arguments in a recorded step are the same objects the tool ran with, and
a redactor that edited them in place would change what the agent holds,
not just what the file says.

**THE BOUNDARY IS THE WRITE.** The patterns live in `TrajectoryLog`,
the one thing that writes a line. That means the terminal, the browser
([notes/63](63-the-whole-turn-written-down.md)) and every run of an
eval suite ([notes/65](65-the-turn-behind-the-red-line.md)) are all
scrubbed by the same flag, with nothing to add in each of them.

The banner says it is on: `recording runs -> runs/suite.jsonl (full,
redacting 2 pattern(s))`, and so does the browser's `rec` chip.

## A scrubbed task, and `--fossil`

A case built from a scrubbed turn would replay `[redacted]` to the
model, not the words the person typed. `--fossil` still prints the case,
and adds a note on stderr:

```
note: the task was scrubbed when it was recorded; put the real words back in user_message where it says [redacted]
```

It does not try to guess the words back. The file never had them, which
is the point.

## Both roads

Nothing here touches a model. The receipt is `qwen3.8:latest`, and
what gets scrubbed is the same whichever model produced it.

## What was deliberately not built

**No scrubbing of a file already written.** `--trace-redact` together
with `--turns`, `--mark`, `--fossil` or `--trace-prune` is an error. A
pattern added afterwards is a pattern the file was already shared
without, and a command that appeared to fix that would give false
comfort. Recording afresh with the flag is the fix. For an old file,
note 57's ten lines against JSONL still apply.

**No package key.** Which patterns to scrub depends on who runs the
agent and on what data, just as `--trace` itself does. It is the
operator's flag, not something the package author decides.

**No "scrub everything that looks secret" preset.** A pattern that
fires on any long random-looking string would also scrub hashes, ids and
file names, which are the evidence the recording exists to keep. The
`token` preset matches credential formats it can recognise, and nothing
else.

## What is not here yet

* **Names, addresses and other free text.** A regular expression cannot
  find "the customer's name". That needs a model or a list, and either
  one is a different kind of feature.

## Receipt

A file with an owner's email and a deploy key in it, read by
`qwen3.8:latest` in the terminal, recorded with `--trace-full
--trace-redact email --trace-redact token`:

```
$ cat handover.txt
Release owner: ana@example.com
Deploy key: sk-ant-demo00000000000000000000 (rotate monthly)

$ yantra --trace run.jsonl --trace-full --trace-redact email --trace-redact token \
    --prompt "Read handover.txt. Who owns the release, and what is the deploy key? Quote both exactly."
- Release owner: `ana@example.com`
- Deploy key: `sk-ant-demo00000000000000000000` (rotate monthly)
── end_turn · 2638 in / 70 out · 2 iteration(s)
```

The answer on screen is untouched, because scrubbing is about the file.
What went to disk:

```json
"steps": [
    {
        "name": "read_file",
        "ok": true,
        "arguments": {"path": "handover.txt"},
        "result": "     1\tRelease owner: [redacted]\n     2\tDeploy key: [redacted] (rotate monthly)"
    }
],
"answer": "- Release owner: `[redacted]`\n- Deploy key: `[redacted]` (rotate monthly)",
"redacted": 4
```

Four matches: two in what the tool returned and two in the answer. The
tool name, the path it was given and the counts are all kept.

`1873 passed, 1 skipped` (was 1852).
