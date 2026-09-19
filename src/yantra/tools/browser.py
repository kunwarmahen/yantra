"""Real browsing -- four verbs on a real browser, behind an extra.

web_fetch reads the web's DOCUMENTS; these tools operate its APPS. The
difference is JavaScript: modern pages ship an empty shell and build
themselves in the browser, so web_fetch's stdlib stripper finds nothing
to strip -- and bot-defended sites refuse plain HTTP clients outright.
A real engine answers both, which is why this family rides Playwright
rather than more HTML parsing.

Scope decisions worth writing down:

* OPTIONAL EXTRA, CONDITIONALLY REGISTERED. ``uv add
  'yantra[browse]'`` plus ``playwright install chromium`` is the
  whole opt-in -- installing the dependency IS the signal. The four
  browser_* tools appear in default_registry only when playwright is
  importable, so users who never asked for a ~300MB browser keep the
  old tool count (and the tool-selection threshold keyed to it) intact.
* TEXT OUT, NO SCREENSHOTS. Every action returns readable prose plus
  numbered interactive element refs ([e1], [e2], ...) harvested from
  the live DOM; clicking/filling takes a ref and returns the refreshed
  page. Screenshots would dogfood the image pipeline but text is the
  wire format local models are best at -- vision stays opt-in elsewhere.
* NOT read_only, ALL FOUR. Same rule as web_fetch: this is network
  egress from OUTSIDE every sandbox wall, and a browser compounds it
  (form submissions mutate remote state). Each verb gates individually;
  --yolo owns the tradeoff explicitly.
* LOGINS LIVE IN A PROFILE, NEVER IN MODEL CONTEXT. With
  $YANTRA_BROWSER_PROFILE set, every session launches the browser on
  that on-disk profile (launch_persistent_context), so one login --
  the human's via --browse-login's headed window, or the model's via
  an approved fill -- survives restarts. Deliberately NOT a
  get-cookies verb: raw session tokens in model context are one
  prompt injection away from exfiltration; a persisted profile keeps
  them invisible to the model entirely, while the agent simply IS
  logged in. Unset, each session starts fresh -- nothing persists,
  which is also the default.
* THE BROWSER CAN BE ONE THE MACHINE ALREADY HAS.
  $YANTRA_BROWSER_EXECUTABLE takes a Playwright channel ('chrome') or
  a path ('/snap/bin/brave'); $YANTRA_BROWSER_HEADED=1 runs with a
  real window, on a self-started Xvfb when no display is attached. The
  automation flags come off every launch regardless. This is not
  disguise -- the bundled default is a stripped headless SHELL whose
  user-agent says HeadlessChrome and which carries no codecs, no
  Widevine and no API keys, and being a browser people actually run is
  all a robot check is asking. notes/58 argues the whole of it.
* HONEST ABOUT WALLS. Captchas and bot detection still refuse us --
  none of the above is stealth, and no profile changes that. Login
  walls refuse us only until the profile carries a login; when a site
  serves a robot check, the snapshot shows the check instead of
  pretending the mission succeeded.

One mechanical subtlety: Playwright's sync API binds its objects to
the thread that started them, but tools execute on whatever worker
thread the loop hands them (asyncio.to_thread in the async agent).
So every BrowserSession operation funnels through a single-worker
executor -- one thread sees all the traffic, whichever loop twin runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ClassVar

from yantra.config import BROWSER_CHANNELS, browser_executable, \
    browser_headed, browser_login_command, browser_profile, \
    shadowed_by_shell
from yantra.errors import ToolError
from yantra.tools.base import Tool, ToolContext, require_str
from yantra.tools.web_fetch import MAX_RESULT_CHARS, _clip

#: How long a navigation may take before we call the page dead.
GOTO_TIMEOUT_MS = 15_000
#: Per-action cap (click/fill) -- enough for slow frameworks, bounded.
ACTION_TIMEOUT_MS = 10_000
#: A short settle beat after navigation/actions: domcontentloaded fires
#: before SPA frameworks paint their content into the DOM.
SETTLE_MS = 400
#: Element refs listed per snapshot; beyond this the model gets a count.
MAX_ELEMENTS = 60
#: The invisible screen headed mode gets when no display is attached.
XVFB_SCREEN = "1920x1080x24"
#: How long Xvfb has to create its socket before we call the try lost.
XVFB_START_TIMEOUT_S = 5.0
#: Display numbers we will claim -- :0-:9 belong to real logins.
XVFB_DISPLAYS = range(90, 120)

#: Chromium announces its driver in two places, and sites read both:
#: --enable-automation (which is what sets navigator.webdriver) and the
#: AutomationControlled Blink feature. Dropping them does not disguise
#: anything -- the browser IS ordinary; these flags were the only part
#: claiming otherwise.
_AUTOMATION_ARGS = ("--disable-blink-features=AutomationControlled",)
_AUTOMATION_DEFAULT_ARGS_DROPPED = ("--enable-automation",)

_BROWSER_EXTRA_HINT = (
    "playwright is not installed -- the browser_* tools are the optional "
    "[browse] extra:\n"
    "  uv add 'yantra[browse]'\n"
    "  uv run playwright install chromium"
)

#: One evaluate() per snapshot: tag every visible interactive element
#: with a ref attribute AND collect the page text in the same pass, so
#: the model's view and the click targets can never disagree.
_SNAPSHOT_JS = """
() => {
  const sel = [
    'a[href]', 'button', 'input', 'textarea', 'select', 'summary',
    '[role="button"]', '[role="link"]', '[role="textbox"]',
    '[role="checkbox"]', '[role="combobox"]',
  ].join(', ');
  const labelOf = (el) => {
    const attr = el.getAttribute('aria-label') || el.getAttribute('placeholder')
      || el.value || el.getAttribute('name');
    return ((attr || el.innerText || '').replace(/\\s+/g, ' ').trim()
      ).slice(0, 80);
  };
  const kindOf = (el) => {
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role');
    const type = (el.type || '').toLowerCase();
    if (tag === 'a' || role === 'link') return 'link';
    if (tag === 'button' || tag === 'summary' || role === 'button'
        || type === 'submit' || type === 'button') return 'button';
    if (tag === 'textarea' || role === 'textbox'
        || (tag === 'input'
            && ['text', 'search', 'email', 'password', 'tel', 'url',
                'number'].includes(type))) return 'textbox';
    if (tag === 'select' || role === 'combobox') return 'select';
    if (type === 'checkbox' || role === 'checkbox') return 'checkbox';
    return 'other';
  };
  const elements = [];
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if ((box.width === 0 && box.height === 0)
        || style.visibility === 'hidden' || style.display === 'none') continue;
    const ref = 'e' + (++n);
    el.setAttribute('data-yantra-ref', ref);
    elements.push({ref, kind: kindOf(el), label: labelOf(el)});
  }
  return {
    text: document.body ? document.body.innerText : '',
    elements,
  };
}
"""


def _require_http(url: str) -> str:
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        # file:// would turn the browser into an unconfined file reader --
        # refused for the same reason web_fetch refuses it.
        raise ToolError(f"only http(s) urls are supported, got {scheme!r}")
    return url


def _terminate(proc: subprocess.Popen) -> None:
    """Ask a child to stop, insist if it will not."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def _is_snap(executable: str) -> bool:
    """True when this binary runs under snap confinement."""
    if executable.startswith("/snap/"):
        return True
    try:
        # /snap/bin/brave is a symlink to /usr/bin/snap, which re-execs
        # the real binary inside the sandbox -- the confinement follows
        # whichever spelling of the name you launched.
        return Path(executable).resolve().name == "snap"
    except OSError:
        return False


