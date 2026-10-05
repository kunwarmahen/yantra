# 114 — Nobody watching

Until now, every Yantra run had somebody in front of it. Even a one-shot
`yantra "what is in README.md?"` was typed by a person who was still
there when the answer came back. Three parts of Yantra rely on that
quietly:

* a permission prompt waits for a keystroke;
* `ask_user` waits for an answer;
* `browser_handoff` opens a window and waits up to ten minutes for
  somebody to sign in ([note 93](93-a-page-for-a-person.md)).

Now picture a program starting Yantra at 08:00 on a weekday, with the
prompt "check x.com and tell me what's new", while the person is asleep.
Each of those three waits is now a run that hangs, or one that guesses.
And when it's over, the program that started it has to read prose to
find out what happened.

This note covers what Yantra does when nobody is watching, and the one
thing it hands back for a program to read.

## Saying so: `--unattended`

```
yantra --agent ~/agents/reader --json --unattended \
       --allow-tools 'browser_*' --prompt "what's new on x.com/home?"
```

`--unattended` (or `YANTRA_UNATTENDED=1` for a program that starts
Yantra) means nobody is in front of this run. Each place that would
wait now answers straight away:

| What would wait | Unattended, instead |
|---|---|
| a permission prompt | read-only tools run; anything else is **refused**, unless it was allowed ahead of time |
| `ask_user` | the question is kept, and the turn ends (as a headless run always has) |
| `browser_handoff` | nothing opens; the need is kept, and the model is told to stop browsing and say what it needs |

Learning and the end-of-conversation look back are off too, for the
reason they're off on any run with nobody to ask ([note 96](96-solve-it-once.md), [note 101](101-looking-back.md)).

## Allowed ahead of time: `--allow-tools`

A scheduled check of x.com needs the browser. The browser tools aren't
read-only, because they reach the network ([note 28](28-browser-tools.md)),
so without help an unattended run would refuse every one of them and
check nothing.

The person did say yes to the browser, though. They said it when they
accepted the schedule, which was hours earlier. `--allow-tools GLOB`
(repeatable) carries that yes into the run: the tools it names run
without asking.

**A TOOL THAT ASKS EVERY TIME STILL ASKS.** A tool marked `always_ask`,
such as a purchase or sending a message, needs a yes to that particular
call. A name in a list, written before the call existed, is exactly
the blanket approval it refuses (the same rule `--yolo` follows). So it
falls through to the unattended gate and is refused. The gate is
`allow_named` in `permissions.py`, wrapped around whichever gate would
otherwise decide.

## What it hands back: `--json`

With `--json`, everything a person would read (the banner, the streamed
answer, the tool cards) goes to stderr. Stdout gets one object:

```json
{
  "format": "yantra.run.v1",
  "ok": true,
  "text": "I couldn't open your X timeline — every attempt at x.com/home ...",
  "stop_reason": "end_turn",
  "detail": "",
  "cost_usd": 0.0,
  "priced": false,
  "needs_person": ["X.com/home needs a signed-in account — the persistent browser profile isn't logged in, so the timeline can't load. Sign in on the shared profile and I can retry."],
  "busy": [],
  "refused": []
}
```

`ok` means only that the turn finished by itself. Three lists sit beside
it, kept apart because a caller should treat each one differently:

* **`needs_person`**: something only a person can fix. A sign-in, a
  captcha, a question. Running again won't help until they act.
* **`busy`**: something another process was using (a browser profile,
  below). Nobody needs to act, and the next run may find it free.
* **`refused`**: tools the run reached for that weren't allowed ahead
  of time. The model was told no, and it often answers fine without
  them. This is how a person finds out what to allow next time.

These were one list in the first version. Then a live run found that
`bash` was refused, worked around it, and gave a complete answer, and
the scheduler paused the schedule because "something needed a person".
A refusal the model got past is something to report, not a reason to
stop. So the three lists are separate, and only `needs_person` means
"stop until somebody acts".

The `format` field is a contract, the same way `setu status --json` is
([note 95](95-the-accounts-you-connected.md)). A program should refuse a
format it doesn't know rather than guess at one.

## A handoff with nobody to hand to

Unattended, `browser_handoff` doesn't wait and doesn't open a window.
It writes down what the model said a person needs, and tells the model
to stop browsing and explain in its answer. With nothing to approve,
the tool counts as read-only, so it always runs. A gate that refused
it would hide the one thing the run most needs to report.

