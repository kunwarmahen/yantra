"""browser_* tools: refs, lifecycle, gating, conditional registration.

The suite stays offline-green WITHOUT playwright installed: tests
inject a FakePage implementing the sliver of the page API the session
touches, so run() exercises the real snapshot/ref/selector logic. The
launch paths are pinned by stubbing the playwright import itself.

The browser-choice tests encode one bias above all: A LOGIN THAT SAVES
NOTHING MUST NOT LOOK LIKE A LOGIN THAT WORKED. Every way of getting
there is silent in real life -- a snap-confined browser refused a
hidden profile directory and exits 0, a browser already running hands
its window to the running copy and exits 0, and a profile written
under one cookie key is EMPTIED by a browser holding another -- so
each has a test demanding a loud refusal or a count that can be
wrong. The rest pin that the automation flags are always dropped,
that a channel and a path reach Playwright by their own doors,
and that headed mode never leaves an Xvfb behind.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

import yantra.cli.main as cli_main
from yantra.errors import ConfigError, ToolError
from yantra.tools import BrowserClick, BrowserClose, BrowserFill, \
    BrowserOpen, default_registry
from yantra.tools.base import ToolContext
from yantra.tools.browser import MAX_ELEMENTS, BrowserSession, \
    _COOKIE_KEY_ARG, LoginInterrupted, _VirtualDisplay, _cookie_count, \
    run_login_session

URL = "https://fake.local/"


@pytest.fixture(autouse=True)
def _no_inherited_browser_env(monkeypatch):
    """The suite must say what browser it means -- never the dev's .env."""
    monkeypatch.delenv("YANTRA_BROWSER_EXECUTABLE", raising=False)
    monkeypatch.delenv("YANTRA_BROWSER_HEADED", raising=False)


#: What every launch carries now: headless unless asked otherwise, the
#: two flags that used to announce a driver gone, and the cookie key
#: store named rather than inherited -- see _COOKIE_KEY_ARG.
BASE_OPTIONS = {
    "headless": True,
    "args": ["--disable-blink-features=AutomationControlled",
             "--password-store=basic"],
    "ignore_default_args": ["--enable-automation"],
}


@pytest.fixture
def ctx(tmp_path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


# ---------------------------------------------------------------------------
# FakePage: the contract under test is that the session talks to a page
# through goto/url/title/evaluate/locator/wait_* ONLY, and that evaluate()
# receives THE tagging script (the thing real clicks resolve against).
# ---------------------------------------------------------------------------


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self.page = page
        self.selector = selector

    def click(self, timeout=None) -> None:
        self.page.clicks.append(self.selector)
        self.page.threads.add(threading.get_ident())
        if self.page.on_click:
            self.page.on_click()

    def fill(self, text, timeout=None) -> None:
        self.page.fills.append((self.selector, text))

    def select_option(self, label=None, timeout=None) -> None:
        self.page.selects.append((self.selector, label))


class FakePage:
    def __init__(self, *, title="Fake Page", url=URL, text="hello world",
                 elements=None, on_click=None) -> None:
        self.title_value = title
        self.url = url
        self.text = text
        self.elements = elements if elements is not None else [
            {"ref": "e1", "kind": "link", "label": "Home"},
            {"ref": "e2", "kind": "textbox", "label": "Search"},
        ]
        self.on_click = on_click
        self.gotos: list[str] = []
        self.clicks: list[str] = []
        self.fills: list = []
        self.selects: list = []
        self.closed = False
        self.threads: set[int] = set()  # worker threads seen at the page

    def title(self):
        return self.title_value

    def goto(self, url, **kwargs):
        self.threads.add(threading.get_ident())
        self.gotos.append(url)

    def evaluate(self, script):
        assert "data-yantra-ref" in script  # snapshots must tag targets
        self.threads.add(threading.get_ident())
        return {"text": self.text, "elements": self.elements}

    def locator(self, selector):
        return FakeLocator(self, selector)

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, state, timeout=None):
        pass

    def close(self):
        self.closed = True


def make_session(page: FakePage) -> BrowserSession:
    """A session that believes it already launched, around a fake page."""
    session = BrowserSession()
    session._pw = object()  # truthy is all _open checks before touching it
    session._browser = object()
    session._page = page
    return session


ELEMENTS_LINE = "interactive elements (pass a ref to browser_click/browser_fill):"


