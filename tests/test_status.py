"""`yantra status`: what a program that starts Yantra can ask it.

The bias here is the bare word. Everything else Yantra takes is a flag,
and a positional word is a question for a model -- so the failure these
tests are designed against is ``yantra status --json`` quietly turning
into a paid turn whose answer a reader then refuses for having no
``format``. The rest guards the contract a reader leans on: the format
name, no secret in the output, and a problem reported as a line rather
than an exit.
"""

from __future__ import annotations

import json

import httpx
import pytest

from yantra import status
from yantra.cli.main import main


@pytest.fixture
def no_local_server(monkeypatch):
    def refuse(*args, **kwargs):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(status.httpx, "get", refuse)


def _tags(*names):
    class Reply:
        def json(self):
            return {"models": [{"name": n} for n in names]}
    return lambda *args, **kwargs: Reply()


class TestTheWordIsNotAPrompt:
    def test_status_json_answers_without_building_an_agent(self, monkeypatch,
                                                           capsys):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")

        def no_parser():
            raise AssertionError("status must not reach the prompt parser")
        monkeypatch.setattr("yantra.cli.main.build_parser", no_parser)
        assert main(["status", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["format"] == "yantra.status.v1"
        assert data["provider"] == "anthropic"

    def test_anything_else_after_status_is_refused(self, capsys):
        assert main(["status", "--verbose"]) == 2
        assert "--prompt" in capsys.readouterr().err

    def test_the_plain_form_is_lines_for_a_person(self, monkeypatch, capsys):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert main(["status"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("yantra ")
        assert "chosen by OPENAI_API_KEY" in out


class TestTheReport:
    def test_no_key_value_is_ever_in_it(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
        data = status.report()
        assert "sk-test-secret" not in json.dumps(data)
        assert data["chosen_by"] == "ANTHROPIC_API_KEY"

    def test_no_provider_is_a_problem_not_an_exit(self):
        data = status.report()
        assert data["provider"] is None
        assert "no provider found" in data["problems"][0]

    def test_a_cloud_provider_is_never_asked_anything(self, monkeypatch,
                                                      no_local_server):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        data = status.report()
        assert data["local"] is None and data["problems"] == []

    def test_a_local_server_that_is_down_says_how_to_start_it(
            self, monkeypatch, no_local_server):
        monkeypatch.setenv("YANTRA_PROVIDER", "local")
        data = status.report()
        assert data["local"] == {"answering": False, "pulled": None}
        assert "ollama serve" in data["problems"][0]

    def test_a_model_not_pulled_says_how_to_pull_it(self, monkeypatch):
        monkeypatch.setenv("YANTRA_PROVIDER", "local")
        monkeypatch.setenv("OLLAMA_MODEL", "gemma4:12b")
        monkeypatch.setattr(status.httpx, "get", _tags("qwen3.8:latest"))
        data = status.report()
        assert data["local"] == {"answering": True, "pulled": False}
        assert data["problems"] == ["gemma4:12b is not pulled "
                                    "(ollama pull gemma4:12b)"]

    def test_a_bare_tag_matches_its_latest(self, monkeypatch):
        monkeypatch.setenv("YANTRA_PROVIDER", "local")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen3.8")
        monkeypatch.setattr(status.httpx, "get", _tags("qwen3.8:latest"))
        assert status.report()["local"]["pulled"] is True
