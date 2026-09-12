"""Agent packages: what a manifest may say, and what it may not get wrong.

The bias under every test here is that a package is a file people SHARE,
so the failure mode to design against is not a crash -- it is a manifest
that looks like it configured something and did not. Hence: unknown keys
are errors, declared paths must exist, and a package cannot ship skills
it has also made unreachable.
"""

from __future__ import annotations

import pytest

from yantra.errors import ConfigError
from yantra.package import MANIFEST, find_manifest, load_package


def _pkg(root, manifest: str, *, prompt: str | None = None, name="researcher"):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MANIFEST).write_text(manifest, encoding="utf-8")
    if prompt is not None:
        (folder / "prompt.md").write_text(prompt, encoding="utf-8")
    return folder


class TestMinimal:
    def test_a_name_is_a_whole_package(self, tmp_path):
        spec = load_package(_pkg(tmp_path, '[agent]\nname = "tiny"\n'))
        assert spec.name == "tiny"
        assert spec.provider is None       # left for the environment to answer
        assert spec.model is None
        assert spec.tool_allow is None     # every tool, nothing denied

    def test_an_empty_manifest_is_legal(self, tmp_path):
        """Nothing to say is a valid thing to say; the directory names it."""
        spec = load_package(_pkg(tmp_path, "", name="quiet"))
        assert spec.name == "quiet"

    def test_the_name_defaults_to_the_directory(self, tmp_path):
        spec = load_package(_pkg(tmp_path, '[model]\nprovider = "ollama"\n'))
        assert spec.name == "researcher"

    def test_root_records_where_it_came_from(self, tmp_path):
        folder = _pkg(tmp_path, "")
        assert load_package(folder).root == folder.resolve()


class TestLocating:
    def test_the_directory_or_the_file_both_work(self, tmp_path):
        folder = _pkg(tmp_path, '[agent]\nname = "either"\n')
        assert load_package(folder).name == "either"
        assert load_package(folder / MANIFEST).name == "either"

    def test_a_directory_without_a_manifest_says_so(self, tmp_path):
        with pytest.raises(ConfigError, match=f"no {MANIFEST}"):
            load_package(tmp_path)

    def test_a_missing_path_is_not_a_package(self, tmp_path):
        with pytest.raises(ConfigError, match="not an agent package"):
            load_package(tmp_path / "nope")

    def test_find_manifest_reports_absence_without_raising(self, tmp_path):
        assert find_manifest(tmp_path) is None


class TestStrictness:
    def test_an_unknown_table_is_an_error(self, tmp_path):
        with pytest.raises(ConfigError, match="unknown table"):
            load_package(_pkg(tmp_path, '[modl]\nprovider = "ollama"\n'))

    def test_a_misspelled_key_is_an_error(self, tmp_path):
        """The failure this exists for: 'alow' silently leaving bash armed
        in an agent whose author believes they restricted it."""
        with pytest.raises(ConfigError, match=r"unknown key\(s\) in \[tools\]"):
            load_package(_pkg(tmp_path, '[tools]\nalow = ["read_file"]\n'))

    def test_the_error_names_the_file(self, tmp_path):
        folder = _pkg(tmp_path, '[tools]\nalow = []\n')
        with pytest.raises(ConfigError, match=str(folder / MANIFEST)):
            load_package(folder)

    def test_invalid_toml_is_reported_as_such(self, tmp_path):
        with pytest.raises(ConfigError, match="invalid TOML"):
            load_package(_pkg(tmp_path, "[agent\nname = broken\n"))

    def test_wrong_types_are_caught(self, tmp_path):
        with pytest.raises(ConfigError, match="tools.allow must be a list"):
            load_package(_pkg(tmp_path, '[tools]\nallow = "read_file"\n'))
        with pytest.raises(ConfigError, match="model.max_tokens must be an integer"):
            load_package(_pkg(tmp_path, '[model]\nmax_tokens = "lots"\n',
                              name="b"))
        with pytest.raises(ConfigError, match="model.cache must be true or false"):
            load_package(_pkg(tmp_path, '[model]\ncache = "yes"\n', name="c"))

    def test_a_bad_enum_is_caught_at_load(self, tmp_path):
        with pytest.raises(ConfigError, match="permissions mode"):
            load_package(_pkg(tmp_path, '[permissions]\nmode = "maybe"\n'))


