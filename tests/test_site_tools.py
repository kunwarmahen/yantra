"""Setu's browser road in Yantra: a signed-in site as tools that keep to it.

The bias is A BROWSER CANNOT TELL READING FROM BUYING. A click is a
click; so the tests pin what the site session refuses before anything
reaches the page: a "Buy now" button, any press on a checkout page, a
password field, an address on another site, a typed word in a box that
is not a search box -- and that the Read only level has no tool that
presses anything at all.

Also designed against:

* **A run of actions nobody counted.** Clicks stop at the manifest's
  ``max_actions``; page loads keep the manifest's pace.
* **Being walked off the site.** A click that lands elsewhere is taken
  back and said.
* **A signed-out page taken for an empty one.** The sign-in page says the
  person is signed out, and not to type a password.
* **A level changed in Setu that the session never hears of.** The tools
  are rebuilt; a gone connection takes its tools with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import ScriptedProvider

from yantra import setu_link
from yantra.agent import Agent
from yantra.errors import ToolError
from yantra.mcp import MCPManager
from yantra.tools.base import ToolContext, ToolRegistry
from yantra.tools.site import SiteRules, SiteSession, prefix_for, rules_from, site_tools

HOME = "https://www.shop.test/"
VERBS = {"open": "read", "follow": "read", "scroll": "read", "search": "read",
         "click": "write", "fill": "write"}


@pytest.fixture(autouse=True)
def _quiet_env(monkeypatch):
    for name in ("YANTRA_BROWSER_HEADED", "YANTRA_BROWSER_CLOSE", "YANTRA_BROWSER_EXECUTABLE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("YANTRA_BROWSER_HANDOFF", "link")


class Locator:
    def __init__(self, page, ref):
        self.page, self.ref = page, ref

    def evaluate(self, script):
        element = self.page.by_ref[self.ref]
        if "href" in script:
            return element.get("href", "")
        if "search" in script and "query" in script:
            return element.get("search", False)
        if "password" in script:
            return element.get("secret", False)
        self.page.scripted.append(self.ref)
        return None

    def click(self, timeout=None):
        self.page.clicks.append(self.ref)
        target = self.page.by_ref[self.ref].get("goes")
        if target:
            self.page.url = target

    def fill(self, text, timeout=None):
        self.page.fills.append((self.ref, text))

    def press(self, key, timeout=None):
        self.page.presses.append((self.ref, key))


class Page:
    def __init__(self, url=HOME, elements=()):
        self.url = url
        self.elements = list(elements) or [
            {"ref": "e1", "kind": "link", "label": "Your orders",
             "href": "https://www.shop.test/orders"},
            {"ref": "e2", "kind": "textbox", "label": "Search Shop", "search": True},
            {"ref": "e3", "kind": "button", "label": "Buy Now"},
            {"ref": "e4", "kind": "button", "label": "Add to cart"},
            {"ref": "e5", "kind": "textbox", "label": "Password", "secret": True},
            {"ref": "e6", "kind": "link", "label": "Elsewhere",
             "href": "https://evil.test/x"},
            {"ref": "e7", "kind": "textbox", "label": "Message"},
            {"ref": "e8", "kind": "button", "label": "Leave", "goes": "https://evil.test/"},
        ]
        self.by_ref = {e["ref"]: e for e in self.elements}
        self.gotos, self.clicks, self.fills, self.presses = [], [], [], []
        self.scripted, self.opts, self.scrolls = [], [], 0

    def title(self):
        return "Shop"

    def goto(self, url, **kw):
        self.gotos.append(url)
        self.url = url

    def evaluate(self, script, arg=None):
        if "scrollBy" in script:
            self.scrolls += 1
            return None
        self.opts.append(arg)
        return {"text": "page text", "dialog": False, "skipped": 0,
                "elements": [{k: e[k] for k in ("ref", "kind", "label")}
                             for e in self.elements]}

    def locator(self, selector):
        return Locator(self, selector.split('"')[1])

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, state, timeout=None):
        pass

    def close(self):
        pass


RULES = SiteRules(name="Shop", hosts=("shop.test",), home=HOME,
                  spend_pages=("/checkout/*",), spend_words=("buy now", "place your order"),
                  pace=0.0, max_actions=2)


def session(page=None, rules=RULES):
    s = SiteSession(rules, Path("/nonexistent/profile"), "/usr/bin/chrome", headed=False,
                    close_after=None)
    s._pw = object()
    s._context = object()
    s._page = page or Page()
    s._open("")     # a first snapshot, so refs exist
    return s


class TestReadingPressesNothing:
    def test_open_alone_starts_at_home_and_another_site_is_refused(self):
        s = SiteSession(RULES, Path("/p"), None, headed=False, close_after=None)
        page = Page()
        s._launch = lambda: setattr(s, "_page", page) or setattr(s, "_pw", object())
        s._open("")
        assert page.gotos == [HOME]
        with pytest.raises(ToolError, match="keep to Shop"):
            s._open("https://evil.test/")

    def test_a_path_opens_on_the_site_wherever_it_lives(self):
        s = session()
        s._open("/account/orders")
        assert s._page.gotos[-1] == "https://www.shop.test/account/orders"
        local = session(Page(url="http://shop.localhost:18794/"),
                        rules=SiteRules(name="Shop", hosts=("shop.localhost",),
                                        home="http://shop.localhost:18794/", pace=0.0))
        local._open("/account/orders")
        assert local._page.gotos[-1] == "http://shop.localhost:18794/account/orders"
        with pytest.raises(ToolError, match="keep to Shop"):
            s._open("//evil.test/x")

    def test_follow_loads_the_address_and_clicks_nothing(self):
        s = session()
        s._follow("e1")
        assert s._page.gotos[-1] == "https://www.shop.test/orders"
        assert s._page.clicks == []

    def test_follow_is_for_links_and_this_site_only(self):
        s = session()
        with pytest.raises(ToolError, match="not a link"):
            s._follow("e4")
        with pytest.raises(ToolError, match="keep to Shop"):
            s._follow("e6")

    def test_search_types_only_into_a_search_box(self):
        s = session()
        s._search("e2", "kettle")
        assert s._page.fills == [("e2", "kettle")] and s._page.presses == [("e2", "Enter")]
        with pytest.raises(ToolError, match="not a search box"):
            s._search("e7", "hello")      # a message box: Enter would send it

    def test_the_sites_content_comes_before_its_menus(self):
        s = session()
        assert s._page.opts[-1]["chrome_last"] is True
        s._scroll()
        assert s._page.scrolls == 1 and s._page.opts[-1]["from_view"] is True


class TestSpendingIsThePersons:
    def test_a_buy_button_is_refused_before_it_is_pressed(self):
        s = session()
        with pytest.raises(ToolError, match="'buy now' button"):
            s._click("e3")
        assert s._page.clicks == []

    def test_nothing_is_pressed_on_a_checkout_page(self):
        page = Page(url="https://www.shop.test/checkout/review")
        s = session(page)
        with pytest.raises(ToolError, match="handoff"):
            s._click("e4")
        assert page.clicks == []

    def test_a_password_is_never_typed(self):
        s = session()
        with pytest.raises(ToolError, match="password or payment"):
            s._fill("e5", "hunter2")
        assert s._page.fills == []

    def test_actions_stop_at_the_limit(self):
        s = session()
        s._click("e4")
        s._click("e4")
        with pytest.raises(ToolError, match="limit"):
            s._click("e4")
        assert s._page.clicks == ["e4", "e4"]

    def test_a_click_that_leaves_the_site_is_taken_back(self):
        s = session()
        with pytest.raises(ToolError, match="went back"):
            s._click("e8")
        assert s._page.gotos[-1] == HOME


def test_page_loads_keep_the_pace(monkeypatch):
    slept = []
    monkeypatch.setattr("yantra.tools.site.time.sleep", slept.append)
    s = session(rules=SiteRules(name="Shop", hosts=("shop.test",), home=HOME, pace=3.0))
    s._follow("e1")
    s._follow("e1")
    assert slept and slept[-1] > 2.5


def test_a_sign_in_page_says_the_person_is_signed_out():
    s = session(Page(url="https://www.shop.test/ap/signin?return=orders"))
    assert "signed out" in s._open("")


class TestLevels:
    def names(self, level, ceiling=None, verbs=VERBS):
        return [t.name for t in site_tools("shop", session(), verbs, level, ceiling)]

    def test_read_only_has_nothing_that_presses(self):
        assert self.names("read") == ["shop_open", "shop_follow", "shop_scroll",
                                      "shop_search", "shop_handoff"]

    def test_write_adds_click_and_fill_asked_every_time(self):
        tools = {t.name: t for t in site_tools("shop", session(), VERBS, "write")}
        assert {"shop_click", "shop_fill"} <= set(tools)
        assert not tools["shop_click"].read_only and tools["shop_open"].read_only
        assert not any(t.always_ask for t in tools.values())

    def test_a_packages_ceiling_wins_over_the_level(self):
        assert "shop_click" not in self.names("write", ceiling="read")

    def test_a_click_is_never_a_read_and_unlisted_verbs_do_not_exist(self):
        tools = {t.name: t for t in site_tools(
            "shop", session(), {"open": "read", "click": "read"}, "write")}
        assert set(tools) == {"shop_open", "shop_click", "shop_handoff"}
        assert not tools["shop_click"].read_only

    def test_the_approval_names_the_button_and_the_page(self):
        tools = {t.name: t for t in site_tools("shop", session(), VERBS, "write")}
        said = tools["shop_click"].summary({"ref": "e4"}, ToolContext(cwd=Path(".")))
        assert "Add to cart" in said and HOME in said

    def test_two_accounts_get_their_own_prefix(self):
        assert prefix_for("amazon", "personal", False) == "amazon"
        assert prefix_for("amazon", "work", True) == "amazon_work"


# ---- through Setu's report ------------------------------------------------------


def report(level="read", connected=True):
    card = {"id": "shop", "name": "Shop", "road": "browser", "connected": connected,
            "hosts": ["shop.test"], "verbs": VERBS, "levels": [],
            "browser": {"start_url": HOME, "spend_pages": ["/checkout/*"],
                        "spend_words": ["buy now"], "pace": 1.0, "max_actions": 20,
                        "guide": "Orders: {home}/orders", "headed": False}}
    rows = [{"ref": "shop:personal", "connector": "shop", "account": "personal",
             "email": "shop.in", "level": level, "level_label": level.title(),
             "installed": True, "mcp": None,
             "browser": {"profile": "/tmp/p/shop-personal", "executable": "/usr/bin/chrome",
                         "home": "https://www.shop.in/"}}] if connected else []
    return {"format": setu_link.FORMAT, "version": "0", "command": "setu", "problems": [],
            "connections": rows, "connectors": [card]}


def synced(tmp_path, data, setu=None):
    agent = Agent(ScriptedProvider([]), model="m", tools=ToolRegistry())
    manager = MCPManager(agent.registry, agent=agent,
                         memory_path=tmp_path / ".yantra" / "mcp.json")
    setu = setu or setu_link.Setu(mode="on")
    setu.link = setu_link.Link(data=data, road="test")
    done = setu.sync(manager, agent)
    return setu, agent, manager, done


class TestThroughSetu:
    def test_a_browser_connection_becomes_its_own_tools(self, tmp_path):
        setu, agent, manager, done = synced(tmp_path, report())
        assert done.connected == {"shop": 5}
        assert "shop_open" in agent.registry and "shop_click" not in agent.registry
        assert manager.sessions == {}               # no server was started
        layer = setu_link.prompt_text(setu.link, sites={"shop:personal": "shop"})
        assert "`shop_*`" in layer and "Orders: https://www.shop.in/orders" in layer
        row = setu.describe(manager)["connections"][0]
        assert row["road"] == "browser" and row["tools"] == 5 and row["tools_as"] == "shop_"

    def test_the_rules_come_from_the_card_and_home_from_the_connection(self):
        data = report()
        rules = rules_from(data["connectors"][0], data["connections"][0])
        assert rules.home == "https://www.shop.in/"
        assert rules.guide == "Orders: https://www.shop.in/orders"
        assert rules.spending_words("Buy Now") == "buy now"

    def test_a_level_changed_in_setu_rebuilds_the_tools(self, tmp_path):
        setu, agent, manager, _ = synced(tmp_path, report("read"))
        setu.link = setu_link.Link(data=report("write"), road="test")
        done = setu.sync(manager, agent)
        assert "shop_click" in agent.registry
        assert any("restarted" in n for n in done.notes)

    def test_a_gone_connection_takes_its_tools(self, tmp_path):
        setu, agent, manager, _ = synced(tmp_path, report())
        setu.link = setu_link.Link(data=report(connected=False), road="test")
        done = setu.sync(manager, agent)
        assert "shop_open" not in agent.registry and done.dropped == ["shop"]

    def test_a_package_that_did_not_ask_gets_none(self, tmp_path):
        _, agent, _, done = synced(tmp_path, report(),
                                   setu_link.Setu(mode="on", allow={}))
        assert "shop_open" not in agent.registry and done.connected == {}

    def test_announced_like_any_other(self, tmp_path):
        setu, _, _, done = synced(tmp_path, report())
        assert "shop (5 tool(s))" in setu_link.announce(setu.link, done.connected)
