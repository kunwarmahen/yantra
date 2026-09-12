"""AgentSpec: the merge rules, the validation, and the assembly order.

What these pin, in one sentence each: an override wins only where it
actually said something; a spec describes what was asked for rather than
what this machine has; and ``build`` produces an agent whose tool
admission, prompt layers and skills cannot contradict each other.

Every build here hands in a ScriptedProvider, so nothing reads the
environment or reaches a network -- which is also the point of ``build``
accepting a pre-resolved provider in the first place.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import ScriptedProvider, assistant_text

from yantra.errors import ConfigError
from yantra.spec import AgentSpec
from yantra.tools.base import Tool, ToolRegistry


def _skill(root: Path, name: str = "demo") -> Path:
    """A minimal discoverable skill, so "skills are on" can be observed."""
    folder = root / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A demo skill for tests.\n---\n\n"
        f"Do the demo thing.\n",
        encoding="utf-8",
    )
    return folder


def _build(spec: AgentSpec, **kwargs):
    """Build against a stub provider: no keys, no network, no env reads."""
    return spec.build(
        provider=ScriptedProvider([assistant_text("ok")]),
        provider_name="anthropic",
        model="test-model",
        **kwargs,
    )


class _Marker(Tool):
    """A stand-in for a tool a HOST registers after build (ask_user,
    load_skill, an MCP server's tools all arrive that way)."""

    name = "marker"
    description = "test tool"
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}
    read_only = True

    def summary(self, args, ctx=None) -> str:  # pragma: no cover
        return "marker"

    def run(self, args, ctx):  # pragma: no cover - never called
        return "marked"


class TestMerge:
    def test_the_override_wins_where_it_said_something(self):
        package = AgentSpec(provider="anthropic", model="sonnet", max_iterations=12)
        cli = AgentSpec(model="opus")
        merged = package.merge(cli)
        assert merged.model == "opus"
        assert merged.provider == "anthropic"   # untouched
        assert merged.max_iterations == 12      # untouched

    def test_unset_fields_do_not_clobber(self):
        """The whole reason unset is None: an all-default override is a
        no-op, so a host can always merge its flags in unconditionally."""
        package = AgentSpec(provider="ollama", model="qwen3:8b", cache=True)
        assert package.merge(AgentSpec()) == package

    def test_an_empty_list_cannot_clear_a_restriction(self):
        """Documented asymmetry: lifting a package's deny takes an explicit
        flag, not an empty one. A CLI that passed [] for every unset list
        would otherwise silently disarm every package it ran."""
        package = AgentSpec(tool_deny=("bash",))
        assert package.merge(AgentSpec(tool_deny=())).tool_deny == ("bash",)

    def test_merging_leaves_both_operands_alone(self):
        package = AgentSpec(model="a")
        cli = AgentSpec(model="b")
        package.merge(cli)
        assert package.model == "a" and cli.model == "b"


class TestValidate:
    def test_unknown_permission_mode(self):
        with pytest.raises(ConfigError, match="permissions mode"):
            AgentSpec(permissions_mode="maybe").validate()

    def test_unknown_env_context_level(self):
        with pytest.raises(ConfigError, match="env context"):
            AgentSpec(env_context="lots").validate()

    def test_negative_numbers(self):
        with pytest.raises(ConfigError, match="max_iterations"):
            AgentSpec(max_iterations=-1).validate()

    def test_an_empty_allow_list_is_a_mistake_not_a_lockdown(self):
        # [] parses as "allow nothing", which no author has ever meant
        with pytest.raises(ConfigError, match="tools.allow is present but empty"):
            AgentSpec(tool_allow=()).validate()

    def test_the_permissive_default_spec_validates(self):
        AgentSpec().validate()


class TestToolAdmission:
    def test_allow_is_a_whitelist(self):
        agent = _build(AgentSpec(tool_allow=("read_file", "glob")))
        assert agent.registry.names() == ["glob", "read_file"]

    def test_deny_removes_and_accepts_patterns(self):
        agent = _build(AgentSpec(tool_deny=("bash", "bash_*")))
        names = agent.registry.names()
        assert not [n for n in names if n.startswith("bash")]
        assert "read_file" in names       # everything else survived

    def test_the_policy_still_applies_after_build(self):
        """The reason this is a standing policy and not a one-time sweep:
        ask_user, load_skill and MCP tools are all registered by the host
        AFTER build, and a package that excluded them means it."""
        agent = _build(AgentSpec(tool_allow=("read_file",)))
        agent.registry.register(_Marker())
        assert "marker" not in agent.registry.names()
        assert "marker" in agent.registry.refused_names()

    def test_an_admitted_late_tool_still_registers(self):
        agent = _build(AgentSpec(tool_allow=("read_file", "marker")))
        agent.registry.register(_Marker())
        assert "marker" in agent.registry.names()

    def test_no_policy_admits_everything(self):
        agent = _build(AgentSpec())
        assert "bash" in agent.registry.names()
        assert agent.registry.refused_names() == []