class TestPrompt:
    def test_prompt_md_is_picked_up_by_convention(self, tmp_path):
        spec = load_package(_pkg(tmp_path, '[agent]\nname = "r"\n',
                                 prompt="You are careful.\n"))
        assert spec.prompt == "You are careful."

    def test_no_prompt_file_is_fine(self, tmp_path):
        assert load_package(_pkg(tmp_path, "")).prompt is None

    def test_a_declared_prompt_must_exist(self, tmp_path):
        """Convention is optional; a request is not. Asking for a file that
        is not there is a typo, not a preference."""
        with pytest.raises(ConfigError, match="not a file"):
            load_package(_pkg(tmp_path, '[agent]\nprompt = "persona.md"\n'))

    def test_a_declared_prompt_elsewhere_is_read(self, tmp_path):
        folder = _pkg(tmp_path, '[agent]\nprompt = "persona.md"\n')
        (folder / "persona.md").write_text("You are terse.\n", encoding="utf-8")
        assert load_package(folder).prompt == "You are terse."

    def test_an_empty_prompt_file_reads_as_no_prompt(self, tmp_path):
        # an empty layer and a missing one render identically anyway
        assert load_package(_pkg(tmp_path, "", prompt="   \n")).prompt is None


class TestSkills:
    def _with_skill(self, tmp_path, manifest, name="researcher"):
        folder = _pkg(tmp_path, manifest, name=name)
        skill = folder / "skills" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: demo\ndescription: Demo.\n---\n\nDo it.\n",
            encoding="utf-8")
        return folder

    def test_a_skills_directory_is_found_without_being_declared(self, tmp_path):
        spec = load_package(self._with_skill(tmp_path, ""))
        assert spec.skills is True
        assert spec.skill_dirs == (spec.root / "skills",)

    def test_no_skills_directory_leaves_the_decision_open(self, tmp_path):
        """None, not False: the host still gets to enable its own skills."""
        assert load_package(_pkg(tmp_path, "")).skills is None

    def test_declared_dirs_must_exist(self, tmp_path):
        with pytest.raises(ConfigError, match="is not a directory"):
            load_package(_pkg(tmp_path, '[skills]\ndirs = ["procedures"]\n'))

    def test_skills_can_be_turned_off_explicitly(self, tmp_path):
        spec = load_package(self._with_skill(tmp_path,
                                            "[skills]\nenabled = false\n"))
        assert spec.skills is False

    def test_disabled_patterns_travel(self, tmp_path):
        spec = load_package(self._with_skill(tmp_path,
                                            '[skills]\ndisabled = ["deploy-*"]\n'))
        assert spec.skills_disabled == ("deploy-*",)

    def test_shipping_skills_while_excluding_load_skill_is_rejected(self, tmp_path):
        """The manifest is the only place this is worth saying: later, all
        anyone sees is a roster of skills the model cannot reach."""
        with pytest.raises(ConfigError, match="excludes load_skill"):
            load_package(self._with_skill(
                tmp_path, '[tools]\nallow = ["read_file"]\n'))

    def test_allowing_load_skill_makes_it_coherent(self, tmp_path):
        spec = load_package(self._with_skill(
            tmp_path, '[tools]\nallow = ["read_file", "load_skill"]\n'))
        assert spec.skills is True

    def test_a_pattern_covering_load_skill_counts(self, tmp_path):
        spec = load_package(self._with_skill(
            tmp_path, '[tools]\nallow = ["read_file", "*_skill"]\n'))
        assert spec.skills is True


