# 112 — A site with no API

Setu connects your accounts by signing in to the site's API: Gmail's,
Home Assistant's ([note 95](95-the-accounts-you-connected.md)). Some of
the sites people use most give a person no API at all. Amazon has APIs
for sellers and for affiliates, and neither one sees *your* orders. X
has an API, but reading through it costs money every month. For sites
like these, the only way in is the website itself.

Yantra already had a browser ([note 28](28-browser-tools.md)), and
`--browse-login` already kept a sign-in in a profile. Pointing the plain
`browser_*` tools at Amazon would almost work, and that "almost" is the
problem. A browser can't tell reading from buying. "Track my package"
and "Buy now" are both a click. A tool set that may click anything is
either asked about on every page turn (useless for reading) or not
asked about at all (and then one click spends money). And one profile
signed in to everything means a page on one site can ride another
site's sign-in.

So a site with no API becomes a Setu connector of its own kind, the
**browser road**, and Yantra gives each such connection a small set of
tools that keep to that one site.

## Signing in: your own browser, a profile of its own

```
setu connect amazon --as personal
```

opens a window of your own Chrome (or Chromium, Brave, Edge) on Amazon's
sign-in page. It runs as an ordinary program, with nothing driving it,
so a site checking for robots sees none. The window uses a fresh
**profile**, a directory where the browser keeps cookies, made for this
one connection (`~/.local/state/setu/profiles/amazon-personal/`). Sign
in, close the window, and you're done.

Setu then checks that you actually signed in. Closing the window before
signing in still leaves cookies behind, so counting cookies would prove
nothing. Instead, each site's manifest names the cookies that exist only
after a sign-in (`at-*` on Amazon, `auth_token` on X). Setu looks for
those names. It reads names only, never values, which are encrypted
anyway. If none is there, nothing is saved.

Amazon has a store per country, and one connector covers them all. The
store whose sign-in cookie turns up becomes the connection's home, so a
person who signed in to amazon.in has the agent start at amazon.in.

The connection records which browser signed in. Yantra opens the
profile with that same browser, because Chromium refuses a profile
written by a newer version of itself. Both sides also pass the same
cookie-key flag (`--password-store=basic`), because a browser holding
the other key *deletes* cookies it cannot read.

## The tools: reading presses nothing

For `amazon:personal` at *Read only*, the model gets:

| tool | what it does |
|---|---|
| `amazon_open(url?)` | a page of Amazon, signed in; no address means the home store |
| `amazon_follow(ref)` | go to a link by its `[eN]` ref |
| `amazon_scroll()` | one screen down, showing only what is new |
| `amazon_search(ref, text)` | type into a search box and press Enter |
| `amazon_handoff(mode)` | give the page to you |

None of these can press anything, and that is why they may run without
asking:

* **`follow` loads the link's address. It does not click.** No script on
  the page runs for it, so a "link" that is really a buy button gets
  nothing. A link with no address of its own is refused and pointed at
  `click`.
* **`search` types only into a search box**, meaning an input whose type,
  role, label or surrounding form says *search*, and never one inside a
  form that posts. That matters more than it sounds: on X, Enter in the
  message box *sends* the message.
* **`open` and `follow` stay on the site.** An address outside the
  manifest's hosts is refused. Another site is `browser_open`'s job,
  without this sign-in.

At the write level (*Read and act* on Amazon, *Read and post* on X), two
more tools appear: `click` and `fill`. They are asked about every time,
and the approval names the button and the page:

```
approve x_click?   X: click e14 ('Post') on https://x.com/home
```

## Spending is never the agent's

Nothing on the browser road spends money, at any level. There is no
spend tool to approve; those pages go to you instead:

