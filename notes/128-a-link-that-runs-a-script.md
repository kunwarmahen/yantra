# 128 — a link that runs a script

*[Note 112](112-a-site-with-no-api.md) turned a signed-in website into
tools that read without pressing anything, and
[note 110](110-which-account-and-who-may-use-it.md) gave a package only
the accounts its `[connections] needs` names. This note is about one
connected account, Yahoo Mail, that failed both ways at once: the
agent behind a chat couldn't see it, and once it could, it couldn't
open a single message.*

## The problem, as you would meet it

Yahoo was connected in Setu, Read only, and it worked on Yantra's page.
Asked the same thing from Telegram, through Dvara, the agent went to
the person's phone instead and came back saying Yahoo wasn't signed in
there.

Two separate things were wrong.

**The package never asked for Yahoo.** Dvara gives an agent only what
its package needs *and* the person has. The household's `minder`
package needed `amazon:read`, `x:read` and `homeassistant:write`.
Yahoo wasn't on the list, so the agent got no Yahoo tools and was told
nothing about Yahoo. Yantra's own page is the person's own session, so
it sees every connection, and that's why the same question worked
there. With no tool and no word about the account, the model did what
a helpful model does: it found another way in. The phone was the only
one left.

**Reading Yahoo means pressing.** Yahoo Mail's message list is a column
of `<div role="link">` with no `href`. The snapshot calls each row a
link, `follow` looks for the address to load, finds none, and refuses:

```
e3 has no address to load (a link that runs a script); it needs click, which is asked about
```

`click` exists only at the write level. A Read only Yahoo connection
could list the inbox and never open a message in it.

## Every account, said once

A package can now ask for every account its person has connected:

```toml
[connections]
needs = ["*:read", "homeassistant:write"]
```

`*` is every connector, now and later, at that level. A connector named
on its own keeps the higher of the two, so the line above reads
everything and still switches lights. It is a need like any other:
Yantra asks once ("minder wants to read your connected accounts, and
any you connect later. Allow?"), and Dvara gives it within the person's
own Setu folder and nobody else's.

This loosens nothing for an agent somebody else wrote. A star is the
package asking, in its own `agent.toml`, where the person can read it.
It suits a household's own agent, which should read what its person
reads. A package from elsewhere should still name what it uses.

## Named, not given

The quieter fix matters more. When a package asked for some accounts
but not this one, the agent is now told:

```
The person also has Yahoo (`yahoo:personal`) connected, but this agent was
not given it: its package's [connections] needs does not name it ("*:read"
there names every account). If asked, say so; do not reach it another way,
such as the phone or a browser.
```

A missing tool is no longer read as a missing account. The answer
becomes "minder wasn't given Yahoo", which the owner can fix in one
line, instead of a trip to the phone.

A package that asks for *no* accounts still hears of none. Naming the
person's accounts to an agent that never asked would tell a stranger's
package which banks and inboxes they use.

## When a link is a script

The rule that reading presses nothing is the reason the site tools run
without asking. It still holds by default. A site's own file can now
claim the exception:

```toml
[browser]
script_links = true
```

There, `follow` on a link with no address presses the link, and
reading Yahoo works. The press is narrow:

* **A link, never a button.** `follow` on a button is refused as before.
* **The spending rules still apply.** On a spending page nothing is
  pressed, and a link whose words spend (`Buy now`) is refused.
* **A short label that changes things is refused.** Six words or fewer
  is a control's name, checked for *delete, send, archive, move, mark,
  reply, post…*. Following such a link isn't reading; it needs `click`,
  which is asked about.
* **A long label is content.** A mail row carries its preview, and one
  in this inbox reads "You sent money · You sent $165.00 to…".
  Checking every word would refuse half the inbox for the words in it.
* **Off the site is taken back**, as after any click.

Opening a message marks it read, in Yahoo as in any webmail. That is a
change to the mailbox, but it's the same one a person makes by
reading. A `follow` that loads an address does the same thing.

The switch is off unless the site file sets it, because the guarantee
it relaxes is the one that lets these tools skip the question. When it
is off, the refusal says where the switch lives, so the owner can find
it:

```
e3 has no address to load (a link that runs a script); it needs click, which is asked about. (A site whose links are all like this can let reading press them: script_links = true in its Setu site file)
```

## Receipt

Run against the real Yahoo inbox (`/n/inbox/priority`), on a throwaway
copy of the connection's profile. The same row, first without the
switch, then with it:

```
script_links=False: follow e3: [e3] link Credit Karma Did you make 8 new transactions?·Monitor
  refused: e3 has no address to load (a link that runs a script); it needs click, which is asked about. (A site whose links are all like this can let reading press them: script_links = true in its Setu site file)
script_links=True: follow e3: [e3] link Credit Karma Did you make 8 new transactions?·Monitor
  -> https://mail.yahoo.com/n/list/folders=00_1&listFilter=PRIORITY/messages/00_127634?listFilter=PRIORITY
```

The page `follow` returned held the message itself ("Monitoring your
transactions can pay off in the long run… Reviewing your transactions
helps you catch anything unexpected…"), not the list.

## Not a connector of its own, yet

Yahoo Mail also speaks IMAP, with an app password made on Yahoo's
account page. A connector on that road would search the whole mailbox
on the server and never need a browser. The browser road reads what
the inbox shows, a screen at a time. For "anything from the bank this
week?" that is enough, and it needed only one line in a file. A
dedicated connector is worth building when someone wants search across
years of mail, or a server with no Chrome on it.

## What is not here yet

- **Script links found by Setu.** `setu connect --site` writes cautious
  defaults and leaves `script_links` off; it could notice a page full of
  `role="link"` rows with no address and suggest it.
- **A Yahoo IMAP connector**, as above.
