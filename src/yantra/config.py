"""Resolve provider credentials/models from environment variables.

Base-URL contract (matches the official SDKs so env files stay portable):

* ``ANTHROPIC_BASE_URL`` EXCLUDES the version segment -- the adapter
  appends ``/v1/messages``:
      https://api.anthropic.com     (direct)
      https://openrouter.ai/api     (OpenRouter)
* ``OPENAI_BASE_URL`` INCLUDES it -- the adapter appends ``/chat/completions``:
      https://api.openai.com/v1     (direct)
      https://openrouter.ai/api/v1  (OpenRouter)
* same for ``RESPONSES_BASE_URL`` -- the adapter appends ``/responses``
  (the Responses API lives on the same /v1 surface as chat-completions)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from yantra.errors import ConfigError
from yantra.providers.base import ProviderSettings

_DEFAULTS: dict[str, dict[str, str]] = {
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "model": "claude-sonnet-4-5",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    # OpenAI's Responses API: same /v1 surface, /responses endpoint.
    # Works against OpenAI directly, OpenRouter, or a local Ollama >= 0.13.3.
    "responses": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    # local server; model is a local tag you have pulled (ollama pull ...)
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3.8:latest",
    },
}

# Cloud windows are six figures; a typical local pull is not. Auto-compaction
# does the math against THIS number -- guessing 200k for an 8k local model
# would mean never compacting before the provider 400s.
_DEFAULT_CONTEXT_WINDOWS: dict[str, int] = {
    "anthropic": 200_000,
    "openai": 200_000,
    "responses": 200_000,
    "ollama": 8192,
}

_dotenv_loaded = False
#: .env keys a real environment variable outranked (see shadowed_by_shell)
_shadowed: set[str] = set()


def _load_dotenv() -> None:
    """Import a .env file from the cwd into os.environ -- ONCE.

    ``uv run`` does NOT load .env for you (only ``--env-file`` does), and
    asking users to remember that flag is bad CLI manners. This loader is
    ~25 lines instead of a python-dotenv dependency: KEY=VALUE lines,
    full-line AND trailing '#' comments (``.env.example`` annotates every
    variable with one), optional quotes, split on the FIRST '=' so values
    may contain '='. Real environment variables ALWAYS win -- .env only
    fills gaps. Library callers get the same convenience via
    load_settings(); pass through if that's not what you want.
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True

    path = Path(".env")
    if not path.is_file():
        return
    try:
        lines = path.read_text().splitlines()
    except OSError:
        # present but unreadable (root-owned, restrictive container
        # mount): treat as absent -- real env vars still work
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), _clean_value(value.strip())
        # a blank VALUE is template residue (``OLLAMA_API_KEY=   `` left
        # over from copying .env.example); importing it would shadow the
        # code's own fallbacks with ""
        if key and value:
            if key in os.environ:
                # The shell wins, quietly -- and quiet is the problem: a
                # stale `export` from an old tutorial line outranks the
                # .env the user is LOOKING AT while debugging. Remember
                # which keys lost so an error can say whose value it is.
                if os.environ[key] != value:
                    _shadowed.add(key)
                continue
            os.environ[key] = value


def shadowed_by_shell(key: str) -> bool:
    """True when .env set this key to something the environment overrode.

    For error messages only: the value in force did NOT come from the
    file the user is editing, and saying so is the difference between a
    fix and another round of confusion.
    """
    return key in _shadowed


def _clean_value(raw: str) -> str:
    """One optional quote layer; then cut an unquoted `` # comment`` tail.

    A '#' inside quotes stays part of the value; so does one glued to an
    unquoted value (``abc#def``) -- only whitespace marks a comment.
    """
    if raw[:1] in ("'", '"'):
        close = raw.find(raw[0], 1)
        return raw[1:close] if close != -1 else raw[1:]
    for i, char in enumerate(raw):
        if char == "#" and (i == 0 or raw[i - 1].isspace()):
            return raw[:i].rstrip()
    return raw


#: Other words for a provider this harness knows by one name. "local" is
#: the only one so far, and it earns its place: roughly half the people
#: reading this repo think "I want to run a local model", not "I want
#: Ollama" -- a brand they may first meet in an error message. The
#: harness answers to both and stores the canonical one (notes/54).
ALIASES: dict[str, str] = {"local": "ollama"}

