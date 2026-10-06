"""A signed-in website as a set of tools: Setu's browser road, kept to its site.

Some sites give a person no way in but the website -- Amazon's orders,
X's timeline. Setu signs the person in there by hand, in a window of
their own browser, on a profile kept for that one connection; this
module turns that profile into tools the model can use (``amazon_open``,
``x_scroll``), one set per connection.

They are the browser tools' own machinery -- the same snapshot, refs and
handoff -- on a session that keeps to rules the connector's manifest
wrote down, because a generic browser cannot tell reading from buying:

* ITS OWN PROFILE, ITS OWN BROWSER. Each connection's session opens that
  connection's profile with the browser that signed in to it (a newer
  browser's profile is refused by an older one), never the profile the
  plain ``browser_*`` tools use.
* ONLY ITS SITE. ``open`` and ``follow`` refuse an address outside the
  manifest's hosts, and an action that lands elsewhere is taken back.
  Another site is ``browser_open``'s, without this sign-in.
* READING CANNOT PRESS ANYTHING. ``follow`` takes a link's address and
  loads it -- no click, so no script on the page runs for it; ``search``
  types into a box only when it is a search box, and presses Enter
  there. With ``scroll`` and ``open`` that is the whole Read only level,
  and none of it is asked about.
* ACTING IS ASKED, AND COUNTED. ``click`` and ``fill`` exist at the write
  level only, are asked about every time (their class is write), and
  stop after the manifest's ``max_actions`` in one session.
* SPENDING IS NEVER THE AGENT'S. On a page the manifest lists as
  spending (checkout, returns) nothing is clicked, and a button whose
  words spend ("buy now", "place your order") is refused anywhere; so is
  typing into a password or card field. The model is told to hand the
  page to the person instead -- there is no tool that would press it.
* A PERSON'S PACE. Page loads are at least the manifest's ``pace`` apart;
  X locks accounts that read like robots.
* SIGNED OUT IS SAID. A page that turns out to be the site's sign-in
  says so, and how to get the person to sign in again (``handoff``
  return, or ``setu connect``).
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from yantra.config import browser_handoff, browser_headed
from yantra.errors import ToolError
from yantra.tools.base import Tool, ToolContext, require_str
from yantra.tools.browser import BrowserHandoff, BrowserSession, _VirtualDisplay

#: The classes a level reaches, by the level's name.
LEVEL_RANK = {"read": 0, "write": 1}
CLASS_RANK = {"read": 0, "write": 1, "spend": 2}
#: Words in the address of a page that asks a visitor to sign in.
SIGN_IN_HINTS = ("signin", "sign_in", "login", "/onboarding")
#: Words a robot check puts at the top of its page. Conservative: a
#: false alarm here is a wrong line in a health count, so only phrases
#: that mean "prove you are a person" are listed.
ROBOT_HINTS = ("captcha", "are you a robot", "not a robot", "are you human",
               "verify you are human", "verify you're human", "unusual traffic",
               "press and hold", "robot check", "automated access",
               "checking your browser", "just a moment...")
#: How long a page that builds itself in the browser (X) gets to put
#: some text on the screen before the snapshot is taken anyway.
PAINT_TIMEOUT_MS = 8_000
#: A scroll's wait for the next part of an endless page to load.
SCROLL_SETTLE_MS = 1_200

#: Is this textbox a search box? Its own type or role, a word in its
#: labels, or a search landmark around it -- and never one whose form
#: posts, which is a form that changes something.
_IS_SEARCH_JS = """
(el) => {
  const form = el.form || el.closest('form');
  if (form && (form.getAttribute('method') || 'get').toLowerCase() === 'post') return false;
  if ((el.type || '').toLowerCase() === 'password') return false;
  const role = (el.getAttribute('role') || '').toLowerCase();
  if ((el.type || '').toLowerCase() === 'search' || role === 'searchbox') return true;
  const words = [el.getAttribute('aria-label'), el.getAttribute('placeholder'),
                 el.getAttribute('name'), el.id, el.getAttribute('title')].join(' ');
  if (/search|query|keyword/i.test(words)) return true;
  return el.closest('[role="search"], form[role="search"]') !== null;
}
"""

#: A field the agent never types into: passwords and payment details.
_IS_SECRET_JS = """
(el) => {
  const type = (el.type || '').toLowerCase();
  const auto = (el.getAttribute('autocomplete') || '').toLowerCase();
  const words = [el.getAttribute('aria-label'), el.getAttribute('placeholder'),
                 el.getAttribute('name'), el.id].join(' ');
  return type === 'password' || auto.startsWith('cc-') || auto.includes('password')
    || /card.?number|cvv|cvc|security code|expir|password|passcode|\\bpin\\b|otp/i.test(words);
}
"""


@dataclass(frozen=True, slots=True)
class SiteRules:
    """What the manifest says about keeping to a site."""

    name: str
    hosts: tuple[str, ...]
    home: str
    spend_pages: tuple[str, ...] = ()
    spend_words: tuple[str, ...] = ()
    pace: float = 1.0
    max_actions: int = 20
    headed: bool = False
    guide: str = ""

    def allowed(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in self.hosts)

    def spends_here(self, url: str) -> bool:
        path = urlparse(url).path or "/"
        return any(fnmatch.fnmatchcase(path, pattern) for pattern in self.spend_pages)

    def spending_words(self, label: str) -> str | None:
        text = label.lower()
        for word in self.spend_words:
            if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text):
                return word
        return None


class SiteSession(BrowserSession):
    """A BrowserSession on one connection's profile, kept to one site."""

    def __init__(self, rules: SiteRules, profile: Path, executable: str | None,
                 *, headed: bool | None = None,
                 close_after: float | None | str = "default") -> None:
        super().__init__(profile=profile, executable=executable,
                         headed=(rules.headed or browser_headed()) if headed is None
                         else headed, close_after=close_after)
        self.rules = rules
        self.actions = 0
        self._last_load = 0.0
        #: Told each thing that went wrong -- a robot check, a signed-out
        #: page, a refusal, the limit, a handoff -- once per page per
        #: session (Setu keeps a week of them; setu_link wires it).
        self.on_event: Any = None
        self._noted: set[tuple[str, str]] = set()
        #: How the person signs in again on this road, for the signed-out
        #: page (setu_link sets the host's own words: a chat's /connect).
        self.reconnect = "tell them to run `setu connect` for this site"

    def _note(self, kind: str, where: str = "") -> None:
        if self.on_event is None or (kind, where) in self._noted:
            return
        self._noted.add((kind, where))
        try:
            self.on_event(kind)
        except Exception:
            pass        # a health line never costs the tool call it describes

    # -- launch: a site that refuses headless gets a window nobody sees --

    def _launch_options(self) -> dict[str, Any]:
        options = super()._launch_options()
        if self.rules.headed and self._headed and "env" not in options:
            # headed because the SITE asked, not the person: on their own
            # screen it would be a window popping up mid-answer
            self._display = _VirtualDisplay()
            options["env"] = {**os.environ, "DISPLAY": self._display.start()}
        return options

    # -- the verbs (each funnels to the worker thread, as the base does) --

    def follow(self, ref: str) -> str:
        return self._call(lambda: self._follow(ref))

    def scroll(self) -> str:
        return self._call(self._scroll)

    def search(self, ref: str, text: str) -> str:
        return self._call(lambda: self._search(ref, text))

    # -- bodies --------------------------------------------------------

    def _pace(self) -> None:
        wait = self.rules.pace - (time.monotonic() - self._last_load)
        if wait > 0:
            time.sleep(wait)
        self._last_load = time.monotonic()

    def _check_address(self, url: str) -> None:
        if not self.rules.allowed(url):
            raise ToolError(
                f"{url} is not {self.rules.name} ({', '.join(self.rules.hosts[:3])}"
                f"{'…' if len(self.rules.hosts) > 3 else ''}); these tools keep to "
                f"{self.rules.name} -- another site is browser_open's, without this sign-in")

    def _snapshot(self, **opts: bool) -> str:
        text = super()._snapshot(chrome_last=True, **opts)
        where = urlparse(self._url)
        address = f"{where.path}?{where.query}".lower()
        head = text[:2000].lower()
        if any(hint in head for hint in ROBOT_HINTS):
            self._note("robot_check", where.path)
            give = ("Hand the page to the person with handoff mode='return', or tell "
                    "them" if browser_handoff() == "window" else "Tell the person")
            text = (f"(this looks like {self.rules.name}'s robot check, not the page asked "
                    f"for. Do not try to solve it. {give} the site is checking for "
                    "robots right now)\n" + text)
        if any(hint in address for hint in SIGN_IN_HINTS):
            self._note("signed_out", where.path)
            text = (f"(this is {self.rules.name}'s sign-in page: the person is signed out. "
                    f"Do not type a password. {self._sign_in_again()})\n" + text)
        return text

    def _sign_in_again(self) -> str:
        """What to do on a signed-out page. A handoff is a sign-in only
        when it is a window on THIS profile: as a link it opens the
        person's own browser, which signs in their phone and leaves this
        connection as signed out as it was (a door's chat got exactly
        that link, and the next question the same sign-in page)."""
        if browser_handoff() == "window":
            return (f"Hand the page to them with handoff mode='return' to sign in again, "
                    f"or {self.reconnect}")
        return (f"Do not hand them this page or its address: opening it signs in their "
                f"own browser, not this connection. To sign in again, {self.reconnect}")

    def _settle(self) -> None:
        super()._settle()
        try:
            self._page.wait_for_function(
                "() => document.body && document.body.innerText.trim().length > 40",
                timeout=PAINT_TIMEOUT_MS)
        except Exception:
            pass        # a page with little text is still a page; snap it

    def _landed(self, before: str) -> None:
        """After anything that can navigate: off the site means back."""
        url = self._page.url
        if url and url != "about:blank" and not self.rules.allowed(url):
            try:
                self._page.goto(before, wait_until="domcontentloaded")
            except Exception:
                pass
            raise ToolError(f"that went to {url}, outside {self.rules.name}; went back. "
                            "Another site is browser_open's, without this sign-in")

    def _open(self, url: str) -> str:
        if not url and self._page is None:
            url = self.rules.home          # open() alone starts at home
        if url and url.startswith("/") and not url.startswith("//"):
            # a path, as a guide writes it: on this site, wherever it lives
            # (a port included), never a host the model guessed
            url = urljoin(self.rules.home, url)
        if url:
            self._check_address(url)
            self._pace()
        out = super()._open(url)
        self._landed(self.rules.home)
        return out

    def _follow(self, ref: str) -> str:
        page = self._require_page()
        element = self._resolve(ref)
        if element["kind"] != "link":
            raise ToolError(f"{ref} is a {element['kind']}, not a link -- follow loads "
                            "links only; pressing buttons is click's, and asked about")
        href = self._locator(page, ref).evaluate(
            "el => el.href || (el.closest('a') || {}).href || ''")
        if not href or not str(href).startswith(("http://", "https://")):
            raise ToolError(f"{ref} has no address to load (a link that runs a script); "
                            "it needs click, which is asked about")
        self._check_address(href)
        self._pace()
        try:
            page.goto(href, wait_until="domcontentloaded", timeout=15_000)
        except Exception as exc:
            raise ToolError(f"could not load {href}: {type(exc).__name__}: {exc}") from exc
        self._settle()
        return self._snapshot()

    def _scroll(self) -> str:
        page = self._require_page()
        page.evaluate("() => window.scrollBy(0, Math.round(window.innerHeight * 0.85))")
        page.wait_for_timeout(SCROLL_SETTLE_MS)
        return self._snapshot(from_view=True)

    def _search(self, ref: str, text: str) -> str:
        page = self._require_page()
        element = self._resolve(ref)
        locator = self._locator(page, ref)
        if element["kind"] != "textbox" or not locator.evaluate(_IS_SEARCH_JS):
            raise ToolError(f"{ref} is not a search box -- search types into those only. "
                            "Typing anywhere else is fill's, and asked about")
        before = page.url
        self._pace()
        try:
            locator.fill(text, timeout=10_000)
            locator.press("Enter", timeout=10_000)
        except Exception as exc:
            raise ToolError(f"search in {ref} failed: {type(exc).__name__}: {exc}") from exc
        self._settle()
        self._landed(before)
        return self._snapshot()

    def _refuse_spending(self, ref: str) -> None:
        element = self._resolve(ref)
        if self.rules.spends_here(self._page.url):
            self._note("refused", ref)
            raise ToolError(
                f"this page ({urlparse(self._page.url).path}) is where {self.rules.name} "
                "spends money or does what cannot be undone -- nothing here is yours to "
                "press. Tell the person what it shows and hand it over: handoff "
                "mode='finish'")
        word = self.rules.spending_words(element.get("label", ""))
        if word:
            self._note("refused", ref)
            raise ToolError(
                f"{ref} ({element.get('label')!r}) is a '{word}' button -- buying, paying "
                "and what cannot be undone are the person's to press. Hand the page over "
                "with handoff mode='finish'")

    def _count_action(self) -> None:
        if self.actions >= self.rules.max_actions:
            self._note("limit")
            raise ToolError(f"{self.rules.max_actions} actions on {self.rules.name} this "
                            "session is the limit its connector sets; stop and tell the "
                            "person what is left to do")
        self.actions += 1

    def _click(self, ref: str) -> str:
        page = self._require_page()
        self._refuse_spending(ref)
        self._count_action()
        before = page.url
        self._pace()
        out = super()._click(ref)
        self._landed(before)
        return out

    def _fill(self, ref: str, text: str, enter: bool = False) -> str:
        page = self._require_page()
        self._resolve(ref)
        if self._locator(page, ref).evaluate(_IS_SECRET_JS):
            self._note("refused", ref)
            raise ToolError(f"{ref} asks for a password or payment details -- never typed "
                            "by the agent. Hand the page to the person: handoff "
                            "mode='return' to sign in, mode='finish' to pay")
        self._refuse_spending(ref)
        if enter:
            self._count_action()
            self._pace()
        before = page.url
        out = super()._fill(ref, text, enter)
        self._landed(before)
        return out


