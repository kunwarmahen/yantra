# 65 — The turn behind the red line

Two records in this harness each had an answer the other one needed,
and no way to point at it.

**A report says a case went red, and not what the agent did.** An eval
report ([notes/42](42-two-runs-of-the-same-suite.md)) holds *verdicts*:
which cases passed, how many runs, what it cost. A trace file
([notes/57](57-a-turn-written-down.md)) holds *trajectories*: which tools
ran, in what order, and whether each worked. The question after "case
seven broke" is always "what did it do instead?", and the answer was in
neither file. `--eval --trace FILE` was accepted, recorded nothing, and
said nothing about it.

**A child refused by the gate looked like a child whose read failed.**
When a sub-agent (a *child*, [notes/08](08-sub-agents.md)) is turned
away by the permission gate, its history holds an error result, and so
does a read of a file that is not there. The parent's own steps have
recorded *why* a call was refused since [notes/39](39-a-clock-and-a-word.md).
A child's, recorded since [notes/63](63-the-whole-turn-written-down.md),
did not. Those two failures want opposite fixes (open the gate, or fix
the path) and the recording made them look the same.

## A suite writes its turns down

```
yantra --agent examples/agents/researcher --eval --trace runs/suite.jsonl --report runs/today.json
```

Every run of every case that reaches a model is now one line in the
trace file, with the case's id on it:

```json
{"format": "yantra.trace.v1", "id": "08ad4e35d0034a59bca972cd9adc6547", "case": "outlines-a-one-word-lookup",
 "task": "Which file in sources/ mentions 'token bucket'? Answer with the path.",
 "steps": [{"name": "grep", "ok": true}], "iterations": 2, "tokens": 5158, "outcome": "end_turn", …}
```

The report names those turns from the other side. Each case row gains
the ids of its runs:

```json
{"id": "outlines-a-one-word-lookup", "passed": false, "attempts": 2, "passes": 0, …,
 "traces": ["08ad4e35d0034a59bca972cd9adc6547", "cac47280…"]}
```

Either file finds the other. On screen, a red case prints the turns of
its *failing* runs under its failures (a green run's turn is not what
anyone opens the file for), and the run ends by saying how to make one a
case:

```
  FAIL  outlines-a-one-word-lookup  ✗✗ 0/2 runs · 0.00-0.66 at 95% · 5.0s · 10245 tok
        required tool not used: outline (2 of 2 runs)
        turns: 08ad4e35 cac47280

SUITE RED · 0/1 passed · 2 runs · 10245 tokens
report: runs/red.json
a red run's turn becomes a case with: --fossil ID --trace runs/red.jsonl
```

`--against` does the same for a case that *broke* since the last run.
It prints the new run's turns beside the `broke` line, because that is
the line somebody will want to look into.

### Recording after grading, not instead of it

**THE RUN THAT IS GRADED IS THE RUN THAT IS RECORDED.** The terminal and
the browser record a turn by tapping its live event stream as it goes
past. The eval runners have no stream to tap: they call `agent.run()`,
on purpose, so a suite drives the agent exactly the way a library caller
would. Changing *how* a case runs so it could be recorded would make the
recording a witness to a different run from the one that got the
verdict.

So the suite's recorder works from what the agent kept instead of what
it streamed. It reads the history, the token counts and the refusals
after the run is over, and that holds everything a SHAPE line needs.
It is safe because a suite builds a fresh agent for every run of every
case, so its history is exactly one turn. A test checks it: the same
case gets the same verdict with and without a trace file.

A run that crashed is recorded too, since it is the one most worth
opening. `outcome` says why when the harness said why: `max_iterations`,
`over_budget`. Anything else is `crashed`. A run that never reached a
model (a roster-only case, or one whose roster check failed first,
[notes/35](35-roster-and-pass-rates.md)) is not a turn, and writes no
line.

A trace file that cannot be written is found **before** the suite
starts. Otherwise it would take a suite's worth of tokens to discover.

The privacy rule is unchanged. **SHAPE, NOT CONTENT, IS THE DEFAULT**;
`--trace-full` adds arguments, results and the answer under `--eval`
exactly as it does in a session. A suite's trace file is safe to attach
to a pull request for the same reason a session's is.

