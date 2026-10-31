"""A site that turns the headless browser away gets one try with a window.

The bias these tests encode: A FAILED LOAD MUST NEVER BE REPORTED AS A
BLOCKED MACHINE WHILE A WINDOW WOULD HAVE LOADED IT, AND A WINDOW MUST
NEVER APPEAR WHERE THE PERSON CAN SEE IT. A Kroger-family shop drops
headless Chrome with ERR_HTTP2_PROTOCOL_ERROR and serves the same Chrome
headed; a chat told "my box is blocked" for an hour is the failure. So a
drop or a 403 is tried once more headed, on Xvfb even on a desktop, and
only once; a 401 (a sign-in) and a plain timeout are not, and a person
who set YANTRA_BROWSER_HEADED=0 gets no window at all.

And a page that never loaded can still be handed over: browser_handoff
'finish' gives the address last opened, still with no address argument.

Everything runs offline, on test_browser's fakes.
"""

from __future__ import annotations

import types

import pytest

from test_browser import URL, FakePage, FakeChromium, install_fake_playwright
from yantra.config import browser_window_retry
from yantra.errors import ToolError
from yantra.tools.browser import BrowserSession
import yantra.tools.browser as browser_mod

DROPPED = ("Page.goto: net::ERR_HTTP2_PROTOCOL_ERROR at " + URL)


class ShieldedPage(FakePage):
    """A page whose site drops headless browsers: ``fail`` raises on
    goto, ``status`` is the answer's HTTP status otherwise."""

    def __init__(self, fail: str = "", status: int = 200) -> None:
        super().__init__()
        self.fail = fail
        self.status = status

    def goto(self, url, **kwargs):
        super().goto(url, **kwargs)
        if self.fail:
            raise Exception(self.fail)
        return types.SimpleNamespace(status=self.status)


class Displays:
    """Stands in for Xvfb: counts the screens started."""

    started = 0

    def __init__(self) -> None:
        self.display = ""

    def start(self) -> str:
        Displays.started += 1
        self.display = ":99"
        return self.display

    def stop(self) -> None:
        pass


@pytest.fixture
def xvfb(monkeypatch):
    Displays.started = 0
    monkeypatch.setattr(browser_mod, "_VirtualDisplay", Displays)
    real_which = browser_mod.shutil.which
    monkeypatch.setattr(browser_mod.shutil, "which",
                        lambda name: "/usr/bin/Xvfb" if name == "Xvfb"
                        else real_which(name))
    monkeypatch.setenv("DISPLAY", ":0")     # a desktop the window must avoid
    return Displays


def shielded(monkeypatch, headless: FakePage, headed: FakePage, **kw):
    """A session whose first browser gets ``headless`` and whose second
    (the retry's) gets ``headed``; returns it and the launch recorder."""
    pages = iter([headless, headed])
    chromium = FakeChromium(browser=types.SimpleNamespace(
        new_page=lambda: next(pages), close=lambda: None))
    install_fake_playwright(monkeypatch, chromium)
    session = BrowserSession(profile=None, executable=None, headed=False,
                             close_after=None, window_retry=True, **kw)
    return session, chromium


