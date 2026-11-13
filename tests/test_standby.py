"""A model standing by: when yours doesn't answer, another does, and you hear which.

The bias: every failure here is a SILENT one. A standby asked for the
primary's model name (the cloud asked for "qwen3.8"). A switch nobody was
told about, so a person's words went to another company's servers
unannounced. An agent that said "wait" switched anyway. A stream that
had begun answering was replayed elsewhere, so words were said twice. A
down model reported as "[Errno 111] Connection refused".
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from conftest import ScriptedProvider, assistant_text

from yantra.agent import Agent, TurnEnd
from yantra.async_agent import AsyncAgent
from yantra.errors import ContextOverflowError, ProviderError
from yantra.providers import standby
from yantra.providers.base import collect
from yantra.providers.standby import Standby, down_words, parse
from yantra.types import StartEvent, TextDelta

REFUSED = httpx.ConnectError("[Errno 111] Connection refused")


def down(exc: Exception = REFUSED, name: str = "ollama") -> ScriptedProvider:
    provider = ScriptedProvider([])
    provider.name = name

    def stream(**kwargs):
        provider.requests.append(kwargs)
        raise exc
        yield  # pragma: no cover

    async def astream(**kwargs):
        provider.requests.append(kwargs)
        raise exc
        yield  # pragma: no cover

    provider.stream = stream            # type: ignore[method-assign]
    provider.astream = astream          # type: ignore[method-assign]
    return provider


def up(text: str = "hello", name: str = "ollama") -> ScriptedProvider:
    provider = ScriptedProvider([assistant_text(text)])
    provider.name = name
    return provider


class TestTheSetting:
    def test_entries_keep_the_models_own_colons_and_an_address(self):
        assert parse("anthropic:claude-sonnet-5-5, ollama:gemma4:12b@http://box:11434/v1") == [
            ("anthropic", "claude-sonnet-5-5", ""),
            ("ollama", "gemma4:12b", "http://box:11434/v1")]
        assert parse("") == [] and parse(None) == []

    @pytest.mark.parametrize("bad", ["anthropic", ":model", "ollama:x@box:11434"])
    def test_a_bad_entry_says_the_shape(self, bad):
        with pytest.raises(ValueError, match="PROVIDER:MODEL"):
            parse(bad)

    def test_a_standby_that_cant_be_built_is_left_out_and_said(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        said: list[str] = []
        built = standby.standbys("anthropic:claude-sonnet-5-5", say=said.append)
        assert built == [] and "left out" in said[0]

    def test_wait_and_an_empty_list_give_the_primary_back(self):
        primary = up()
        assert standby.wrap(primary, "m", []) is primary
        assert standby.wrap(primary, "m", [(up(), "x")], "wait") is primary
        assert isinstance(standby.wrap(primary, "m", [(up(), "x")]), Standby)

    def test_the_composite_still_looks_like_its_primary(self):
        primary = up()
        primary.settings = "the primary's"
        wrapped = Standby(primary, "qwen3.8:latest", [(up(name="anthropic"), "claude")])
        assert wrapped.name == "ollama" and wrapped.settings == "the primary's"


class TestTheSwitch:
    def test_each_backend_gets_its_own_model_and_the_switch_is_said(self):
        primary, spare = down(), up("hi from the spare", name="anthropic")
        wrapped = Standby(primary, "qwen3.8:latest", [(spare, "claude-sonnet-5-5")])
        response = collect(wrapped.stream(messages=[], system=None, tools=[],
                                          model="qwen3.8:latest"))
        assert response.message.text() == "hi from the spare"
        assert primary.requests[0]["model"] == "qwen3.8:latest"
        assert spare.requests[0]["model"] == "claude-sonnet-5-5"
        said = wrapped.take_switches()
        assert said == ("qwen3.8:latest (ollama) didn't answer (nothing is listening "
                        "there), so claude-sonnet-5-5 (anthropic) answered",)
        assert wrapped.take_switches() == ()

    def test_a_healthy_primary_says_nothing(self):
        wrapped = Standby(up(), "m", [(up(), "x")])
        collect(wrapped.stream(messages=[], system=None, tools=[], model="m"))
        assert wrapped.take_switches() == ()

    def test_a_request_shaped_failure_is_not_tried_elsewhere(self):
        spare = up()
        wrapped = Standby(down(ContextOverflowError("prompt is too long")), "m",
                          [(spare, "x")])
        with pytest.raises(ContextOverflowError):
            collect(wrapped.stream(messages=[], system=None, tools=[], model="m"))
        assert spare.requests == []

    def test_a_stream_that_began_is_never_said_twice(self):
        class Dies(ScriptedProvider):
            name = "ollama"

            def stream(self, **kwargs):
                yield StartEvent(model="m")
                yield TextDelta("partial ")
                raise ProviderError("reset mid-stream")
        spare = up()
        wrapped = Standby(Dies([]), "m", [(spare, "x")])
        with pytest.raises(ProviderError, match="mid-stream"):
            collect(wrapped.stream(messages=[], system=None, tools=[], model="m"))
        assert spare.requests == []

    def test_all_down_is_one_error_naming_each(self):
        wrapped = Standby(down(), "qwen3.8:latest", [(down(name="anthropic"), "claude")])
        with pytest.raises(ProviderError, match="no model answered") as caught:
            collect(wrapped.stream(messages=[], system=None, tools=[],
                                   model="qwen3.8:latest"))
        words = down_words(caught.value)
        assert words.startswith("The model didn't answer, so nothing was done.")
        assert "qwen3.8:latest (ollama): nothing is listening there" in words
        assert "claude (anthropic)" in words


class TestThePersonHearsIt:
    def test_the_turn_end_carries_the_switch(self):
        wrapped = Standby(down(), "qwen3.8:latest", [(up("answered"), "gemma4:12b")])
        agent = Agent(wrapped, model="qwen3.8:latest")
        end = [e for e in agent.run_streaming("hi") if isinstance(e, TurnEnd)][-1]
        assert end.response.message.text() == "answered"
        assert end.switched and "gemma4:12b (ollama) answered" in end.switched[0]

    def test_the_async_twin_too(self):
        async def go():
            wrapped = Standby(down(), "q", [(up("answered"), "g")])
            agent = AsyncAgent(wrapped, model="q")
            return [e async for e in agent.run_streaming("hi") if isinstance(e, TurnEnd)][-1]
        end = asyncio.run(go())
        assert end.switched and "g (ollama) answered" in end.switched[0]

    def test_a_down_model_with_no_standby_is_said_in_words(self):
        assert down_words(REFUSED) == ("The model didn't answer (nothing is listening "
                                       "there), so nothing was done. Ask again in a "
                                       "little while.")
        assert down_words(ValueError("a bug")) is None


class TestAnAgentMaySayWait:
    def test_when_down_is_read_and_checked(self, tmp_path):
        from yantra import load_package
        from yantra.errors import ConfigError
        pkg = tmp_path / "mailreader"
        pkg.mkdir()
        (pkg / "prompt.md").write_text("read mail")
        (pkg / "agent.toml").write_text('[agent]\nname = "m"\nprompt = "prompt.md"\n'
                                        '[model]\nwhen_down = "wait"\n')
        assert load_package(pkg).when_down == "wait"
        (pkg / "agent.toml").write_text('[agent]\nname = "m"\nprompt = "prompt.md"\n'
                                        '[model]\nwhen_down = "later"\n')
        with pytest.raises(ConfigError, match='"switch"'):
            load_package(pkg)

    def test_a_built_agent_waits_or_switches_as_its_package_says(self, tmp_path,
                                                                monkeypatch):
        from yantra.spec import AgentSpec
        monkeypatch.setenv("YANTRA_FALLBACK", "ollama:gemma4:12b")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
        switch = AgentSpec(name="a", prompt="p", cwd=tmp_path).build(
            provider=up(), provider_name="ollama", model="qwen3.8:latest")
        wait = AgentSpec(name="b", prompt="p", cwd=tmp_path, when_down="wait").build(
            provider=up(), provider_name="ollama", model="qwen3.8:latest")
        assert isinstance(switch.provider, Standby)
        assert switch.provider.standing_by[0][1] == "gemma4:12b"
        assert not isinstance(wait.provider, Standby)
