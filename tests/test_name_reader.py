"""A local model reading for names before a line is written (notes/89).

The bias here is a second reader that makes a recording LESS safe than
the list alone, or safe only while nothing goes wrong:

* **It removes instead of adds.** The operator's list and patterns must
  still run, whatever the model says or fails to say.
* **Half a name.** The model lists "Dmitri Nkosi" and not the bare
  "Dmitri" three lines later -- the one pattern the measurement found.
* **A failure that writes everything.** An error, a list cut off by the
  token limit, or prose instead of a list must withhold the contents,
  never write them unscrubbed -- and say so where the operator looks.
* **Text it never saw.** A child's steps, a tool's arguments, and a text
  longer than one question are all things the recorder writes.
* **What it found leaking out.** Only the model's tag is ever shown.
"""

from __future__ import annotations

import json

import pytest

from yantra.cli.main import main
from yantra.name_reader import (CHUNK_CHARS, NameReader, ReaderFailed,
                                chunks, read_names, with_each_word)
from yantra.trace import (WITHHELD, ChildRun, ToolStep, Trajectory,
                          TrajectoryLog)
from yantra.types import Message, ModelResponse, TextBlock, Usage

from conftest import ScriptedProvider


def answer(text: str, *, stop: str = "end_turn") -> ModelResponse:
    return ModelResponse(message=Message("assistant", [TextBlock(text)]),
                         stop_reason=stop, usage=Usage(), model="qwen")


class Broken:
    """A provider that is down."""

    def complete(self, **kwargs):
        raise ConnectionError("ollama is not running")


def turn(result: str, *, task: str = "who owns it?") -> Trajectory:
    return Trajectory(
        id="c" * 32, at="2026-09-25T12:00:00Z", provider="ollama",
        model="qwen3.8:latest", detail="full", task=task,
        steps=[ToolStep("read_file", ok=True, arguments={"path": "a.txt"},
                        result=result)],
        answer="done")


def recorded(tmp_path, trajectory, reader, **kwargs) -> dict:
    path = tmp_path / "t.jsonl"
    TrajectoryLog(path, detail="full", reader=reader, **kwargs) \
        .record(trajectory)
    return json.loads(path.read_text())


def reading(*answers: str) -> NameReader:
    return NameReader("qwen3.8:latest", provider=ScriptedProvider(
        [answer(a) for a in answers]))


class TestWhatIsScrubbed:
    def test_a_name_the_model_finds_is_scrubbed(self, tmp_path):
        line = recorded(tmp_path, turn("owner: Ana Lima"),
                        reading("Ana Lima"))
        assert line["steps"][0]["result"] == "owner: [redacted]"
        assert line["redacted"] == 1

    def test_the_bare_first_name_goes_with_the_full_one(self, tmp_path):
        text = "[02] Dmitri Nkosi: hi\n[09] Lucía: looping in Dmitri"
        line = recorded(tmp_path, turn(text), reading("Dmitri Nkosi\nLucía"))
        assert "Dmitri" not in line["steps"][0]["result"]
        assert "Lucía" not in line["steps"][0]["result"]

    def test_the_list_still_runs_when_the_model_finds_nothing(self,
                                                              tmp_path):
        line = recorded(tmp_path, turn("owner: Bo Chen, bo@example.com"),
                        reading("NONE"), redact_words=["Bo Chen"],
                        redact=["email"])
        assert line["steps"][0]["result"] == "owner: [redacted], [redacted]"
        assert line["redacted"] == 2

    def test_both_scrubs_are_counted_on_the_line(self, tmp_path):
        line = recorded(tmp_path, turn("Ana Lima and Bo Chen"),
                        reading("Ana Lima"), redact_words=["Bo Chen"])
        assert line["steps"][0]["result"] == "[redacted] and [redacted]"
        assert line["redacted"] == 2

    def test_the_reader_sees_everything_the_file_would_hold(self, tmp_path):
        provider = ScriptedProvider([answer("NONE")])
        trajectory = turn("result text", task="task text")
        trajectory.steps[0].arguments = {"path": "args text"}
        trajectory.children = [ChildRun(
            number=1, agent="helper", model="qwen", task="child task",
            answer="child answer",
            steps=[ToolStep("read_file", ok=True, result="child read")])]
        recorded(tmp_path, trajectory,
                 NameReader("qwen3.8:latest", provider=provider))
        shown = provider.requests[0]["messages"][0].text()
        for text in ("task text", "args text", "result text", "done",
                     "child task", "child answer", "child read"):
            assert text in shown


