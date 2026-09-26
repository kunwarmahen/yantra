"""browser_handoff: the page goes to a person, and the right one comes back.

The bias these tests encode: A HANDOFF MUST NEVER SHOW THE PERSON A
PAGE THEY DID NOT SEE IN THE APPROVAL PROMPT, AND MUST NEVER WAIT FOR
SOMEBODY WHO CANNOT BE THERE. The first is why the tool takes no
address -- it hands over the page the agent is on, and the prompt
names that page. The second is why ``return`` refuses outright when
there is no screen or no profile, instead of opening a window nobody
sees or one whose sign-in evaporates as it closes.

Everything runs offline: the page is test_browser's FakePage, the
person's window is a stub that records what it was asked to open, and
the person's own browser is a stub of ``webbrowser.open``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_browser import URL, FakePage, make_session
from yantra.config import browser_handoff
from yantra.errors import ConfigError, ToolError
from yantra.tools.base import ToolContext
from yantra.tools.browser import (HANDOFF_WAIT_SECONDS, BrowserHandoff,
                                  BrowserSession, _run_unautomated_login)
import yantra.tools.browser as browser_mod


@pytest.fixture(autouse=True)
def _a_screen(monkeypatch):
    """A desktop, unless a test takes it away."""
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("YANTRA_BROWSER_HANDOFF", raising=False)


@pytest.fixture
def opened(monkeypatch):
    """Stub the person's own browser; the list is what it was asked to open."""
    calls: list[str] = []

    def fake_open(url):
        calls.append(url)
        return True
    monkeypatch.setattr(browser_mod.webbrowser, "open", fake_open)
    return calls


def session_on(page: FakePage, profile: Path | None = None):
    session = make_session(page)
    session._profile = profile
    session.open(URL)   # a snapshot, so the session knows its address
    return session


class TestFinish:
    def test_the_page_opens_in_the_persons_browser(self, opened):
        page = FakePage()
        session = session_on(page)
        out = session.handoff("finish", "window")
        assert opened == [URL]
        assert "Your part is done" in out
        assert page.closed and session._page is None   # agent lets go

    def test_no_screen_means_a_link_in_the_answer(self, opened):
        out = session_on(FakePage()).handoff("finish", "link")
        assert opened == []
        assert URL in out and "link" in out

    def test_a_browser_that_would_not_open_falls_back_to_the_link(
            self, monkeypatch):
        monkeypatch.setattr(browser_mod.webbrowser, "open", lambda url: False)
        out = session_on(FakePage()).handoff("finish", "window")
        assert URL in out and "link" in out

    def test_nothing_open_nothing_to_hand(self):
        with pytest.raises(ToolError, match="no page open"):
            BrowserSession(profile=None, executable=None, headed=False,
                           close_after=0.0).handoff("finish", "window")