#: The manifest's word for "whatever this machine already prefers". A
#: package that leaves ``provider`` blank behaves identically; this lets
#: it SAY so, because a blank reads as an omission and a reviewer cannot
#: tell a decision from a gap (notes/54).
FOLLOW_THE_MACHINE = "auto"


def canonical_provider(name: str) -> str:
    """One provider's many spellings -> the one this harness uses.

    Applied at every edge a name arrives through -- the flag, the
    manifest, the environment -- rather than at the point of use, so a
    provider name inside the harness is always the canonical one and
    nothing downstream has to know an alias exists.
    """
    cleaned = name.strip().lower()
    return ALIASES.get(cleaned, cleaned)


def known_providers() -> list[str]:
    """Every name a caller may use, aliases included, sorted."""
    return sorted([*_DEFAULTS, *ALIASES])


def load_settings(name: str) -> ProviderSettings:
    """Read <PROVIDER>_API_KEY / <PROVIDER>_BASE_URL from the environment."""
    _load_dotenv()
    name = canonical_provider(name)
    prefix = name.upper()
    if name == "ollama":
        # A local server needs no secret. Send a placeholder so the
        # Authorization header exists (proxies in front of Ollama may
        # require one; OLLAMA_API_KEY overrides).
        return ProviderSettings(
            api_key=os.environ.get("OLLAMA_API_KEY", "ollama"),
            base_url=os.environ.get(
                "OLLAMA_BASE_URL", _DEFAULTS["ollama"]["base_url"]
            ),
        )
    api_key = os.environ.get(f"{prefix}_API_KEY")
    if not api_key and name == "anthropic":
        # Claude Code's convention; some gateways set this instead.
        api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not api_key:
        raise ConfigError(
            f"No API key for provider {name!r}: set {prefix}_API_KEY "
            f"(see .env.example)"
        )
    return ProviderSettings(
        api_key=api_key,
        base_url=os.environ.get(
            f"{prefix}_BASE_URL", _DEFAULTS[name]["base_url"]
        ),
    )


def default_model(name: str) -> str:
    """Explicit env override wins; otherwise a per-provider default."""
    name = canonical_provider(name)
    env_var = f"{name.upper()}_MODEL"
    return os.environ.get(env_var, _DEFAULTS[name]["model"])


def default_context_window(name: str) -> int:
    """Window assumption used when --context-window is not given."""
    env_var = f"{name.upper()}_CONTEXT_WINDOW"
    if value := os.environ.get(env_var):
        return int(value)
    return _DEFAULT_CONTEXT_WINDOWS[name]


def default_tool_select() -> int | None:
    """Width for dynamic tool loading from $YANTRA_TOOLS_PER_TURN.

    Same semantics as --tool-select, which this backs when .env is more
    convenient than a flag: K forces that width on every session, 0
    forces selection off, and None (unset) leaves the auto-enable rule
    to decide. A non-integer is config corruption -- fail loudly rather
    than guess.
    """
    raw = os.environ.get("YANTRA_TOOLS_PER_TURN")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(
            f"YANTRA_TOOLS_PER_TURN must be an integer, got {raw!r}"
        ) from None


