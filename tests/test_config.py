"""The hand-rolled .env loader (no python-dotenv dependency).

.env.example annotates every variable with a trailing '#' comment, so a
new user's FIRST action -- copy it to .env and uncomment a line -- feeds
this parser the annotated form. A regression doesn't fail quietly: it
crashes at startup (int('8192  # default ...')). These tests pin that
copy-paste path, plus the documented contracts: real environment
variables always win, values may contain '=', and the load happens ONCE.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yantra import config
from yantra.errors import ConfigError
from yantra.config import (
    _load_dotenv,
    browser_profile,
    canonical_provider,
    default_context_window,
    default_model,
    default_tool_select,
    disabled_tool_patterns,
    guess_provider,
    load_settings,
)


@pytest.fixture(autouse=True)
def _fresh_loader(monkeypatch, tmp_path):
    """Empty cwd + an un-latched loader, so each test parses its own .env.

    The loader writes straight into os.environ; deleting the test keys
    beforehand keeps one test's values from masquerading as "real"
    environment variables in the next.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_dotenv_loaded", False)
    monkeypatch.setattr(config, "_shadowed", set())
    for var in ("YANTRA_TEST_KEY", "YANTRA_TEST_WINDOW",
                "OLLAMA_CONTEXT_WINDOW"):
        monkeypatch.delenv(var, raising=False)


def _write_env(text: str) -> None:
    Path(".env").write_text(text)


class TestParsing:
    def test_trailing_comment_is_stripped(self):
        # the exact shape of every annotated line in .env.example
        _write_env(
            "YANTRA_TEST_WINDOW=8192"
            "                            # default (auto-compaction math)\n"
        )
        _load_dotenv()
        assert os.environ["YANTRA_TEST_WINDOW"] == "8192"

    def test_hash_inside_quotes_is_value(self):
        _write_env('YANTRA_TEST_KEY="sk-abc#def"\n')
        _load_dotenv()
        assert os.environ["YANTRA_TEST_KEY"] == "sk-abc#def"

    def test_glued_hash_in_unquoted_value_is_value(self):
        # only whitespace marks a comment start -- secrets may contain '#'
        _write_env("YANTRA_TEST_KEY=abc#def\n")
        _load_dotenv()
        assert os.environ["YANTRA_TEST_KEY"] == "abc#def"

    def test_comment_after_closing_quote_ignored(self):
        _write_env('YANTRA_TEST_KEY="hello"  # greeting\n')
        _load_dotenv()
        assert os.environ["YANTRA_TEST_KEY"] == "hello"

    def test_quotes_and_first_equals_split(self):
        _write_env('YANTRA_TEST_KEY = "a=b=c"\n')
        _load_dotenv()
        assert os.environ["YANTRA_TEST_KEY"] == "a=b=c"


class TestContract:
    def test_blank_value_is_skipped(self):
        # ``KEY=   `` is template residue from copying .env.example; it
        # must not shadow code fallbacks with ""
        _write_env("YANTRA_TEST_KEY=   \n")
        _load_dotenv()
        assert "YANTRA_TEST_KEY" not in os.environ

    def test_real_environment_wins(self, monkeypatch):
        monkeypatch.setenv("YANTRA_TEST_KEY", "from-shell")
        _write_env("YANTRA_TEST_KEY=from-file\n")
        _load_dotenv()
        assert os.environ["YANTRA_TEST_KEY"] == "from-shell"

    def test_a_shell_value_that_beat_the_file_is_remembered(self,
                                                             monkeypatch):
        # The override is silent, and silence is what makes it cost an
        # afternoon: the user edits .env and nothing changes. Errors
        # need to be able to say whose value is actually in force.
        monkeypatch.setenv("YANTRA_TEST_KEY", "from-shell")
        _write_env("YANTRA_TEST_KEY=from-file\n")
        _load_dotenv()
        assert config.shadowed_by_shell("YANTRA_TEST_KEY")

    def test_agreeing_values_are_not_called_a_conflict(self, monkeypatch):
        monkeypatch.setenv("YANTRA_TEST_KEY", "same")
        _write_env("YANTRA_TEST_KEY=same\n")
        _load_dotenv()
        assert not config.shadowed_by_shell("YANTRA_TEST_KEY")

    def test_a_key_only_the_file_sets_is_not_shadowed(self):
        _write_env("YANTRA_TEST_KEY=from-file\n")
        _load_dotenv()
        assert not config.shadowed_by_shell("YANTRA_TEST_KEY")

    def test_loads_once(self):
        _write_env("YANTRA_TEST_KEY=first\n")
        _load_dotenv()
        _write_env("YANTRA_TEST_KEY=second\n")
        _load_dotenv()
        assert os.environ["YANTRA_TEST_KEY"] == "first"

    def test_no_file_no_error(self):
        _load_dotenv()  # must not raise


