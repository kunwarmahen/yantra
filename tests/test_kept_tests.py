"""Kept tests: a conversation from History replayed with fakes.

THE BIAS these tests encode is against a replay that is not one: a real
tool running during a test (an email sent again at 3am), a fake that
only answers the identical call (red every time a model rephrases), a
kept turn asked without the conversation before it, a judge or a
word check that passes when nobody checked, and a turn that looked at a
picture replayed blind. Every tool here that is not a fake raises if it
runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, ClassVar

import pytest
from conftest import ScriptedProvider, assistant_text, assistant_tool_call

from yantra.errors import ConfigError
from yantra.eval_suite import load_cases, render_case
from yantra.evals import EvalCase, EvalRunner
from yantra.kept_tests import (NOT_RECORDED, Call, Recording, best_match,
                               forget, from_payload, keep, kept_in, night,
                               said, suite_dir, suites)
from yantra.permissions import allow_read_only
from yantra.tools.base import Tool, ToolRegistry


class LiveMailTool(Tool):
    """Stands for a tool that changes the world. Must never run."""
    name: ClassVar[str] = "send_mail"
    description: ClassVar[str] = "sends an email"
    parameters: ClassVar[dict] = {"type": "object",
                                  "properties": {"to": {"type": "string"}}}

    def summary(self, args: dict[str, Any], ctx) -> str:
        return "send"

    def run(self, args: dict[str, Any], ctx) -> str:
        raise AssertionError("a real tool ran during a replay")


class LiveSearchTool(LiveMailTool):
    name: ClassVar[str] = "search_mail"
    description: ClassVar[str] = "searches the mailbox"
    parameters: ClassVar[dict] = {"type": "object",
                                  "properties": {"q": {"type": "string"},
                                                 "box": {"type": "string"}}}
    read_only: ClassVar[bool] = True


def _payload() -> dict:
    """Two turns: a greeting, then the kept one with a search and an answer."""
    return {"format": 1, "model": "qwen3.8:latest", "history": [
        {"role": "user", "content": [{"kind": "text", "text": "hello"}]},
        {"role": "assistant", "content": [{"kind": "text", "text": "hi!"}]},
        {"role": "user", "content": [{"kind": "text",
                                      "text": "what did Amazon refund me?"}]},
        {"role": "assistant", "content": [
            {"kind": "tool_call", "id": "c1", "name": "search_mail",
             "arguments": {"q": "amazon refund", "box": "inbox"}}]},
        {"role": "user", "content": [
            {"kind": "tool_result", "tool_call_id": "c1",
             "content": "Refund of $42.10 issued May 20", "is_error": False}]},
        {"role": "assistant", "content": [
            {"kind": "text", "text": "Amazon refunded $42.10 on May 20."}]},
    ]}


def _runner(script: list) -> tuple[EvalRunner, ScriptedProvider]:
    provider = ScriptedProvider(script)
    tools = ToolRegistry()
    tools.register(LiveMailTool())
    tools.register(LiveSearchTool())
    # The unattended gate, as the night run has: a fake must get through
    # it by being read-only, whatever the tool it stands for is.
    return EvalRunner(provider, "m", tools=tools,
                      permissions=allow_read_only), provider


class TestWhatIsRecorded:
    def test_the_last_turn_is_kept_with_the_conversation_before_it(self):
        rec = from_payload(_payload(), conversation="abc123")
        assert rec.question == "what did Amazon refund me?"
        assert rec.answer == "Amazon refunded $42.10 on May 20."
        assert [m["role"] for m in rec.before] == ["user", "assistant"]
        assert rec.used == ["search_mail"]
        assert rec.calls == [Call("search_mail",
                                  {"q": "amazon refund", "box": "inbox"},
                                  "Refund of $42.10 issued May 20")]

    def test_a_turn_with_no_answer_yet_cannot_be_kept(self):
        payload = _payload()
        payload["history"] = payload["history"][:3]
        with pytest.raises(ConfigError, match="no answer yet"):
            from_payload(payload)

    def test_only_the_schemas_of_tools_it_called_are_kept(self):
        rec = from_payload(_payload(), tools={
            "search_mail": {"description": "d", "parameters": {}},
            "bash": {"description": "b", "parameters": {}}})
        assert set(rec.tools) == {"search_mail"}


class TestPictures:
    """A tool that answered with a picture is replayed with it."""

    @staticmethod
    def payload() -> dict:
        shot = {"kind": "image", "media_type": "image/png", "data": "iVBORw0KGgo="}
        return {"format": 1, "history": [
            {"role": "user", "content": [{"kind": "text", "text": "is the page red?"}]},
            {"role": "assistant", "content": [
                {"kind": "tool_call", "id": "c1", "name": "read_image",
                 "arguments": {"path": "page.png"}},
                {"kind": "tool_call", "id": "c2", "name": "search_mail",
                 "arguments": {"q": "red"}}]},
            {"role": "user", "content": [
                {"kind": "tool_result", "tool_call_id": "c1", "content": "page.png"},
                {"kind": "tool_result", "tool_call_id": "c2", "content": "no mail"},
                shot]},
            {"role": "assistant", "content": [{"kind": "text", "text": "Yes, red."}]},
        ], "steps": {"c1": {"pictures": 1}, "c2": {}}}

    def test_each_calls_pictures_are_recorded_and_kept_in_the_file(self):
        recording = from_payload(self.payload())
        shot, mail = recording.calls
        assert shot.pictures == [{"media_type": "image/png", "data": "iVBORw0KGgo="}]
        assert mail.pictures == []
        raw = recording.to_json()
        assert "pictures" not in raw["calls"][1]                 # written as it always was
        assert Recording.from_json(json.loads(json.dumps(raw))).calls[0].pictures == \
            shot.pictures

    def test_the_stand_in_answers_with_the_picture(self):
        from yantra.kept_tests import ReplayTool
        from yantra.tools.base import ToolOutput
        recording = from_payload(self.payload())
        tool = ReplayTool("read_image", "reads an image", {"type": "object"}, recording)
        got = tool.run({"path": "page.png"}, None)
        assert isinstance(got, ToolOutput) and got.text == "page.png"
        assert [(i.media_type, i.data) for i in got.images] == [("image/png", "iVBORw0KGgo=")]
        mail = ReplayTool("search_mail", "searches", {"type": "object"}, recording)
        assert mail.run({"q": "red"}, None) == "no mail"         # text alone, as before


class TestSameNameBestMatch:
    calls = [Call("search", {"q": "amazon", "box": "inbox"}, "first"),
             Call("search", {"q": "refund", "box": "spam"}, "second")]

    def test_the_identical_call_gets_its_own_answer(self):
        assert best_match(self.calls, "search",
                          {"q": "refund", "box": "spam"}).content == "second"

    def test_a_reworded_call_gets_the_closest_recording(self):
        assert best_match(self.calls, "search",
                          {"q": "amazon refunds", "box": "spam"}).content == "second"

    def test_a_tie_goes_to_the_earliest(self):
        assert best_match(self.calls, "search", {"q": "x"}).content == "first"

    def test_a_tool_never_called_has_no_match(self):
        assert best_match(self.calls, "send", {}) is None


class TestKeeping:
    def test_a_kept_case_loads_with_its_recording_and_appends(self, tmp_path):
        rec = from_payload(_payload(), conversation="abc12345ffff")
        first = keep(rec, tmp_path, words=["May 20", " "], title="Amazon refund")
        second = keep(rec, tmp_path)
        assert (first, second) == ("kept-abc12345", "kept-abc12345-2")
        cases = load_cases(tmp_path)
        assert [c.id for c in cases] == [first, second]
        one = cases[0]
        assert one.user_message == rec.question
        assert one.required_tools == ["search_mail"]
        assert one.answer_contains == ["May 20"]
        assert one.judge is True
        assert one.replay.answer == rec.answer

    def test_editing_the_recording_changes_the_case(self, tmp_path):
        rec = from_payload(_payload(), conversation="abc12345")
        keep(rec, tmp_path)
        before = load_cases(tmp_path)[0].fingerprint
        replay = tmp_path / "replay" / "kept-abc12345.json"
        raw = json.loads(replay.read_text())
        raw["answer"] = "something else"
        replay.write_text(json.dumps(raw))
        assert load_cases(tmp_path)[0].fingerprint != before

    def test_a_replay_outside_the_suite_is_refused(self, tmp_path):
        (tmp_path / "cases.toml").write_text(
            '[[case]]\nid = "x"\nuser_message = "q"\nreplay = "../r.json"\n')
        with pytest.raises(ConfigError, match="inside"):
            load_cases(tmp_path)

    def test_a_judge_with_nothing_to_compare_is_refused(self, tmp_path):
        (tmp_path / "cases.toml").write_text(
            '[[case]]\nid = "x"\nuser_message = "q"\njudge = true\n')
        with pytest.raises(ConfigError, match="only a replay"):
            load_cases(tmp_path)

    def test_answer_contains_renders_back(self):
        out = render_case(EvalCase(id="x", description="", user_message="q",
                                   answer_contains=["May 20"]))
        assert 'answer_contains = ["May 20"]' in out

    def test_each_agent_has_its_own_folder(self, tmp_path):
        assert suite_dir("minder", tmp_path) == tmp_path / "minder"
        assert suite_dir(None, tmp_path) == tmp_path / "yantra"
        assert suite_dir("../x y", tmp_path) == tmp_path / "x-y"


def _case(**over) -> EvalCase:
    rec = from_payload(_payload(), conversation="abc")
    base = dict(id="k", description="", user_message=rec.question,
                required_tools=rec.used, replay=rec)
    return EvalCase(**{**base, **over})


class TestReplaying:
    def test_tools_answer_from_the_recording_and_nothing_real_runs(self):
        runner, provider = _runner([
            assistant_tool_call("t1", "search_mail", {"q": "refund from amazon"}),
            assistant_tool_call("t2", "send_mail", {"to": "me"}),
            assistant_text("Refunded $42.10 on May 20."),
        ])
        result = runner.run_case(_case(answer_contains=["May 20", "$42.10"]))
        assert result.passed, result.failures
        assert result.tool_calls_seen == ["search_mail", "send_mail"]
        results = [b for m in provider.requests[-1]["messages"]
                   for b in m.content if getattr(b, "tool_call_id", None)]
        assert results[0].content == "Refund of $42.10 issued May 20"
        assert results[1].content == NOT_RECORDED.format(name="send_mail")

    def test_the_kept_turn_is_asked_with_the_conversation_before_it(self):
        runner, provider = _runner([assistant_text("May 20")])
        runner.run_case(_case(required_tools=[]))
        sent = [m.text() for m in provider.requests[0]["messages"]]
        assert sent[:3] == ["hello", "hi!", "what did Amazon refund me?"]

    def test_a_recorded_tool_tonight_lacks_is_stood_in(self):
        rec = from_payload(_payload(), conversation="abc")
        rec.calls.append(Call("phone_read", {}, "battery 80%"))
        runner, provider = _runner([
            assistant_tool_call("t1", "phone_read", {}),
            assistant_text("80%")])
        result = runner.run_case(_case(replay=rec, required_tools=[]))
        assert result.passed, result.failures
        offered = {t.name for t in provider.requests[0]["tools"]}
        assert {"phone_read", "search_mail", "send_mail"} <= offered

    def test_missing_words_each_fail_by_name(self):
        runner, _ = _runner([assistant_text("Amazon refunded you.")])
        result = runner.run_case(_case(required_tools=[],
                                       answer_contains=["may 20", "$42.10"]))
        assert result.failures == ["answer does not say: may 20",
                                   "answer does not say: $42.10"]

    def test_the_words_ignore_case(self):
        runner, _ = _runner([assistant_text("refund on MAY 20")])
        assert runner.run_case(_case(required_tools=[],
                                     answer_contains=["May 20"])).passed

    def test_the_judge_compares_with_the_kept_answer(self):
        runner, provider = _runner([assistant_text("It was $42.10, May 20."),
                                    assistant_text("PASS same facts")])
        assert runner.run_case(_case(required_tools=[], judge=True)).passed
        asked = provider.requests[-1]["messages"][0].text()
        assert "Amazon refunded $42.10 on May 20." in asked
        assert provider.requests[-1]["tools"] == []

    def test_a_judge_that_disagrees_fails_the_case(self):
        runner, _ = _runner([assistant_text("It was $10."),
                             assistant_text("FAIL wrong amount")])
        result = runner.run_case(_case(required_tools=[], judge=True))
        assert result.failures == ["judge: FAIL wrong amount"]

    def test_a_judge_that_cannot_answer_fails_closed(self):
        runner, _ = _runner([assistant_text("It was $42.10.")])  # nothing left
        result = runner.run_case(_case(required_tools=[], judge=True))
        assert not result.passed
        assert result.failures[0].startswith("judge could not answer")

    def test_a_judge_without_a_replay_is_refused(self):
        with pytest.raises(ValueError, match="only a replay"):
            EvalCase(id="x", description="", user_message="q", judge=True)

    def test_an_error_recorded_is_an_error_replayed(self):
        rec = Recording(question="q", answer="a",
                        calls=[Call("search_mail", {}, "mailbox locked", True)])
        runner, provider = _runner([assistant_tool_call("t1", "search_mail", {}),
                                    assistant_text("locked")])
        runner.run_case(EvalCase(id="x", description="", user_message="q",
                                 replay=rec))
        result = [b for m in provider.requests[-1]["messages"]
                  for b in m.content if getattr(b, "tool_call_id", None)][0]
        assert result.is_error and "mailbox locked" in result.content


class TestTheCommand:
    @pytest.fixture
    def kept(self, tmp_path, monkeypatch):
        from yantra.history import ConversationLog
        monkeypatch.setenv("YANTRA_HISTORY", str(tmp_path / "h.sqlite"))
        monkeypatch.setenv("YANTRA_TESTS", str(tmp_path / "tests"))
        log = ConversationLog(tmp_path / "h.sqlite")
        log.keep("feedbeef0001", _payload(), road="terminal")
        log.close()
        return tmp_path

    def test_keep_test_writes_a_case_for_the_agent(self, kept, capsys):
        from yantra.cli.main import main
        assert main(["--keep-test", "feedbeef", "--words", "May 20"]) == 0
        cases = load_cases(Path(kept) / "tests" / "yantra")
        assert cases[0].id == "kept-feedbeef"
        assert cases[0].answer_contains == ["May 20"]
        assert "--eval --kept" in capsys.readouterr().out

    def test_an_unknown_id_is_said(self, kept, capsys):
        from yantra.cli.main import main
        assert main(["--keep-test", "nope"]) == 2
        assert "no conversation" in capsys.readouterr().err

    def test_words_without_keep_test_are_refused(self, capsys):
        from yantra.cli.main import main
        assert main(["--words", "x"]) == 2
        assert "--keep-test" in capsys.readouterr().err

    def test_kept_without_eval_is_refused(self, capsys):
        from yantra.cli.main import main
        assert main(["--kept"]) == 2
        assert "belong to --eval" in capsys.readouterr().err

    def test_eval_kept_with_nothing_kept_says_how(self, tmp_path, monkeypatch,
                                                  capsys):
        from yantra.cli.main import main
        monkeypatch.setenv("YANTRA_TESTS", str(tmp_path))
        assert main(["--eval", "--kept"]) == 2
        assert "no conversation is kept" in capsys.readouterr().err


class TestFromTheTerminalAndThePage:
    """The same keep, from the two surfaces that show History."""

    def test_chats_test_keeps_one_with_its_words(self, tmp_path, monkeypatch):
        from test_history import repl_with, talked

        from yantra.history import ConversationLog
        monkeypatch.setenv("YANTRA_TESTS", str(tmp_path / "tests"))
        log = ConversationLog(tmp_path / "h.sqlite")
        talked(log, "x1", "how long to reset the router", "hold it ten seconds")
        repl, out = repl_with(log, ["/chats", "/chats test 1 ten seconds, hold",
                                    "/quit"], [])
        repl.run()
        assert "kept as a test: kept-x1" in out.getvalue()
        [case] = load_cases(tmp_path / "tests" / "yantra")
        assert case.answer_contains == ["ten seconds", "hold"]

    def test_the_page_keeps_one_and_says_what_it_checks(self, tmp_path,
                                                        monkeypatch):
        pytest.importorskip("fastapi")
        from test_history import talked, web

        from yantra.history import ConversationLog
        monkeypatch.setenv("YANTRA_TESTS", str(tmp_path / "tests"))
        log = ConversationLog(tmp_path / "h.sqlite")
        talked(log, "old", "the router question", "hold reset")
        session, _, client = web(log, [])
        session.tests_for = "minder"
        got = client.post("/api/chats/old/test", json={"words": ["reset"]})
        assert got.status_code == 200, got.text
        assert got.json()["case"] == "kept-old"
        [case] = load_cases(tmp_path / "tests" / "minder")
        assert case.answer_contains == ["reset"]
        assert client.post("/api/chats/gone/test").status_code == 400


FAKE_YANTRA = '''
import json, os, sys
from pathlib import Path
from yantra.eval_report import CaseRecord, SuiteRun, write_report
args = sys.argv[1:]
suite = Path(args[args.index("--suite") + 1])
report = Path(args[args.index("--report") + 1])
Path(os.environ["FAKE_LOG"]).open("a").write(json.dumps(args) + "\\n")
if (suite / "broken").exists():
    sys.exit("error: the model is not answering")
red = set(json.loads((suite / "red.json").read_text())) if (suite / "red.json").exists() else set()
cases = [CaseRecord(id=c, passed=c not in red, attempts=1, passes=int(c not in red),
                    min_pass_rate=1.0, tokens=10, seconds=1.0, ran_model=True,
                    failures=[] if c not in red else ["judge: FAIL"])
         for c in ("kept-a", "kept-b")]
write_report(report, SuiteRun(suite="s", provider="ollama", model="qwen3.8:latest",
             at="2026-10-22T03:00:00Z", repeat=1, cases_in_suite=2, cases=cases))
'''


class TestEveryNight:
    """The bias: a night that quietly skipped a suite, or called a broken
    one green, is a card that checked less than it says."""

    @pytest.fixture
    def root(self, tmp_path, monkeypatch):
        fake = tmp_path / "fake_yantra.py"
        fake.write_text(FAKE_YANTRA)
        monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
        root = tmp_path / "tests"
        rec = from_payload(_payload(), conversation="abc")
        keep(rec, root / "yantra")
        keep(rec, root / "people" / "naina" / "minder", agent="minder",
             package=tmp_path, person="naina")
        return root, [sys.executable, str(fake)]

    def calls(self, root):
        return [json.loads(line) for line in
                (root.parent / "calls.jsonl").read_text().splitlines()]

    def test_every_suite_is_found_yours_and_each_persons(self, root):
        found = suites(root[0])
        assert [p.relative_to(root[0]).as_posix() for p in found] == [
            "people/naina/minder", "yantra"]

    def test_each_suite_runs_against_its_own_agent(self, root):
        tests, command = root
        summary = night(tests, command=command, extra=["--model", "qwen3.8:latest"])
        assert summary["cases"] == 4 and summary["passed"] == 4 and summary["green"]
        naina, mine = self.calls(tests)
        assert "--agent" in naina and "--agent" not in mine
        assert naina[-2:] == ["--model", "qwen3.8:latest"]
        assert json.loads((tests / "last.json").read_text())["format"] == "yantra.tests.v1"

    def test_what_broke_since_last_time_is_named(self, root):
        tests, command = root
        night(tests, command=command)
        import time as clock
        clock.sleep(1.1)                      # a new report file name
        (tests / "yantra" / "red.json").write_text('["kept-b"]')
        summary = night(tests, command=command)
        mine = next(r for r in summary["suites"] if r["agent"] == "yantra")
        assert mine["broke"] == ["kept-b"] and mine["red"] == ["kept-b"]
        assert summary["broke"] == 1 and not summary["green"]
        assert "broke: kept-b" in said(summary)

    def test_a_suite_that_could_not_run_is_said_and_never_green(self, root):
        tests, command = root
        (tests / "yantra" / "broken").write_text("")
        summary = night(tests, command=command)
        assert summary["errors"] == 1 and not summary["green"]
        assert "the model is not answering" in said(summary)

    def test_a_gone_package_is_said(self, root, tmp_path):
        tests, command = root
        about_file = tests / "people" / "naina" / "minder" / "agent.json"
        about_file.write_text(json.dumps({"agent": "minder", "package": str(tmp_path / "gone"),
                                          "person": "naina"}))
        row = next(r for r in night(tests, command=command)["suites"] if r["person"])
        assert "package is gone" in row["error"]

    def test_nothing_kept_is_not_green(self, tmp_path):
        summary = night(tmp_path / "empty")
        assert summary["green"] is False
        assert said(summary) == "No conversation is kept as a test yet."

    def test_tests_runs_them_from_the_command_line(self, root, monkeypatch, capsys):
        from yantra.cli import main as cli
        tests, command = root
        monkeypatch.setenv("YANTRA_TESTS", str(tests))
        monkeypatch.setattr("yantra.kept_tests._yantra", lambda: command)
        assert cli.main(["--tests", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["passed"] == 4

    def test_suite_goes_with_kept(self, capsys):
        from yantra.cli.main import main
        assert main(["--suite", "x"]) == 2
        assert "--eval --kept" in capsys.readouterr().err


class TestChoosingTheTurn:
    def test_an_earlier_turn_is_kept_with_only_what_was_known_then(self):
        payload = _payload()
        payload["history"] += [
            {"role": "user", "content": [{"kind": "text", "text": "and in June?"}]},
            {"role": "assistant", "content": [
                {"kind": "tool_call", "id": "c2", "name": "search_mail",
                 "arguments": {"q": "june refund"}}]},
            {"role": "user", "content": [
                {"kind": "tool_result", "tool_call_id": "c2", "content": "none"}]},
            {"role": "assistant", "content": [{"kind": "text", "text": "None in June."}]}]
        rec = from_payload(payload, turn=2)
        assert rec.question == "what did Amazon refund me?"
        assert rec.answer == "Amazon refunded $42.10 on May 20."
        assert [c.content for c in rec.calls] == ["Refund of $42.10 issued May 20"]
        assert from_payload(payload).question == "and in June?"
        assert from_payload(payload, turn=1).answer == "hi!"

    def test_a_turn_that_is_not_there_is_said(self):
        with pytest.raises(ConfigError, match="has 2 turns, not 5"):
            from_payload(_payload(), turn=5)


class TestTakingOneBack:
    def test_the_list_says_how_each_did_last_time(self, tmp_path):
        rec = from_payload(_payload(), conversation="abc12345")
        keep(rec, tmp_path, words=["May 20"])
        keep(rec, tmp_path)
        runs = tmp_path / "runs"
        runs.mkdir()
        (runs / "20261023T030000.json").write_text(json.dumps({"cases": [
            {"id": "kept-abc12345", "passed": False, "failures": ["judge: FAIL x"]}]}))
        first, second = kept_in(tmp_path)
        assert (first.passed, first.why, first.words) == (False, ["judge: FAIL x"], ["May 20"])
        assert second.passed is None and first.kept_at

    def test_forget_takes_out_one_block_and_its_recording_and_nothing_else(self, tmp_path):
        rec = from_payload(_payload(), conversation="abc12345")
        keep(rec, tmp_path)
        keep(rec, tmp_path)
        cases = tmp_path / "cases.toml"
        cases.write_text(cases.read_text() + "# a note I wrote by hand\n")
        assert forget(tmp_path, "kept-abc12345")
        assert [c.id for c in load_cases(tmp_path)] == ["kept-abc12345-2"]
        assert "# a note I wrote by hand" in cases.read_text()
        assert cases.read_text().startswith("# Conversations you kept as tests.")
        assert not (tmp_path / "replay" / "kept-abc12345.json").exists()
        assert (tmp_path / "replay" / "kept-abc12345-2.json").exists()
        assert forget(tmp_path, "nope") is False

    def test_the_last_one_taken_leaves_no_suite(self, tmp_path):
        keep(from_payload(_payload(), conversation="abc"), tmp_path)
        assert forget(tmp_path, "kept-abc")
        assert not (tmp_path / "cases.toml").exists()

    def test_terminal_lists_and_deletes(self, tmp_path, monkeypatch):
        from test_history import repl_with

        monkeypatch.setenv("YANTRA_TESTS", str(tmp_path))
        keep(from_payload(_payload(), conversation="abc"), tmp_path / "yantra",
             words=["May 20"])
        repl, out = repl_with(None, ["/tests", "/tests delete 1", "/tests", "/quit"], [])
        repl.run()
        text = out.getvalue()
        assert "1. what did Amazon refund me? -- not run yet (must say May 20)" in text
        assert "taken back: kept-abc" in text
        assert "no conversation is kept as a test of this agent yet" in text

    def test_chats_test_n_turn_k(self, tmp_path, monkeypatch):
        from test_history import repl_with

        from yantra.history import ConversationLog
        monkeypatch.setenv("YANTRA_TESTS", str(tmp_path / "tests"))
        log = ConversationLog(tmp_path / "h.sqlite")
        log.keep("x1", _payload(), road="terminal")
        repl, out = repl_with(log, ["/chats", "/chats test 1 turn 1 hi", "/quit"], [])
        repl.run()
        [case] = load_cases(tmp_path / "tests" / "yantra")
        assert case.user_message == "hello" and case.answer_contains == ["hi"]

    def test_turn_goes_with_keep_test(self, capsys):
        from yantra.cli.main import main
        assert main(["--turn", "2"]) == 2
        assert "--keep-test" in capsys.readouterr().err


class TestThePagesTests:
    def test_the_page_lists_keeps_a_turn_and_deletes(self, tmp_path, monkeypatch):
        pytest.importorskip("fastapi")
        from test_history import web

        from yantra.history import ConversationLog
        monkeypatch.setenv("YANTRA_TESTS", str(tmp_path / "tests"))
        log = ConversationLog(tmp_path / "h.sqlite")
        log.keep("old", _payload(), road="terminal")
        _, _, client = web(log, [])
        assert client.post("/api/chats/old/test", json={"turn": 1}).json()["question"] == "hello"
        assert client.post("/api/chats/old/test", json={"turn": "x"}).status_code == 400
        [suite] = client.get("/api/tests").json()["suites"]
        assert suite["suite"] == "yantra" and suite["mine"]
        assert [t["question"] for t in suite["tests"]] == ["hello"]
        assert client.delete("/api/tests", params={"suite": "../x", "case": "kept-old"}
                             ).status_code == 404
        assert client.delete("/api/tests", params={"suite": "yantra", "case": "kept-old"}
                             ).status_code == 200
        assert client.get("/api/tests").json()["suites"] == []
