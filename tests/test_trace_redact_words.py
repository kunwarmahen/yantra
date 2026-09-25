"""A list of names, scrubbed before a line is written (notes/86).

The bias here is the list that protects less than its owner believes.
A word list fails quietly in more ways than a pattern does, and each is
pinned below:

* **A name only partly scrubbed.** "Ana Lima" matched as "Ana" then
  "Lima" leaves "[redacted] [redacted]" -- two words, the second
  capitalised, which is half the name back. The longest entry wins.
* **The wrong words scrubbed.** "Bo" on the list must not eat "Bob",
  and "Ana" must not eat "Banana": a scrubber that mangles ordinary
  prose is a scrubber people switch off.
* **A name written differently.** Case, and a line break inside a
  name, are how a name actually turns up in a file.
* **A list that scrubs nothing.** An empty file, or one holding only
  comments, is refused at startup: whoever passed it thinks they are
  covered.
* **The list leaking through the thing that hides it.** Banner, page
  state and the line itself carry a COUNT of entries, never an entry.
* **Slow at size.** A real customer list is thousands of names; a
  recorder that took a second a turn would be left switched off.
"""

from __future__ import annotations

import json
import time

import pytest

from yantra.cli.main import main
from yantra.errors import ConfigError
from yantra.trace import ToolStep, Trajectory, TrajectoryLog, read_word_list


def turn(result: str, *, task: str = "who owns it?") -> Trajectory:
    return Trajectory(
        id="b" * 32, at="2026-09-24T12:00:00Z", provider="ollama",
        model="qwen3.8:latest", detail="full", task=task,
        steps=[ToolStep("read_file", ok=True, arguments={"path": "a.txt"},
                        result=result)],
        answer=result)


def scrubbed(tmp_path, text: str, words: list[str]) -> str:
    path = tmp_path / "t.jsonl"
    TrajectoryLog(path, detail="full", redact_words=words).record(turn(text))
    return json.loads(path.read_text())["steps"][0]["result"]


class TestWhatIsScrubbed:
    def test_the_longest_entry_wins(self, tmp_path):
        assert scrubbed(tmp_path, "owner: Ana Lima.", ["Ana", "Ana Lima"]) \
            == "owner: [redacted]."

    def test_any_case(self, tmp_path):
        assert scrubbed(tmp_path, "ANA and ana", ["Ana"]) \
            == "[redacted] and [redacted]"

    def test_a_name_broken_across_a_line_is_one_match(self, tmp_path):
        assert scrubbed(tmp_path, "Ana\n  Lima", ["Ana Lima"]) == "[redacted]"

    def test_only_whole_words(self, tmp_path):
        assert scrubbed(tmp_path, "Bob ate a banana", ["Bo", "Ana"]) \
            == "Bob ate a banana"

    def test_an_entry_ending_in_punctuation(self, tmp_path):
        """No word boundary after a full stop, so ``\\b`` would miss it."""
        assert scrubbed(tmp_path, "billed to Acme Inc. today",
                        ["Acme Inc."]) == "billed to [redacted] today"

    def test_regex_characters_are_literal(self, tmp_path):
        assert scrubbed(tmp_path, "see A.B and AxB", ["A.B"]) \
            == "see [redacted] and AxB"

    def test_every_content_field_and_the_count(self, tmp_path):
        path = tmp_path / "t.jsonl"
        TrajectoryLog(path, detail="full", redact_words=["Ana Lima"]).record(
            turn("owner Ana Lima", task="is Ana Lima the owner?"))
        raw = json.loads(path.read_text())
        assert "Lima" not in json.dumps(raw)
        assert raw["redacted"] == 3          # task, result, answer

    def test_words_and_patterns_together(self, tmp_path):
        path = tmp_path / "t.jsonl"
        TrajectoryLog(path, detail="full", redact=["email"],
                      redact_words=["Ana Lima"]).record(
            turn("Ana Lima <ana@example.com>"))
        assert json.loads(path.read_text())["steps"][0]["result"] \
            == "[redacted] <[redacted]>"


class TestTheFile:
    def test_comments_and_blank_lines_are_skipped(self, tmp_path):
        path = tmp_path / "names.txt"
        path.write_text("# from the CRM export\n\nAna Lima\n  Bo   Chen \n")
        assert read_word_list(path) == ["Ana Lima", "Bo Chen"]

    @pytest.mark.parametrize("text", ["", "\n\n", "# only a comment\n"])
    def test_a_list_that_scrubs_nothing_is_refused(self, tmp_path, text):
        path = tmp_path / "names.txt"
        path.write_text(text)
        with pytest.raises(ConfigError, match="no entries"):
            read_word_list(path)

    def test_a_one_letter_entry_is_refused_with_its_line(self, tmp_path):
        path = tmp_path / "names.txt"
        path.write_text("Ana\na\n")
        with pytest.raises(ConfigError, match=":2:"):
            read_word_list(path)

    def test_a_paragraph_is_not_an_entry(self, tmp_path):
        with pytest.raises(ConfigError, match="longer than"):
            TrajectoryLog(tmp_path / "t.jsonl", redact_words=["x" * 500])

    def test_a_missing_file_is_an_error_not_a_crash(self, tmp_path):
        with pytest.raises(ConfigError, match="cannot read"):
            read_word_list(tmp_path / "nope.txt")


class TestOnlyTheCountIsShown:
    def test_the_banner(self, tmp_path):
        log = TrajectoryLog(tmp_path / "t.jsonl", redact=["email"],
                            redact_words=["Ana Lima", "ana lima", "Bo Chen"])
        assert log.label == "shape, redacting 1 pattern(s) and 2 word(s)"
        assert log.redact_words == 2         # duplicates by case collapse

    def test_the_page(self, tmp_path):
        from test_web_server import make_session
        session, _ = make_session([])
        session.trace = TrajectoryLog(tmp_path / "t.jsonl",
                                      redact_words=["Ana Lima"])
        rec = session.state()["recording"]
        assert rec["redacting_words"] == 1 and "Lima" not in json.dumps(rec)


class TestTheFlag:
    def names(self, tmp_path, text="Ana Lima\n"):
        path = tmp_path / "names.txt"
        path.write_text(text)
        return str(path)

    def test_it_needs_a_trace(self, tmp_path, capsys):
        assert main(["--trace-redact-words", self.names(tmp_path)]) == 2
        assert "--trace-redact-words" in capsys.readouterr().err

    def test_an_empty_list_fails_before_anything_runs(self, tmp_path, capsys):
        assert main(["--trace", str(tmp_path / "t.jsonl"),
                     "--trace-redact-words", self.names(tmp_path, "#\n"),
                     "--prompt", "hi"]) == 2
        assert "no entries" in capsys.readouterr().err

    def test_it_does_not_rescrub_a_written_file(self, tmp_path, capsys):
        assert main(["--turns", "--trace", str(tmp_path / "t.jsonl"),
                     "--trace-redact-words", self.names(tmp_path)]) == 2


def test_twenty_thousand_names_do_not_slow_the_recorder(tmp_path):
    names = [f"Customer{i:05d} Surname{i % 97}" for i in range(20_000)]
    log = TrajectoryLog(tmp_path / "t.jsonl", detail="full",
                        redact_words=names)
    text = ("an ordinary line of a file that was read " * 400
            + names[12_345])
    started = time.monotonic()
    log.record(turn(text))
    assert time.monotonic() - started < 0.5
    assert json.loads(log.path.read_text())["redacted"] == 2
