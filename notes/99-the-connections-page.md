# 99 — The Connections page

[Note 95](95-the-accounts-you-connected.md) gave Yantra the accounts a
person connected through Setu, but only at startup, and only from the
terminal. To connect Gmail you left Yantra, ran `setu connect gmail
--client-file …`, and restarted it. To stop the agent reading your mail,
you ran `setu disconnect` and restarted again. Nothing in the browser
page even said which accounts the agent could reach.

The Connections page closes that gap. A chip in the header opens a panel
of cards. The accounts you connected come first, then the connectors
installed but not connected. From there you can connect an account,
change its access level, and disconnect it, and the agent's tools follow
without a restart.

## Signing in stays Setu's

The obvious design is for Yantra to run the sign-in itself: build
Google's address, catch its reply on `/api/connections/callback`, and
swap the code for a key. That would put Yantra between the person and
their key, and moving keys out of harnesses is the whole point of
Setu. It would also split the sign-in code across two projects, and
they would drift apart.

So the page does not sign in. It **asks Setu to**, with one new mode of
Setu's own command:

```
$ setu connect gmail --as work --level read --json
{"event": "started", "ref": "gmail:work", "level": "read", "level_label": "Read only", "scopes": [...]}
{"event": "url", "url": "https://accounts.google.com/o/oauth2/auth?..."}
{"event": "connected", "ref": "gmail:work", "email": "…", "level": "read", ...}
```

Each line is one JSON object, so Yantra never has to read prose. The
`url` event is Google's own sign-in page. The panel shows it as a
button, and the person signs in there. Google sends its reply back to a
port Setu opened, Setu saves the key, and the last line says so. Yantra
relays what Setu prints and then asks Setu for its report again. It
never holds a code, a key, or the contents of a client file.

## One rule this imposes: the same computer

Google's reply for a desktop app goes to `127.0.0.1`. That is the
computer running Setu, and so the computer running Yantra. A page opened
on a phone would send the person to Google, and Google would send them
back to the phone's own `127.0.0.1`, where nothing is listening. The
sign-in would just hang.

So the server allows a sign-in only when the request comes from the
same computer. From anywhere else, the card shows the command to run
there, and the server refuses with the same words:

```
403  signing in only works from a page on the computer running Yantra --
     there, run: setu connect gmail --as work --level read
```

The refusal was chosen over a workaround on purpose. The workaround is
a web-type OAuth client with a registered public redirect, and that
means Yantra hosting a callback. That's the design the previous section
turned down.

## The client file, remembered once

Until Setu has its own app registered and approved by Google, a sign-in
needs the person's own "Desktop app" OAuth client file. Setu used to
ask for its path on every `setu connect`. A page can't keep asking, so
Setu now remembers the path with `setu config client-file PATH`. It
checks the file when the path is set, and refuses a Web client or a
missing file at that point rather than at a sign-in next week. It keeps
only the path, beside the vault and never inside it. The file holds a
client secret, so it is read at connect time and copied nowhere.

Setu's report now says whether each connector is ready (`ready`,
`not_ready`, and `needs_setup` naming the setting that's missing). The
panel shows one amber box per missing setting, with a field for it, and
after that the Connect buttons turn on. There are two kinds so far: the
Google client file's path, and the address of your Home Assistant, which
is your own server and has no client file at all. You sign in to it on
its own login page, and the page shows that address the same way it
shows Google's. Either goes through `POST /api/connections/setup` to
`setu config`, which checks it. Yantra keeps neither.

## The tools follow, but never under a running turn

`agent.setu` is the session's handle on all of this. Startup is its
first `sync`, and the page calls `sync` again after a sign-in, a
disconnect, or a refresh. `sync` makes the MCP servers match what Setu
reports:

* a new connection gets its server, and its tools are classed by the
  manifest exactly as at startup (note 95: read runs, write asks, spend
  always asks, anything unlisted is never registered);
* a connection that's gone loses its server and every tool it had;
* the `connections` prompt layer is rewritten, so the model's list of
  accounts is never stale.

A sign-in takes as long as the person takes, so it runs on its own
thread, and the chat stays usable meanwhile. If it finishes while a turn
is running, the new tools wait until that turn ends. A turn's tool list
and prompt never change under it. Disconnect and refresh are refused
while a turn runs, the same as every other change to the tool list.

