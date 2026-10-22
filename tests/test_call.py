"""One tool, run once, with no model: ``yantra --call TOOL --args JSON``.

The bias these tests encode: A CALL WITH NOBODY BEHIND IT MAY ONLY READ.
A program on a timer (Prahari) runs these, so nobody approves them and
no model judged them wanted; a tool that changes something must be
refused by name, not run because it happened to be in the registry.

The second bias: THE MODEL IS NEVER ASKED. The provider here has no
script at all, so any model call fails the test -- and a missing API
key is no reason to refuse a call that needs none.

Everything is offline: the files are in a temporary folder.
"""

from __future__ import annotations

import json

import pytest

from conftest import ScriptedProvider
from yantra import unattended
from yantra.errors import ConfigError


@pytest.fixture(autouse=True)
def _fresh():
    unattended.clear()
    yield
    unattended.clear()


def run(monkeypatch, tmp_path, argv, *, no_key=False):
    import yantra.cli.main as cli_main

    provider = ScriptedProvider([])          # asked anything, it fails
    real = cli_main.load_settings

    def settings(name):
        if no_key and name == "anthropic":
            raise ConfigError("ANTHROPIC_API_KEY is not set")
        return real(name) if no_key else object()

    monkeypatch.setattr(cli_main, "guess_provider", lambda: "anthropic")
    monkeypatch.setattr(cli_main, "load_settings", settings)
    monkeypatch.setattr(cli_main, "get_provider", lambda *a, **k: provider)
    monkeypatch.setenv("YANTRA_UNATTENDED", "")
    for name in ("ENV_CONTEXT", "SETU", "MEMORY", "SAMAY", "SPARSH"):
        monkeypatch.setenv(f"YANTRA_{name}", "off")
    rc = cli_main.main(["--cwd", str(tmp_path), *argv])
    assert provider.requests == []           # the model was never asked
    return rc


def said(capsys) -> dict:
    out = capsys.readouterr().out
    return json.loads(out)                   # one object, nothing else


class TestOneToolNoModel:
    def test_a_reading_tool_answers_in_one_object(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "price.txt").write_text("ALAB 47.10\n")
        rc = run(monkeypatch, tmp_path, ["--json", "--call", "read_file",
                                         "--args", '{"path": "price.txt"}'])
        call = said(capsys)
        assert rc == 0
        assert call["format"] == "yantra.call.v1"
        assert call["ok"] is True and call["tool"] == "read_file"
        assert "ALAB 47.10" in call["text"]
        assert call["needs_person"] == [] and call["detail"] == ""

    def test_without_json_the_text_alone_is_printed(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "a.txt").write_text("hello\n")
        assert run(monkeypatch, tmp_path, ["--call", "read_file",
                                           "--args", '{"path": "a.txt"}']) == 0
        assert "hello" in capsys.readouterr().out

    def test_a_tool_that_writes_is_refused_and_does_nothing(self, tmp_path, monkeypatch,
                                                            capsys):
        rc = run(monkeypatch, tmp_path, [
            "--json", "--allow-tools", "write_*", "--call", "write_file",
            "--args", '{"path": "x.txt", "content": "x"}'])
        call = said(capsys)
        assert rc == 1 and call["ok"] is False
        assert "only tools that read" in call["detail"]
        assert not (tmp_path / "x.txt").exists()      # a glob does not widen it


class TestOneCallSaidYesTo:
    """Fetching an address is not read-only (an address can carry data
    out); a person who accepted that one address on a timer said yes to
    exactly it, and to nothing near it."""

    ARGS = '{"path": "x.txt", "content": "x"}'

    def test_every_argument_fixed_runs_it(self, tmp_path, monkeypatch, capsys):
        rc = run(monkeypatch, tmp_path, [
            "--json", "--allow-tools", "write_file(path=x.txt, content=x)",
            "--call", "write_file", "--args", self.ARGS])
        assert rc == 0 and said(capsys)["ok"] is True
        assert (tmp_path / "x.txt").read_text() == "x"

    def test_an_argument_left_free_is_not_a_yes(self, tmp_path, monkeypatch, capsys):
        rc = run(monkeypatch, tmp_path, [
            "--json", "--allow-tools", "write_file(path=x.txt)",
            "--call", "write_file", "--args", self.ARGS])
        assert rc == 1 and not (tmp_path / "x.txt").exists()
        assert "word for word" in said(capsys)["detail"]

    def test_a_different_value_is_not_a_yes(self, tmp_path, monkeypatch, capsys):
        rc = run(monkeypatch, tmp_path, [
            "--json", "--allow-tools", "write_file(path=y.txt, content=x)",
            "--call", "write_file", "--args", self.ARGS])
        assert rc == 1 and not (tmp_path / "x.txt").exists()
        said(capsys)

    def test_a_glob_name_with_fixed_arguments_is_not_a_yes(self, tmp_path, monkeypatch,
                                                            capsys):
        rc = run(monkeypatch, tmp_path, [
            "--json", "--allow-tools", "write_*(path=x.txt, content=x)",
            "--call", "write_file", "--args", self.ARGS])
        assert rc == 1 and not (tmp_path / "x.txt").exists()
        said(capsys)

    def test_a_tool_that_is_not_here_says_the_near_names(self, tmp_path, monkeypatch,
                                                          capsys):
        rc = run(monkeypatch, tmp_path, ["--json", "--call", "read_fil"])
        call = said(capsys)
        assert rc == 1
        assert "no tool 'read_fil'" in call["detail"] and "read_file" in call["detail"]

    def test_a_tool_failure_is_the_answer(self, tmp_path, monkeypatch, capsys):
        rc = run(monkeypatch, tmp_path, ["--json", "--call", "read_file",
                                         "--args", '{"path": "missing.txt"}'])
        call = said(capsys)
        assert rc == 1 and call["ok"] is False and call["detail"]

    def test_arguments_that_are_not_an_object_are_refused(self, tmp_path, monkeypatch,
                                                          capsys):
        for bad in ("[1, 2]", "{nope"):
            assert run(monkeypatch, tmp_path, ["--json", "--call", "read_file",
                                               "--args", bad]) == 1
            assert said(capsys)["ok"] is False

    def test_no_api_key_is_no_reason_to_refuse(self, tmp_path, monkeypatch, capsys):
        (tmp_path / "a.txt").write_text("still here\n")
        rc = run(monkeypatch, tmp_path, ["--json", "--call", "read_file",
                                         "--args", '{"path": "a.txt"}'], no_key=True)
        assert rc == 0 and "still here" in said(capsys)["text"]


class TestWhatACallIsNot:
    def test_a_call_takes_no_prompt(self, tmp_path, monkeypatch, capsys):
        assert run(monkeypatch, tmp_path, ["--call", "read_file", "--prompt", "hi"]) == 2
        assert "takes no prompt" in capsys.readouterr().err

    def test_args_without_a_call(self, tmp_path, monkeypatch, capsys):
        assert run(monkeypatch, tmp_path, ["--args", "{}", "--prompt", "hi"]) == 2
        assert "--args goes with --call" in capsys.readouterr().err

    def test_a_call_is_unattended(self, tmp_path, monkeypatch, capsys):
        import os
        (tmp_path / "a.txt").write_text("x\n")
        run(monkeypatch, tmp_path, ["--json", "--call", "read_file",
                                    "--args", '{"path": "a.txt"}'])
        assert os.environ.get("YANTRA_UNATTENDED") == "1"