## A refusal inside a child keeps its word

Each agent now keeps `turn_refusals`: the gate's refusal code for every
call it turned away in the turn just run. It starts empty with each new
turn. The parent never needed it, because its codes reach the recorder
on the event stream. A child's stream goes nowhere a recorder can see,
so the spawner reads the codes off the child when it finishes, next to
the history it already reads, and each child step carries one:

```json
"children": [{"number": 1, "agent": "reader", "steps": [
  {"name": "read_file", "ok": false, "refusal": "policy"},
  {"name": "grep", "ok": false}
]}]
```

The first call was refused by a rule. The second ran and failed. The
key is only written when there was a refusal, so every line recorded
before this reads the same as it did. `--fossil` says it too:

```
note: sub-agent #1 reader on m: read_file (refused: policy), grep (failed); finished
```

`SubagentResult.steps` changed shape to carry it: each step is now
`(tool, worked, refusal)` rather than `(tool, worked)`. That breaks a
library caller that unpacked pairs. The field only arrived in note 63,
and the alternative was a second list that had to line up with the
first by position.

## Both roads

Nothing here depends on the provider. The receipt below is a local run
on `qwen3.8:latest`. A cloud run writes the same lines, with `usd`
filled in on each trace line, the same figure the report stores
([notes/48](48-what-the-run-cost.md)).

## What was deliberately not built

**No link from a fossil case back to its turn, beyond the id.** A case
made by `--fossil` is already named `trace-3f9c21ab`. A `from_trace`
key would be one more thing an author has to keep true while editing
the case, and the case stops being that turn the moment somebody edits
its task.

**No trace directory per suite run.** One file, appended to, the same as
a session. The `case` key is what separates a suite's lines from
someone's typed turns in a shared file, and a filter on it is one line
of `jq`.

## What is not here yet

* **Nothing decides what was a failure.** Still true from note 57: a
  green run can be wrong, and a red run's turn is where to *start*
  looking, not a verdict on what went wrong.
* **No retention, rotation or redaction.** Still true from note 57, and
  more pressing now: `--repeat 10` on a suite writes ten lines a case.

## Receipt

The whole researcher suite, `examples/agents/researcher` on
`qwen3.8:latest`, with `--trace` and `--report`: `SUITE GREEN · 6/6
passed · 37477 tokens · 2 case(s) cost nothing`, four lines in the trace
file (one per case that reached a model, none for the two roster-only
cases), and the declared `fact_checker` child inside its parent's line:

```
86403107 outlines-before-reading       end_turn [('outline', True), ('read_file', True)]
0e44a8cc cites-what-it-read            end_turn [('list_dir', True), ('read_file', True)]
bf1d4563 cannot-write-even-when-asked  end_turn [('glob', True), ('list_dir', True), ('outline', True), ('read_file', True)]
3cd2d8d8 delegation-works-end-to-end   end_turn [('fact_checker', True)]
  child #1 fact_checker: glob, glob, outline, grep, read_file; finished
```

The red run above is a copy of that package with one case written to
fail: it demands `outline` for a one-word lookup, and the model sensibly
reached for `grep` both times. Its first turn, followed back:

```
$ yantra --fossil 08ad4e35 --trace runs/red.jsonl
[[case]]
id = "trace-08ad4e35"
description = "recorded 2026-09-24T12:02:15Z on ollama/qwen3.8:latest; ended end_turn"
user_message = "Which file in sources/ mentions 'token bucket'? Answer with the path."
required_tools = ["grep"]
max_tokens = 7737
```

The fossil asserts what the agent *did*, not what the strict case
wanted, which is the point: the red line said something was wrong, and
the turn said the case was.

A child refused for real: a parent on `qwen3.8:latest` whose gate turns
away every `read_file` by policy, asked to have a child read `prompt.md`:

```
78aaf6c6 [('spawn_subagent', True, None), ('glob', True, None), ('read_file', False, 'policy')]
child #1 [('read_file', False, 'policy')] None
```

`1711 passed, 1 skipped` (was 1688).
