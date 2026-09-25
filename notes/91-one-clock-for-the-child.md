# 91 — One clock for the child

[Note 80](80-the-time-left-told.md) told the model how much of a turn's
approval time was left. `--wait-budget 8` gives a turn eight seconds,
in total, of waiting for a person to click Approve. After a prompt uses
some of it, the model's next request says *3 of this turn's 8 seconds
for waiting on approval are left*, so it can ask for the approval it
needs most instead of finding out from a refusal.

Only the agent the person was talking to was told. A sub-agent is a
second agent the first one starts to do part of the job
([note 08](08-sub-agents.md)). When it asks to write a file, the same
person is kept waiting, and in the browser the same eight seconds are
spent. But nobody told the child. It learned the time was gone the old
way, from a refusal. The parent heard about it afterwards, once the
child had returned. Note 80 listed this as open.

Looking at it turned up a worse problem on the library side.

## A child's prompts were a new turn

The library clock, `with_wait_budget`
([note 51](51-a-turns-worth-of-waiting.md)), cannot see where one turn
ends and the next begins. It sees a stream of questions, so each
question carries a turn id, and a new id means a fresh allowance.

A sub-agent has its own turn id, which is right for most things. But it
put that id on its approval prompts too. So:

* the parent asks for a write and spends 20 of its 30 seconds;
* the child's first prompt has a new id, and the wrapper gives it a
  fresh 30;
* the parent's next prompt has the parent's id again, which is
  different from the last one the wrapper saw, and gets a fresh 30 as
  well.

Starting a sub-agent refilled the clock. The dollar limit was built to
avoid exactly this: a child charges its parent's meter, not a fresh one
([note 34](34-budgets.md)), because otherwise delegating would be the
cheapest way around the limit. The approval clock had the same hole,
and nobody had noticed.

## One clock, and the child hears it

**A CHILD ASKS AS ITS PARENT'S TURN.** When the spawner builds a child,
it stamps the parent's turn id onto the child as `clock_turn`. The
child's approval prompts carry that id, so any clock, the browser's or
the library's, counts them against the turn the person is actually
waiting on. The child's own turn id is unchanged. It still marks where
the child's own work starts and ends, for everything else.

**THE CHILD READS THE SAME NOTICE.** The spawner also hands the child
the parent's `approval_notice`, so the child is told the time left in
the same words, from the same clock. A child's first request is told at
once if the parent has already spent some time, often on approving the
spawn itself. After the child returns, the parent is told the new
figure on its next request, as before.

Both are one line each in the spawner. A host does not wire anything.
If it passed `agent.approval_notice = gate.approval_notice` for the
parent (note 80), the children get it too.

## The tradeoff

A child and its parent now share one allowance. If the child spends it
all, the parent's next write is refused without anyone being asked.
That is what the person set the clock for: *this much waiting per thing
I asked for*, not per agent the model decided to start. The alternative
is a clock that anyone can reset by delegating.

## Both roads

The clock and the notice are plain text on the request, so every
provider carries them the same way. The receipt below is
`qwen3.8:latest`, which read the child's notice and did its one write
without trying anything else.

## What was deliberately not built

**No separate allowance for a child.** "Give each sub-agent ten
seconds" sounds reasonable, but it is the reset under a different name:
the person waits the same either way, and the number they set would no
longer be the most they could be kept waiting.

**No notice about the child for the parent.** The parent is not told
*your child spent 5 seconds*. It is told what is left, which is what it
needs to decide its next approval. Who spent the time does not change
that.

## What is not here yet

* **A child still cannot hold.** With `--on-timeout hold`
  ([note 88](88-not-yet.md)), a child's unanswered prompt is refused as
  `held_in_child`, not held. A child cannot stop its parent's turn to
  wait. Still true by design.

## Receipt

The browser server in-process with `--subagents --wait-budget 8
--on-timeout deny` on `qwen3.8:latest`. Each request to Ollama was
checked for an approval notice as it was sent. A small websocket client
played the person: it approved the spawn after three seconds, the
child's write after two, and left the next prompt unanswered. The task
asked the model to have a helper write `one` to `a.txt`, then write
`two` to `b.txt` itself.

```
{'tool_name': 'spawn_subagent', 'summary': "spawn sub-agent (budget: 5 left): 'Write the file a.txt ...' · tools=['write_file']", 'wait_left': 8.0}
  approved after 3.0s
CHILD  WAS TOLD: [approval notice] 5 of this turn's 8 seconds for waiting on approval are left. Each approval prompt spends from it, and once it is gone anything that needs approval is refused without asking. If you still need approvals, ask for the one you need most first.
{'tool_name': 'write_file', 'summary': 'NEW FILE a.txt (1 lines)', 'wait_left': 5.0}
  approved after 2.0s
CHILD  WAS TOLD: [approval notice] 3 of this turn's 8 seconds for waiting on approval are left. ...
PARENT WAS TOLD: [approval notice] 3 of this turn's 8 seconds for waiting on approval are left. ...
{'type': 'tool_result', 'name': 'spawn_subagent', 'refusal': None}
{'tool_name': 'write_file', 'summary': 'NEW FILE b.txt (1 lines)', 'wait_left': 3.0}
  left unanswered
PARENT WAS TOLD: [approval notice] This turn has used all 8 seconds it may spend waiting for approval. ...
{'type': 'tool_result', 'name': 'write_file', 'refusal': 'timeout'}
{'type': 'tool_result', 'name': 'read_file', 'refusal': None}
{'type': 'turn_end', 'text': 'The subagent finished: a.txt now contains "one" (verified above). However, my write of "two" to b.txt failed — the approval request went unanswered and timed out. ...'}
```

The child's prompt shows `wait_left: 5.0`: the three seconds spent
approving the spawn were already gone, and the child was told so before
it asked. The parent's own prompt started with the 3 seconds the child
left, not a fresh 8.

The refill, pinned without the fix: the test that runs a spawn, a
child's prompt and then a parent's prompt under a 0.25-second clock
expects the parent's to time out. Before this change it was approved,
because the child's turn id had reset the clock twice.

`2104 passed, 1 skipped` (was 2100).
