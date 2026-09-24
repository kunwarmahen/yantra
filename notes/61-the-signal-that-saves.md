# 61 — The signal that saves

*Continues [notes/59](59-one-key-both-halves.md), which fixed the login
that `--browse-login` was destroying and left one way to lose it: press
Ctrl-C instead of closing the window.*

## The bug, as you meet it

You run `--browse-login`, a Chrome window opens, you sign in. Then,
instead of closing the window, you do what you do to every other
command in a terminal: you press Ctrl-C.

Yantra told you, honestly, that this might not have worked:

```
(cancelled -- the window was killed rather than closed, so a sign-in
you just finished may not have reached disk)
```

"May not" was accurate, and not good enough. The two signals that
old path sent Chrome, sent in the same order to a real Chrome, kept a
sign-in made seconds earlier **3 times in 10**. The other seven, the
agent would have met the sign-in page again.

## Why a browser loses a login it already has

Chrome does not write cookies to disk the moment it gets them. It holds
them in memory and writes them out in batches — on a timer, and when it
shuts down. A login you finished five seconds ago is, most of the time,
only in memory. Whether it reaches disk depends entirely on *how* the
browser stops.

So the question is which ways of stopping Chrome include that final
write. It turned out to be the one fact nobody had checked.

## Measured, not assumed

The obvious guess is "any polite signal". A process asked to stop with
SIGTERM — the standard *please exit* — is supposed to shut down
cleanly. Here is what a real Chrome did, headed on a virtual display,
three runs of each, with a cookie set five seconds before the signal:

```
signal                      cookie on disk
SIGINT                      3 of 3
SIGTERM                     0 of 3
SIGINT, then SIGTERM (old)  2 of 3
SIGKILL                     0 of 3
```

And headless, the same shape but starker: SIGINT 3 of 3, everything
else 0.

**SIGTERM saves nothing.** It exits just as fast as SIGINT and skips
the write. Only SIGINT — the signal a terminal sends on Ctrl-C — makes
Chrome write its cookies on the way out.

Which explains the old behaviour exactly. The login window ran in the
terminal's *process group* — the set of processes a terminal delivers
Ctrl-C to — so pressing it sent SIGINT to both Yantra and Chrome at
once. Chrome began a clean shutdown. Yantra, a moment later, caught its
own interrupt and sent Chrome a SIGTERM, which landed on top of the
shutdown already under way. Whether the write finished first was a
race. Ten more runs of just those two:

```
SIGINT alone            kept the cookie 10 of 10
SIGINT, then SIGTERM     kept the cookie  3 of 10
```

## The fix

**THE BROWSER GETS ITS OWN PROCESS GROUP.** It is started as the leader
of its own session (`start_new_session=True`), so the terminal's Ctrl-C
reaches Yantra and nothing else. Yantra now decides what the browser is
sent, instead of racing the terminal to it.

**ONE SIGINT, THEN PATIENCE.** Yantra sends Chrome exactly one SIGINT —
the signal the measurements say saves — and waits up to 15 seconds for
it to exit. A normal quit takes well under one. Nothing else is sent
while it closes, because a second signal on a quit already under way is
the race this note removes.

**A SECOND CTRL-C IS SOMEBODY INSISTING.** Press it again while Chrome
is closing, or let the 15 seconds run out, and the browser is killed —
the whole process group, so its renderer and GPU helpers do not linger
holding the profile locked. A person who presses Ctrl-C twice would
rather have it gone than saved, and that is their call.

**THE MESSAGE SAYS WHICH ONE HAPPENED.** The two endings are different
facts, so they get different sentences:

```
(cancelled) the browser was asked to close and did, so what you signed
into is kept -- 46 cookies
```

```
(cancelled) the browser was killed before it finished closing, so a
sign-in you just finished may not have reached disk
```

Both exit with 130, the usual code for a command stopped by Ctrl-C. The
interruption is still an interruption; it just no longer costs you the
login.

## Why not simply use SIGTERM, like everything else

`_terminate()`, the helper every other subprocess in this repo is
stopped with, sends SIGTERM — and for a shell command or an MCP server
that is right. It stays that way. The login browser gets its own
function because it is the one child here whose shutdown *writes
something you need*, and for that child the standard signal is
measurably the wrong one. A general helper that sent SIGINT to
everything would be wrong for the processes it was written for.

## What was deliberately not built

**No flush-then-kill.** Chrome has no command-line way to say "write
your cookies now". The only way to make it write is to let it shut down,
so the fix is to ask it to shut down in the one way that writes.

**The Playwright window is unchanged.** Without
`YANTRA_BROWSER_EXECUTABLE`, `--browse-login` opens Playwright's own
Chromium, which Playwright starts and stops itself. That path keeps the
old message. It is also the path sign-in pages refuse
([notes/58](58-the-browser-you-already-have.md)), so it is rarely the
one a login goes through.

**Other browsers were not measured.** The numbers above are Google
Chrome 154 on Linux. Brave and Chromium are built from the same code
and will very likely behave the same, but "very likely" is not a
measurement, and the table is only for the browser it was measured on.

## Receipt

The real command, run the way a person runs it, with Chrome on a
virtual display, a page that sets a cookie, and a terminal's Ctrl-C
(SIGINT to the command's process group) six seconds in:

```
$ YANTRA_BROWSER_EXECUTABLE=chrome uv run yantra --browse-login http://127.0.0.1:18770/
login setup · profile /tmp/.../cliprof.Od9h
/usr/bin/google-chrome is opening at http://127.0.0.1:18770/ -- log in yourself (2FA and
captchas are yours to beat), then CLOSE THE WINDOW. ...
^C
(cancelled) the browser was asked to close and did, so what you signed into is kept -- 1 cookies
exit 130
```

The same test against the previous code, three runs each, came out
clean on all three — a race does not lose every time, which is exactly
why it survived: the ten-run table above is what showed it.

`1630 passed, 1 skipped` (was 1623).