def _hidden_part(path: Path) -> str | None:
    """The first dot-directory in a path, if any -- snap cannot see them."""
    return next((part for part in path.parts if part.startswith(".")
                 and part not in (".", "..")), None)


def check_profile_reachable(profile: Path, executable: str | None) -> None:
    """Refuse a profile the chosen browser would silently fail to write.

    Public because the CLI asks FIRST, before it promises the user a
    window: an encouraging banner followed by a refusal reads as a
    crash, and the refusal is the useful half.

    A snap-confined browser is allowed into $HOME but NOT into its
    hidden directories, and it does not complain: it exits 0 having
    created nothing, so a login window appears, takes your password and
    saves the session precisely nowhere. Better to refuse the
    combination up front than to hand back an empty profile that looks
    like a working one.
    """
    if executable is None or not _is_snap(executable):
        return
    hidden = _hidden_part(profile)
    if hidden is None:
        return
    shell = ("\nthis path came from YANTRA_BROWSER_PROFILE exported in your "
             "SHELL, which\noutranks the .env you are probably looking at -- "
             "`unset YANTRA_BROWSER_PROFILE`\nfirst, or fix the export\n"
             if shadowed_by_shell("YANTRA_BROWSER_PROFILE") else "")
    raise ToolError(
        f"{executable} is a snap, and snap-confined browsers cannot write "
        f"into hidden directories -- {profile} is under {hidden!r}, so the "
        "profile would be silently discarded (the browser exits 0 and "
        f"creates nothing).\n{shell}"
        "point YANTRA_BROWSER_PROFILE somewhere visible in your home, e.g.\n"
        "  YANTRA_BROWSER_PROFILE=~/yantra-browser-profile\n"
        "or use a non-snap browser (a .deb Chrome/Chromium has no such "
        "restriction)")