def browser_profile() -> Path | None:
    """Persistent Chromium profile dir from $YANTRA_BROWSER_PROFILE.

    Set (a directory path) => every browser_* session -- and the headed
    one-shot opened by ``--browse-login`` -- shares this on-disk
    profile: log in once and every later session rides the saved
    cookies/localStorage. Unset => each session starts fresh, nothing
    persists (the safe default; a profile IS a plaintext credential
    store, so keeping it off until asked for is the honest default).
    A blank value counts as unset -- copying .env.example leaves empty
    templates around, and they must not shadow the code's own fallback.
    """
    raw = os.environ.get("YANTRA_BROWSER_PROFILE", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


#: Browser channels Playwright resolves BY NAME, no path needed -- it
#: knows where each branded build installs itself on every OS. Anything
#: not in here is taken as a binary to find on disk instead.
BROWSER_CHANNELS = frozenset({
    "chromium", "chrome", "chrome-beta", "chrome-dev", "chrome-canary",
    "msedge", "msedge-beta", "msedge-dev", "msedge-canary",
})

#: Channel name -> the commands it is called on a Linux PATH. Only
#: --browse-login needs this: launching a browser WITHOUT Playwright
#: means resolving the binary ourselves, and a channel name is not a
#: binary. Empty for channels with no stable Linux command.
_CHANNEL_COMMANDS: dict[str, tuple[str, ...]] = {
    "chromium": ("chromium", "chromium-browser"),
    "chrome": ("google-chrome", "google-chrome-stable"),
    "chrome-beta": ("google-chrome-beta",),
    "chrome-dev": ("google-chrome-unstable",),
    "msedge": ("microsoft-edge", "microsoft-edge-stable"),
    "msedge-beta": ("microsoft-edge-beta",),
    "msedge-dev": ("microsoft-edge-dev",),
}


def browser_executable() -> str | None:
    """Which browser the browser_* tools drive ($YANTRA_BROWSER_EXECUTABLE).

    Unset => Playwright's own bundled Chromium, the default since these
    tools existed. Set => any Chromium-family browser already on this
    machine, given either way round:

    * a CHANNEL name Playwright knows ('chrome', 'msedge', 'chromium')
      -- returned lower-cased, for ``channel=`` at launch;
    * a PATH or a command on $PATH ('/snap/bin/brave', 'brave') --
      resolved to an absolute path here, for ``executable_path=``.

    Why bother: the bundled build is an unbranded Chromium carrying no
    proprietary codecs, no Widevine and no Google API keys, and its
    default binary is the headless SHELL, whose user-agent says
    HeadlessChrome out loud. A real installed browser is simply a
    normal client, which is all a site checking for robots is asking.
    It is NOT stealth -- see notes/28.

    Resolution happens HERE, at config time, so a typo is a startup
    error naming the variable rather than a Playwright stack trace
    fifteen seconds into a run. Blank counts as unset (.env.example
    residue must never shadow the code's fallback).
    """
    raw = os.environ.get("YANTRA_BROWSER_EXECUTABLE", "").strip()
    if not raw:
        return None
    if raw.lower() in BROWSER_CHANNELS:
        return raw.lower()
    if os.sep in raw or raw.startswith("~"):
        path = Path(raw).expanduser()
        if not path.exists():
            raise ConfigError(
                f"YANTRA_BROWSER_EXECUTABLE={raw!r} is not a file on this "
                f"machine (looked at {path}); give a path to a "
                "Chromium-family browser, or one of: "
                f"{', '.join(sorted(BROWSER_CHANNELS))}")
        return str(path)
    found = shutil.which(raw)
    if found is None:
        raise ConfigError(
            f"YANTRA_BROWSER_EXECUTABLE={raw!r} is not on $PATH and is not a "
            "Playwright channel; give a path to a Chromium-family browser, "
            f"or one of: {', '.join(sorted(BROWSER_CHANNELS))}")
    return found


def browser_login_command(executable: str | None) -> str | None:
    """The binary --browse-login can run WITHOUT Playwright, or None.

    A path is already the answer; a channel name has to be looked up on
    $PATH, because launching a browser as a plain subprocess is the
    entire point (see run_login_session) and ``channel=`` is a
    Playwright concept that no subprocess understands.
    """
    if executable is None:
        return None
    if executable not in BROWSER_CHANNELS:
        return executable
    for command in _CHANNEL_COMMANDS.get(executable, ()):
        found = shutil.which(command)
        if found is not None:
            return found
    return None


def browser_headed() -> bool:
    """Run the browser_* tools WITH a window ($YANTRA_BROWSER_HEADED).

    Off by default. On, the browser is genuinely headed -- and if no
    display is attached, Yantra puts one under it (Xvfb), so the window
    exists without ever being visible. That combination is the point:
    headless mode is a distinct Chromium build with distinct
    fingerprints, and no flag talks it out of them, while a headed
    browser on an invisible screen has nothing to hide because there is
    nothing different about it.

    Costs a real X server process and more memory than headless. Off
    remains the default because most pages never ask.
    """
    raw = os.environ.get("YANTRA_BROWSER_HEADED", "").strip().lower()
    if not raw:
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(
        f"YANTRA_BROWSER_HEADED must be 1|0 (or true/false), got {raw!r}")


def browser_close_policy() -> float | None:
    """When the browser_* tools close on their own ($YANTRA_BROWSER_CLOSE).

    Returns 0.0 for ``turn`` (the default: close as every turn ends), a
    number of seconds for an idle close (the page survives into the next
    turn, and goes once nobody has used it for that long), or None for
    ``model`` (never on its own: browser_close, or the process exiting).

    Three answers because the one default is right for one person and
    wrong for another. Closing at the end of the turn is what makes a
    question asked in the web UI leave no window behind; it is also what
    turns "now click the cheapest one" into a fresh page load, and a
    model that reaches for a ref from the last turn into an error.
    """
    raw = os.environ.get("YANTRA_BROWSER_CLOSE", "").strip().lower()
    if raw in ("", "turn"):
        return 0.0
    if raw in ("model", "never", "off"):
        return None
    try:
        seconds = float(raw)
    except ValueError:
        seconds = -1.0
    if seconds <= 0:
        raise ConfigError(
            "YANTRA_BROWSER_CLOSE must be turn, model, or a number of idle "
            f"seconds (e.g. 300), got {raw!r}")
    return seconds


def default_env_context() -> str:
    """Session awareness level from $YANTRA_ENV_CONTEXT.

    'full' (default) injects auto-detected context -- time/timezone, host,
    working directory, plus your city from ONE public-IP lookup to
    ipinfo.io -- into the system prompt; 'local' keeps machine facts only;
    'off' restores the ask-the-user-everything behavior. Same semantics as
    --env-context, which this backs when .env is more convenient. An unset
    or blank value means full (blank = template residue from copying
    .env.example); anything else is config corruption -- fail loudly.
    """
    raw = os.environ.get("YANTRA_ENV_CONTEXT")
    if raw is None or not raw.strip():
        return "full"
    value = raw.strip().lower()
    if value not in ("off", "local", "full"):
        raise ConfigError(
            f"YANTRA_ENV_CONTEXT must be off|local|full, got {raw!r}"
        )
    return value


def disabled_skill_patterns() -> list[str]:
    """Glob patterns from $YANTRA_DISABLED_SKILLS (comma-separated).

    The skills twin of YANTRA_DISABLED_TOOLS: each pattern is fnmatch-ed
    against discovered skill names, so this hides one skill ('pr-review')
    or a family ('deploy-*'). A disabled skill stays DISCOVERED -- it is
    still listed by /skills, marked [off] -- but leaves the roster in the
    system prompt and refuses to load, so the model neither sees it nor
    can reach it. Reversible in-session with /skills on NAME.
    """
    raw = os.environ.get("YANTRA_DISABLED_SKILLS", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def disabled_tool_patterns() -> list[str]:
    """Glob patterns from $YANTRA_DISABLED_TOOLS (comma-separated).

    Each pattern is fnmatch-ed against registered tool names, so this
    hides one tool ('web_fetch'), a family ('browser_*'), or a whole
    MCP server ('mcp__slack__*'). Disabled tools are UNREGISTERED before
    catalog building, so they are never sent, never executed, and never
    suggested by list_available_tools either.
    """
    raw = os.environ.get("YANTRA_DISABLED_TOOLS", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def guess_provider() -> str:
    """Whichever provider the environment already declares.

    Lives here rather than in the CLI because an AgentSpec with no
    provider declared has to answer the same question, and two copies of
    this ladder would drift the day a fourth dialect arrives.

    The ladder, top rung first:

    * ``YANTRA_PROVIDER`` -- say it outright and nothing is guessed. The
      one answer that works for a local model, because "no key" is the
      whole point of the local road and a key ladder can never see it.
    * a key: ``ANTHROPIC_API_KEY`` (or ``ANTHROPIC_AUTH_TOKEN``), then
      ``OPENAI_API_KEY``, then ``RESPONSES_API_KEY``.
    * an ``OLLAMA_*`` line someone put in the environment or uncommented
      in ``.env``. A local server that happens to be listening is still
      never evidence -- nothing is probed here -- but a model tag or base
      URL written down by hand IS a statement of intent, and it is the
      only one a keyless setup can make. It ranks below the keys so that
      a machine holding both keeps answering the way it always has.
    """
    _load_dotenv()  # .env fills gaps; real env vars already set would win anyway
    declared = canonical_provider(os.environ.get("YANTRA_PROVIDER", ""))
    if declared:
        if declared not in _DEFAULTS:
            raise ConfigError(
                f"YANTRA_PROVIDER must be one of "
                f"{'|'.join(known_providers())}, got {declared!r}"
            )
        return declared
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("RESPONSES_API_KEY"):
        return "responses"
    if any(os.environ.get(f"OLLAMA_{suffix}")
           for suffix in ("MODEL", "BASE_URL", "API_KEY")):
        return "ollama"
    raise ConfigError(
        "no provider found: set ANTHROPIC_API_KEY (or OPENAI_API_KEY) in the "
        "environment or .env -- or run a local model with: yantra --provider "
        "local (YANTRA_PROVIDER=local in .env makes that the default)"
    )