# ---- the tools ----------------------------------------------------------------


#: What each verb says to the model, and its parameters.
VERBS: dict[str, tuple[str, dict[str, Any]]] = {
    "open": ("Open a page of {site}, signed in as the person (their own {site} account). "
             "No url: their {site} home. Only {site}'s own addresses. Returns the page "
             "as text plus numbered elements [e1], [e2]...",
             {"url": {"type": "string",
                      "description": "An address on {site}, or a path on it like "
                                     "/account/orders. Omit for its home page, or to "
                                     "re-read the page already open."}}),
    "follow": ("Go to a link on the open {site} page by its [eN] ref -- the way to page "
               "through orders, posts and results. Loads the link's address; presses "
               "nothing.",
               {"ref": {"type": "string", "description": "A link's ref, e.g. 'e12'."}}),
    "scroll": ("Scroll the open {site} page down one screen and read what comes into view "
               "-- endless pages (a timeline) load more as they scroll.", {}),
    "search": ("Type words into a search box on the open {site} page and press Enter.",
               {"ref": {"type": "string", "description": "The search box's ref."},
                "text": {"type": "string", "description": "What to search for."}}),
    "click": ("Click a button or control on the open {site} page by its [eN] ref (asked "
              "about every time). Buying, paying and what cannot be undone are refused: "
              "hand those pages to the person.",
              {"ref": {"type": "string", "description": "Element ref, e.g. 'e3'."}}),
    "fill": ("Type into a box on the open {site} page by its [eN] ref (asked about every "
             "time) -- a post, a reply, a message. enter=true presses Enter after. Never "
             "a password or card number.",
             {"ref": {"type": "string", "description": "Textbox ref, e.g. 'e2'."},
              "text": {"type": "string", "description": "Text to type."},
              "enter": {"type": "boolean", "description": "Press Enter after (default "
                                                          "false)."}}),
}
REQUIRED = {"follow": ["ref"], "search": ["ref", "text"], "click": ["ref"],
            "fill": ["ref", "text"]}