class TestServers:
    def test_a_stdio_server(self, tmp_path):
        spec = load_package(_pkg(tmp_path, '''
[[mcp]]
name = "local"
command = "python"
args = ["-m", "server"]
env = { TOKEN = "${MY_TOKEN}" }
'''))
        assert len(spec.mcp) == 1
        server = spec.mcp[0]
        assert server.name == "local" and server.command == "python"
        assert server.args == ["-m", "server"]
        # the placeholder stays a placeholder on disk (expanded at connect)
        assert server.env == {"TOKEN": "${MY_TOKEN}"}

    def test_an_http_server(self, tmp_path):
        spec = load_package(_pkg(tmp_path, '''
[[mcp]]
name = "docs"
url = "https://example.invalid/mcp"
headers = { Authorization = "Bearer ${DOCS_TOKEN}" }
'''))
        assert spec.mcp[0].url == "https://example.invalid/mcp"
        assert spec.mcp[0].headers == {"Authorization": "Bearer ${DOCS_TOKEN}"}

    def test_exactly_one_transport(self, tmp_path):
        both = '[[mcp]]\nname = "x"\ncommand = "p"\nurl = "https://h/mcp"\n'
        with pytest.raises(ConfigError, match="exactly one of command"):
            load_package(_pkg(tmp_path, both))
        with pytest.raises(ConfigError, match="exactly one of command"):
            load_package(_pkg(tmp_path, '[[mcp]]\nname = "x"\n', name="b"))

    def test_a_server_needs_a_name(self, tmp_path):
        with pytest.raises(ConfigError, match="needs a name"):
            load_package(_pkg(tmp_path, '[[mcp]]\ncommand = "p"\n'))

    def test_a_single_table_is_accepted_as_one_server(self, tmp_path):
        """``[mcp]`` instead of ``[[mcp]]`` is the easiest TOML mistake to
        make, and the intent is never ambiguous."""
        spec = load_package(_pkg(tmp_path, '[mcp]\nname = "one"\ncommand = "p"\n'))
        assert len(spec.mcp) == 1 and spec.mcp[0].name == "one"

    def test_unknown_server_keys_are_errors(self, tmp_path):
        with pytest.raises(ConfigError, match=r"unknown key\(s\) in \[\[mcp\]\]"):
            load_package(_pkg(tmp_path, '[[mcp]]\nname = "x"\ncmd = "p"\n'))

    def test_no_servers_is_an_empty_tuple(self, tmp_path):
        assert load_package(_pkg(tmp_path, "")).mcp == ()


class TestEverythingTogether:
    MANIFEST_TEXT = '''
[agent]
name = "researcher"
description = "Reads sources and writes briefs with citations."
version = "0.2.0"

[model]
provider = "anthropic"
model = "claude-sonnet-5"
max_iterations = 12
context_window = 180000
cache = true

[tools]
allow = ["read_file", "glob", "grep", "web_fetch"]
deny = ["browser_*"]
per_turn = 6

[permissions]
mode = "ask"

[env]
context = "local"
'''

    def test_every_field_lands_where_it_belongs(self, tmp_path):
        spec = load_package(_pkg(tmp_path, self.MANIFEST_TEXT,
                                 prompt="You are a researcher."))
        assert (spec.name, spec.version) == ("researcher", "0.2.0")
        assert spec.description.startswith("Reads sources")
        assert (spec.provider, spec.model) == ("anthropic", "claude-sonnet-5")
        assert (spec.max_iterations, spec.context_window) == (12, 180000)
        assert spec.cache is True
        assert spec.tool_allow == ("read_file", "glob", "grep", "web_fetch")
        assert spec.tool_deny == ("browser_*",)
        assert spec.tools_per_turn == 6
        assert spec.permissions_mode == "ask"
        assert spec.env_context == "local"
        assert spec.prompt == "You are a researcher."

    def test_the_command_line_still_wins(self, tmp_path):
        """The portability promise: someone runs this Anthropic-authored
        package against their own hardware without editing the file."""
        from yantra.spec import AgentSpec
        spec = load_package(_pkg(tmp_path, self.MANIFEST_TEXT))
        overridden = spec.merge(AgentSpec(provider="ollama", model="qwen3:8b"))
        assert (overridden.provider, overridden.model) == ("ollama", "qwen3:8b")
        assert overridden.max_iterations == 12    # the package still decides this


