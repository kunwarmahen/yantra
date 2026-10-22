# 136 — A sign-in from another device

[Note 99](99-the-connections-page.md) let the Connections panel sign in
to an account, with one rule: only from a page on the computer running
Yantra. Google sends its answer back to `127.0.0.1`, and on a phone that
address is the phone itself, where nothing is listening. So a page on
any other device was given the command to type at the computer instead.

Then every page started opening by the house's name
(`http://beast.local:8321/` for Yantra's), on the computer too. To Yantra, a request
over that name doesn't come from `127.0.0.1`, so it counts as another
device. The **Another site?** box at the foot of the panel lost its
address field and its sign-in button and showed only
`setu connect --site example.com --as personal`. Every connect button
was greyed out. Setu's own page refused the same way: *adding a site
needs you at the computer running Setu*.

## The window comes to the person

Setu already had an answer for people who aren't at the computer: the
**streamed window**. Chrome runs on the computer running Setu, and a
small page shows a live picture of it on the person's phone and sends
back their taps and typing. Amazon and X are signed in to that way from
Telegram, and so is Google's page. Because that browser runs on the
computer, Google's return to `127.0.0.1` lands where Setu is listening.

The panel now uses that window. A sign-in started from a page on
another device runs `setu connect … --json --remote`. Setu's `link`
event, the window's address, shows in the panel as **open the sign-in
window**. A connector's card does the same: its *connect* button works
again and opens the window.

One condition remains: the window's link has to open on that device.
Setu's window says where it listens (`SETU_WINDOW_HOST`) and what its
links say (`SETU_WINDOW_URL`, `SETU_WINDOW_NAME`). If all of that is
only `127.0.0.1`, the link would point the phone at itself, so the
panel still shows the command, and the server refuses with it:

```
403  signing in from another device needs Setu's streamed window to have
     an address it can open (SETU_WINDOW_HOST, _URL or _NAME); on the
     computer running Yantra, run: setu connect --site example.com --as personal
```

The panel's data says which case applies (`"window": true`), using the
same test Setu applies to itself.

## A new site has nothing to wait for

The streamed window ends by itself when the site's sign-in cookie
appears: Amazon's `at-main`, X's `auth_token`. A site you add by its
address has no such name yet. Setu learns the names afterwards, from
the cookies that weren't there for a signed-out visitor. That is why a
new site used to need someone at the computer.

So for a new site the window ends on the person's word. The window
always had an **I've signed in** button. After it is pressed, Setu reads
the site's page as it does when a window at the computer closes. A
sign-out link means you're in; a password box means you're not, and
nothing is saved. Anything else comes to the panel as a question, *Did
you sign in?*, with yes and no. The proof didn't move; only the window
did.

The tradeoff: a window that ends on a button can end early. Pressed
before signing in, it saves nothing, because the page still shows a
sign-in. Pressed on a page that shows neither, it asks. A yes there is
taken at the person's word, as it is at the computer.

## Sarathi gives Yantra's page its own window

The panel can only stream what Setu's window can show. Under Sarathi,
Yantra's page gets a window of its own:

- **Containers:** port 8792, after Dvara's 8790 and Setu's page's 8791.
  With `[house]` it is published on the house address, its links say
  the house's name, and it is in the firewall line. Without a house it
  is on `127.0.0.1`, for you alone. In a container even your own
  browser comes in through Podman's bridge, so the page counts it as
  another device too.
- **Plain programs:** with `[house]`, any free port on the house
  address. Without a house the page runs on this computer itself, and
  a browser here is local.

## Live receipt

A scratch Yantra on `qwen3.8:latest`, with a scratch Setu folder and
`SETU_WINDOW_HOST=192.168.1.44`. The page was opened at
`http://192.168.1.44:8805/`, which is this computer, but not
`127.0.0.1`. A script played both the page and the phone, against real
Chrome on Xvfb:

```
from 192.168.1.44: local = False  window = True
add-site: 200 {'ok': True, 'ref': 'example.org:personal'}
panel shows: {'event': 'link', 'url': 'http://192.168.1.44:33663/w/e4AD…'}
  phone opens: 200
  phone presses I've signed in: 200
panel asks: Setu could not tell from Example's page whether you are signed in (som ...
  answer: 200
end: {'ref': 'example:personal', 'running': False, 'event': 'connected',
      'email': 'example.com', …}
```

The last line shows a mistake the run turned up. `example.org` was
connected as `example.com`. Both addresses are named "example", and
example.com had been added a minute earlier. Setu took the name for the
site and signed in to the old one again. Setu now refuses a different
address that only shares a name, and asks for `--id`. Setu's own runs,
`setu connect --site example.com --json --remote` and then
`setu connect example --json --remote`, each connected through the
window the same way.

## What was not built

* **The window on Yantra's own address.** Setu runs the window, and
  Setu's settings say where it listens. Yantra could have proxied it
  through its own port, but then Yantra would carry the frames and the
  typing of someone else's sign-in. It only passes on the link.
* **Setu's page in the panel from another device.** Yantra starts its
  copy of Setu's page on `127.0.0.1`, which a phone can't reach
  ([note 135](135-setus-own-page-in-the-panel.md)). There, the panel
  is still Yantra's own cards, now with sign-ins that work.