class TestSnapshots:
    def test_open_returns_header_text_and_elements(self):
        out = make_session(FakePage()).open(URL)
        assert f"title: Fake Page\nsource: {URL}" in out
        assert "hello world" in out
        assert "[e1] link Home" in out
        assert "[e2] textbox Search" in out
        assert ELEMENTS_LINE in out

    def test_unlabeled_element_omits_the_label(self):
        page = FakePage(elements=[{"ref": "e3", "kind": "button",
                                   "label": ""}])
        out = make_session(page).open(URL)
        assert out.endswith("[e3] button")  # no stray space, no empty quotes

    def test_empty_page_says_so_without_elements_section(self):
        page = FakePage(text="", elements=[])
        out = make_session(page).open(URL)
        assert "[no text content]" in out
        assert "(no interactive elements found)" in out

    def test_long_text_clips_head_and_tail(self):
        out = make_session(FakePage(text="x" * 30_000)).open(URL)
        assert "[... 22000 chars omitted ...]" in out

    def test_element_list_caps_with_a_count(self):
        many = [{"ref": f"e{i}", "kind": "link", "label": f"L{i}"}
                for i in range(1, 71)]
        out = make_session(FakePage(elements=many)).open(URL)
        assert f"[showing {MAX_ELEMENTS} of 70 elements]" in out
        assert "[e60]" in out and "[e61]" not in out

    def test_open_without_url_needs_a_page(self):
        with pytest.raises(ToolError, match="no page open -- pass a url"):
            BrowserSession().open("")

    def test_second_open_navigates_the_same_page(self):
        page = FakePage()
        session = make_session(page)
        session.open(URL)
        session.open(f"{URL}page2")
        assert page.gotos == [URL, f"{URL}page2"]

    def test_non_http_scheme_refused_before_launching(self):
        with pytest.raises(ToolError, match="only http"):
            BrowserSession().open("file:///etc/passwd")


class TestRefs:
    def test_click_targets_the_ref_attribute_and_refreshes(self):
        def navigate():
            page.text = "results for query"
            page.elements = [{"ref": "e1", "kind": "link", "label": "Next"}]
        page = FakePage(on_click=navigate)
        session = make_session(page)
        session.open(URL)
        out = session.click("e1")
        assert page.clicks == ['[data-yantra-ref="e1"]']
        assert "results for query" in out  # refreshed snapshot came back
        assert '[e1] link Next' in out

    def test_fill_types_into_a_textbox_ref(self):
        page = FakePage()
        session = make_session(page)
        session.open(URL)
        out = session.fill("e2", "wire adapters")
        assert page.fills == [('[data-yantra-ref="e2"]', "wire adapters")]
        assert ELEMENTS_LINE in out  # refreshed page returned

    def test_fill_on_a_dropdown_selects_the_option_by_label(self):
        page = FakePage(elements=[{"ref": "e5", "kind": "select",
                                   "label": "Category"}])
        session = make_session(page)
        session.open(URL)
        session.fill("e5", "books")
        assert page.selects == [('[data-yantra-ref="e5"]', "books")]

    def test_fill_on_a_button_is_refused_with_guidance(self):
        page = FakePage(elements=[{"ref": "e4", "kind": "button",
                                   "label": "Go"}])
        session = make_session(page)
        session.open(URL)
        with pytest.raises(ToolError, match=r"e4 is a button"):
            session.fill("e4", "nope")

    def test_unknown_ref_lists_known_refs(self):
        session = make_session(FakePage())
        session.open(URL)
        with pytest.raises(ToolError, match="known: e1, e2"):
            session.click("e9")

    def test_action_without_a_page_names_the_door(self):
        with pytest.raises(ToolError, match="browser_open"):
            BrowserSession().click("e1")
        with pytest.raises(ToolError, match="browser_open"):
            BrowserSession().fill("e1", "x")


class TestLifecycle:
    def test_close_reports_and_resets(self):
        page = FakePage()
        session = make_session(page)
        session.open(URL)
        assert session.close() is True
        assert page.closed
        assert session._pw is None and session._page is None

    def test_close_when_never_opened_is_a_noop(self):
        assert BrowserSession().close() is False

    def test_reopen_after_close_re_arms_launch(self, monkeypatch):
        session = make_session(FakePage())
        session.open(URL)
        session.close()
        monkeypatch.setitem(sys.modules, "playwright", None)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
        with pytest.raises(ToolError, match=r"yantra\[browse\]"):
            session.open(URL)  # launch door re-arms after close

    def test_all_traffic_on_one_worker_thread(self):
        # Playwright's sync API binds to its starting thread; whichever
        # loop twin calls us (asyncio.to_thread varies), the SESSION must
        # funnel every operation onto one dedicated worker.
        page = FakePage()
        session = make_session(page)
        session.open(URL)
        session.click("e1")
        session.fill("e2", "q")
        session.close()
        assert len(page.threads) == 1
        assert threading.get_ident() not in page.threads  # never the caller


