"""Who may use the page: a token, once the page listens beyond this computer.

The page drives an agent that can run a shell. On ``127.0.0.1`` only this
computer reaches it, and that was the whole guard. A page on the home
network (``--host 192.168.1.44``, or ``0.0.0.0`` in a container) is
reached by every phone, laptop and television on the Wi-Fi -- and
without a guard, any of them can tell the agent to run anything.

A PAGE THAT LISTENS BEYOND THIS COMPUTER HAS A TOKEN. It is
``$YANTRA_WEB_TOKEN`` when that is set (Sarathi sets it, and hands the
same token to its home page's link), otherwise one made once and kept
in ``.yantra/web.token`` (mode 0600). On 127.0.0.1 there is none unless
``$YANTRA_WEB_TOKEN`` asks for one.

THE LINK CARRIES IT ONCE; A COOKIE AFTER THAT. The address the server
prints is ``http://HOST:PORT/?token=...``. That visit sets a cookie on
the page's own address (HttpOnly, ``SameSite=Lax``, a year long) and
sends the browser on to ``/`` without the token in the bar. Every
request after it -- the page's fetches, its pictures, its downloads, the
websocket -- carries the cookie with no change to the page's code. A
program may send ``Authorization: Bearer TOKEN`` instead. So nobody
remembers a token: a device opens the link once, from the address
printed here or from Sarathi's home page, and keeps working.

LAX, BECAUSE A PAGE ELSEWHERE CAN SEND REQUESTS HERE. A cookie the
browser attached to every request would let another site's page post to
``/api/message``. ``SameSite=Lax`` keeps it off everything another site
starts -- a post, a fetch, a websocket -- except opening the page itself
from a link. Strict would keep it off that too, and a link tapped in a
chat app would land on the page saying it needs its link. No ``GET``
here changes anything, so a link can only show the page.

What is left open: ``/static`` (the page's own code, no secrets), and
``/`` answers a page saying how to get in. Nothing else.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

ENV_TOKEN = "YANTRA_WEB_TOKEN"
COOKIE = "yantra_token"
#: A year: a device that opened the link once keeps working.
COOKIE_AGE = 365 * 24 * 3600
LOOPBACK = ("127.0.0.1", "localhost", "::1")

LOCKED_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Yantra</title>
<style>body{font:16px system-ui,sans-serif;max-width:34em;margin:3em auto;padding:0 16px;
line-height:1.5}code{background:#8882;padding:0 .3em;border-radius:3px}</style></head>
<body><h1>Yantra</h1><p>This page needs its link, the one with <code>?token=</code> in it.
Open Yantra from Sarathi's home page, or use the address <code>yantra --web</code>
printed when it started. This browser remembers it after that.</p></body></html>"""


def wanted(host: str) -> bool:
    """Whether a page on ``host`` needs a token."""
    return bool(os.environ.get(ENV_TOKEN, "").strip()) or host not in LOOPBACK


def page_token(folder: Path | None = None) -> str:
    """``$YANTRA_WEB_TOKEN``, or the one kept in ``.yantra/web.token``
    (made the first time, readable only by you)."""
    given = os.environ.get(ENV_TOKEN, "").strip()
    if given:
        return given
    path = (folder or Path.cwd() / ".yantra") / "web.token"
    if path.exists():
        kept = path.read_text().strip()
        if kept:
            return kept
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(token + "\n")
    return token


def allowed(token: str, *, cookie: str | None, header: str | None) -> bool:
    """Whether a request carries the token, as the cookie or a Bearer header."""
    if cookie and secrets.compare_digest(cookie.encode(), token.encode()):
        return True
    return bool(header) and secrets.compare_digest(header.encode(),
                                                   f"Bearer {token}".encode())
