# 132 — a token for the page

*The web page ([notes/22](22-web-ui.md)) drives an agent that can run a
shell. On `127.0.0.1` that was fine: only this computer reaches it. Put it
on the home network, so a phone or a laptop can open it, and every device
on the Wi-Fi can open it too.*

## What `--host` used to mean

`--host 192.168.1.44`, or `--host 0.0.0.0` inside a container, made the
page reachable from the house. Nothing else changed. The page has no
accounts and no sign-in. It never needed them, because the page's whole
guard was the address it listened on. Moving it took the guard away.
Anyone on the Wi-Fi, a guest's phone or a smart TV, could post to
`/api/message` and the agent would do what it said, with the shell and
the files and the connected accounts. The approvals don't help, because
the same visitor answers them.

## A page that listens beyond this computer has a token

`web/gate.py` decides one thing: whether a request carries the page's
token. It is on whenever the page listens anywhere but `127.0.0.1`,
`localhost` or `::1`, or whenever `$YANTRA_WEB_TOKEN` is set. The token is
`$YANTRA_WEB_TOKEN` if that is set (Sarathi sets it, and gives the same
token to its home page's link). Otherwise Yantra makes one, once, into
`.yantra/web.token`, readable only by you. On `127.0.0.1` with no
variable, nothing changes.

Without the token, every route answers 401. That covers the API, the
websocket and `/`. `/` answers with a short page saying where the link
comes from. The page's own code under `/static` stays open, because it
holds no secrets and a health check needs something to ask for.

## The link carries it once; a cookie after that

The address the server prints is

```
yantra web UI -> http://192.168.1.44:8321/?token=E8HSkYBA…
```

Opening it sets a cookie for the page's own address and sends the
browser on to `/`, so the token leaves the address bar. Every request
after that carries the cookie with no change to the page's code: its
fetches, its pictures, its downloads and the websocket. A program may
send `Authorization: Bearer TOKEN` instead.

The cookie is HttpOnly and lasts a year, so nobody has to remember a
token. Each device opens the link once, from the address printed here or
from Sarathi's home page, and keeps working.

**Lax, not Strict.** A cookie the browser attached to every request
would let another site's page post to `/api/message` from your browser.
`SameSite=Lax` keeps it off anything another site starts (a post, a
fetch, a websocket) except opening the page from a link. Strict keeps it
off that too, and a link tapped in a chat app would then land on the
"needs its link" page every time. No `GET` here changes anything, so a
link from elsewhere can only show the page.

## In a container

The image listens on `0.0.0.0` inside, so it always has a token. The
health check moved from `/` to `/static/favicon.svg`, the one thing
served without a token. `./start.sh container start` reads the token
from the container's log and prints the link with it. The banner is
flushed before the server starts, so the log has it in time.

## Live receipt

The page on the house address, from a scratch folder, asked by its house
name (`qwen3.8:latest` as the model; no turn was needed):

```
$ yantra --web --host 192.168.1.44 --port 8398 --provider ollama --no-setu --no-samay
  yantra web UI -> http://192.168.1.44:8398/?token=E8HSkYBA…

no token, /api/state:    401
no token, /:             401
wrong token link:        401
right token link:        303 -> http://linux-al-ml-beast.local:8398/
cookie: yantra_token httponly: 1
with cookie, /:          200
with cookie, /api/state: {"held":null,"provider":"ollama","model":"qwen3.8:latest",…
-rw------- …/.yantra/web.token
```

## What the tests pin

`tests/test_web_gate.py` plays a stranger first. The API, the websocket
and the page are closed without the token. A wrong token in the link sets
nothing. The page's own code needs no token. Then it plays the owner: the
link once, a 303 to `/`, a cookie that is HttpOnly and Lax, and after
that the page, the API and the websocket all work with no token in sight.
A Bearer header works too. The kept token is made once, with mode 0600,
and `$YANTRA_WEB_TOKEN` outranks it.

## What was deliberately not built

* **Accounts.** One owner, one agent, one token. A page that serves
  several people is Dvara's, and Dvara has its own people.
* **HTTPS.** On the home network the token crosses the Wi-Fi with only
  the Wi-Fi's own encryption, as Setu's streamed window does. For the
  open internet, put the page behind your own HTTPS.
* **A token on `127.0.0.1` by default.** Only this computer reaches it
  there, and a page you start by hand should open as it always has.