class TestAFailureWithholds:
    @pytest.mark.parametrize("reader", [
        NameReader("qwen3.8:latest", provider=Broken()),
        NameReader("qwen3.8:latest", provider=ScriptedProvider(
            [answer("Ana Lima\nBo", stop="max_tokens")])),
        NameReader("qwen3.8:latest", provider=ScriptedProvider(
            [answer("Here is what I found. " * 20)])),
    ], ids=["provider down", "cut off", "prose"])
    def test_nothing_is_written_unscrubbed(self, tmp_path, reader):
        line = recorded(tmp_path, turn("owner: Ana Lima"), reader)
        assert "Ana" not in json.dumps(line)
        assert line["steps"][0]["result"] == WITHHELD
        assert line["task"] == WITHHELD
        assert line["withheld"].startswith("the name reader failed")

    def test_the_shape_of_the_turn_survives(self, tmp_path):
        line = recorded(tmp_path, turn("x"),
                        NameReader("m", provider=Broken()))
        assert line["steps"][0]["name"] == "read_file"
        assert line["outcome"] == "end_turn"

    def test_turns_says_the_contents_were_withheld(self, tmp_path, capsys):
        path = tmp_path / "t.jsonl"
        TrajectoryLog(path, detail="full",
                      reader=NameReader("m", provider=Broken())) \
            .record(turn("x"))
        assert main(["--trace", str(path), "--turns"]) == 0
        assert "contents withheld: the name reader failed" in \
            capsys.readouterr().out


class TestReadingPieces:
    def test_a_long_text_is_asked_in_pieces_no_bigger_than_measured(self):
        text = "\n".join(f"{i},Person Number{i}" for i in range(2000))
        pieces = chunks([text])
        assert len(pieces) > 1
        assert all(len(p) <= CHUNK_CHARS for p in pieces)
        assert "".join(pieces).replace("\n", "") == text.replace("\n", "")

    def test_a_line_is_not_cut_when_it_fits(self):
        pieces = chunks(["a" * 5000, "b" * 5000])
        assert pieces == ["a" * 5000, "b" * 5000]

    def test_every_piece_is_read_and_one_failure_fails_the_turn(self):
        text = "\n".join("x" * 100 for _ in range(200))
        provider = ScriptedProvider([answer("Ana"), answer("Bo", stop="max_tokens")])
        with pytest.raises(ReaderFailed):
            NameReader("m", provider=provider).names([text])

    def test_none_is_no_names(self):
        assert read_names(ScriptedProvider([answer("NONE")]), "m", "t") == []

    def test_each_word_of_a_name_but_no_single_letters(self):
        assert with_each_word(["Ana Lima", "J. Okafor", "Bo"]) == [
            "Ana Lima", "Ana", "Lima", "J. Okafor", "J.", "Okafor", "Bo"]


class TestWhatIsShown:
    def test_the_banner_names_the_model_and_nothing_it_found(self, tmp_path):
        log = TrajectoryLog(tmp_path / "t.jsonl", detail="full",
                            reader=NameReader("qwen3.8:latest"))
        assert log.label == "full, redacting names read by qwen3.8:latest"

    def test_it_is_always_a_local_model(self):
        assert NameReader("qwen3.8:latest").provider_name == "ollama"

    def test_a_reader_needs_a_model(self):
        with pytest.raises(ValueError, match="Ollama model tag"):
            NameReader("  ")

    def test_the_flag_needs_a_trace(self, capsys):
        assert main(["--trace-redact-reader", "qwen3.8:latest",
                     "--prompt", "hi"]) == 2
        assert "--trace-redact-reader" in capsys.readouterr().err


class TestOrder:
    def test_an_address_is_still_an_address(self, tmp_path):
        """The receipt that found this: "ana" hidden first left
        "[redacted]@example.com", which the email pattern no longer saw."""
        line = recorded(tmp_path, turn("owner: Ana Lima (ana@example.com)"),
                        reading("Ana Lima\nana"), redact=["email"])
        assert line["steps"][0]["result"] == "owner: [redacted] ([redacted])"
        assert "example.com" not in json.dumps(line)

    def test_the_reader_is_shown_what_the_list_left(self, tmp_path):
        provider = ScriptedProvider([answer("NONE")])
        recorded(tmp_path, turn("Bo Chen wrote it"),
                 NameReader("m", provider=provider), redact_words=["Bo Chen"])
        assert "Bo Chen" not in provider.requests[0]["messages"][0].text()
