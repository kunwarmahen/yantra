"""env_context: session awareness -- facts, policy, composition, flips.

Pins the contracts every surface depends on: mode resolution (env var,
default full, junk fails loudly), one-time collection behind a
monkeypatched network seam (the suite NEVER touches the wire), additive
composition over the operator's own --system prompt, live flips, and the
checkpoint rule (attach captures the base exactly ONCE; loads reapply so
restored sessions don't stack stale copies).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from yantra import config
from yantra.env_context import (
    POLICY,
    EnvContext,
    _format_location,
)
from yantra.errors import ConfigError


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Reaching the geo seam without stubbing it first is a test bug --
    fail loudly here rather than quietly calling ipinfo from CI."""
    monkeypatch.setattr(
        "yantra.env_context._http_get",
        lambda url: (_ for _ in ()).throw(
            AssertionError(f"test hit the network seam: {url}")),
    )


def _geo(payload):
    """Stub the seam with a canned ipinfo response."""
    def fake(url):
        return payload
    return fake


def _ctx(mode: str = "local", cwd: str = "/tmp/wksp") -> EnvContext:
    return EnvContext(mode, Path(cwd))


class TestModeResolution:
    @pytest.mark.parametrize("value", ["off", "local", "full"])
    def test_valid_values_pass_through(self, monkeypatch, value):
        monkeypatch.setenv("YANTRA_ENV_CONTEXT", value)
        assert config.default_env_context() == value

    def test_unset_means_full(self, monkeypatch):
        monkeypatch.delenv("YANTRA_ENV_CONTEXT", raising=False)
        assert config.default_env_context() == "full"

    def test_blank_counts_as_unset(self, monkeypatch):
        # copying .env.example leaves empty templates behind; they must not
        # shadow the code's own default (same rule as browser_profile)
        monkeypatch.setenv("YANTRA_ENV_CONTEXT", "   ")
        assert config.default_env_context() == "full"

    def test_junk_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("YANTRA_ENV_CONTEXT", "psychic")
        with pytest.raises(ConfigError, match="off|local|full"):
            config.default_env_context()

    def test_constructor_rejects_unknown_mode(self):
        with pytest.raises(ValueError, match="off|local|full"):
            EnvContext("psychic")


class TestCollection:
    def test_local_facts_render_machine_lines_plus_policy(self):
        ctx = _ctx()
        ctx.ensure_facts()
        block = ctx.render_block()
        assert "- Session started:" in block
        assert "- Host:" in block
        assert "/tmp/wksp" in block
        assert "Location" not in block  # local mode carries no location
        assert POLICY in block  # the nudge rides EVERY enabled level

    def test_full_with_geo_renders_location_line(self, monkeypatch):
        monkeypatch.setattr("yantra.env_context._http_get",
                            _geo({"city": "Pune", "region": "Maharashtra",
                                  "country": "IN"}))
        ctx = _ctx("full")
        ctx.ensure_facts()
        assert "- Location: Pune, Maharashtra, IN" in ctx.render_block()
        assert "(from public IP)" in ctx.render_block()

    def test_geo_failure_is_soft(self, monkeypatch):
        def boom(url):
            raise OSError("network unreachable")

        monkeypatch.setattr("yantra.env_context._http_get", boom)
        ctx = _ctx("full")
        ctx.ensure_facts()
        assert "Location" not in ctx.render_block()  # line simply dropped
        assert "OSError" in ctx.geo_error  # surfaced for the startup notice

    def test_unusable_geo_payload_still_counts_as_done(self, monkeypatch):
        monkeypatch.setattr("yantra.env_context._http_get", _geo({}))
        ctx = _ctx("full")
        ctx.ensure_facts()
        assert ctx.describe()["location"] is None
        assert "no usable fields" in ctx.geo_error

    def test_adjacent_duplicate_region_collapses(self):
        # some responses set region == city; printing it twice looks broken
        assert _format_location(
            {"city": "Pune", "region": "Pune", "country": "IN"}) == "Pune, IN"

    def test_off_collects_nothing_at_all(self):
        ctx = _ctx("off")
        ctx.ensure_facts()
        assert ctx.render_block() is None


class TestComposition:
    def test_custom_base_survives_above_the_block(self):
        ctx = _ctx("local")
        ctx.ensure_facts()
        ctx._base_system = "You are a pirate."
        composed = ctx.compose()
        assert composed.startswith("You are a pirate.")
        assert POLICY in composed

    def test_off_with_no_base_stays_none(self):
        # byte-identical to the pre-feature wire shape: system=None
        assert _ctx("off").compose() is None

    def test_describe_snapshot_shape(self, monkeypatch):
        monkeypatch.setattr("yantra.env_context._http_get",
                            _geo({"city": "Pune"}))
        ctx = _ctx("full")
        ctx.ensure_facts()
        d = ctx.describe()
        assert d["mode"] == "full"
        assert d["location"] == "Pune"
        assert d["error"] is None
        assert "- Location: Pune" in d["block"]

    def test_panel_leads_with_mode_and_indents_facts(self):
        ctx = _ctx()
        ctx.ensure_facts()
        lines = ctx.panel().splitlines()
        assert lines[0] == "env context: local"
        assert any(line.startswith("  - Working directory:")
                   for line in lines)