class _VirtualDisplay:
    """An X server with no monitor -- the invisible half of headed mode.

    Headed is what defeats headless fingerprinting, but a window on the
    user's screen during an agent run is its own kind of broken (and on
    a server there is no screen at all). Xvfb resolves the
    contradiction: a real display the browser paints into honestly,
    which nothing renders. The session owns the process and kills it on
    teardown.

    Display numbers are claimed by looking for a free socket and then
    racing for it. Two Yantras starting in the same millisecond can pick
    the same number; the loser sees Xvfb die and tries the next one,
    which is cheaper than a lock file nobody cleans up.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self.display = ""

    def start(self) -> str:
        if shutil.which("Xvfb") is None:
            raise ToolError(
                "YANTRA_BROWSER_HEADED=1 needs somewhere to put the window: "
                "no display is attached and Xvfb is not installed.\n"
                "  sudo apt install xvfb      (Debian/Ubuntu)\n"
                "  sudo dnf install xorg-x11-server-Xvfb    (Fedora)\n"
                "or run where $DISPLAY is set, or drop "
                "YANTRA_BROWSER_HEADED to go back to headless")
        for number in XVFB_DISPLAYS:
            socket = Path(f"/tmp/.X11-unix/X{number}")
            if socket.exists():
                continue
            proc = subprocess.Popen(
                ["Xvfb", f":{number}", "-screen", "0", XVFB_SCREEN,
                 "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + XVFB_START_TIMEOUT_S
            while time.monotonic() < deadline:
                if socket.exists():
                    self._proc = proc
                    self.display = f":{number}"
                    return self.display
                if proc.poll() is not None:
                    break  # lost the race for this number, or Xvfb refused
                time.sleep(0.05)
            _terminate(proc)
        raise ToolError(
            f"could not start Xvfb on any display in {XVFB_DISPLAYS.start}-"
            f"{XVFB_DISPLAYS.stop - 1} -- every number was taken or Xvfb "
            "would not start")

    def stop(self) -> None:
        if self._proc is not None:
            try:
                _terminate(self._proc)
            except Exception:
                pass  # a display we cannot kill is not the caller's problem
        self._proc = None
        self.display = ""


class BrowserSession:
    """The live browser behind all four tools: launch lazily, one page.

    Two launch modes share one body: ephemeral (no profile -- a fresh
    Chromium every time) and persistent ($YANTRA_BROWSER_PROFILE set --
    launch_persistent_context on that dir, so cookies/localStorage
    survive restarts). State machine: nothing open -> page open ->
    closed. ``open`` is the only door in; ``close`` is idempotent; any
    action without a page says so in model-readable words. All
    playwright traffic runs on ONE dedicated worker thread (see module
    docstring for why).
    """

    def __init__(self, profile: Path | None | str = "default",
                 executable: str | None = "default",
                 headed: bool | None = None) -> None:
        #: ``"default"``/None sentinels: read the environment once, at
        #: construction -- tests (and embedders) pass values instead.
        self._profile: Path | None = (
            browser_profile() if profile == "default" else profile)
        self._executable: str | None = (
            browser_executable() if executable == "default" else executable)
        self._headed: bool = (
            browser_headed() if headed is None else headed)
        self._pw = None
        self._browser = None   # ephemeral mode
        self._context = None   # persistent mode (launch_persistent_context)
        self._page = None
        self._elements: dict[str, dict] = {}
        self._exec: ThreadPoolExecutor | None = None
        self._display: _VirtualDisplay | None = None

    # -- plumbing ----------------------------------------------------------

    def _call(self, fn):
        """Run fn on the session's single worker thread, await the result."""
        if self._exec is None:
            self._exec = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="yantra-browser")
        return self._exec.submit(fn).result()

    def _launch_options(self) -> dict[str, Any]:
        """Every knob the two launch doors share, resolved once.

        ``channel=`` and ``executable_path=`` are the same choice spelled
        two ways -- Playwright finds a branded build BY NAME on any OS,
        or takes the path when the browser is somewhere only this
        machine knows (a snap, a flatpak, a build directory).
        """
        options: dict[str, Any] = {
            "headless": not self._headed,
            "args": list(_AUTOMATION_ARGS),
            "ignore_default_args": list(_AUTOMATION_DEFAULT_ARGS_DROPPED),
        }
        if self._executable is not None:
            if self._executable in BROWSER_CHANNELS:
                options["channel"] = self._executable
            else:
                options["executable_path"] = self._executable
        if self._headed and not (os.environ.get("DISPLAY")
                                 or os.environ.get("WAYLAND_DISPLAY")):
            self._display = _VirtualDisplay()
            # env= reaches the BROWSER process only: nothing else in this
            # Yantra learns about a display it has no business drawing on.
            options["env"] = {**os.environ, "DISPLAY": self._display.start()}
        return options

    def _launch(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ToolError(_BROWSER_EXTRA_HINT) from exc
        if self._profile is not None:
            check_profile_reachable(self._profile, self._executable)
        try:
            options = self._launch_options()
            self._pw = sync_playwright().start()
            if self._profile is None:
                self._browser = self._pw.chromium.launch(**options)
                self._page = self._browser.new_page()
            else:
                # Chromium locks the dir -- one live session per profile,
                # which is also the guard against two agents fighting
                # over one identity.
                self._profile.mkdir(parents=True, exist_ok=True)
                self._context = self._pw.chromium.launch_persistent_context(
                    user_data_dir=str(self._profile), **options)
                pages = self._context.pages
                self._page = pages[0] if pages else self._context.new_page()
        except ToolError:
            raise
        except Exception as exc:
            self._teardown()
            named = (f" ({self._executable})" if self._executable
                     else "")
            raise ToolError(
                f"could not start the browser{named}: "
                f"{type(exc).__name__}: {exc}\n"
                "if the browser binary itself is missing, run:\n"
                "  uv run playwright install chromium\n"
                "or point YANTRA_BROWSER_EXECUTABLE at a browser you "
                "already have") from exc

    def _teardown(self) -> None:
        for obj, closer in ((self._page, "close"),
                            (self._context, "close"),  # closes its pages too
                            (self._browser, "close"),
                            (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, closer)()
                except Exception:
                    pass  # tearing down a corpse; never mask the real error
        self._pw = self._browser = self._context = self._page = None
        self._elements = {}
        if self._display is not None:
            self._display.stop()  # outlives the browser otherwise
            self._display = None

    def _require_page(self):
        if self._page is None:
            raise ToolError("no page open -- browser_open(url) first")
        return self._page

    # -- the four verbs ----------------------------------------------------

    def open(self, url: str) -> str:
        """Navigate (url given) or re-read the current page (empty url)."""
        return self._call(lambda: self._open(url))

    def click(self, ref: str) -> str:
        return self._call(lambda: self._click(ref))

    def fill(self, ref: str, text: str) -> str:
        return self._call(lambda: self._fill(ref, text))

    def close(self) -> bool:
        return self._call(self._shutdown)

    # -- bodies (all on the worker thread) ---------------------------------

    def _snapshot(self) -> str:
        data = self._page.evaluate(_SNAPSHOT_JS)
        title = (self._page.title() or "").strip()
        header = (f"title: {title}\nsource: {self._page.url}\n\n" if title
                  else f"source: {self._page.url}\n\n")
        text = (data.get("text") or "").strip() or "[no text content]"

        elements = data.get("elements") or []
        self._elements = {e["ref"]: e for e in elements}
        shown = elements[:MAX_ELEMENTS]
        lines = [f'[{e["ref"]}] {e["kind"]} {e["label"]}'.rstrip()
                 for e in shown]
        blocks = [header, _clip(text, MAX_RESULT_CHARS)]
        if lines:
            blocks.append(
                "interactive elements (pass a ref to browser_click/"
                "browser_fill):\n" + "\n".join(lines)
                + (f"\n[showing {MAX_ELEMENTS} of {len(elements)} elements]"
                   if len(elements) > MAX_ELEMENTS else ""))
        else:
            blocks.append("(no interactive elements found)")
        return "\n".join(blocks)

    def _settle(self) -> None:
        try:
            self._page.wait_for_timeout(SETTLE_MS)
            self._page.wait_for_load_state("domcontentloaded",
                                           timeout=ACTION_TIMEOUT_MS)
        except Exception:
            pass  # SPAs may never fire it again post-hydration; snap anyway

    def _resolve(self, ref: str) -> dict:
        element = self._elements.get(ref)
        if element is None:
            known = ", ".join(sorted(self._elements)[:8]) or "(none yet)"
            raise ToolError(
                f"unknown ref {ref!r} -- refs come from your LAST browser "
                f"output; known: {known}")
        return element

    @staticmethod
    def _locator(page, ref: str):
        return page.locator(f'[data-yantra-ref="{ref}"]')

    def _open(self, url: str) -> str:
        if url:
            _require_http(url)  # refuse before paying for a launch
        elif self._page is None:
            raise ToolError("no page open -- pass a url to open one")
        if self._pw is None:
            self._launch()
        if url:
            try:
                self._page.goto(url, wait_until="domcontentloaded",
                                timeout=GOTO_TIMEOUT_MS)
            except ToolError:
                raise
            except Exception as exc:
                raise ToolError(
                    f"could not load {url}: {type(exc).__name__}: {exc}"
                    ) from exc
            self._settle()
        return self._snapshot()

    def _act(self, ref: str, verb: str) -> str:
        page = self._require_page()
        self._resolve(ref)
        try:
            getattr(self._locator(page, ref), verb)(
                timeout=ACTION_TIMEOUT_MS)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"{verb} on {ref} failed: {type(exc).__name__}: {exc} -- "
                "the page may have changed since your last snapshot; "
                "browser_open(url) reloads it") from exc
        self._settle()
        return self._snapshot()

    def _click(self, ref: str) -> str:
        return self._act(ref, "click")

    def _fill(self, ref: str, text: str) -> str:
        page = self._require_page()
        element = self._resolve(ref)
        try:
            locator = self._locator(page, ref)
            if element["kind"] == "select":
                locator.select_option(label=text, timeout=ACTION_TIMEOUT_MS)
            elif element["kind"] in ("textbox",):
                locator.fill(text, timeout=ACTION_TIMEOUT_MS)
            else:
                raise ToolError(
                    f'{ref} is a {element["kind"]}; text goes into textboxes '
                    "(and dropdown options) -- browser_click presses buttons")
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(
                f"fill on {ref} failed: {type(exc).__name__}: {exc} -- "
                "the page may have changed; browser_open(url) reloads it"
                ) from exc
        self._settle()
        return self._snapshot()

    def _shutdown(self) -> bool:
        was_open = self._page is not None
        self._teardown()
        if self._exec is not None:
            self._exec.shutdown(wait=False)  # we ARE the worker; safe
            self._exec = None  # next verb builds a fresh one
        return was_open


def _profile_has_state(profile: Path) -> bool:
    """True once a browser has actually written this profile directory."""
    return (profile / "Local State").exists() or (profile / "Default").is_dir()


def _run_unautomated_login(command: str, profile: Path,
                           url: str | None) -> None:
    """Run a real browser as a PLAIN SUBPROCESS, wait for it to close.

    NO PLAYWRIGHT, deliberately -- this is the whole reason the function
    exists. A headed Playwright window is still an automated one: it
    carries --enable-automation, answers CDP, and reports
    navigator.webdriver, and the large identity providers refuse to
    sign you in when they see that ("this browser or app may not be
    secure"). Headed never fixed it because headed was never the thing
    being detected. Launched this way the browser is not automated in
    any sense -- it is the same binary you use yourself, pointed at a
    different profile directory -- so the sign-in is as ordinary as
    sign-ins get, and the cookies it leaves behind are what every later
    headless run rides.

    The catch is that the login profile and the agent profile must be
    written by the SAME browser: Chromium refuses a profile stamped by
    a newer version of itself. That is why this path opens only when
    YANTRA_BROWSER_EXECUTABLE names the browser both halves will use.
    """
    argv = [command, f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check"]
    if url:
        argv.append(url)
    try:
        proc = subprocess.Popen(argv)
    except OSError as exc:
        raise ToolError(
            f"could not run {command}: {type(exc).__name__}: {exc}") from exc
    try:
        proc.wait()  # the window IS the progress bar
    except KeyboardInterrupt:
        _terminate(proc)
        raise
    if not _profile_has_state(profile):
        # Two ways to get here, and the user cannot tell them apart from
        # the outside: snap confinement refusing a hidden directory
        # (silently, exit 0), or the browser handing the URL to a copy
        # of itself that was already running and exiting immediately.
        raise ToolError(
            f"{command} exited without writing anything to {profile} -- "
            "nothing was saved.\n"
            "usually one of two things:\n"
            "  * that browser was ALREADY RUNNING, so it handed the window "
            "to the running copy and quit -- close it and try again\n"
            "  * the profile sits in a directory the browser is not allowed "
            "to write (snap confinement cannot see hidden dirs) -- put "
            "YANTRA_BROWSER_PROFILE somewhere visible in your home")


def run_login_session(profile: Path, url: str | None = None,
                      executable: str | None = "default") -> None:
    """Open a HEADED browser on ``profile`` and block until it closes.

    The human half of profile persistence -- 2FA, captchas, SSO are
    beaten by hand ONCE in a visible window (``--browse-login``), and
    every later headless session on the same profile simply IS logged
    in. The model's half needs nothing new: an approved browser_fill of
    a login form lands in the same profile.

    TWO DOORS, and which one opens is the difference between a login
    that works and one that is refused. With YANTRA_BROWSER_EXECUTABLE
    naming a browser this machine already has, the window is a plain
    subprocess of that browser with no automation attached to it at all
    (_run_unautomated_login) -- the only form that gets past a sign-in
    page checking for robots. Without it, this falls back to
    Playwright's own headed Chromium, which beats captchas and 2FA but
    is still visibly automated, and unbranded besides.

    Raises ToolError for the known walls (missing extra, missing
    binary, unreachable profile); a locked profile or other launch
    failure propagates as ToolError too.
    """
    # Scheme check FIRST: it costs nothing and needs no browser, so a
    # file:// URL gets the honest answer whether or not the optional
    # extra is installed. Behind the import it would masquerade as a
    # missing dependency on machines without playwright.
    if url:
        _require_http(url)
    if executable == "default":
        executable = browser_executable()
    check_profile_reachable(profile, executable)
    profile.mkdir(parents=True, exist_ok=True)

    command = browser_login_command(executable)
    if command is not None:
        _run_unautomated_login(command, profile, url)
        return

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ToolError(_BROWSER_EXTRA_HINT) from exc
    options: dict[str, Any] = {
        "headless": False,
        "args": list(_AUTOMATION_ARGS),
        "ignore_default_args": list(_AUTOMATION_DEFAULT_ARGS_DROPPED),
    }
    if executable in BROWSER_CHANNELS:
        options["channel"] = executable
    pw = sync_playwright().start()
    try:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile), **options)
        page = context.pages[0] if context.pages else context.new_page()
        if url:
            page.goto(url, wait_until="domcontentloaded",
                      timeout=GOTO_TIMEOUT_MS)
        context.wait_for_event("close")  # the window IS the progress bar
    except Exception as exc:
        raise ToolError(
            f"could not open the login window: {type(exc).__name__}: {exc}\n"
            "if the browser binary itself is missing, run:\n"
            "  uv run playwright install chromium\n"
            "if another session holds this profile, close it first -- "
            "Chromium locks the directory") from exc
    finally:
        try:
            pw.stop()
        except Exception:
            pass  # the corpse's problems are not the caller's