* **On a spending page, nothing is pressed.** The manifest lists them
  (Amazon's `/gp/buy/*`, `/checkout/*`, returns, order edits), and any
  click there is refused.
* **A spending button is refused anywhere.** The manifest also lists
  words: "buy now", "place your order", "cancel order", "subscribe", and
  on X "delete" and "deactivate", because those can't be undone either.
* **Passwords and card numbers are never typed.** A password field, or a
  field asking for a card number, CVV or one-time code, is refused.

Every refusal tells the model what to do instead: hand the page over
with `handoff`. *finish* opens the page for you to complete; *return*
gives you a window on the connection's own profile, for when you were
signed out, and the agent carries on once you close it.

## A person's pace

X's rules forbid automated access outside its API, and X locks accounts
it takes for bots. That would be *your* account, so the X manifest asks
for three things:

* **Page loads at least 3 seconds apart** (`pace`). Amazon's is 1 second.
* **At most 10 actions a session** (`max_actions`) before the tools stop
  and say so.
* **A real window** (`headed`). X answers a headless browser with an
  error page. A site that asks for this gets a real browser window on an
  invisible screen (Xvfb), never a window popping up on yours
  mid-answer.

## Small things a real page needed

* **Content before menus.** The browser lists at most 60 elements per
  page. Amazon's header alone has more links than that, so the order
  links never made the list. Site tools list a page's content first and
  its header, menus and footer after.
* **Scrolling shows what's new.** After `scroll`, the text starts where
  the screen starts, and elements above the screen are dropped.
* **Pages that build themselves.** X's page is empty when the load event
  fires. A site session waits up to 8 seconds for text to appear.
* **Signed out is said.** If a page turns out to be the site's sign-in
  page, the snapshot says so: don't type a password, hand it over, or
  run `setu connect` again.

## In the session

Setu's status report carries a browser connection with no `mcp` (there
is no server to run) and a `browser` block instead: the profile, the
browser that wrote it, and the home address. The connector card carries
the manifest's rules. `Setu.sync` builds one `SiteSession` per
connection and registers its tools. A level changed in Setu rebuilds
them, a disconnect removes them, and a package gets them only through
`[connections] needs = ["amazon:read"]`, with the level as a ceiling,
as for every other connector ([note 110](110-which-account-and-who-may-use-it.md)).

The connections prompt layer names the tools' prefix and gives the
manifest's short guide to the site, with the person's own store filled
in:

```
- Amazon through the person's own signed-in browser: tools `amazon_*`
  (not `mcp__`): Read only. Buying, paying and what cannot be undone are
  never yours: hand that page over with `amazon_handoff`.
  Your orders: https://www.amazon.in/gp/css/order-history -- add
  ?timeFilter=months-3 for the last three months, ...
```

## Receipt

The guards, against a small local shop page in real Chrome:

```
OK      search box: title: Results
REFUSED search into message box: e6 is not a search box -- search types into those only.
REFUSED follow offsite: https://evil.example/ is not Shop (localhost); these tools keep to Shop
REFUSED buy now: e5 ('Buy Now') is a 'buy now' button -- buying, paying and what cannot be
        undone are the person's to press. Hand the page over with handoff mode='finish'
REFUSED password: e7 asks for a password or payment details -- never typed by the agent.
OK      add to cart: title: added
REFUSED click on checkout page: this page (/checkout/review.html) is where Shop spends money
        or does what cannot be undone -- nothing here is yours to press.
```

And a whole turn on `qwen3.8:latest`, with an X connection whose
profile was signed out on purpose:

```
setu: x (5 tool(s)) -- via .../setu
· thinking
The person wants to know what's new on their X timeline. Let me read their home timeline.
→ x_open()
  (this is X's sign-in page: the person is signed out. Do not type a password. Hand the
  page to them with handoff mode='return' to sign in again, or tell them to run
  `setu connect` for this site)
  source: https://x.com/i/jf/onboarding/web?redirect_after_login=%2Fhome&mode=login
You're signed out of X on the connected browser, so I can't read your timeline. Let me
hand you the login page for a moment — sign in, close the window, and I'll carry on.
→ x_handoff()
```

The model found the site's tools by itself, read the signed-out note,
didn't touch the password box, and reached for the handoff.

## What was deliberately not built

* **No spend level.** A browser-road manifest offers *read* and *write*
  only, and Setu refuses one that names more. A website has no
  permission a button press can be checked against, so "the agent may
  buy" would rest entirely on matching words on a page. Buying is
  handed over every time.
* **Not an MCP server.** Each site could have been a Playwright server
  that Setu runs, which would also work in other MCP clients. The
  browser tools already exist in Yantra, with their snapshot, refs,
  handoff and invisible screen, and a second copy in Setu would drift
  from the first. The cost is that other harnesses must build their own
  site tools from Setu's report.
* **No stealth.** A real browser, a real window, at a person's pace, is
  all of it. A site that still refuses ([note 28](28-browser-tools.md))
  gets the honest snapshot of its robot check.
* **No proxy holding the profile to the site's own domains.** The tools
  refuse other addresses, but the page's own scripts still reach
  wherever they reach. Limiting the browser itself to the site's domains
  needs a forwarding proxy and per-site lists of the domains its images
  and scripts come from. That can come later.
* **One set of tools per account.** Two Amazon accounts get `amazon_personal_*`
  and `amazon_work_*`, not one merged set with an `account` argument.
  Each is a separate browser, and merging them would mean holding two
  browsers open to answer one question.