class TestAttachAndFlips:
    def test_attach_starts_off_as_pure_none(self):
        agent = SimpleNamespace(system=None)
        _ctx("off").attach(agent)
        assert agent.system is None
        assert agent.env_context.mode == "off"

    def test_attach_collects_and_composes_immediately(self):
        agent = SimpleNamespace(system=None)
        _ctx().attach(agent)
        assert "- Working directory: /tmp/wksp" in agent.system

    def test_flips_are_live_on_the_agent(self, monkeypatch):
        monkeypatch.setattr("yantra.env_context._http_get",
                            _geo({"city": "Pune"}))
        agent = SimpleNamespace(system=None)
        ctx = _ctx("off")
        ctx.attach(agent)
        ctx.flip("full")
        assert "Location: Pune" in agent.system
        ctx.flip("off")
        assert agent.system is None

    def test_upgrade_looks_up_exactly_once_across_downgrades(self, monkeypatch):
        calls: list[str] = []

        def counting(url):
            calls.append(url)
            return {"city": "Pune"}

        monkeypatch.setattr("yantra.env_context._http_get", counting)
        agent = SimpleNamespace(system=None)
        ctx = _ctx("local")
        ctx.attach(agent)
        assert calls == []  # local start: no network, ever
        ctx.flip("full")
        ctx.flip("off")
        ctx.flip("full")  # cached location survives downgrades
        assert len(calls) == 1
        assert "Location: Pune" in agent.system

    def test_bad_flip_argument_raises_and_keeps_level(self):
        agent = SimpleNamespace(system=None)
        ctx = _ctx()
        ctx.attach(agent)
        before = agent.system
        with pytest.raises(ValueError):
            ctx.flip("psychic")
        assert ctx.mode == "local"
        assert agent.system == before

    def test_reapply_never_swallows_a_restored_block_into_the_base(self):
        # /load restores the COMPOSED string a checkpoint saved; reapply must
        # overwrite it from the captured base, not adopt it as the new base
        agent = SimpleNamespace(system=None)
        ctx = _ctx()
        ctx.attach(agent)
        agent.system = "STALE-RESTORED-BLOCK"
        ctx.reapply()
        assert "STALE-RESTORED-BLOCK" not in agent.system
        assert POLICY in agent.system

    def test_second_attach_also_cannot_recapture(self):
        # defensive symmetry with the load path: attach is idempotent about
        # the base even if a host calls it again on a composed agent
        agent = SimpleNamespace(system=None)
        ctx = _ctx()
        ctx.attach(agent)
        composed_once = agent.system
        ctx.attach(agent)
        assert ctx._base_system is None  # still the original (None) base
        assert agent.system == composed_once


class TestLiveClock:
    """The fact sheet is frozen at startup; the time is not. A session
    opened at 15:16 and asked at 15:40 for "in ten minutes" must count
    from 15:40 -- so the live time rides with each request, never stored."""

    def _fixed_now(self, monkeypatch, hour, minute):
        from datetime import datetime

        class Fixed(datetime):
            @classmethod
            def now(cls, tz=None):
                # naive = local, as the real now() is before astimezone()
                return datetime(2026, 10, 10, hour, minute, 33)
        monkeypatch.setattr("yantra.env_context.datetime", Fixed)

    def test_off_says_nothing(self):
        assert _ctx("off").now_line() is None

    def test_reads_the_clock_when_asked_not_at_start(self, monkeypatch):
        ctx = _ctx()
        ctx.ensure_facts()
        self._fixed_now(monkeypatch, 15, 40)
        assert ctx.now_line().startswith("Current time: Saturday 2026-10-10 15:40")

    def test_block_names_the_start_as_the_start(self):
        ctx = _ctx()
        ctx.ensure_facts()
        block = ctx.render_block()
        assert "- Session started:" in block
        assert "- Time:" not in block

    def _agent(self, script):
        from conftest import ScriptedProvider
        from yantra.agent import Agent
        from yantra.tools.base import ToolRegistry
        agent = Agent(ScriptedProvider(script), model="m", tools=ToolRegistry())
        _ctx().attach(agent)
        return agent

    def test_sent_with_the_request_never_stored(self, monkeypatch):
        from conftest import assistant_text
        self._fixed_now(monkeypatch, 15, 40)
        agent = self._agent([assistant_text("ok")])
        agent.run("remind me in ten minutes")
        sent = agent.provider.last_request()["messages"][-1].text()
        assert "Current time: Saturday 2026-10-10 15:40" in sent
        assert all("Current time" not in m.text() for m in agent.history)

    def test_each_turn_tells_again_one_minute_tells_once(self, monkeypatch):
        from conftest import assistant_text, assistant_tool_call
        self._fixed_now(monkeypatch, 15, 40)
        agent = self._agent([assistant_tool_call("c1", "nope", {}),
                             assistant_text("ok"), assistant_text("ok")])
        agent.run("first")
        first, second = agent.provider.requests[:2]
        assert "Current time" in first["messages"][-1].text()
        assert "Current time" not in second["messages"][-1].text()
        self._fixed_now(monkeypatch, 15, 52)
        agent.run("later")
        assert "15:52" in agent.provider.last_request()["messages"][-1].text()