class SiteTool(Tool):
    """One verb on one connection's SiteSession."""

    name = ""
    description = ""
    parameters: dict[str, Any] = {}

    def __init__(self, prefix: str, verb: str, klass: str, session: SiteSession) -> None:
        site = session.rules.name
        text, props = VERBS[verb]
        self.verb, self.session = verb, session
        self.name = f"{prefix}_{verb}"
        self.description = text.format(site=site)
        self.parameters = {
            "type": "object",
            "properties": {k: {**v, "description": v["description"].format(site=site)}
                           for k, v in props.items()},
            "additionalProperties": False,
            **({"required": REQUIRED[verb]} if verb in REQUIRED else {}),
        }
        self.read_only = klass == "read"
        self.always_ask = False         # spending is never offered at all
        self.requires = () if verb == "open" else (f"{prefix}_open",)

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        site = self.session.rules.name
        if self.verb == "open":
            return f"{site}: open {args.get('url') or 'home'}"
        if self.verb == "fill":
            text = str(args.get("text", ""))
            then = " + Enter" if args.get("enter") is True else ""
            return f'{site}: type into {args.get("ref")} "{text[:200]}"{then} ' \
                   f'on {self.session.url or "(no page)"}'
        if self.verb == "click":
            label = (self.session._elements.get(str(args.get("ref")), {}) or {}).get("label")
            return f"{site}: click {args.get('ref')}" + (f" ({label!r})" if label else "") \
                + f" on {self.session.url or '(no page)'}"
        if self.verb == "search":
            return f"{site}: search {args.get('text', '')!r}"
        return f"{site}: {self.verb} {args.get('ref', '')}".rstrip()

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        s = self.session
        if self.verb == "open":
            return s.open(require_str(args, "url", optional=True))
        if self.verb == "follow":
            return s.follow(require_str(args, "ref"))
        if self.verb == "scroll":
            return s.scroll()
        if self.verb == "search":
            return s.search(require_str(args, "ref"), require_str(args, "text"))
        if self.verb == "click":
            return s.click(require_str(args, "ref"))
        return s.fill(require_str(args, "ref"), require_str(args, "text"),
                      enter=args.get("enter") is True)

    def turn_ended(self) -> None:
        self.session.release()


