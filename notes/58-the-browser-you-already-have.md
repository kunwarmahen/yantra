# 58 — the browser you already have

*`--browse-login` (notes/28) opens a visible window so you can beat a
login wall by hand. Against Google it did not work: the page answered
"couldn't sign you in — this browser or app may not be secure" and
that was that. The window was visible, so being invisible was never
the problem. What the page objected to was that the browser was being
DRIVEN — and that it was not a browser anybody actually uses.*

## What a site is actually looking at

"Headless" sounds like the thing a bot check would catch, and it is
easy to assume the fix is a window. It is not. Three separate tells
travel with an automated browser, and only one of them is about
windows at all:

1. **`navigator.webdriver`.** Any page can read this property in one
   line of JavaScript. Chromium sets it to `true` when it is launched
   with `--enable-automation`, which is what Playwright passes by
   default. A window does not change it. This is the tell that refuses
   a Google sign-in.
2. **The binary is a different binary.** Playwright's default
   `chromium` is not Chrome and not even full Chromium — it is
   `chromium_headless_shell`, a stripped build whose user-agent says
   `HeadlessChrome` in plain text. Nothing subtle is happening.
3. **It is nobody's browser.** The bundled build carries no
   proprietary codecs, no Widevine, no Google API keys. Google has
   refused sign-in from unbranded Chromium builds for years, entirely
   independently of automation.

Here is the default, read out of a running page:

```
navigator.webdriver : true
userAgent           : ... HeadlessChrome/151.0.7922.34 Safari/537.36
brands              : Not=A?Brand | HeadlessChrome | Chromium
```

Three ways of saying "a robot", and a headed window fixes none of
them. That is the whole reason the old `--browse-login` failed.

## Three answers, smallest first

**Drop the flags.** `--enable-automation` and the
`AutomationControlled` Blink feature are the only parts of the browser
claiming a driver is attached. Both go, on every launch, with no knob:
there is nothing to opt into, because the browser IS ordinary and
these flags were the lie. `navigator.webdriver` reads `false`
afterwards.

**Name a browser you already have.**
`YANTRA_BROWSER_EXECUTABLE=chrome` — or `/snap/bin/brave`, or any
Chromium-family path — and the tools drive that instead of the bundled
build. A channel name goes to Playwright as `channel=` (it knows where
each branded build installs itself on every OS); a path goes as
`executable_path=`. Same knob, two spellings, because "the browser I
have" is sometimes a name and sometimes a place only this machine
knows.

**Headed on a screen nobody looks at.** `YANTRA_BROWSER_HEADED=1`
launches with a real window. On a desktop that means a window on your
desktop, which during an agent run is its own kind of broken, and on a
server there is no screen at all. So when no display is attached
Yantra starts an **Xvfb** — a real X server with no monitor — and
hands its `DISPLAY` to the browser process alone, through Playwright's
per-launch `env=`. The browser is genuinely headed and paints
honestly; nothing renders. This is the one that matters most, because
headless is not a flag on a normal browser, it is a different build
with different fingerprints, and no argument talks it out of them.

## The login door is not a Playwright door

The three above make the agent's own sessions ordinary. They do not
help the sign-in, because a headed Playwright window is still an
automated one: it answers CDP, and `--browse-login`'s job is the
single moment when automation is least welcome.

So with a browser named, **`--browse-login` does not use Playwright at
all**. It runs the binary as a plain subprocess —

```
brave --user-data-dir=<profile> --no-first-run --no-default-browser-check <url>
```

— and waits for the process to exit. There is no driver, no CDP, no
flag to scrub. It is the same browser you use yourself, pointed at a
different profile directory, and the sign-in is as ordinary as
sign-ins get. The cookies it leaves behind are what every later
headless run rides, exactly as before.

The subprocess road opens only when `YANTRA_BROWSER_EXECUTABLE` is
set, and that restriction is deliberate rather than lazy: **a profile
belongs to the browser that wrote it.** Chromium refuses a profile
directory stamped by a newer version of itself, so a login written by
system Chrome 153 and later opened by Playwright's bundled Chromium
151 would fail in a way nobody would enjoy debugging. One variable
names the browser for both halves. Unset, everything behaves as it
always did — Playwright's own headed window, which still beats
captchas and 2FA, just not a robot check. The CLI says so before it
opens the window rather than after.