**ITS DESCRIPTION SAYS WHAT IT IS FOR WHEN NOBODY IS THERE.** The first
version kept the attended wording ("a window opens for them ... once
they close it you get the page back"). Shown X's sign-in wall, with no
person to hand it to, `qwen3.8:latest` didn't call the tool. Asked by
the schedule to reply `NOTHING NEW` when there was nothing to report,
it gave exactly that reply. A run that never got in was filed as a
quiet success and told to nobody. In an unattended run the description
is now different:

> Nobody is watching this run. Call this when the page needs a PERSON
> — a sign-in or login page, 2FA, a captcha, a payment — instead of
> guessing or giving up quietly...

On the next three runs, all three called it, and the model's own
reasoning cites the change:

```
X requires login. The /home page always requires auth - it redirects and
returns a failure code when not logged in. This is a sign-in situation.
Per my handoff tool description, I should use browser_handoff since a
person is needed for sign-in.
→ browser_handoff()
```

## One browser per profile, across processes

Your logins live in a browser profile (`YANTRA_BROWSER_PROFILE`), and
Chromium allows only one browser on a profile at a time. That rule
protects you: two agents can't fight over one signed-in identity. But
the refusal used to be a page of Playwright text about a
`SingletonLock`, and it came instantly. A run that would have found the
profile free thirty seconds later never got to wait.

That used to be rare. Now a scheduled run at 08:00 can land on the
profile your `yantra --web` has held since breakfast
(`YANTRA_BROWSER_CLOSE=model` keeps it open on purpose). `leases.py`
can't help here: it guards one process's tools, and this is two
processes.

So every Yantra now takes a **lock file in the profile**
(`tools/profile_lock.py`) before it launches a browser there, and gives
it back when that browser closes:

* **It waits, then says who has it.** A person's launch waits 10
  seconds and an unattended one waits 2 minutes (`YANTRA_BROWSER_WAIT`
  overrides both). After that, the refusal names the holder: its pid,
  since when, and whether it's a person or a schedule. Unattended, it
  also lands in `busy`.
* **The kernel releases it.** The lock is `flock` on an open file, so a
  process that crashes, or is killed with `kill -9`, lets go when it
  exits. There's no stale lock to clean up and no expiry to guess.
* `--browse-login` takes the same lock, so signing in by hand can't
  collide with a scheduled run halfway through.

**THE TRADEOFF, SAID IN THE REFUSAL.** If your web page keeps its
browser open all day, a scheduled browser run on the same profile will
keep finding it busy. The refusal says so, and says what to do about
it: give scheduled runs a profile of their own and sign in there once
with `yantra --browse-login URL`.

## Receipt

Local model `qwen3.8:latest` through Ollama, browser headless on a
scratch profile, Setu off, so the only road to x.com is the plain
browser on a profile that isn't signed in:

```
$ yantra --cwd work --json --unattended --allow-tools 'browser_*' \
         --prompt "Open https://x.com/home with the browser and tell me
                   the 3 most interesting posts on my timeline."
```

The JSON above is what came back: `ok` (the turn finished), one item in
`needs_person`, and nothing refused. Through a scheduler (Samay, a
separate project, runs Yantra exactly like this), the same run, three
times on fresh state:

```
needs_person  I couldn't open your X timeline — https://x.com/home refused to load …
needs_person  I couldn't open your X timeline: x.com/home refused the request with …
needs_person  I couldn't open your X timeline. The browser was refused by x.com eve…
```

The same profile, held by another process, with `YANTRA_BROWSER_WAIT=5`:

```
busy   I hit a wall on this one, so here's the honest status: - **Browser:**…
       not allowed: web_fetch, bash
```

That answer also said it was *"not going to invent titles"*. The refused
list shows which fallbacks it tried, so the person can decide whether
`web_fetch` should be allowed next time.

One more finding. With Setu on, the same prompt read the person's real
timeline through their Setu X connection ([note 112](112-a-site-with-no-api.md)):
its read verbs are classed read-only, so they ran unattended without
`--allow-tools`. An unattended run reaches what any run of that agent
reaches. Whatever schedules it should say so when it asks the person to
accept.

## What was deliberately not built

* **Borrowing the browser another Yantra already has open.** It would
  answer "busy" better than waiting does, but it means two processes
  steering one browser, and the lock is what keeps that from happening.
* **A per-turn unattended switch for a long-lived host.** The lists in
  `unattended.py` belong to the process, because the process *is* the
  run: one prompt, one answer, then exit. A host that serves many
  unattended turns needs the flag per turn, and gets it when one exists.
* **Retrying.** An unattended run doesn't try the browser again after a
  busy profile or a sign-in wall. Whether and when to try again belongs
  to whatever scheduled it, which knows the next time it's due.