class SiteHandoff(BrowserHandoff):
    """browser_handoff on a connection's own profile: the page goes to the
    person -- to sign in again, or to finish a purchase themselves."""

    def __init__(self, prefix: str, session: SiteSession, reach: str) -> None:
        super().__init__(session, reach)
        self.name = f"{prefix}_handoff"
        self.requires = (f"{prefix}_open",)
        self.description = (f"Give the open {session.rules.name} page to the PERSON. "
                            + BrowserHandoff.description.split("PERSON. ", 1)[-1])
        self._site = session
        if reach == "link":
            # A LINK NEVER SIGNS THIS PROFILE IN. It opens the person's own
            # browser; 'return' would hand back a page as signed out as it
            # was. Told so in words, a small model on a door's chat sent
            # the sign-in address anyway -- so the mode is not offered.
            self.description = (
                f"Give the open {session.rules.name} page to the PERSON to finish "
                "themselves (mode='finish'): anything that spends money, needs "
                "payment or personal details, or is theirs to decide. They get a "
                "link to open on their own device, and your browsing ends. It "
                "cannot sign this connection in: on a sign-in page or a robot "
                "check, do not hand over -- tell them how to sign in again, as "
                "the page you read says.")
            self.parameters = {**BrowserHandoff.parameters, "properties": {
                **BrowserHandoff.parameters["properties"],
                "mode": {"type": "string", "enum": ["finish"],
                         "description": "'finish': theirs from here on."}}}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        self._site._note("handoff", str(args.get("mode") or ""))
        if self.reach == "link" and args.get("mode") == "return":
            return ("Not handed over: here a handoff is a link to the person's own "
                    "browser, and signing in there leaves this connection signed out. "
                    f"To sign it in again, {self._site.reconnect}.")
        return super().run(args, ctx)