class TestEndToEnd:
    def test_example_file_line_reaches_context_window(self):
        """The reported crash: uncommented .env.example line -> int()."""
        _write_env(
            "OLLAMA_CONTEXT_WINDOW=8192"
            "                            # default (auto-compaction math)\n"
        )
        _load_dotenv()
        assert default_context_window("ollama") == 8192


class TestToolSelectEnv:
    """YANTRA_TOOLS_PER_TURN backs --tool-select: same semantics, .env
    convenience. K forces the width on, 0 forces selection off, unset
    leaves the auto-enable rule to decide."""

    def test_unset_is_none(self, monkeypatch):
        monkeypatch.delenv("YANTRA_TOOLS_PER_TURN", raising=False)
        assert default_tool_select() is None

    def test_zero_and_width_pass_through(self, monkeypatch):
        monkeypatch.setenv("YANTRA_TOOLS_PER_TURN", "0")
        assert default_tool_select() == 0
        monkeypatch.setenv("YANTRA_TOOLS_PER_TURN", "14")
        assert default_tool_select() == 14

    def test_non_integer_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("YANTRA_TOOLS_PER_TURN", "seven")
        with pytest.raises(ConfigError, match="must be an integer"):
            default_tool_select()


class TestDisabledToolsEnv:
    def test_unset_means_nothing_disabled(self, monkeypatch):
        monkeypatch.delenv("YANTRA_DISABLED_TOOLS", raising=False)
        assert disabled_tool_patterns() == []

    def test_comma_split_strips_blanks(self, monkeypatch):
        monkeypatch.setenv("YANTRA_DISABLED_TOOLS",
                           " web_fetch , browser_* ,, mcp__slack__* ,")
        assert disabled_tool_patterns() == [
            "web_fetch", "browser_*", "mcp__slack__*"]


