# 122 — The app instead of the browser

[Note 112](112-a-site-with-no-api.md) gave sites with no API a browser
road: the person signs in on a profile, and the agent's site tools keep
to the site's rules. X keeps turning that road away. Its bot checks
answer a driven browser with "temporarily limited your access", even
when a person signed in by hand. X's own phone app isn't treated that
way, and the agent can already work a phone ([note 119](119-a-phone-and-what-asks.md)).

But a phone the agent works by itself is the wrong answer on its own.
Sparsh's rules are the same in every app: a tap on "Post" is held, a tap
on "Like" runs unasked. The person who connected X at *Read only* in
Setu meant nothing to be liked. And on Amazon's app, "Buy now" would be
held, where the browser road never presses it at all.

## Setu says what may be done; Sparsh does it; Yantra joins them

Three pieces, each in its own project:

* **Setu** (its phone road): a site's manifest gains a `[phone]` table,
  with the app's package, the words on its buttons that act, and a
  guide. The spend words and pace are the browser road's.
  `setu connect x --phone` records the app and a level. **IT HOLDS
  NOTHING**: the sign-in is the app's, on the phone.
* **Sparsh** (its per-app rules): `SPARSH_APP_RULES` gives an app
  `refuse` words, `ask` words and a `pace`. A harness can only add to
  the person's rules this way, never loosen them.
* **Yantra** (`setu_link.phone_rules`): Setu's phone connections become
  those rules, handed to Sparsh when its server starts.

The level decides the words:

| level | spend words (buy, pay, delete) | act words (post, like, follow) |
|---|---|---|
| Read only | refused | refused |
| Read and post / act | refused | asked about |

A package's ceiling lowers the level, as for every connection
([note 110](110-which-account-and-who-may-use-it.md)). Two connections on
one app (X personal and work) keep the stricter of each: an app on a
phone is one app, whichever account is signed in to it. The rules are
written for both phones, by Android package and iPhone bundle.

The model is told, in the `connections` layer: *X through its own app on
the person's phone (`com.twitter.android`), with the phone tools: Read
only. … Use the app rather than a browser for X.* The startup line
names the connection, so it isn't mistaken for none at all:

```
setu: texts:personal (on the phone) -- via …/setu
```

## REFUSED, NOT HELD

The first live run found the one thing the tests didn't. Sparsh's
refusal said *"says "send", which is not done here: Setu keeps Texts on
this phone at Read only…"*. `gemma4:26b` read that as a held step and
spent six turns looking for a hold id to confirm:

> Wait, the error message says: `ERROR: Not done: item "Send SMS — SMS"
> says "send", which is not done here...`. It doesn't mention a hold ID.

A held step and a refused one are different promises. A yes gets a held
step through; nothing gets a refused one through except the person
changing the level. So the sentence now says so in capitals: *REFUSED,
NOT HELD: there is no hold and nothing to confirm. Stop and tell the
person.* The next run took three steps.

## Live receipt

The emulator has neither X nor Amazon (both need a Play Store sign-in),
so a stand-in site, "Texts", named Messages as its app in a
`sites/texts.toml` of a scratch Setu. `gemma4:26b` on Ollama, asked
*"Using Texts on my phone, text 555-0123: phone road test"*:

At **Read only**:

```
setu: texts:personal (on the phone) -- via …/setu
│ Not done: item "Send SMS — SMS" says "send", which is not done here: Setu    │
│ … posted, liked, followed or added. REFUSED, NOT HELD: there is no hold and  │
I can't send that text message because the connection to your Texts app is
currently in **Read only** mode, which prevents me from sending messages or
posting information. You would need to change the connection level to allow
me to perform this action.
── end_turn · 8341 in / 1128 out · 3 iteration(s)
```

At **Read and send**, the same request:

```
│ NOT DONE -- this needs the person's yes: tap item "Send SMS — SMS" in        │
│ com.google.android.apps.messaging -- held because it says "send". Call       │
╭─ approve mcp__sparsh__confirm()? ────────────────────────────────────────────╮
│ Do this on the phone?                                                        │
The user denied the request to send the message. I will not proceed with
sending the text.
```

The phone's sent messages afterwards: none with that text, either time.

## What was deliberately not built

* **Setu checking the app is signed in.** The browser road proves a
  sign-in by its cookie. Setu can't see inside an app on a phone, so it
  says to sign in there and records nothing more. The agent finds out
  when it opens the app.
* **A connection made mid-session reaching a running Sparsh.** The rules
  go over when Sparsh's server starts. A phone connection made after
  that applies from the next start. Restarting the phone tools under a
  running turn would renumber its screen.
* **Rules for apps nobody connected.** Without a phone connection, X's
  app is an app like any other, under the person's own Sparsh rules.
  The phone road adds rules; it doesn't make an app forbidden until
  connected.

## What the tests hold

`tests/test_sparsh_link.py::TestTheAppsRules`: Read only refuses acting
and spending, on both phones; the level above asks about acting and
still refuses spending; a package's ceiling lowers the level; two
connections keep the stricter; a withdrawn connector gives no rules; the
model is told to use the app; the rules reach Sparsh's server; the
startup line names the connection. 2725 tests before, 2733 after (1
skipped, as before).

## What is not here yet

* The real X and Amazon apps, on a real phone.
* An iPhone: the rules are written for its bundle ids, untried.