class TestLaunchPaths:
    def test_missing_playwright_names_the_install(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "playwright", None)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
        with pytest.raises(ToolError,
                           match=r"yantra\[browse\]"):
            BrowserSession().open(URL)

    def test_chromium_binary_missing_names_playwright_install(self,
                                                              monkeypatch):
        class Boom:
            def start(self):
                raise RuntimeError("Executable doesn't exist at .../chromium")

        stub = types.ModuleType("playwright.sync_api")
        stub.sync_playwright = lambda: Boom()
        monkeypatch.setitem(sys.modules, "playwright",
                            types.ModuleType("playwright"))
        monkeypatch.setitem(sys.modules, "playwright.sync_api", stub)
        with pytest.raises(ToolError, match="playwright install chromium"):
            BrowserSession().open(URL)


class FakeContext:
    """The sliver of BrowserContext the session touches: pages list,
    new_page, wait_for_event('close'), close."""

    def __init__(self, pages=()) -> None:
        self.pages = list(pages)
        self.closed = False
        self.waited_for: str | None = None

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    def wait_for_event(self, event: str) -> None:
        self.waited_for = event

    def close(self) -> None:
        self.closed = True


class FakeProc:
    """A browser subprocess that opens, is closed by the human, exits."""

    def __init__(self) -> None:
        self.waited = False

    def wait(self) -> int:
        self.waited = True
        return 0


class FakeChromium:
    """Records WHICH launch door was used -- launch() vs
    launch_persistent_context() is the whole ephemeral/persistent
    contract."""

    def __init__(self, browser=None, context=None) -> None:
        self.launch_calls: list[dict] = []
        self.persistent_calls: list[dict] = []
        self._browser = browser
        self._context = context

    def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        return self._browser

    def launch_persistent_context(self, **kwargs):
        self.persistent_calls.append(kwargs)
        return self._context