class TestPromptLayers:
    def test_the_package_prompt_precedes_the_operators(self):
        agent = _build(AgentSpec(prompt="You are a researcher.",
                                 system="Answer in German."))
        assert agent.system == "You are a researcher.\n\nAnswer in German."

    def test_the_package_prompt_alone(self):
        agent = _build(AgentSpec(prompt="You are a researcher."))
        assert agent.system == "You are a researcher."
        assert agent.prompt.get("base") is None

    def test_no_prompt_at_all_stays_none(self):
        # byte-identical to the pre-package wire shape: system is omitted
        assert _build(AgentSpec()).system is None


class TestSkills:
    def test_skills_are_skipped_when_load_skill_is_excluded(self, tmp_path):
        """Composing a skill roster while the tool that loads them is gone
        advertises capabilities the model does not have."""
        _skill(tmp_path)
        agent = _build(AgentSpec(skills=True, tool_allow=("read_file",)),
                       cwd=tmp_path)
        assert getattr(agent, "skills", None) is None
        assert agent.prompt.get("skills") is None

    def test_skills_attach_when_load_skill_is_admitted(self, tmp_path):
        _skill(tmp_path)
        agent = _build(AgentSpec(skills=True), cwd=tmp_path)
        assert agent.skills.names() == ["demo"]
        assert "load_skill" in agent.registry.names()
        assert "demo" in agent.system

    def test_skills_off_registers_no_loader(self, tmp_path):
        _skill(tmp_path)
        agent = _build(AgentSpec(skills=False), cwd=tmp_path)
        assert "load_skill" not in agent.registry.names()


class TestDefaults:
    def test_context_window_falls_back_per_provider(self):
        agent = _build(AgentSpec())
        assert agent.context_window == 200_000        # the cloud assumption

    def test_a_declared_context_window_wins(self):
        assert _build(AgentSpec(context_window=8192)).context_window == 8192

    def test_unset_limits_keep_the_agents_own_defaults(self):
        agent = _build(AgentSpec())
        assert agent.max_iterations == 25
        assert agent.max_tokens == 16384

    def test_declared_limits_are_passed_through(self):
        agent = _build(AgentSpec(max_iterations=3, max_tokens=512))
        assert agent.max_iterations == 3
        assert agent.max_tokens == 512

    def test_env_context_is_not_attached_unless_asked(self):
        """None means DO NOT ATTACH, which is not the same as "off": at
        "full" attaching costs a network call, and a library building an
        agent must not reach the network for forgetting to say no."""
        assert getattr(_build(AgentSpec()), "env_context", None) is None

    def test_env_context_attaches_when_a_level_is_given(self, tmp_path):
        agent = _build(AgentSpec(env_context="off"), cwd=tmp_path)
        assert agent.env_context is not None

    def test_cwd_argument_beats_the_specs_own(self, tmp_path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        agent = _build(AgentSpec(cwd=tmp_path), cwd=other)
        assert agent.ctx.cwd == other.resolve()

    def test_a_host_may_supply_its_own_registry(self):
        registry = ToolRegistry()
        registry.register(_Marker())
        agent = _build(AgentSpec(), registry=registry)
        assert agent.registry.names() == ["marker"]


class TestAsyncTwin:
    """One spec, two loops. The assembly body is shared so the two agents
    cannot drift into being configured differently."""

    def test_build_async_applies_the_same_spec(self, tmp_path):
        spec = AgentSpec(prompt="You are a researcher.", system="Be terse.",
                         tool_allow=("read_file", "glob"), max_iterations=4,
                         context_window=9000)
        agent = spec.build_async(
            provider=ScriptedProvider([assistant_text("ok")]),
            provider_name="anthropic", model="test-model", cwd=tmp_path,
        )
        assert agent.registry.names() == ["glob", "read_file"]
        assert agent.system == "You are a researcher.\n\nBe terse."
        assert agent.max_iterations == 4
        assert agent.context_window == 9000

    def test_the_policy_outlives_an_async_build_too(self, tmp_path):
        agent = AgentSpec(tool_allow=("read_file",)).build_async(
            provider=ScriptedProvider([]), provider_name="anthropic",
            model="m", cwd=tmp_path,
        )
        agent.registry.register(_Marker())
        assert "marker" in agent.registry.refused_names()

    def test_parallelism_is_the_hosts_call_not_the_specs(self, tmp_path):
        agent = AgentSpec().build_async(
            provider=ScriptedProvider([]), provider_name="anthropic",
            model="m", cwd=tmp_path, max_parallel_tools=2,
        )
        assert agent.max_parallel_tools == 2
