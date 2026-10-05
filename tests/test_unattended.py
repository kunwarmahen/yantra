"""A run nobody is watching: it never waits, and it says what it needed.

The bias these tests encode: AN UNATTENDED RUN MUST NEITHER HANG ON A
PERSON NOR HIDE THAT IT NEEDED ONE. Every place Yantra would wait -- a
permission prompt, ask_user, a browser handoff, a profile another Yantra
holds -- either answers at once or gives up within a stated time, and
what it gave up on is in the JSON a scheduler reads, not only in prose
the model may or may not write.

The second bias is about what a person ALREADY said: tools named with
--allow-tools run without asking, except the ones that ask on every
call, which no answer given ahead of time is allowed to cover.

Everything is offline: a scripted provider, fake pages, and the lock
tested against a second real process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from test_browser import URL, FakePage, make_session
from yantra import unattended
from yantra.config import browser_handoff
from yantra.errors import ToolError, UserUnavailable
from yantra.permissions import PermissionRequest, allow_named, allow_read_only
from yantra.tools.base import ToolContext
from yantra.tools.browser import BrowserHandoff
from yantra.tools.profile_lock import LOCK_NAME, ProfileLock


@pytest.fixture(autouse=True)
def _fresh():
    unattended.clear()
    yield
    unattended.clear()


def request(name: str, *, read_only: bool = False,
            always_ask: bool = False) -> PermissionRequest:
    return PermissionRequest(tool_name=name, arguments={}, summary=name,
                             read_only=read_only, always_ask=always_ask)


class TestAllowedAheadOfTime:
    def test_a_named_tool_runs_without_asking(self):
        gate = allow_named(["browser_*"])
        assert gate(request("browser_open")) is True

    def test_an_unnamed_write_falls_through_and_is_refused(self):
        gate = allow_named(["browser_*"])
        req = request("write_file")
        assert gate(req) is False
        assert "unattended" in req.reason

    def test_read_only_tools_need_no_name(self):
        assert allow_named([])(request("read_file", read_only=True)) is True

    def test_a_tool_that_asks_every_time_is_not_covered_by_a_name(self):
        """A purchase named in advance is still a purchase nobody approved."""
        seen = []

        def inner(req):
            seen.append(req.tool_name)
            return allow_read_only(req)
        gate = allow_named(["shop_*"], inner=inner)
        assert gate(request("shop_buy", always_ask=True)) is False
        assert seen == ["shop_buy"]

    def test_blank_patterns_name_nothing(self):
        assert allow_named(["", "  "])(request("bash")) is False


class TestNobodyToHandTo:
    def test_unattended_wins_over_the_handoff_setting(self, monkeypatch):
        monkeypatch.setenv("YANTRA_BROWSER_HANDOFF", "window")
        monkeypatch.setenv("YANTRA_UNATTENDED", "1")
        assert browser_handoff() == "nobody"

    def test_attended_runs_keep_their_setting(self, monkeypatch):
        monkeypatch.setenv("YANTRA_BROWSER_HANDOFF", "link")
        assert browser_handoff() == "link"

    def test_the_need_is_written_down_and_the_model_told_to_stop(
            self, monkeypatch):
        opened = []
        import yantra.tools.browser as browser_mod
        monkeypatch.setattr(browser_mod.webbrowser, "open",
                            lambda url: opened.append(url) or True)
        session = make_session(FakePage())
        session.open(URL)
        out = session.handoff("return", "nobody", "sign in to x.com again")
        assert opened == []                        # no window, no link opened
        assert "Stop using the browser" in out
        assert unattended.needs() == [f"{URL}: sign in to x.com again"]

    def test_the_tool_needs_no_approval_when_nobody_is_there(self):
        """A gate that refused it would hide the one thing the run
        most needs to report."""
        tool = BrowserHandoff(make_session(FakePage()), "nobody")
        assert tool.read_only is True
        assert "Nobody is watching this run" in tool.description
        assert BrowserHandoff(make_session(FakePage()), "link").read_only is False

    def test_the_tool_passes_its_reason_through(self):
        session = make_session(FakePage())
        session.open(URL)
        tool = BrowserHandoff(session, "nobody")
        tool.run({"mode": "return", "reason": "2FA code needed"},
                 ToolContext(cwd=Path(".")))
        assert unattended.needs() == [f"{URL}: 2FA code needed"]


class RefusingPage(FakePage):
    """x.com to a signed-out headless browser: a 403, thrown by Chromium
    when no page comes with it, or answered with a page when one does."""

    def __init__(self, *, throws: bool) -> None:
        super().__init__()
        self.throws = throws

    def goto(self, url, **kwargs):
        super().goto(url, **kwargs)
        if self.throws:
            raise RuntimeError(f"Page.goto: net::ERR_HTTP_RESPONSE_CODE_FAILURE at {url}")
        return type("Response", (), {"status": 403})()


class TestARefusingSiteShowsTheWayOut:
    """Live, x.com started answering a signed-out browser with a 403. The
    model explained it in prose, the run counted as done, and the
    schedule told the person again every morning instead of pausing."""

    def test_unattended_a_thrown_403_points_at_the_handoff(self, monkeypatch):
        monkeypatch.setenv("YANTRA_UNATTENDED", "1")
        session = make_session(RefusingPage(throws=True))
        with pytest.raises(ToolError) as err:
            session.open(URL)
        assert "ERR_HTTP_RESPONSE_CODE_FAILURE" in str(err.value)
        assert "call browser_handoff now" in str(err.value)

    def test_unattended_a_403_page_says_so_above_the_page(self, monkeypatch):
        monkeypatch.setenv("YANTRA_UNATTENDED", "1")
        out = make_session(RefusingPage(throws=False)).open(URL)
        assert out.startswith(f"(HTTP 403: {URL} refused this browser.)")
        assert "call browser_handoff now" in out

    def test_the_model_decides_nothing_is_written_down_for_it(self, monkeypatch):
        monkeypatch.setenv("YANTRA_UNATTENDED", "1")
        make_session(RefusingPage(throws=False)).open(URL)
        assert unattended.needs() == []

    def test_a_blank_page_is_not_named_as_where_to_go(self):
        session = make_session(FakePage(url="about:blank"))
        session.open(URL)
        session.handoff("return", "nobody", "x.com refused the browser (403)")
        assert unattended.needs() == ["x.com refused the browser (403)"]

    def test_with_somebody_there_nothing_is_added(self, monkeypatch):
        monkeypatch.delenv("YANTRA_UNATTENDED", raising=False)
        with pytest.raises(ToolError) as err:
            make_session(RefusingPage(throws=True)).open(URL)
        assert "browser_handoff" not in str(err.value)
        assert "browser_handoff" not in make_session(RefusingPage(throws=False)).open(URL)


class TestNobodyToAsk:
    def test_a_question_is_kept_and_fails_the_turn(self):
        with pytest.raises(UserUnavailable, match="which account"):
            unattended.NobodyChannel().ask("which account?", [])
        assert unattended.needs() == ["a question: which account?"]

    def test_needs_are_said_once_each(self):
        unattended.note("sign in")
        unattended.note("  sign   in ")
        assert unattended.needs() == ["sign in"]


class TestProfileLock:
    def test_a_second_holder_is_refused_with_the_first_ones_name(
            self, tmp_path):
        first = ProfileLock(tmp_path)
        first.acquire(wait=0)
        try:
            with pytest.raises(ToolError) as exc:
                ProfileLock(tmp_path).acquire(wait=0)
            assert f"pid {os.getpid()}" in str(exc.value)
            assert "--browse-login" in str(exc.value)
        finally:
            first.release()

    def test_a_busy_profile_is_kept_apart_from_needs(self, tmp_path):
        first = ProfileLock(tmp_path)
        first.acquire(wait=0)
        try:
            with pytest.raises(ToolError):
                ProfileLock(tmp_path).acquire(wait=0)
        finally:
            first.release()
        assert unattended.needs() == []
        assert len(unattended.busy()) == 1

    def test_released_means_free(self, tmp_path):
        first = ProfileLock(tmp_path)
        first.acquire(wait=0)
        first.release()
        second = ProfileLock(tmp_path)
        second.acquire(wait=0)
        assert second.held
        second.release()

    def test_taking_a_lock_you_hold_is_a_no_op(self, tmp_path):
        lock = ProfileLock(tmp_path)
        lock.acquire(wait=0)
        lock.acquire(wait=0)
        lock.release()
        assert not lock.held

    def test_a_killed_holder_lets_go(self, tmp_path):
        """No stale lock to clean up: the kernel releases it with the
        process."""
        code = (f"import sys,time; sys.path.insert(0, {str(Path('src').resolve())!r});"
                "from yantra.tools.profile_lock import ProfileLock;"
                f"ProfileLock({str(tmp_path)!r}).acquire(wait=0);"
                "print('held', flush=True); time.sleep(60)")
        child = subprocess.Popen([sys.executable, "-c", code],
                                 stdout=subprocess.PIPE, text=True)
        try:
            assert child.stdout.readline().strip() == "held"
            with pytest.raises(ToolError, match=f"pid {child.pid}"):
                ProfileLock(tmp_path).acquire(wait=0)
        finally:
            child.kill()
            child.wait()
            child.stdout.close()
        lock = ProfileLock(tmp_path)
        lock.acquire(wait=1)
        assert lock.held
        lock.release()

    def test_a_waiting_launch_gets_it_once_it_is_free(self, tmp_path):
        first = ProfileLock(tmp_path)
        first.acquire(wait=0)
        import threading
        threading.Timer(0.3, first.release).start()
        started = time.monotonic()
        second = ProfileLock(tmp_path)
        second.acquire(wait=5)
        assert second.held and time.monotonic() - started < 4
        second.release()

    def test_the_wait_is_longer_for_a_schedule(self, monkeypatch):
        from yantra.tools import profile_lock
        assert profile_lock.lock_wait() == profile_lock.ATTENDED_WAIT
        monkeypatch.setenv("YANTRA_UNATTENDED", "1")
        assert profile_lock.lock_wait() == profile_lock.UNATTENDED_WAIT
        monkeypatch.setenv("YANTRA_BROWSER_WAIT", "3")
        assert profile_lock.lock_wait() == 3.0

    def test_the_session_takes_the_lock_before_launching(self, tmp_path,
                                                         monkeypatch):
        """Refused before Playwright starts: a busy profile costs no
        browser launch, and the holder is named."""
        pytest.importorskip("playwright")
        import yantra.tools.browser as browser_mod
        monkeypatch.setattr(browser_mod, "check_profile_reachable",
                            lambda *a: None)
        monkeypatch.setenv("YANTRA_BROWSER_WAIT", "0")
        held = ProfileLock(tmp_path)
        held.acquire(wait=0)
        try:
            session = browser_mod.BrowserSession(profile=tmp_path,
                                                 executable=None)
            with pytest.raises(ToolError, match="in use by another Yantra"):
                session._launch()
            assert session._pw is None
            assert not session._lock.held
        finally:
            held.release()
        assert (tmp_path / LOCK_NAME).exists()


class TestOneShotJson:
    def _run(self, monkeypatch, tmp_path, argv, script):
        import yantra.cli.main as cli_main
        provider = ScriptedProvider(script)
        monkeypatch.setattr(cli_main, "guess_provider", lambda: "anthropic")
        monkeypatch.setattr(cli_main, "load_settings", lambda name: object())
        monkeypatch.setattr(cli_main, "get_provider", lambda *a, **k: provider)
        # main() sets this itself for --unattended; registered here first
        # so the test's teardown takes it back out of the environment.
        monkeypatch.setenv("YANTRA_UNATTENDED", "")
        monkeypatch.setenv("YANTRA_ENV_CONTEXT", "off")
        monkeypatch.setenv("YANTRA_SETU", "off")
        monkeypatch.setenv("YANTRA_MEMORY", "off")
        return cli_main.main(["--cwd", str(tmp_path), *argv])

    def test_json_needs_a_prompt(self, tmp_path, monkeypatch, capsys):
        assert self._run(monkeypatch, tmp_path, ["--json"], []) == 2
        assert "--json answers one prompt" in capsys.readouterr().err

    def test_stdout_is_one_object_and_nothing_else(self, tmp_path,
                                                    monkeypatch, capsys):
        rc = self._run(monkeypatch, tmp_path,
                       ["--json", "--prompt", "hi"],
                       [assistant_text("hello there")])
        out = capsys.readouterr().out
        assert rc == 0
        run = json.loads(out)                  # one object: nothing else on stdout
        assert run["format"] == "yantra.run.v1"
        assert run["ok"] is True and run["stop_reason"] == "end_turn"
        assert run["text"] == "hello there"
        assert run["needs_person"] == [] and run["busy"] == []
        assert run["refused"] == []

    def test_an_unattended_write_is_refused_and_reported(self, tmp_path,
                                                          monkeypatch, capsys):
        rc = self._run(monkeypatch, tmp_path,
                       ["--json", "--unattended", "--prompt", "save it"],
                       [assistant_tool_call("c1", "write_file",
                                            {"path": "x.txt", "content": "x"}),
                        assistant_text("could not save")])
        run = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert not (tmp_path / "x.txt").exists()
        # Reported, but not a need: the model was told no and answered.
        assert run["refused"] == ["write_file"]
        assert run["needs_person"] == []

    def test_a_tool_allowed_ahead_of_time_runs(self, tmp_path, monkeypatch,
                                               capsys):
        rc = self._run(monkeypatch, tmp_path,
                       ["--json", "--unattended", "--allow-tools", "write_*",
                        "--prompt", "save it"],
                       [assistant_tool_call("c1", "write_file",
                                            {"path": "x.txt", "content": "x"}),
                        assistant_text("saved")])
        run = json.loads(capsys.readouterr().out)
        assert rc == 0 and run["ok"]
        assert (tmp_path / "x.txt").read_text() == "x"
        assert run["needs_person"] == []

    def test_a_question_nobody_can_answer_still_prints_the_object(
            self, tmp_path, monkeypatch, capsys):
        rc = self._run(monkeypatch, tmp_path,
                       ["--json", "--unattended", "--prompt", "help"],
                       [assistant_tool_call("c1", "ask_user",
                                            {"question": "which inbox?"})])
        run = json.loads(capsys.readouterr().out)
        assert rc == 1
        assert run["ok"] is False and run["stop_reason"] == "error"
        assert run["needs_person"] == ["a question: which inbox?"]


class TestNobodyWithNoPage:
    def test_a_page_that_never_loaded_is_not_named(self):
        """X refused the headless browser outright: there is no address
        to put in front of the reason."""
        session = make_session(FakePage())
        session.handoff("return", "nobody", "sign in to X")
        assert unattended.needs() == ["sign in to X"]


class TestOneRecordPerTurn:
    """A service runs several unattended turns at once; each one's
    sign-in wall is its own."""

    def test_a_scope_is_unattended_and_keeps_its_own_record(self):
        assert not unattended.is_unattended()
        with unattended.scope() as record:
            assert unattended.is_unattended()
            unattended.note("sign in to x.com")
            unattended.note_refused("bash")
        assert record.needs == ["sign in to x.com"]
        assert record.refused == ["bash"]
        assert unattended.needs() == [] and unattended.refused() == []

    def test_two_turns_at_once_do_not_mix(self):
        import asyncio

        async def turn(need):
            with unattended.scope() as record:
                await asyncio.sleep(0.01)
                await asyncio.to_thread(unattended.note, need)
                await asyncio.sleep(0.01)
                return record.needs

        async def both():
            return await asyncio.gather(turn("a"), turn("b"))
        assert asyncio.run(both()) == [["a"], ["b"]]

    def test_the_browser_worker_sees_the_turns_record(self, tmp_path):
        """The lock is taken on the browser's own thread; a busy profile
        must land on the turn that hit it."""
        held = ProfileLock(tmp_path)
        held.acquire(wait=0)
        session = make_session(FakePage())
        try:
            with unattended.scope() as record:
                with pytest.raises(ToolError):
                    session._call(lambda: ProfileLock(tmp_path).acquire(wait=0))
            assert len(record.busy) == 1
            assert unattended.busy() == []
        finally:
            held.release()
            session.close()