## A login that saves nothing must not look like one that worked

Two failures here are silent in exactly the same way, and both were
found by running the thing rather than reasoning about it.

**Snap confinement cannot see hidden directories.** A snap-packaged
browser is allowed into your home but not into its dot-directories,
and it does not complain — it exits 0 having created nothing:

```
$ brave --user-data-dir=~/.local/state/yantra/browser-profile ...
$ echo $?
0
$ ls ~/.local/state/yantra/browser-profile
ls: cannot access ...: No such file or directory
```

Which is the worst possible shape for a login flow: a window opens,
takes your password, and saves it precisely nowhere. Since
`.env.example` recommended a path under `~/.local/state`, this was the
DEFAULT outcome for a snap browser. So the pairing is refused up
front, before the CLI promises anybody a window:

```
error: /snap/bin/brave is a snap, and snap-confined browsers cannot write
into hidden directories -- /home/.../.local/state/yantra/browser-profile is
under '.local', so the profile would be silently discarded (the browser
exits 0 and creates nothing).
point YANTRA_BROWSER_PROFILE somewhere visible in your home, e.g.
  YANTRA_BROWSER_PROFILE=~/yantra-browser-profile
or use a non-snap browser (a .deb Chrome/Chromium has no such restriction)
```

**A shell `export` outranks the `.env` you are editing.** `.env` fills
gaps only — a real environment variable always wins, which is correct
and is also invisible at the moment it matters. An earlier version of
the tutorial in this repo said `export
YANTRA_BROWSER_PROFILE=~/.local/state/...`; anyone who pasted that
line still has it in that terminal, and can then fix `.env`, save it,
re-run, and watch the same refusal print the same old path. So the
loader now remembers which keys the shell outranked, and the refusal
says whose value it is actually complaining about:

```
this path came from YANTRA_BROWSER_PROFILE exported in your SHELL, which
outranks the .env you are probably looking at -- `unset YANTRA_BROWSER_PROFILE`
first, or fix the export
```

Only when the two disagree — a shell and a file that agree are not a
conflict worth a paragraph.

**A browser that was already running hands off and quits.** Launch
Chrome while Chrome is open and the new process may pass its arguments
to the running copy and exit immediately — again 0, again nothing
written. Indistinguishable from the first case from the outside, so
the check is the same one, made afterwards: if the profile has no
`Local State` and no `Default/`, nothing was saved, and that is an
error naming both possibilities rather than a cheerful "profile
saved".

## What was deliberately not built

**No stealth suite.** No spoofed user-agent, no patched `navigator`
properties, no canvas noise, no plugin fabrication. Everything here
makes the browser ordinary by *being* ordinary — a real binary, a real
window, flags that were lying removed. Nothing pretends to be
something it is not, which also means the honesty in notes/28 survives
unchanged: **captchas and bot checks can still refuse us, and when a
site serves a robot check the snapshot shows the check** instead of
claiming the mission succeeded. The wall moved; it did not vanish.

**No automatic hunt for a system browser.** Yantra will not go looking
for Chrome on your disk and quietly switch to it. The profile-stamping
problem above is why: a browser Yantra chose for you this week is a
browser it might choose differently next week, and the profile would
not survive the change. You name it, or you get the bundled one.

**No display sharing.** Each session that needs a virtual screen
starts its own Xvfb and kills it on teardown. A pool would save a
process; it would also leave an X server running after a crash, and
the number of browsers a Yantra runs at once is one.

None of this is provider-shaped. The model never learns which browser
it is driving, and a local Ollama run and a cloud run get the
identical page text — the browser choice lives entirely below the
tools, which is why it needed no new tool and no prompt changes.

## What the tests pin

- a channel reaches Playwright as `channel=`, a path as
  `executable_path=`, and neither launch re-adds the automation flags
- a bare command is resolved on `$PATH` at CONFIG time, so a typo is a
  startup error naming the variable, not a stack trace fifteen seconds
  into a run; a browser that is not installed is a `ConfigError`
- headed with no display starts exactly one Xvfb and STOPS it on
  close; headed with a display starts none and passes no `env=`;
  missing Xvfb names the package to install
- `--browse-login` with a browser named runs a subprocess and never
  touches Playwright; with a channel it looks the command up on
  `$PATH`; with nothing named it opens the old Playwright window
