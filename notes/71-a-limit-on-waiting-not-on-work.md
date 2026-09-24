# 71 — A limit on waiting, not on work

[Note 51](51-a-turns-worth-of-waiting.md) built `with_wait_budget`: a
turn may spend so many seconds, in total, waiting for a person to
approve things. Each question waits for whatever is left, and once it is
all spent, nobody is asked again that turn.

That last part was built too bluntly. Once the allowance was gone, the
wrapper refused **every** call for the rest of the turn without asking
the gate inside it at all. That included calls nobody was ever going to
be asked about: a `read_file` the gate approves on the spot, or a write
a standing rule allows. A limit on how long a person is kept waiting
also stopped the agent reading files.

It came up while building a *daily* version of the same limit in dvara,
the service that runs these agents for people. For a whole day it would
have meant an agent unable to read anything until midnight because its
person was slow to answer in the morning. dvara put its check somewhere
else. For one turn the cost is smaller, but it is the same mistake.

## The fix

**THE INNER GATE IS STILL ASKED.** Once the allowance is spent:

* **An answer given on the spot** (a read, a standing yes) was never
  going to wait. It costs no allowance and passes straight through.
* **An answer that would wait** is refused with `out_of_time`, as
  before, and without being awaited.

The original rule, *never post a question nobody will wait for*, still
holds, and that is why the order is safe. A gate that asks a person
returns a coroutine, and a coroutine does nothing until it is awaited.
The wrapper closes it unstarted, so no question is ever posted. A gate
that starts work before returning (a task already running) has its task
cancelled, which is exactly what happens to it when a turn is dropped.
Every gate Yantra ships returns a coroutine.

## Both roads

Nothing here touches a model. The wrapper is the same on every
provider.

## What was deliberately not built

**No change to `with_deadline`.** It puts a clock on each question and
has no allowance to run out of, so it never had this problem.

## What is not here yet

* ~~**No frontend passes a wait budget.**~~ The browser does since
  [note 78](78-a-clock-on-the-page.md), keeping this note's rule: once
  the time is gone, only a prompt that would wait is refused. Was: Still
  true from note 51: the terminal answers inline, and the browser has no
  flag for it. dvara uses its own daily check.

## Receipt

A script driving the wrapper directly: a gate that approves reads on
the spot and asks a person, who is away, about writes. The turn may
wait 2 seconds in total.

```
$ uv run python wait_demo.py
  asking about write_file...
write_file  -> refused (timeout)
write_file  -> refused (out_of_time)
read_file   -> allowed
```

The same script against the previous version:

```
  asking about write_file...
write_file  -> refused (timeout)
write_file  -> refused (out_of_time)
read_file   -> refused (out_of_time)
```

The second write was not asked about in either version; only the read
changed.

`1772 passed, 1 skipped` (was 1770).
