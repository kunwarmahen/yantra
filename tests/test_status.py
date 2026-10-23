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


@pytest.fixture(autouse=True)
def no_siblings(monkeypatch):
    """The real Setu and Samay on this machine stay out: a test that is
    about them says where they are."""
    monkeypatch.setenv("YANTRA_SETU", "off")
    monkeypatch.setenv("YANTRA_SAMAY", "off")
    monkeypatch.setenv("YANTRA_SPARSH", "off")


@pytest.fixture(autouse=True)
def no_model_shown(monkeypatch):
    """No real Ollama is asked what a model holds; a test that is about
    it says what the server answers."""
    def refuse(*args, **kwargs):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(status.httpx, "post", refuse)


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
        assert data["local"] == {"answering": True, "pulled": False,
                                 "holds": None, "holds_from": None}
        assert data["problems"] == ["gemma4:12b is not pulled "
                                    "(ollama pull gemma4:12b)"]

    def test_a_bare_tag_matches_its_latest(self, monkeypatch):
        monkeypatch.setenv("YANTRA_PROVIDER", "local")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen3.8")
        monkeypatch.setattr(status.httpx, "get", _tags("qwen3.8:latest"))
        assert status.report()["local"]["pulled"] is True


class TestHowMuchTheModelHolds:
    """The bias: a window Yantra plans past, which Ollama cuts silently --
    the tools first -- and nothing anywhere says why the agent forgot."""

    def local(self, monkeypatch, *, loaded=None, num_ctx=None, window="65536"):
        monkeypatch.setenv("YANTRA_PROVIDER", "local")
        monkeypatch.setenv("OLLAMA_MODEL", "qwen3.8")
        monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", window)

        class Reply:
            def __init__(self, data):
                self.data = data

            def json(self):
                return self.data

        def get(url, **kw):
            if url.endswith("/api/ps"):
                return Reply({"models": [{"name": "qwen3.8:latest",
                                          "context_length": loaded}] if loaded else []})
            return Reply({"models": [{"name": "qwen3.8:latest"}]})

        def post(url, **kw):
            params = f"temperature 1\nnum_ctx {num_ctx}" if num_ctx else "temperature 1"
            return Reply({"parameters": params})
        monkeypatch.setattr(status.httpx, "get", get)
        monkeypatch.setattr(status.httpx, "post", post)
        return status.report()

    def test_the_loaded_models_own_number_comes_first(self, monkeypatch):
        data = self.local(monkeypatch, loaded=65536, num_ctx=4096)
        assert (data["local"]["holds"], data["local"]["holds_from"]) == (65536, "loaded")
        assert data["problems"] == []

    def test_a_window_smaller_than_the_plan_is_a_problem(self, monkeypatch):
        data = self.local(monkeypatch, num_ctx=8192)
        assert data["context_window"] == 65536
        assert "holds 8192 tokens but Yantra plans for 65536" in data["problems"][0]
        assert "OLLAMA_CONTEXT_WINDOW=8192" in data["problems"][0]

    def test_a_model_that_says_nothing_is_not_a_problem(self, monkeypatch):
        data = self.local(monkeypatch)
        assert data["local"]["holds"] is None and data["problems"] == []


class TestTheSiblings:
    def test_turned_off_is_not_found_and_not_a_problem(self):
        data = status.report()
        assert data["setu"]["found"] is False and data["samay"]["found"] is False
        assert data["sparsh"] == {"found": False, "program": None, "phones": []}
        assert not any(p.startswith(("setu", "samay", "sparsh")) for p in data["problems"])

    def test_prahari_off_is_not_found_and_a_missing_one_is_a_problem(self, monkeypatch):
        assert status.report()["prahari"] == {"found": False, "program": None,
                                              "serving": False}
        monkeypatch.setenv("YANTRA_PRAHARI", "/nowhere/prahari")
        data = status.report()
        assert "prahari: no prahari program at /nowhere/prahari" in data["problems"]
        assert "prahari: not found" in status.lines(data)

    def test_a_named_program_that_is_not_there_is_a_problem(self, monkeypatch):
        monkeypatch.setenv("YANTRA_SAMAY", "/nowhere/samay")
        data = status.report()
        assert data["samay"] == {"found": False, "program": None, "serving": False}
        assert "samay: no samay program at /nowhere/samay" in data["problems"]

    def test_a_setu_found_is_counted_never_listed(self, monkeypatch, tmp_path):
        setu = tmp_path / "setu"
        setu.write_text('#!/bin/sh\necho \'{"format": "setu.status.v1", "connections": '
                        '[{"ref": "gmail:mine", "email": "me@example.com"}]}\'\n')
        setu.chmod(0o755)
        monkeypatch.setenv("YANTRA_SETU", str(setu))
        data = status.report()
        assert data["setu"] == {"found": True, "road": str(setu), "connections": 1}
        assert "me@example.com" not in json.dumps(data)