class TestBrowserProfileEnv:
    """$YANTRA_BROWSER_PROFILE: where the browser family keeps logins.
    Unset/blank => None (fresh sessions, nothing persists -- the safe
    default for what is ultimately a plaintext credential store)."""

    def test_unset_is_none(self, monkeypatch):
        monkeypatch.delenv("YANTRA_BROWSER_PROFILE", raising=False)
        assert browser_profile() is None

    def test_blank_template_residue_is_none(self, monkeypatch):
        # copying .env.example leaves ``YANTRA_BROWSER_PROFILE=`` behind
        monkeypatch.setenv("YANTRA_BROWSER_PROFILE", "   ")
        assert browser_profile() is None

    def test_tilde_expands(self, monkeypatch):
        monkeypatch.setenv("YANTRA_BROWSER_PROFILE", "~/.state/prof")
        assert browser_profile() == Path.home() / ".state" / "prof"

    def test_relative_paths_anchor_to_cwd(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("YANTRA_BROWSER_PROFILE", "prof")
        assert browser_profile() == tmp_path / "prof"

    def test_absolute_passes_through(self, monkeypatch, tmp_path):
        monkeypatch.setenv("YANTRA_BROWSER_PROFILE", str(tmp_path))
        assert browser_profile() == tmp_path


class TestGuessProvider:
    """What bare ``yantra`` runs against when nothing said.

    The local road has no key to find, so a ladder made only of keys
    could never see it -- and the keyless setup that the tutorial tells
    half its readers to build died at the doorstep. Two rungs answer
    that: an outright ``YANTRA_PROVIDER``, and an ``OLLAMA_*`` line
    somebody wrote by hand. Nothing here touches the network: a local
    server that happens to be listening is still not a declaration.
    """

    @pytest.fixture(autouse=True)
    def _no_credentials(self, monkeypatch):
        for var in ("YANTRA_PROVIDER", "ANTHROPIC_API_KEY",
                    "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY",
                    "RESPONSES_API_KEY", "OLLAMA_MODEL",
                    "OLLAMA_BASE_URL", "OLLAMA_API_KEY"):
            monkeypatch.delenv(var, raising=False)

    def test_key_ladder_order_is_unchanged(self, monkeypatch):
        monkeypatch.setenv("RESPONSES_API_KEY", "sk-r")
        assert guess_provider() == "responses"
        monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
        assert guess_provider() == "openai"
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a")
        assert guess_provider() == "anthropic"

    def test_auth_token_counts_as_an_anthropic_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
        assert guess_provider() == "anthropic"

    @pytest.mark.parametrize("var, value", [
        ("OLLAMA_MODEL", "qwen3.8:latest"),
        ("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        ("OLLAMA_API_KEY", "proxy-secret"),
    ])
    def test_an_ollama_line_is_a_declaration(self, monkeypatch, var, value):
        monkeypatch.setenv(var, value)
        assert guess_provider() == "ollama"

    def test_ollama_comes_from_a_copied_env_file(self, monkeypatch):
        # the exact shape .env.example leaves behind once its Ollama
        # block is uncommented and no key is ever filled in
        _write_env(
            "ANTHROPIC_API_KEY=\n"
            "OPENAI_API_KEY=\n"
            "OLLAMA_MODEL=qwen3.8:latest    # default; any pulled tag\n"
        )
        assert guess_provider() == "ollama"

    def test_a_key_still_outranks_ollama_config(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_MODEL", "qwen3.8:latest")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a")
        assert guess_provider() == "anthropic"

    def test_declaration_outranks_every_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a")
        monkeypatch.setenv("YANTRA_PROVIDER", "  Ollama  ")
        assert guess_provider() == "ollama"

    def test_unknown_declaration_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("YANTRA_PROVIDER", "llama.cpp")
        with pytest.raises(ConfigError, match="YANTRA_PROVIDER must be one of"):
            guess_provider()

    def test_blank_declaration_is_template_residue(self, monkeypatch):
        monkeypatch.setenv("YANTRA_PROVIDER", "   ")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-o")
        assert guess_provider() == "openai"

    def test_nothing_declared_names_both_roads(self):
        """And names the local one with the word most of its users would
        reach for rather than the brand (notes/54)."""
        with pytest.raises(ConfigError) as exc:
            guess_provider()
        message = str(exc.value)
        assert "ANTHROPIC_API_KEY" in message
        assert "--provider local" in message
        assert "YANTRA_PROVIDER=local" in message


class TestTheWordForAProvider:
    """One road, two words, and a blank that can say what it means.

    The bias: roughly half the people reading this repo want "a local
    model" and meet the word "Ollama" for the first time in an error
    message. An alias is cheap; the thing that is NOT cheap is an alias
    that leaks -- a provider name inside the harness must always be the
    canonical one, or two spellings end up as two cache keys, two price
    lookups and two report columns for one model. So these tests check
    both that the alias is accepted at every edge and that it is gone
    immediately afterwards.
    """

    def test_local_is_another_word_for_ollama(self):
        assert canonical_provider("local") == "ollama"

    def test_it_is_forgiving_about_shape(self):
        assert canonical_provider("  LOCAL ") == "ollama"

    def test_a_canonical_name_is_left_alone(self):
        for name in ("anthropic", "openai", "responses", "ollama"):
            assert canonical_provider(name) == name

    def test_an_unknown_name_is_not_invented_into_one(self):
        """Normalising is not guessing: a typo stays a typo and fails
        where names are checked."""
        assert canonical_provider("olama") == "olama"

    def test_the_environment_accepts_it(self, monkeypatch):
        monkeypatch.setenv("YANTRA_PROVIDER", "local")
        assert guess_provider() == "ollama"

    def test_settings_accept_it_and_need_no_key(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
        settings = load_settings("local")
        assert settings.base_url.startswith("http://localhost:11434")

    def test_the_default_model_is_the_local_one(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_MODEL", raising=False)
        assert default_model("local") == default_model("ollama")

    def test_a_provider_can_be_built_by_the_alias(self):
        from yantra.providers import get_provider
        provider = get_provider("local", load_settings("local"))
        assert provider.name == get_provider(
            "ollama", load_settings("ollama")).name

    def test_the_unknown_provider_error_lists_both_words(self, monkeypatch):
        monkeypatch.setenv("YANTRA_PROVIDER", "gemini")
        with pytest.raises(ConfigError, match="local"):
            guess_provider()


class TestAManifestThatFollowsTheMachine:
    def _manifest(self, tmp_path, body):
        from yantra.package import MANIFEST
        (tmp_path / MANIFEST).write_text(f'[agent]\nname = "p"\n{body}')
        return tmp_path

    def test_auto_means_whatever_the_machine_prefers(self, tmp_path):
        """Identical to leaving it out -- which is the point: the package
        can now SAY it rather than leaving a gap a reviewer has to guess
        at."""
        from yantra.package import load_package
        spec = load_package(self._manifest(tmp_path,
                                           '[model]\nprovider = "auto"\n'))
        assert spec.provider is None

    def test_a_blank_still_means_the_same_thing(self, tmp_path):
        from yantra.package import load_package
        assert load_package(self._manifest(tmp_path, "")).provider is None

    def test_local_is_stored_canonically(self, tmp_path):
        """Or the manifest's word would reach pricing, reports and the
        provider registry as a second name for one road."""
        from yantra.package import load_package
        spec = load_package(self._manifest(tmp_path,
                                           '[model]\nprovider = "local"\n'))
        assert spec.provider == "ollama"

    def test_a_provider_nobody_ships_is_refused_against_the_file(self,
                                                                 tmp_path):
        from yantra.package import load_package
        with pytest.raises(ConfigError, match="model.provider must be one of"):
            load_package(self._manifest(tmp_path,
                                        '[model]\nprovider = "gemini"\n'))
