# 127 — a sign-in that ran out

*[Note 95](95-the-accounts-you-connected.md) gave Yantra the person's
accounts through Setu, and [note 99](99-the-connections-page.md) let the
page sign in to one. This note is about the day one of those sign-ins
stops working: what the person sees, where it is written down, and how
it comes back.*

## The problem, as you would meet it

Gmail worked on Monday. On the following Tuesday, Yantra's page in its
container started with:

```
setu: gmail:mine unavailable: mcp server 'gmail-mine' exited unexpectedly (code 2) during 'initialize'
setu: gmail:personal unavailable: mcp server 'gmail-personal' exited unexpectedly (code 2) during 'initialize'
```

It looked like a container problem. It wasn't. Google expires the
sign-ins of an OAuth app in **Testing** after seven days, and both
connections had passed it. The connector said so on its stderr:

```
setu-gmail: gmail:mine could not refresh (refresh refused: HTTP 400 Token has been expired or revoked.). Google may have expired it -- apps in Testing mode lose their sign-in after 7 days -- run `setu connect gmail --as mine` again
```

Three things went wrong around that one line. Yantra showed the exit
code and dropped the reason. `setu list` showed nothing amiss, because
the refusal happened inside a connector and was written nowhere. And
fixing it meant restarting the containers: the page connected Setu's
accounts once, at start-up, and never looked again.

## Where the refusal is written

**SETU MARKS THE CONNECTION.** When Google *answers* a refresh with a
refusal (an HTTP 4xx, `invalid_grant`), Setu writes `expired: {since,
why}` on the connection. Google being *unreachable* is not a refusal and
marks nothing: a network blip is not a dead sign-in. A refresh that works
again removes the mark, and so does signing in again, which writes a new
record. Home Assistant's refusals are marked the same way.

The mark is shown where people look:

```
$ setu list
gmail:mine     kunwarmahen@gmail.com   Read, draft and send  last used 2026-10-10T18:01:23Z
               SIGN-IN EXPIRED since 2026-10-11 19:36 -- refused: HTTP 400 Token has been expired or revoked; sign in again: setu connect gmail --as mine
```

`setu status --json` carries it as `"expired"`, Setu's page shows it on
the connection's card, and `sarathi status` ends Setu's line with
`SIGN-IN EXPIRED, sign in again: setu connect gmail --as mine`.

## What Yantra does with it

**AN EXPIRED CONNECTION IS NAMED, NOT STARTED,** the way a locked one
already was. A connector that can only fail is a startup error, not a set
of tools. The startup line says `sign-in expired, not started:
gmail:mine, gmail:personal`, and the `connections` prompt layer tells the
model:

```
Connected, but the site refused the sign-in, so not usable until the person signs in again (tell them so, with the command; you cannot do it):
- Gmail `gmail:mine`: `setu connect gmail --as mine`
```

So *"anything new in my mail?"* gets the command to run, not a tool call
that fails. Dvara builds its turns from the same report, so a person
behind it hears the same thing on Telegram.

**THE PAGE CATCHES UP BEFORE EACH TURN,** as the terminal already did.
Only the times of Setu's files are read; when one moved (a sign-in made
in a terminal, a mark written, a level changed), Setu is asked again and
the tools are made to match. A connection that expires mid-session leaves
on the next turn, and one signed in to again comes back on the next turn.
Nothing is restarted, in a container or out.

**A SERVER THAT DIES SAYS WHY.** An MCP server's stderr still passes
through to Yantra's own, but a thread drains it and keeps the last few
lines. A server that exits before it answers is reported with the last
one: `exited unexpectedly (code 2) during 'initialize': … run setu
connect gmail --as mine`. That holds for every MCP server, not only
Setu's.

## Receipt

Against this machine's two real, really expired Gmail connections. One
refresh each through `setu run` (as a connector makes it) wrote the mark,
and `setu list` showed both as `SIGN-IN EXPIRED since 2026-10-11 19:36`
with their commands. `sarathi status` ended Setu's line the same way.
Yantra, reading that Setu, listed both as expired, not started, and its
prompt layer gave the model both commands. In the tests, a fake Setu
marks `gmail:personal` and the page's next turn has no Gmail tools and the
command in its prompt; clearing the mark brings the tools back on the
turn after, with nothing restarted.

**THE FIRST LOOK IS A LOOK.** The first real run found a hole. Yantra
started at 15:47 with Gmail expired; the person signed in again at 15:53;
the next turn still said the sign-in was refused. Nothing had recorded
Setu's file times when the report was first read, so the first check took
the times *after* the renewal as its starting point and saw no change.
Now the times are taken the moment Yantra first reads Setu, before any
connector starts, and a handle that was never stamped counts as moved: a
look too many costs one report, a look too few costs the account. The
test that missed it had made one check before changing anything; the
new one doesn't.

## The cure, which is not code

The seven days are Google's rule for apps in Testing. Publishing the app
(OAuth consent screen, **Publish app**, *In production*) ends it. For
one's own use that needs no verification, only a click past Google's
"unverified app" warning when signing in. Setu's README now says that
in place of "leave the app in Testing".

## What is not here yet

* ~~**A look before it's needed.**~~ `setu check` refreshes every
  sign-in that can run out, now, and marks the refused ones (Setu's
  README); a refusal keeps the time it was first seen.
* ~~**Telling the person unasked.**~~ Dvara runs that check daily and
  tells each new refusal once to whoever can fix it, with the words that
  do (Dvara's note 39).