- the snap/hidden-directory pairing is refused for BOTH doors, before
  any cost, and the refusal names `YANTRA_BROWSER_PROFILE`
- a window that exits having written nothing raises rather than
  reporting success
- a `.env` key the shell outranked is remembered and named in the
  refusal; identical values are not called a conflict, and with no
  override the message does not blame a shell

## Receipts

`1612 passed, 1 skipped` (was 1589).

The same page read three ways, on one Linux desktop — the property
each line reports is the one a sign-in page reads:

```
--- bundled Chromium, headless (the default) ---
navigator.webdriver : false
userAgent           : ... HeadlessChrome/151.0.7922.34 Safari/537.36
brands              : Not=A?Brand | HeadlessChrome | Chromium

--- Brave, headed on Xvfb (YANTRA_BROWSER_EXECUTABLE=/snap/bin/brave) ---
navigator.webdriver : false
userAgent           : ... Chrome/152.0.0.0 Safari/537.36
brands              : Chromium | Not?A_Brand | Brave

--- Chrome channel, headed on Xvfb (YANTRA_BROWSER_EXECUTABLE=chrome) ---
navigator.webdriver : false
userAgent           : ... Chrome/153.0.0.0 Safari/537.36
brands              : Google Chrome | Not_A Brand | Chromium
```

`webdriver: false` everywhere is the flag scrub, which is free and
applies to the old default too. The second and third are the part a
branded browser buys: a user-agent and a brand list belonging to a
browser millions of people run, rather than to a build that says
`HeadlessChrome` out loud.

End to end, with nothing running afterwards that should not be:

```
$ YANTRA_BROWSER_PROFILE=~/yantra-login-demo \
  YANTRA_BROWSER_EXECUTABLE=/snap/bin/brave \
  uv run yantra --browse-login https://example.com
login setup · profile /home/mahen/yantra-login-demo
/snap/bin/brave is opening at https://example.com -- log in yourself (2FA and
captchas are yours to beat), then CLOSE THE WINDOW. ...

$ ls ~/yantra-login-demo/Default/Cookies
-rw------- 1 mahen mahen 20480 ... /home/mahen/yantra-login-demo/Default/Cookies
```

and the agent riding that profile afterwards, headed on a virtual
screen it never showed anyone:

```
title: Example Domain
source: https://example.com/

Example Domain
This domain is for use in documentation examples without needing permission.
...
$ pgrep Xvfb ; echo "none -- teardown killed it"
none -- teardown killed it
```

And the thing this was all for — a Google sign-in, beaten by hand in
an un-driven Brave, then inherited by a headless agent session on the
same profile:

```
profile     : /home/<user>/yantra-browser-profile
landed on   : https://mail.google.com/mail/u/0/#inbox
page title  : Inbox - <account> - Gmail
cookies     : 58 in the profile
verdict     : SIGNED IN -- Gmail served the app
```

Landing on `/mail/u/0/#inbox` is the whole proof: signed out, Google
redirects to `accounts.google.com` before anything renders. The old
`--browse-login` never got as far as writing those cookies, because
the sign-in page refused the automated window it opened.

One honest wrinkle found in the same run: the FIRST snapshot of Gmail
came back without the inbox in it. `browser_open` reads the page a
beat after `domcontentloaded`, and Gmail is still assembling itself
then; a second `browser_open` with no url returned the full mailbox.
Nothing to do with logins or with which browser is driving — it is the
settle floor argued in notes/28, and it is why a prompt aimed at a big
web app should say "re-read the page" out loud.

Which is the whole check, run as a user would run it — a LOCAL model,
`qwen3.8-64k:latest` on Ollama, driving a signed-in Brave with no
cloud provider anywhere in the loop:

```
$ uv run yantra --provider ollama --yolo --prompt \
    "open https://mail.google.com, re-read the page, then tell me \
     whether it shows an inbox or a sign-in screen"

The page is the Gmail mail interface -- the URL is
mail.google.com/mail/u/0/#inbox, there's a "Search all conversations" box,
the main menu, and the account is already signed in. So the persistent
browser profile retained a login session and loaded the inbox directly.
-- end_turn · 3541 in / 162 out · 5 iteration(s)
```

Five iterations, 162 tokens out. A local model is entirely capable of
driving this; nothing here needs a frontier model, because the hard
part was never the reasoning — it was the browser being allowed in.

Continues notes/28.
