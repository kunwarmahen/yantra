# 59 — One key, both halves

*Continues [notes/58](58-the-browser-you-already-have.md), which taught
`--browse-login` to open a browser sign-in pages would accept. This is
what happened to the login afterwards.*

## The bug, as you meet it

You set a browser you already have, you run `--browse-login`, a real
Chrome window opens, you sign in — 2FA, the lot — and you close the
window. Yantra prints:

```
profile saved -- future browser_* sessions start from these logins
```

Then you ask the agent to open the same site, and it shows you the
sign-in page. You log in again. Same thing. Nothing errors, nothing
warns, and every run looks exactly like a run that worked.

The login was not merely ignored. **It was deleted**, by Yantra, a few
seconds before you were told it was fine.

## Cookies are encrypted, and the key is a launch flag

A browser does not store cookies in the clear. Chromium encrypts each
one before it touches the disk, and where it gets the encryption key is
decided by a command-line flag at startup:

- `--password-store=basic` derives a key from a built-in constant.
- Without that flag, Chromium asks the desktop for its keyring —
  gnome-libsecret on GNOME, kwallet on KDE — and derives the key from a
  secret only your logged-in desktop session can hand out.

You can see which one a profile used without decrypting anything,
because Chromium stamps every stored cookie with a version prefix:
`v10` for the constant-derived key, `v11` for the keyring's.

Now the two halves of Yantra's browser support. `--browse-login`
launches your real Chrome as a plain subprocess with no automation
attached — that is the whole point of notes/58 — so Chrome does what
Chrome does on a desktop and takes the keyring. The agent's sessions go
through Playwright, and Playwright hardcodes `--password-store=basic`
into every browser it starts, because it is built to run on build
servers where no keyring exists.

So the human writes `v11` and the agent arrives holding the key for
`v10`.

## A browser does not skip a cookie it cannot read

This is the part that turns a mismatch into a loss. Chromium does not
keep a cookie it cannot decrypt, on the reasonable grounds that a
cookie it cannot read is a cookie it can never use. It **drops the
row**. So the agent's first launch on that profile does not merely fail
to see your session — it walks the store, fails to decrypt everything
the login wrote, and removes it.

Measured on this machine, same profile, one arm per behaviour:

```
OLD  login writes 47 rows (all v11) -> agent decrypts  0 -> 0 rows remain
NEW  login writes 47 rows (all v10) -> agent decrypts 33 -> 33 rows remain
```

The 47 → 33 in the working arm is ordinary session-cookie eviction —
cookies with no expiry do not survive a browser restart, which is
normal and has nothing to do with encryption. The number that matters
is the middle column: **0 versus 33.** In the old arm the agent could
read none of them, and what it could not read it destroyed.

## The fix: name the key, on both doors

ONE COOKIE KEY, BOTH HALVES. The login subprocess now carries
`--password-store=basic` too, so the browser the human signs into and
the browser the agent drives derive the same key from the same
constant. One line in the argv, and the login survives.

The Playwright side already passed the flag by default, but it now
passes it **explicitly** rather than inheriting it. That is not
redundancy for its own sake: an upstream default that changed quietly
in a Playwright release would take every stored login on every machine
with it, silently, in the same undebuggable way. A flag this
load-bearing belongs somewhere you can grep for it — `_COOKIE_KEY_ARG`
in `tools/browser.py`, referenced by both doors.

## The tradeoff, named

Cookies in an agent profile are now encrypted with a key derived from a
published constant rather than one your desktop keyring guards. That is
genuinely weaker at rest, and it is the price.

It is worth paying because the alternative — teaching the agent side to
use the keyring — makes persistence depend on an unlocked desktop
session. A server has no keyring. A container has no keyring. A machine
you SSH into has a keyring you have not unlocked. Choosing the keyring
would mean the feature works on a laptop and silently fails everywhere
else, which is the failure mode this note exists to remove, wearing a
different hat.