def install_fake_playwright(monkeypatch, chromium: FakeChromium) -> list:
    """Swap the playwright import for a recorder; returns the started
    playwright instances (so tests can pin that stop() always runs)."""
    started: list = []

    class FakePW:
        def __init__(self) -> None:
            self.chromium = chromium
            self.stopped = False

        def stop(self) -> None:
            self.stopped = True
            started.append(self)

    stub = types.ModuleType("playwright.sync_api")
    stub.sync_playwright = lambda: types.SimpleNamespace(start=FakePW)
    monkeypatch.setitem(sys.modules, "playwright",
                        types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", stub)
    return started


class TestPersistentProfile:
    """$YANTRA_BROWSER_PROFILE set => launch_persistent_context on that
    dir; unset => today's plain launch. Same four verbs either way."""

    def test_ephemeral_session_launches_a_plain_browser(self, monkeypatch):
        ephemeral = types.SimpleNamespace(new_page=FakePage)
        chromium = FakeChromium(browser=ephemeral)
        install_fake_playwright(monkeypatch, chromium)
        session = BrowserSession(profile=None)
        session.open(URL)
        assert chromium.launch_calls == [BASE_OPTIONS]
        assert chromium.persistent_calls == []

    def test_profile_launches_a_persistent_context_on_the_dir(
            self, monkeypatch, tmp_path):
        chromium = FakeChromium(context=FakeContext())
        install_fake_playwright(monkeypatch, chromium)
        out = BrowserSession(profile=tmp_path).open(URL)
        (kwargs,) = chromium.persistent_calls
        assert kwargs == {"user_data_dir": str(tmp_path),
                          **BASE_OPTIONS}
        assert chromium.launch_calls == []
        assert "hello world" in out  # traffic flows through the context page

    def test_existing_context_page_is_reused_not_duplicated(
            self, monkeypatch):
        page = FakePage()
        context = FakeContext(pages=[page])  # persistent contexts ship one
        chromium = FakeChromium(context=context)
        install_fake_playwright(monkeypatch, chromium)
        session = BrowserSession(profile=Path("/tmp/never-made"))
        session.open(URL)
        session.open(f"{URL}page2")
        assert len(context.pages) == 1
        assert page.gotos == [URL, f"{URL}page2"]

    def test_close_shuts_down_the_context(self, monkeypatch):
        context = FakeContext()
        chromium = FakeChromium(context=context)
        install_fake_playwright(monkeypatch, chromium)
        session = BrowserSession(profile=Path("/tmp/never-made"))
        session.open(URL)
        assert session.close() is True
        assert context.closed

    def test_default_sentinel_reads_the_env_once(self, monkeypatch, tmp_path):
        monkeypatch.setenv("YANTRA_BROWSER_PROFILE", str(tmp_path))
        assert BrowserSession()._profile == tmp_path
        monkeypatch.delenv("YANTRA_BROWSER_PROFILE")
        assert BrowserSession()._profile is None  # blank/unset => ephemeral

    def test_browser_tools_wire_the_env_knob_through_one_session(
            self, monkeypatch):
        monkeypatch.setattr("yantra.tools.browser.find_spec",
                            lambda name: True)
        monkeypatch.setenv("YANTRA_BROWSER_PROFILE", "~/yantra-prof")
        registry = default_registry()
        names = ("browser_open", "browser_click", "browser_fill",
                 "browser_close")
        sessions = {registry.get(n).browser for n in names}
        assert len(sessions) == 1
        (session,) = sessions
        assert session._profile == Path.home() / "yantra-prof"


class TestLoginSession:
    """run_login_session: the headed half of persistence -- a human beats
    the login wall once; every later headless run inherits it."""

    def test_missing_extra_names_the_install(self, monkeypatch, tmp_path):
        monkeypatch.setitem(sys.modules, "playwright", None)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
        with pytest.raises(ToolError, match=r"yantra\[browse\]"):
            run_login_session(tmp_path)

    def test_non_http_url_refused_before_any_launch(self, tmp_path):
        # no playwright stub installed -- refusal must not depend on it
        with pytest.raises(ToolError, match="only http"):
            run_login_session(tmp_path, "file:///etc/passwd")

    def test_opens_headed_on_the_profile_and_waits_for_the_window(
            self, monkeypatch, tmp_path):
        context = FakeContext()
        chromium = FakeChromium(context=context)
        started = install_fake_playwright(monkeypatch, chromium)
        run_login_session(tmp_path, URL)
        (kwargs,) = chromium.persistent_calls
        assert kwargs == {"user_data_dir": str(tmp_path),
                          **BASE_OPTIONS, "headless": False}
        assert context.pages[0].gotos == [URL]
        assert context.waited_for == "close"  # the window IS the progress bar
        assert started and started[0].stopped

    def test_stop_runs_even_when_waiting_explodes(self, monkeypatch,
                                                  tmp_path):
        context = FakeContext()

        def boom(event):
            raise RuntimeError("boom")

        context.wait_for_event = boom
        chromium = FakeChromium(context=context)
        started = install_fake_playwright(monkeypatch, chromium)
        with pytest.raises(ToolError,
                           match="could not open the login window"):
            run_login_session(tmp_path)
        assert started and started[0].stopped


class TestChosenBrowser:
    """$YANTRA_BROWSER_EXECUTABLE: a channel goes in by name, a path by
    path, and neither launch ever re-adds the automation flags."""

    def test_a_channel_name_reaches_playwright_as_a_channel(
            self, monkeypatch):
        chromium = FakeChromium(browser=types.SimpleNamespace(
            new_page=FakePage))
        install_fake_playwright(monkeypatch, chromium)
        BrowserSession(profile=None, executable="chrome").open(URL)
        assert chromium.launch_calls == [{**BASE_OPTIONS,
                                          "channel": "chrome"}]

    def test_a_path_reaches_playwright_as_an_executable_path(
            self, monkeypatch, tmp_path):
        chromium = FakeChromium(context=FakeContext())
        install_fake_playwright(monkeypatch, chromium)
        BrowserSession(profile=tmp_path,
                       executable="/opt/brave/brave").open(URL)
        (kwargs,) = chromium.persistent_calls
        assert kwargs["executable_path"] == "/opt/brave/brave"
        assert "channel" not in kwargs

    def test_the_env_var_resolves_a_bare_command_on_path(self, monkeypatch):
        monkeypatch.setenv("YANTRA_BROWSER_EXECUTABLE", "my-browser")
        monkeypatch.setattr("yantra.config.shutil.which",
                            lambda name: "/usr/local/bin/my-browser")
        assert BrowserSession()._executable == "/usr/local/bin/my-browser"

    def test_a_browser_that_is_not_installed_is_a_config_error(
            self, monkeypatch):
        monkeypatch.setenv("YANTRA_BROWSER_EXECUTABLE", "not-a-browser")
        monkeypatch.setattr("yantra.config.shutil.which", lambda name: None)
        with pytest.raises(ConfigError, match="YANTRA_BROWSER_EXECUTABLE"):
            BrowserSession()

    def test_launch_failure_names_the_browser_that_failed(self, monkeypatch):
        class Exploding(FakeChromium):
            def launch(self, **kwargs):
                raise RuntimeError("no such file")

        install_fake_playwright(monkeypatch, Exploding())
        session = BrowserSession(profile=None, executable="/opt/nope")
        with pytest.raises(ToolError, match="/opt/nope"):
            session.open(URL)


class TestHeadedMode:
    """$YANTRA_BROWSER_HEADED: a real window, on an invisible screen when
    there is no real one -- and never an Xvfb left running after close."""

    def test_headed_without_a_display_starts_and_stops_an_xvfb(
            self, monkeypatch, tmp_path):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        started, stopped = [], []

        class FakeDisplay:
            def start(self):
                started.append(self)
                return ":91"

            def stop(self):
                stopped.append(self)

        monkeypatch.setattr("yantra.tools.browser._VirtualDisplay",
                            FakeDisplay)
        chromium = FakeChromium(context=FakeContext())
        install_fake_playwright(monkeypatch, chromium)
        session = BrowserSession(profile=tmp_path, headed=True)
        session.open(URL)
        (kwargs,) = chromium.persistent_calls
        assert kwargs["headless"] is False
        assert kwargs["env"]["DISPLAY"] == ":91"
        assert len(started) == 1 and not stopped
        session.close()
        assert len(stopped) == 1  # the screen dies with the browser

    def test_headed_with_a_display_uses_it_and_starts_no_xvfb(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISPLAY", ":0")

        def explode():
            raise AssertionError("must not start Xvfb with a display up")

        monkeypatch.setattr("yantra.tools.browser._VirtualDisplay", explode)
        chromium = FakeChromium(context=FakeContext())
        install_fake_playwright(monkeypatch, chromium)
        BrowserSession(profile=tmp_path, headed=True).open(URL)
        (kwargs,) = chromium.persistent_calls
        assert kwargs["headless"] is False
        assert "env" not in kwargs  # the session's own DISPLAY is enough

    def test_missing_xvfb_names_the_package_to_install(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr("yantra.tools.browser.shutil.which",
                            lambda name: None)
        with pytest.raises(ToolError, match="xvfb"):
            _VirtualDisplay().start()

    def test_the_env_knob_is_a_boolean_and_says_so_when_it_is_not(
            self, monkeypatch):
        monkeypatch.setenv("YANTRA_BROWSER_HEADED", "1")
        assert BrowserSession()._headed is True
        monkeypatch.setenv("YANTRA_BROWSER_HEADED", "off")
        assert BrowserSession()._headed is False
        monkeypatch.setenv("YANTRA_BROWSER_HEADED", "sometimes")
        with pytest.raises(ConfigError, match="YANTRA_BROWSER_HEADED"):
            BrowserSession()


class TestUnautomatedLogin:
    """With a real browser named, --browse-login runs it as a PLAIN
    SUBPROCESS: the automated window is exactly what sign-in pages
    refuse, so the fix is to bring no automation at all."""

    def test_a_named_browser_is_run_as_a_subprocess_not_by_playwright(
            self, monkeypatch, tmp_path):
        chromium = FakeChromium(context=FakeContext())
        install_fake_playwright(monkeypatch, chromium)
        calls = []
        monkeypatch.setattr("yantra.tools.browser.subprocess.Popen",
                            lambda argv, **kw: calls.append(argv) or FakeProc())
        monkeypatch.setattr("yantra.tools.browser._profile_has_state",
                            lambda profile: True)
        run_login_session(tmp_path, URL, executable="/usr/bin/brave")
        (argv,) = calls
        assert argv[0] == "/usr/bin/brave"
        assert f"--user-data-dir={tmp_path}" in argv
        assert argv[-1] == URL
        assert chromium.persistent_calls == []  # playwright never touched

    def test_a_channel_is_looked_up_on_path_for_the_subprocess(
            self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr("yantra.config.shutil.which",
                            lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr("yantra.tools.browser.subprocess.Popen",
                            lambda argv, **kw: calls.append(argv) or FakeProc())
        monkeypatch.setattr("yantra.tools.browser._profile_has_state",
                            lambda profile: True)
        run_login_session(tmp_path, None, executable="chrome")
        assert calls[0][0] == "/usr/bin/google-chrome"

    def test_a_window_that_saved_nothing_is_refused_not_celebrated(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr("yantra.tools.browser.subprocess.Popen",
                            lambda argv, **kw: FakeProc())
        # nothing writes the profile -- exactly what snap confinement and
        # a browser handing off to a running copy both look like
        with pytest.raises(ToolError, match="ALREADY RUNNING"):
            run_login_session(tmp_path, URL, executable="/usr/bin/brave")

    def test_no_browser_named_still_opens_playwrights_own_window(
            self, monkeypatch, tmp_path):
        chromium = FakeChromium(context=FakeContext())
        install_fake_playwright(monkeypatch, chromium)
        run_login_session(tmp_path, URL, executable=None)
        assert len(chromium.persistent_calls) == 1  # unchanged fallback


class InterruptedProc:
    """A login browser the person Ctrl-C's out of, instead of closing.

    The first wait() is the window sitting open, interrupted by the
    keyboard. What happens next is the browser's answer to the signal
    it was sent: ``quits_in`` seconds to shut down cleanly, or None for
    a browser that never does. ``again`` is a second Ctrl-C arriving
    while it is still closing.
    """

    pid = 4242

    def __init__(self, quits_in: float | None = 0.5, again: bool = False):
        self.quits_in = quits_in
        self.again = again
        self.signals: list[int] = []
        self.killed = False
        self._waits = 0

    def wait(self, timeout=None):
        self._waits += 1
        if self._waits == 1:
            raise KeyboardInterrupt
        if self.killed:
            return -9
        if self.again and self._waits == 2:
            raise KeyboardInterrupt
        if self.quits_in is None or (timeout is not None
                                     and self.quits_in > timeout):
            raise subprocess.TimeoutExpired("chrome", timeout)
        return 0

    def send_signal(self, sig):
        self.signals.append(sig)

    def terminate(self):
        self.signals.append(signal.SIGTERM)

    def kill(self):
        self.killed = True


class TestCtrlCDuringLogin:
    """A Ctrl-C used to race the login browser's own shutdown -- the
    old path's two signals, sent to a real Chrome, kept a fresh sign-in
    3 times in 10. The browser now runs in its own session and is ASKED
    to quit with the one signal after which Chromium writes its
    cookies."""

    def _launch(self, monkeypatch, tmp_path, proc):
        seen = {}

        def popen(argv, **kw):
            seen.update(kw)
            return proc

        monkeypatch.setattr("yantra.tools.browser.subprocess.Popen", popen)
        groups = []

        def killpg(pid, sig):
            groups.append((pid, sig))
            proc.killed = True

        monkeypatch.setattr("yantra.tools.browser.os.killpg", killpg)
        monkeypatch.setattr("yantra.tools.browser._cookie_count",
                            lambda profile: 12)
        with pytest.raises(LoginInterrupted) as caught:
            run_login_session(tmp_path, URL, executable="/usr/bin/brave")
        return caught.value, seen, groups

    def test_the_browser_gets_its_own_session_so_ctrl_c_is_not_its(
            self, monkeypatch, tmp_path):
        _, seen, _ = self._launch(monkeypatch, tmp_path, InterruptedProc())
        assert seen.get("start_new_session") is True

    def test_it_is_asked_to_quit_with_one_sigint_and_nothing_else(
            self, monkeypatch, tmp_path):
        # SIGTERM exits just as fast and writes nothing; a second signal
        # on top of a quit under way is a race. One SIGINT, then wait.
        proc = InterruptedProc()
        stop, _, groups = self._launch(monkeypatch, tmp_path, proc)
        assert proc.signals == [signal.SIGINT]
        assert groups == [] and not proc.killed
        assert stop.closed is True and stop.cookies == 12

    def test_a_browser_that_will_not_close_is_killed_group_and_all(
            self, monkeypatch, tmp_path):
        proc = InterruptedProc(quits_in=None)
        stop, _, groups = self._launch(monkeypatch, tmp_path, proc)
        assert groups == [(proc.pid, signal.SIGKILL)]
        assert stop.closed is False

    def test_a_second_ctrl_c_is_someone_insisting_and_is_obeyed(
            self, monkeypatch, tmp_path):
        proc = InterruptedProc(again=True)
        stop, _, groups = self._launch(monkeypatch, tmp_path, proc)
        assert groups == [(proc.pid, signal.SIGKILL)]
        assert stop.closed is False

    def test_it_is_still_a_keyboard_interrupt_to_anyone_who_asks(self):
        assert issubclass(LoginInterrupted, KeyboardInterrupt)

    def test_a_clean_close_says_the_login_was_kept(self, monkeypatch,
                                                    tmp_path, capsys):
        monkeypatch.setenv("YANTRA_BROWSER_PROFILE", str(tmp_path))

        def interrupted(profile, url=None):
            raise LoginInterrupted(closed=True, cookies=46)

        monkeypatch.setattr("yantra.tools.browser.run_login_session",
                            interrupted)
        assert cli_main.main(["--browse-login", URL]) == 130
        out = " ".join(capsys.readouterr().out.split())  # rich wraps
        assert "is kept -- 46 cookies" in out
        assert "may not have reached disk" not in out

    def test_a_kill_says_the_login_may_be_lost(self, monkeypatch, tmp_path,
                                              capsys):
        monkeypatch.setenv("YANTRA_BROWSER_PROFILE", str(tmp_path))

        def interrupted(profile, url=None):
            raise LoginInterrupted(closed=False, cookies=0)

        monkeypatch.setattr("yantra.tools.browser.run_login_session",
                            interrupted)
        assert cli_main.main(["--browse-login", URL]) == 130
        assert "may not have reached disk" in " ".join(
            capsys.readouterr().out.split())


class TestSnapProfileGuard:
    """A snap browser cannot write hidden directories and does not say
    so -- it exits 0 having created nothing. Refuse the pairing."""

    def test_a_snap_browser_refuses_a_hidden_profile_directory(
            self, tmp_path):
        hidden = tmp_path / ".local" / "state" / "yantra"
        with pytest.raises(ToolError, match="snap"):
            run_login_session(hidden, URL, executable="/snap/bin/brave")

    def test_a_shell_override_is_named_so_editing_env_is_not_futile(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr("yantra.tools.browser.shadowed_by_shell",
                            lambda key: True)
        hidden = tmp_path / ".local" / "prof"
        with pytest.raises(ToolError, match="exported in your SHELL"):
            run_login_session(hidden, URL, executable="/snap/bin/brave")

    def test_without_an_override_the_refusal_does_not_blame_the_shell(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr("yantra.tools.browser.shadowed_by_shell",
                            lambda key: False)
        hidden = tmp_path / ".local" / "prof"
        with pytest.raises(ToolError) as caught:
            run_login_session(hidden, URL, executable="/snap/bin/brave")
        assert "SHELL" not in str(caught.value)

    def test_the_refusal_offers_a_visible_path(self, tmp_path):
        hidden = tmp_path / ".config" / "prof"
        with pytest.raises(ToolError, match="YANTRA_BROWSER_PROFILE"):
            run_login_session(hidden, URL, executable="/snap/bin/brave")

    def test_a_snap_browser_is_fine_with_a_visible_profile(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr("yantra.tools.browser.subprocess.Popen",
                            lambda argv, **kw: FakeProc())
        monkeypatch.setattr("yantra.tools.browser._profile_has_state",
                            lambda profile: True)
        run_login_session(tmp_path / "visible", URL,
                          executable="/snap/bin/brave")  # no raise

    def test_a_non_snap_browser_may_use_a_hidden_profile(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr("yantra.tools.browser.subprocess.Popen",
                            lambda argv, **kw: FakeProc())
        monkeypatch.setattr("yantra.tools.browser._profile_has_state",
                            lambda profile: True)
        run_login_session(tmp_path / ".local" / "prof", URL,
                          executable="/usr/bin/google-chrome")  # no raise

    def test_the_session_refuses_the_same_pairing_before_launching(
            self, monkeypatch, tmp_path):
        chromium = FakeChromium(context=FakeContext())
        install_fake_playwright(monkeypatch, chromium)
        session = BrowserSession(profile=tmp_path / ".hidden" / "p",
                                 executable="/snap/bin/brave")
        with pytest.raises(ToolError, match="snap"):
            session.open(URL)
        assert chromium.persistent_calls == []  # refused BEFORE the cost


class TestCookieKeyStore:
    """One key, both halves -- the bug that destroyed logins silently.

    Chromium picks its cookie encryption key from a launch flag.
    Playwright hardcodes --password-store=basic; a browser started
    without it uses the desktop keyring instead. Mix the two across a
    profile and the browser that cannot decrypt a cookie DELETES it, so
    a login beaten by hand vanishes on the agent's first launch --
    after --browse-login has already said it saved. These tests pin the
    flag onto every door, because the failure leaves no trace to debug.
    """

    def test_the_login_subprocess_names_the_same_key_store(
            self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr("yantra.tools.browser.subprocess.Popen",
                            lambda argv, **kw: calls.append(argv) or FakeProc())
        monkeypatch.setattr("yantra.tools.browser._profile_has_state",
                            lambda profile: True)
        run_login_session(tmp_path, URL, executable="/usr/bin/brave")
        (argv,) = calls
        assert _COOKIE_KEY_ARG in argv

    def test_the_agent_launch_names_it_rather_than_inheriting_it(
            self, monkeypatch, tmp_path):
        """Playwright supplies this flag by default today. Naming it
        anyway means an upstream default that changes quietly cannot
        take every stored login with it."""
        chromium = FakeChromium(context=FakeContext())
        install_fake_playwright(monkeypatch, chromium)
        BrowserSession(profile=tmp_path).open(URL)
        (kwargs,) = chromium.persistent_calls
        assert _COOKIE_KEY_ARG in kwargs["args"]

    def test_the_playwright_login_door_names_it_too(self, monkeypatch,
                                                    tmp_path):
        chromium = FakeChromium(context=FakeContext())
        install_fake_playwright(monkeypatch, chromium)
        run_login_session(tmp_path, URL, executable=None)
        (kwargs,) = chromium.persistent_calls
        assert _COOKIE_KEY_ARG in kwargs["args"]


class TestCookieCount:
    """The receipt behind "profile saved" -- a count, never a value.

    _profile_has_state answers whether the DIRECTORY looks like a
    profile, and stays true forever once anything has written it. It
    cannot tell a login that worked from one that saved nothing, which
    is exactly the question the human is asking.
    """

    def test_a_profile_with_no_cookie_store_counts_zero(self, tmp_path):
        assert _cookie_count(tmp_path) == 0

    def test_it_counts_rows_without_reading_a_single_value(self, tmp_path):
        import sqlite3
        db = tmp_path / "Default" / "Cookies"
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        conn.execute("create table cookies (host_key text, name text, "
                     "encrypted_value blob)")
        conn.executemany("insert into cookies values (?, ?, ?)",
                         [(".example.com", "sid", b"v11secret"),
                          (".example.com", "csrf", b"v11secret")])
        conn.commit()
        conn.close()
        assert _cookie_count(tmp_path) == 2

    def test_an_unreadable_store_counts_zero_rather_than_raising(
            self, tmp_path):
        """A login is not worth failing over a receipt: a file that is
        not a database, or a schema this Chromium does not use, reports
        nothing found instead of taking the command down with it."""
        db = tmp_path / "Default" / "Cookies"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"not a database at all")
        assert _cookie_count(tmp_path) == 0


class TestBrowseLoginFlag:
    """CLI wiring: its own mode, dispatched before provider resolution --
    no model, no API key, on purpose."""

    def test_without_a_profile_names_the_env_var(self, monkeypatch, capsys):
        monkeypatch.delenv("YANTRA_BROWSER_PROFILE", raising=False)
        assert cli_main.main(["--browse-login", URL]) == 2
        assert "YANTRA_BROWSER_PROFILE" in capsys.readouterr().err

    def test_with_a_profile_opens_and_reports_saved(self, monkeypatch,
                                                    tmp_path, capsys):
        monkeypatch.setenv("YANTRA_BROWSER_PROFILE", str(tmp_path))
        seen: dict = {}

        def fake_login(profile, url=None):
            seen["args"] = (profile, url)
            return 7  # cookies -- what the receipt counts

        monkeypatch.setattr("yantra.tools.browser.run_login_session",
                            fake_login)
        assert cli_main.main(["--browse-login", URL]) == 0
        assert seen["args"] == (tmp_path, URL)
        out = capsys.readouterr().out
        assert "profile saved" in out
        assert "7 cookies" in out

    def test_a_login_that_saved_no_cookies_says_so_instead_of_saved(
            self, monkeypatch, tmp_path, capsys):
        """The bug this replaces: "profile saved" printed on a profile
        holding nothing, because the old check only asked whether the
        DIRECTORY looked like a profile -- true forever once anything
        had written it. A count can be wrong out loud."""
        monkeypatch.setenv("YANTRA_BROWSER_PROFILE", str(tmp_path))
        monkeypatch.setattr("yantra.tools.browser.run_login_session",
                            lambda profile, url=None: 0)
        assert cli_main.main(["--browse-login", URL]) == 1
        out = capsys.readouterr().out
        assert "nothing was saved" in out
        assert "profile saved" not in out

    def test_combining_with_other_modes_is_refused(self):
        assert cli_main.main(["--browse-login", URL,
                              "--prompt", "hi"]) == 2


class TestGating:
    """Network egress family: every verb gates, like web_fetch."""

    @pytest.mark.parametrize("tool_cls",
                             [BrowserOpen, BrowserClick, BrowserFill,
                              BrowserClose])
    def test_not_read_only(self, tool_cls):
        assert tool_cls.read_only is False

    def test_summaries_show_what_will_happen(self, ctx):
        browser = BrowserSession()
        assert URL in BrowserOpen(browser).summary({"url": URL}, ctx)
        assert "e3" in BrowserClick(browser).summary({"ref": "e3"}, ctx)
        assert 'fill e2' in BrowserFill(browser).summary(
            {"ref": "e2", "text": "query"}, ctx)
        assert "close" in BrowserClose(browser).summary({}, ctx)

    def test_missing_required_args_are_model_readable(self, ctx):
        browser = BrowserSession()
        with pytest.raises(ToolError, match="missing required argument"):
            BrowserClick(browser).run({}, ctx)
        with pytest.raises(ToolError, match="missing required argument"):
            BrowserFill(browser).run({"ref": "e1"}, ctx)


class TestRegistration:
    """The [browse] extra is its own opt-in: no playwright, no tools --
    and the tool count the selection threshold keys on stays put."""

    def test_without_extra_registry_stays_at_sixteen(self, monkeypatch):
        monkeypatch.setattr("yantra.tools.browser.find_spec",
                            lambda name: None)
        registry = default_registry()
        assert len(registry) == 16
        assert "browser_open" not in registry

    def test_with_extra_four_tools_share_one_session(self, monkeypatch):
        monkeypatch.setattr("yantra.tools.browser.find_spec",
                            lambda name: True)
        registry = default_registry()
        names = ("browser_open", "browser_click", "browser_fill",
                 "browser_close")
        assert len(registry) == 20
        for name in names:
            assert name in registry
        sessions = {registry.get(n).browser for n in names}
        assert len(sessions) == 1  # one browser behind all four verbs
