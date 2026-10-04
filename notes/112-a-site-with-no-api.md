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

Signed in for real (amazon.com and X, both at the write level, each
through one `setu connect` window), the same tools read the real account
on both sites.

## A site of your own

Nothing above is written for Amazon or X in particular. The tools, the
guards, the handoff and the pace all read the manifest, and the manifest
is a file of rules with no code in it. So a site can also be added by
hand: write its manifest into Setu's `sites/` folder
(`~/.local/state/setu/sites/example.toml`; Setu's README has an
example), and `setu connect example` works as it does for Amazon. The
Connections page marks such a card **added on this computer**.

Only sites reached through the browser can be added this way. A file
that names a program is refused, because otherwise dropping a file would
be enough to run one. If an installed connector has the same name, the
installed one is used. A file Setu can't read is skipped and reported,
and the rest still load.

A hand-written file needs the name of the cookie that means "signed
in", and finding it takes a browser's developer tools. So Setu can also
write the file itself:

```
setu connect --site news.ycombinator.com --as personal
```

or **Another site?** at the foot of the Connections page. Setu first
visits the site twice with no one signed in, on a throwaway profile,
and notes which cookies any visitor gets. Then the usual window opens.
When the person closes it, Setu loads the site's front page once more,
on the new profile, and reads it the way a person would. A sign-out
link with no password box means signed in. A password box or a "Sign
in" link means not, and nothing is saved: no file, no profile. A page
that shows neither goes to the person, who has just closed the window:
*did you sign in?* On the page that is a yes/no in the sign-in box.

### Why the page, not the cookies

Before building this, I tried both on real sites. The cookies that
appear after a sign-in do include the real one: from X's sign-in,
`auth_token` was one of two session-like names, and Amazon's `at-main`
turned up too. But the real one never came alone. X's came with
`__cuid`, and a second visit to eBay, still signed out, added nine more
cookie names. Since a manifest's rule is "signed in if *any* of these
is set", saving that list would let a window closed without signing in
pass. So the cookie names are kept in the file as evidence, and the page
decides.

On twelve real sites visited signed out, the page check never once said
"signed in". It gave the right answer on seven. The rest came back
*unknown*: X shows a headless browser an empty page, Amazon, Instagram
and eBay put up robot checks, and Reddit and YouTube draw their sign-in
button where a page dump can't see it. *Unknown* is exactly when the
person is asked. A test site that keeps its sign-in only in the
browser's storage, with no cookie at all, was proved by its page
alone. The page's own scripts have to be stripped first: on that site,
the script contained both "Log in" and "Log out" as text.

### The rules it writes

Nobody has read this site's pages, so the rules are fixed and cautious,
not guessed:

- Read only to start, at two seconds a page and ten actions a session;
- a real window on an unseen screen, the setting more sites accept;
- no spending pages, since none are known;
- a long list of button words that spend or can't be undone (buy, pay,
  checkout, subscribe, delete, transfer, withdraw…).

A site whose address or title reads like a bank or a payment service is
connected Read only, whatever was asked: with no spending pages known,
only words on buttons would stand guard. Editing the file is the
deliberate way past that. Signing in again never rewrites the file, so
a person's edits stay. An address Setu already has a connector for
(`amazon.in`) is pointed at that connector instead.

### The guide grows from use

A site added by its address starts with no guide, so the first time the
agent clicks its way around: home, then "Your account", then "Your
orders". Then the next conversation does the same again.

So after a turn that used such a site's tools at least twice, the look
that runs behind the answer ([note 113](113-after-the-answer.md)) asks
the model one more short question: from the pages this turn reached,
what would a guide to this site say? It sees each call and where it
landed (the address and the page's title), never the pages' text. A
guide says where things are, and a page's words are the site's, not
advice to follow. Whatever it writes waits in the **to keep** tray as
an editable box. The terminal asks right after the turn instead, with
*[k]eep / [e]dit first / [N]o*. On **keep**, Setu saves it (`setu site guide ID --set`)
and the connections layer carries it from the next turn. Only sites
added on this computer are looked at; an installed connector's guide
belongs to its author.

The first try wrote a guide full of `ref e1`. Those are numbers for one
page's links and change on every visit, and the model had seen only
page titles, not where each link led. Showing it the address each call
landed on fixed that. On `qwen3.8:latest`, against a small local shop
connected with `--site`:

```
> What is the status of my orders on the Tea & Kettles shop (teashop)?
  → teashop_open, teashop_follow, teashop_follow …        (123 s)
to keep: a guide to Teashop (from 3 pages)
  Your orders: /account/orders
  Order detail: /account/orders/{order_id} (e.g. /account/orders/1042)
  Order list page has links to individual orders
```

Kept, then a new conversation:

```
> On teashop, which of my orders has not been delivered yet?
  → teashop_open(url="/account/orders")                  (one call)
Your undelivered order is Order 1043 – Tea sampler, expected 5 Oct.
```

The first version of that second run found a real bug. The model
opened `/account/orders`, a bare path, and the site tools refused it as
"not Teashop", because a path names no host. It then guessed full
addresses and lost the shop's port. Now `open` takes a path and resolves
it against the connection's home, port included. Guides write paths, so
the tools have to read them.

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