class TestReturn:
    @pytest.fixture
    def window(self, monkeypatch):
        """Stub the person's window. Records what it opened and whether the
        agent's own browser had already stepped aside -- one per profile."""
        seen: dict = {}

        def fake_window(profile, url, executable, timeout):
            seen.update(profile=profile, url=url, timeout=timeout,
                        agent_page_closed=seen["page"].closed)
            return seen.get("person_closes", True)
        monkeypatch.setattr(browser_mod, "_person_window", fake_window)
        return seen

    def _relaunch_into(self, session, monkeypatch, page: FakePage):
        def fake_launch():
            session._pw, session._context, session._page = (
                object(), object(), page)
        monkeypatch.setattr(session, "_launch", fake_launch)

    def test_the_person_helps_and_the_agent_carries_on(
            self, tmp_path, monkeypatch, window):
        first = FakePage()
        window["page"] = first
        session = session_on(first, profile=tmp_path)
        after = FakePage(text="Inbox (3)")
        self._relaunch_into(session, monkeypatch, after)

        out = session.handoff("return", "window")

        assert window["url"] == URL and window["profile"] == tmp_path
        assert window["timeout"] == HANDOFF_WAIT_SECONDS
        assert window["agent_page_closed"]       # stepped aside first
        assert after.gotos == [URL]              # the SAME page, reopened
        assert "closed the window" in out and "Inbox (3)" in out

    def test_a_window_nobody_closed_says_so(self, tmp_path, monkeypatch,
                                            window):
        first = FakePage()
        window.update(page=first, person_closes=False)
        session = session_on(first, profile=tmp_path)
        self._relaunch_into(session, monkeypatch, FakePage())
        out = session.handoff("return", "window")
        assert "had not closed the window" in out
        assert "unfinished" in out

    def test_no_screen_is_refused_not_waited_on(self, tmp_path, monkeypatch,
                                                window):
        monkeypatch.delenv("DISPLAY")
        first = FakePage()
        window["page"] = first
        with pytest.raises(ToolError, match="no screen"):
            session_on(first, profile=tmp_path).handoff("return", "window")
        assert "url" not in window               # nothing opened
        assert not first.closed                  # and nothing lost

    def test_link_reach_is_refused_too(self, tmp_path, window):
        window["page"] = FakePage()
        with pytest.raises(ToolError, match="mode='finish'"):
            session_on(window["page"], profile=tmp_path).handoff(
                "return", "link")

    def test_no_profile_is_refused(self, window):
        window["page"] = FakePage()
        with pytest.raises(ToolError, match="YANTRA_BROWSER_PROFILE"):
            session_on(window["page"]).handoff("return", "window")


class TestTheTool:
    def test_it_takes_no_address(self):
        assert "url" not in BrowserHandoff.parameters["properties"]
        assert BrowserHandoff.requires == ("browser_open",)
        assert BrowserHandoff.read_only is False     # a person must approve

    def test_the_prompt_names_the_page_that_will_open(self, tmp_path):
        tool = BrowserHandoff(session_on(FakePage()), "window")
        text = tool.summary({"mode": "finish", "reason": "book the 10:13"},
                            ToolContext(cwd=tmp_path))
        assert URL in text and "book the 10:13" in text

    def test_a_mode_that_is_neither_is_the_models_to_fix(self, tmp_path):
        tool = BrowserHandoff(session_on(FakePage()), "window")
        with pytest.raises(ToolError, match="finish' or 'return"):
            tool.run({"mode": "later", "reason": "x"},
                     ToolContext(cwd=tmp_path))


class TestReach:
    def test_a_screen_means_a_window(self):
        assert browser_handoff() == "window"

    def test_no_screen_means_a_link(self, monkeypatch):
        monkeypatch.delenv("DISPLAY")
        assert browser_handoff() == "link"

    @pytest.mark.parametrize("raw, expected", [
        ("window", "window"), ("link", "link"), ("off", None)])
    def test_the_operator_decides(self, monkeypatch, raw, expected):
        monkeypatch.setenv("YANTRA_BROWSER_HANDOFF", raw)
        assert browser_handoff() == expected

    def test_nonsense_is_refused(self, monkeypatch):
        monkeypatch.setenv("YANTRA_BROWSER_HANDOFF", "popup")
        with pytest.raises(ConfigError, match="window, link or off"):
            browser_handoff()


class TestAWindowThatWaitsTooLong:
    def test_it_is_asked_to_close_the_way_ctrl_c_asks(self, tmp_path,
                                                      monkeypatch):
        (tmp_path / "Local State").write_text("{}")   # a written profile
        asked: list = []

        class Proc:
            pid = 12345

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired("chrome", timeout)

        monkeypatch.setattr(browser_mod.subprocess, "Popen",
                            lambda argv, **kw: Proc())
        monkeypatch.setattr(browser_mod, "_close_login_browser",
                            lambda proc: asked.append(proc) or True)
        assert _run_unautomated_login("chrome", tmp_path, URL,
                                      timeout=0.01) is False
        assert len(asked) == 1