class TestCliAgentFlag:
    """The package reaching an actual session, and the precedence holding.

    Patched at the CLI's own seams (load_settings/get_provider/run_turn) so
    no key, model or network is involved -- the same shape as the --image
    flag's test.
    """

    def _run(self, tmp_path, monkeypatch, argv, manifest, **kw):
        import yantra.cli.main as cli_main
        from conftest import ScriptedProvider, assistant_text

        folder = _pkg(tmp_path, manifest, **kw)
        provider = ScriptedProvider([assistant_text("done")])
        monkeypatch.setattr(cli_main, "load_settings", lambda name: object())
        monkeypatch.setattr(cli_main, "get_provider", lambda *a, **k: provider)
        seen: dict = {}

        def fake_run_turn(self, text, *, images=None):
            seen["agent"] = self.agent

        monkeypatch.setattr(cli_main.Repl, "run_turn", fake_run_turn)
        rc = cli_main.main([*argv, "go"])
        return rc, seen.get("agent"), folder

    def test_the_package_configures_the_session(self, tmp_path, monkeypatch):
        rc, agent, _ = self._run(
            tmp_path, monkeypatch,
            ["--cwd", str(tmp_path), "--agent", str(tmp_path / "researcher"),
             "--provider", "anthropic", "--yolo"],
            '[agent]\nname = "researcher"\n\n[model]\nmax_iterations = 7\n\n'
            '[tools]\nallow = ["read_file", "glob"]\n',
            prompt="You are a researcher.",
        )
        assert rc == 0
        assert agent.max_iterations == 7
        assert agent.registry.names() == ["glob", "read_file"]
        assert agent.prompt.get("agent") == "You are a researcher."

    def test_a_flag_overrides_the_package(self, tmp_path, monkeypatch):
        rc, agent, _ = self._run(
            tmp_path, monkeypatch,
            ["--cwd", str(tmp_path), "--agent", str(tmp_path / "researcher"),
             "--provider", "anthropic", "--max-iterations", "3", "--yolo"],
            '[model]\nmax_iterations = 7\n',
        )
        assert rc == 0 and agent.max_iterations == 3

    def test_agent_toml_in_the_cwd_is_used_without_a_flag(self, tmp_path, monkeypatch):
        """What makes a package directory feel like a project you cd into."""
        rc, agent, folder = self._run(
            tmp_path, monkeypatch,
            ["--cwd", str(tmp_path / "researcher"), "--provider", "anthropic",
             "--yolo"],
            '[model]\nmax_iterations = 9\n',
        )
        assert rc == 0 and agent.max_iterations == 9

    def test_nothing_upward_is_searched(self, tmp_path, monkeypatch):
        """A manifest one directory up governing this session silently would
        be a surprise nobody asked for."""
        nested = tmp_path / "researcher" / "sub"
        nested.mkdir(parents=True)
        rc, agent, _ = self._run(
            tmp_path, monkeypatch,
            ["--cwd", str(nested), "--provider", "anthropic", "--yolo"],
            '[model]\nmax_iterations = 9\n',
        )
        assert rc == 0 and agent.max_iterations == 25   # the plain default

    def test_a_broken_manifest_exits_two(self, tmp_path, monkeypatch, capsys):
        rc, agent, _ = self._run(
            tmp_path, monkeypatch,
            ["--cwd", str(tmp_path), "--agent", str(tmp_path / "researcher"),
             "--provider", "anthropic"],
            '[tools]\nalow = ["read_file"]\n',
        )
        assert rc == 2 and agent is None
        assert "unknown key" in capsys.readouterr().err

    def test_the_package_can_ask_for_yolo(self, tmp_path, monkeypatch):
        rc, agent, _ = self._run(
            tmp_path, monkeypatch,
            ["--cwd", str(tmp_path), "--agent", str(tmp_path / "researcher"),
             "--provider", "anthropic"],
            '[permissions]\nmode = "yolo"\n',
        )
        assert rc == 0 and agent.permissions.mode == "yolo"