def prefix_for(connector: str, account: str, several: bool) -> str:
    """``amazon``, or ``amazon_work`` when the person has two Amazons."""
    base = re.sub(r"[^A-Za-z0-9_-]", "_", connector)
    return f"{base}_{re.sub(r'[^A-Za-z0-9_-]', '_', account)}" if several else base


def site_tools(prefix: str, session: SiteSession, verbs: dict[str, str],
               reach_level: str, ceiling: str | None = None) -> list[Tool]:
    """The tools a connection's level -- and a package's ceiling -- reach,
    in the manifest's classes; a click or a fill is never a read."""
    top = LEVEL_RANK.get(reach_level, 0)
    if ceiling is not None:
        top = min(top, CLASS_RANK.get(ceiling, 0))
    tools: list[Tool] = []
    for verb in VERBS:
        klass = verbs.get(verb)
        if klass not in CLASS_RANK:
            continue                     # not in the manifest: nobody agreed to it
        if verb in ("click", "fill") and klass == "read":
            klass = "write"
        if CLASS_RANK[klass] > top or klass == "spend":
            continue
        tools.append(SiteTool(prefix, verb, klass, session))
    reach = browser_handoff()
    if reach is not None and tools:
        tools.append(SiteHandoff(prefix, session, reach))
    return tools


def rules_from(card: dict[str, Any], row: dict[str, Any]) -> SiteRules:
    """A connector card's ``browser`` table and a connection's ``browser``
    row (Setu's status report), as the session's rules."""
    spec = card.get("browser") or {}
    where = row.get("browser") or {}
    home = where.get("home") or spec.get("start_url") or ""
    return SiteRules(
        name=card.get("name") or card.get("id", "site"),
        hosts=tuple(card.get("hosts") or ()),
        home=home,
        spend_pages=tuple(spec.get("spend_pages") or ()),
        spend_words=tuple(spec.get("spend_words") or ()),
        pace=float(spec.get("pace", 1.0)),
        max_actions=int(spec.get("max_actions", 20)),
        headed=bool(spec.get("headed", False)),
        guide=str(spec.get("guide") or "").replace("{home}", home.rstrip("/")),
    )