The honest mitigation is to say what the profile is: a directory that
holds live logins, worth the same care as an SSH key, and worth keeping
out of a git repo. That advice was already in `.env.example`; it is
load-bearing now.

## "Saved" has to be able to be wrong

The green line was not lying on purpose. It printed because the check
behind it asked whether the profile directory *looked like* a profile:

```python
def _profile_has_state(profile: Path) -> bool:
    return (profile / "Local State").exists() or (profile / "Default").is_dir()
```

That is true the instant any browser touches the directory, and stays
true forever after. It cannot distinguish a login that worked from a
login that saved nothing, because it is not asking about logins at all.

So `--browse-login` now counts the thing a later session actually
rides:

```
profile saved -- 46 cookies; future browser_* sessions start from these logins
```

and, when there is nothing to ride:

```
nothing was saved -- the profile holds no cookies, so the agent
will meet the same wall you just beat. Sign in FULLY, then close the
window (closing it is what writes the session to disk).
```

with a non-zero exit code, so a script notices too.

**The count is a row count, never a value.** `_cookie_count` opens the
store read-only and asks sqlite how many rows it has. It does not
decrypt, and it could not print a session token if it wanted to — which
is the same rule the rest of this family follows: notes/28 refused to
build a get-cookies verb, because raw session tokens in model context
are one prompt injection away from being exfiltrated. A number is not a
token.

An unreadable store — missing, locked, a schema a future Chromium
changes — counts as zero rather than raising. The count is a receipt
for a human, not control flow, and a working login is not worth failing
over a failed head-count.

## What was deliberately not built

**No cookie migration.** Yantra could, in principle, decrypt a `v11`
store using the keyring and rewrite it as `v10`. It will not. That
means holding your decrypted session cookies in Yantra's own memory and
writing them back, which is precisely the handling this family has
refused from the start — and it would earn a one-time convenience at
the cost of a permanent capability nobody asked for. Signing in again
takes a minute.

**No keyring detection.** A version that sniffed for gnome-libsecret
and matched it would work on the machine it was written on and produce
a *different* profile format per desktop environment, which is a bug
generator with a long tail. One key everywhere is duller and right.

~~**Ctrl-C is still not a clean exit.**~~ — fixed in
[notes/61](61-the-signal-that-saves.md), which found the kill was
really a race, and that the polite signal is the wrong one. Was:
`--browse-login` runs the browser
in the terminal's foreground process group, so Ctrl-C reaches Chrome
directly and kills it mid-flight, and a session it had not yet written
is lost. The cancel message no longer claims otherwise, but the honest
fix — giving the child its own process group and asking it to shut down
properly — is not in this change.

## Receipts

The login half, run as a user runs it — sign in, close the window:

```
$ uv run yantra --browse-login https://www.bing.com
login setup · profile /tmp/yzsmoke
/usr/bin/google-chrome is opening at https://www.bing.com -- log in yourself
(2FA and captchas are yours to beat), then CLOSE THE WINDOW. ...

profile saved -- 46 cookies; future browser_* sessions start from these logins
```

That last line is the whole of the second fix: before, it said `profile
saved` whether the number was 46 or zero.

The agent half, driven by a local model — `qwen3.8:latest` on Ollama,
with no cloud provider anywhere in the loop:

```
$ uv run yantra --provider ollama --yolo --prompt \
    "open https://www.bing.com and tell me the page title"

→ browser_open()
  title: Search - Microsoft Bing
  source: https://www.bing.com/
  ...
  Welcome back

The page title is **"Search - Microsoft Bing"**.
-- end_turn · 3042 in / 77 out · 2 iteration(s)
```

Two iterations, 77 tokens out — and "Welcome back" in the page text,
which is the site recognising a profile that still has its cookies.
That greeting is the whole of this note: before the fix, the profile
arrived at every site as a stranger, every time.

`1623 passed, 1 skipped` (was 1612).

Continues [notes/58](58-the-browser-you-already-have.md) and
[notes/28](28-browser-tools.md).