class _BrowserTool(Tool):
    """Shared wiring: every verb speaks to the same BrowserSession."""

    read_only = False  # network egress family -- gates, like web_fetch

    def __init__(self, browser: BrowserSession) -> None:
        self.browser = browser


class BrowserOpen(_BrowserTool):
    name = "browser_open"
    description = (
        "Open a URL in a real headless Chromium browser -- JavaScript "
        "runs, so JS-rendered pages work where web_fetch sees an empty "
        "shell. Returns the page as readable text plus numbered "
        "interactive elements ([e1], [e2], ...) for browser_click/"
        "browser_fill. Call again with NO url to re-read the current "
        "page. If a persistent profile is configured, logins survive "
        "restarts -- filling a login form once is enough. Bot checks and "
        "captchas may still refuse; one page at a time."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "url": {"type": "string",
                    "description": "Absolute http(s) URL. Omit to re-read "
                                   "the page already open."},
        },
        "additionalProperties": False,
    }

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return (f"browse {args['url']}" if args.get("url")
                else "re-read current page")

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return self.browser.open(require_str(args, "url", optional=True))


class BrowserClick(_BrowserTool):
    name = "browser_click"
    description = (
        "Click an element on the open browser page by its ref ([eN] from "
        "your latest browser output) and get the refreshed page back -- "
        "including wherever the click navigates."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "ref": {"type": "string",
                    "description": "Element ref from the latest browser "
                                   "output, e.g. 'e3'."},
        },
        "required": ["ref"],
        "additionalProperties": False,
    }

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return f"click {require_str(args, 'ref')}"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return self.browser.click(require_str(args, "ref"))