class TestTurnedAway:
    def test_a_dropped_load_is_tried_again_with_a_window(self, monkeypatch, xvfb):
        session, chromium = shielded(monkeypatch, ShieldedPage(fail=DROPPED),
                                     ShieldedPage())
        out = session.open(URL)
        assert [c["headless"] for c in chromium.launch_calls] == [True, False]
        assert "opened again with a window" in out and "hello world" in out

    def test_the_window_goes_on_its_own_screen_even_on_a_desktop(
            self, monkeypatch, xvfb):
        session, chromium = shielded(monkeypatch, ShieldedPage(fail=DROPPED),
                                     ShieldedPage())
        session.open(URL)
        assert xvfb.started == 1
        assert chromium.launch_calls[1]["env"]["DISPLAY"] == ":99"

    def test_a_403_is_tried_again_too(self, monkeypatch, xvfb):
        session, chromium = shielded(monkeypatch, ShieldedPage(status=403),
                                     ShieldedPage())
        session.open(URL)
        assert len(chromium.launch_calls) == 2

    def test_a_sign_in_is_not_a_shield(self, monkeypatch, xvfb):
        session, chromium = shielded(monkeypatch, ShieldedPage(status=401),
                                     ShieldedPage())
        out = session.open(URL)
        assert len(chromium.launch_calls) == 1
        assert "HTTP 401" in out

    def test_a_timeout_is_not_tried_twice(self, monkeypatch, xvfb):
        session, chromium = shielded(
            monkeypatch, ShieldedPage(fail="Timeout 15000ms exceeded."),
            ShieldedPage())
        with pytest.raises(ToolError, match="Timeout") as caught:
            session.open(URL)
        assert len(chromium.launch_calls) == 1
        assert "with a window" not in str(caught.value)

    def test_turned_away_twice_says_it_is_the_site_and_the_way_out(
            self, monkeypatch, xvfb):
        session, chromium = shielded(monkeypatch, ShieldedPage(fail=DROPPED),
                                     ShieldedPage(fail=DROPPED))
        with pytest.raises(ToolError) as caught:
            session.open(URL)
        said = str(caught.value)
        assert len(chromium.launch_calls) == 2          # once more, no more
        assert "not this machine's network" in said
        assert "browser_handoff" in said

    def test_the_window_lasts_until_the_browser_closes(self, monkeypatch, xvfb):
        session, _ = shielded(monkeypatch, ShieldedPage(fail=DROPPED),
                              ShieldedPage())
        session.open(URL)
        assert session._headed
        session.close()
        assert not session._headed and not session._retried_headed

    def test_no_xvfb_means_no_second_try(self, monkeypatch, xvfb):
        monkeypatch.setattr(browser_mod.shutil, "which", lambda name: None)
        session, chromium = shielded(monkeypatch, ShieldedPage(fail=DROPPED),
                                     ShieldedPage())
        with pytest.raises(ToolError, match="ERR_HTTP2_PROTOCOL_ERROR"):
            session.open(URL)
        assert len(chromium.launch_calls) == 1

    def test_a_person_who_said_headless_gets_no_window(self, monkeypatch, xvfb):
        session, chromium = shielded(monkeypatch, ShieldedPage(fail=DROPPED),
                                     ShieldedPage())
        session._window_retry = False
        with pytest.raises(ToolError):
            session.open(URL)
        assert len(chromium.launch_calls) == 1


class TestTheSwitch:
    @pytest.mark.parametrize("raw,on", [("", True), ("1", True),
                                        ("0", False), ("off", False)])
    def test_only_headed_zero_turns_the_retry_off(self, monkeypatch, raw, on):
        monkeypatch.setenv("YANTRA_BROWSER_HEADED", raw)
        assert browser_window_retry() is on


class TestHandingOverAPageThatNeverLoaded:
    def test_finish_gives_the_address_that_failed(self, monkeypatch, xvfb):
        opened: list[str] = []
        monkeypatch.setattr(browser_mod.webbrowser, "open",
                            lambda url: opened.append(url) or True)
        session, _ = shielded(monkeypatch, ShieldedPage(fail=DROPPED),
                              ShieldedPage(fail=DROPPED))
        with pytest.raises(ToolError):
            session.open(URL)
        assert session.url == URL           # what the approval card names
        session.handoff("finish", "window")
        assert opened == [URL]

    def test_finish_after_the_browser_closed_still_has_the_address(
            self, monkeypatch, xvfb):
        session, _ = shielded(monkeypatch, ShieldedPage(fail=DROPPED),
                              ShieldedPage(fail=DROPPED))
        with pytest.raises(ToolError):
            session.open(URL)
        session.close()                     # a turn ended in between
        out = session.handoff("finish", "link")
        assert URL in out and "link" in out

    def test_return_still_needs_a_page(self, monkeypatch, xvfb):
        session, _ = shielded(monkeypatch, ShieldedPage(fail=DROPPED),
                              ShieldedPage(fail=DROPPED))
        with pytest.raises(ToolError):
            session.open(URL)
        session.close()
        with pytest.raises(ToolError, match="no page open"):
            session.handoff("return", "window")

    def test_nothing_ever_opened_is_still_nothing_to_hand(self):
        with pytest.raises(ToolError, match="no page open"):
            BrowserSession(profile=None, close_after=None).handoff(
                "finish", "link")