**Disconnect goes through Setu**, which revokes the key at Google
before forgetting it. It keeps a grant that another of your connections
to the same Google account still uses (note 95 explains why). The page
only asks first, in a dialog that says how many tools the agent is
about to lose.

## What the model sees change

Before a sign-in, the `connections` layer lists Gmail as *installed but
not connected*. After one, it lists `gmail-work` with its address and
level. The next message the model reads says so, and the tools are
there.

## Receipt

The real Setu from its repository, driven by headless Chromium through
the page, with an empty scratch vault and a placeholder client file:

```
chip: 0 connected
sign-in host: accounts.google.com | scope: ['https://www.googleapis.com/auth/gmail.readonly'] | redirect: ['http://127.0.0.1:45915/']
after cancel: Sign-in to gmail:personal cancelled — nothing was saved.
```

In order, the page:

1. opened the panel and showed the amber *client file* box, with
   Connect switched off;
2. saved the client file through Setu, after which the box went away
   and Connect switched on;
3. started a sign-in, which showed *Waiting for you to sign in to
   gmail:personal* and an *open the sign-in* button;
4. checked the button's address: Google's own page, asking for exactly
   the Read only scope, with the reply coming back to a port on this
   computer;
5. cancelled, after which Setu's process ended, its port closed, and
   the vault stayed empty.

The panel also rendered as a bottom sheet at phone width, in dark mode.

A full sign-in against Google needs a real Desktop client file, which
this run didn't have. The rest of the loop — sign in, tools appear;
disconnect, tools go — ran against a fake `setu` program that speaks
the same JSON (`tests/test_connections_page.py`). With a real Gmail
account signed in through Setu since, Yantra on `qwen3.8:latest`
searched it through `mcp__gmail-personal__search_threads`, and Gmail's
API answered `200 OK` without the model ever seeing a token
([note 106](106-what-the-recipe-leaves-out.md) has the recipe run).

`2328 passed, 1 skipped` (was 2313). The tests check that:

* a sign-in's events reach the page in order, and the tools follow;
* a page on another device gets the command instead of a sign-in that
  hangs;
* a sign-in that ends mid-turn waits for the turn;
* disconnect revokes through Setu and pulls the tools;
* a level the connector doesn't have is refused;
* no client file means no sign-in until one is set, and a wrong one is
  refused;
* an account name can't be an option;
* cancel saves nothing.

On the Setu side, `tests/test_config.py` covers the remembered path, a
Web client being refused, the order the settings win in, and
`connect --json`, including a failure that prints one error line
instead of a traceback. That's 73 tests, up from 63.

Writing the tests caught one real bug. An MCP server's tool count still
included a tool the manifest had refused. The card would have said 4
tools where the agent had 3.

## What the catalog says, on the card

When Setu keeps a signed catalog (Setu's `setu catalog use`), each card
says who wrote the connector: **by Setu**, **by someone · reviewed by
Setu**, or **sideloaded** (installed, but not listed). It also shows how
many people installed it. Recipes the catalog lists for that connector,
but that aren't on this computer, appear under it as *in the catalog*.
With no catalog the cards carry no label at all. Setu's own Gmail isn't
called sideloaded just because nothing has been published yet.

A version the catalog **withdrew** isn't started. Its card says why in
amber, the start says so too, and the connection stays in Setu so an
update brings it back:

```
homeassistant-mcp | partner priya 3 | forwarded calls without the level check
problems: ['homeassistant-mcp: withdrawn by Setu -- forwarded calls without the level check']
```

That's the panel's own data, from a scratch Setu with a locally signed
index. Change one byte of the index and Setu refuses all of it and keeps
the copy it had.

## What was deliberately not built

* **Signing in from another device.** It needs a web-type OAuth client
  and a public redirect. That belongs to Setu's own registered app, the
  day it exists, and not to Yantra.
* **Turning a site on.** The plan's first switch installs a verified
  connector from a signed index. The index exists now and is checked,
  but installing by hash waits on where the catalog is published, so
  the page shows the connectors that are installed, with the catalog's
  labels.
* **Which agents may use an account.** A package asking for a
  connection is still the open item from note 95. Today every
  connection is available to the person's own chat.
* **Merging MCP servers into this page.** The servers, skills and tools
  panel stays where it is, and a Setu connection's server still shows
  there too. Folding it into Connections would move a panel people
  already use.