class BrowserFill(_BrowserTool):
    name = "browser_fill"
    description = (
        "Type text into a textbox/textarea on the open browser page, or "
        "pick an option in a dropdown, by its [eN] ref; returns the "
        "refreshed page. Combine with browser_click on a submit button "
        "to run searches and forms."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "ref": {"type": "string",
                    "description": "Textbox/dropdown ref from the latest "
                                   "browser output, e.g. 'e2'."},
            "text": {"type": "string",
                     "description": "Text to type, or the dropdown option "
                                    "to select."},
        },
        "required": ["ref", "text"],
        "additionalProperties": False,
    }

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        text = require_str(args, "text")
        return f'fill {require_str(args, "ref")} "{text[:40]}{"…" if len(text) > 40 else ""}"'

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return self.browser.fill(require_str(args, "ref"),
                                 require_str(args, "text"))


class BrowserClose(_BrowserTool):
    name = "browser_close"
    description = (
        "Shut the headless browser down and free it. Harmless if none is "
        "open; a later browser_open starts a fresh one."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def summary(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return "close browser"

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        if self.browser.close():
            return "browser closed"
        return "no browser was open"


def browser_available() -> bool:
    """True when playwright is importable -- default_registry's gate."""
    return find_spec("playwright") is not None


def browser_tools() -> tuple[Tool, ...]:
    """The four browser verbs sharing one session, or () without the extra."""
    if not browser_available():
        return ()
    browser = BrowserSession()  # reads $YANTRA_BROWSER_PROFILE itself
    return (BrowserOpen(browser), BrowserClick(browser),
            BrowserFill(browser), BrowserClose(browser))
