"""A turn written down, and the one question that kept it unwritten.

The bias here is privacy, and it is not a side concern: a trajectory
holds whatever the AGENT READ, which a verdict never does. A store that
kept everything by default would quietly accumulate other people's data
in a file somebody later pastes into a ticket. So the tests below assert
what a default recording does NOT contain -- tool arguments, tool
results, the model's answer -- and that every line says which level
wrote it, so a reader can tell without reading the contents whether a
file is safe to hand over.

Two more failures designed against:

* **Losing the turn worth keeping.** The turn somebody wants a case for
  is usually the one that crashed or was interrupted, so the recorder
  writes from a finally and the tests kill the stream to prove it.
* **A truncated last line costing you the file.** A killed process
  leaves half a line behind; reading must skip it and count it, never
  raise.
"""

from __future__ import annotations

import pytest

from yantra.errors import ConfigError
from yantra.agent import ToolExecuted, TurnEnd
from yantra.evals import case_from_trajectory
from yantra.trace import (
    FULL,
    SHAPE,
    TrajectoryLog,
    watch,
)
from yantra.types import (Message, ModelResponse, TextBlock, ToolCall,
                          ToolResult, Usage)


def tool_executed(name="read_file", is_error=False, refusal=None,
                  args=None, content="the file said things"):
    return ToolExecuted(
        call=ToolCall("c1", name, args or {"path": "secrets.txt"}),
        result=ToolResult("c1", content, is_error=is_error),
        refusal=refusal)


def turn_end(reason="end_turn", text="here is the answer", tokens=(100, 20)):
    response = ModelResponse(
        message=Message("assistant", [TextBlock(text)]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=tokens[0], output_tokens=tokens[1]),
        model="qwen3.8:27b")
    return TurnEnd(response=response, reason=reason, iterations=2)


def record(*events, detail=SHAPE, task="do the thing"):
    kept = []
    seen = list(watch(task, iter(events), kept.append, provider="ollama",
                      model="qwen3.8:27b", detail=detail))
    return kept[0], seen


class TestShapeIsTheDefault:
    def test_the_task_and_the_tool_names_are_kept(self):
        trace, _ = record(tool_executed(), turn_end())
        assert trace.task == "do the thing"
        assert trace.tools_used == ["read_file"]

    def test_what_the_agent_read_is_not(self):
        """The whole privacy argument in one assertion."""
        trace, _ = record(tool_executed(), turn_end())
        step = trace.steps[0]
        assert step.arguments is None
        assert step.result is None
        assert trace.answer is None

    def test_the_counts_are_kept(self):
        trace, _ = record(tool_executed(), turn_end())
        assert trace.tokens == 120
        assert trace.iterations == 2
        assert trace.seconds >= 0

    def test_a_refusal_code_is_kept_because_it_is_a_token(self):
        """"Everything was refused" is exactly the sort of failure
        somebody wants to turn into a case."""
        trace, _ = record(tool_executed(is_error=True, refusal="user"),
                          turn_end())
        assert trace.steps[0].refusal == "user"
        assert trace.steps[0].ok is False

    def test_duplicate_tool_calls_are_kept_in_order(self):
        """"outline, outline, read_file" is a different trajectory from
        "outline, read_file"."""
        trace, _ = record(tool_executed("outline"), tool_executed("outline"),
                          tool_executed("read_file"), turn_end())
        assert trace.tools_used == ["outline", "outline", "read_file"]


class TestFullIsOptIn:
    def test_it_keeps_arguments_results_and_the_answer(self):
        trace, _ = record(tool_executed(), turn_end(), detail=FULL)
        assert trace.steps[0].arguments == {"path": "secrets.txt"}
        assert trace.steps[0].result == "the file said things"
        assert trace.answer == "here is the answer"

    def test_a_huge_result_is_clipped_rather_than_stored_whole(self):
        """Evidence, not an archive: a 200KB file read makes the store
        useless for the thing it exists for."""
        trace, _ = record(tool_executed(content="x" * 50_000), turn_end(),
                          detail=FULL)
        assert len(trace.steps[0].result) < 3000
        assert "more chars" in trace.steps[0].result

    def test_every_line_says_which_level_wrote_it(self, tmp_path):
        """So a reader can tell whether a file is safe to hand over
        without reading the contents."""
        log = TrajectoryLog(tmp_path / "t.jsonl", detail=FULL)
        trace, _ = record(tool_executed(), turn_end(), detail=FULL)
        log.record(trace)
        assert log.read()[0].detail == FULL

    def test_an_unknown_level_is_refused_by_name(self, tmp_path):
        with pytest.raises(ConfigError, match="trace detail must be one of"):
            TrajectoryLog(tmp_path / "t.jsonl", detail="everything")


class TestTheTee:
    def test_every_event_still_reaches_the_caller(self):
        """A recorder that swallowed the stream would make recording and
        rendering mutually exclusive."""
        events = [tool_executed(), turn_end()]
        _, seen = record(*events)
        assert seen == events

    def test_a_crashed_turn_is_still_recorded(self):
        """The turn somebody most wants a case for is the one that fell
        over."""
        kept = []

        def exploding():
            yield tool_executed()
            raise RuntimeError("provider went away")

        with pytest.raises(RuntimeError):
            list(watch("do it", exploding(), kept.append))
        assert kept[0].outcome == "crashed"
        assert kept[0].tools_used == ["read_file"]

    def test_an_abandoned_turn_is_still_recorded(self):
        """Ctrl-C at the terminal closes the generator; the turn up to
        that point is exactly what somebody wants to look at."""
        kept = []
        stream = watch("do it", iter([tool_executed(), turn_end()]),
                       kept.append)
        next(stream)              # pull one event, then walk away
        stream.close()
        assert len(kept) == 1
        assert kept[0].tools_used == ["read_file"]

    def test_a_turn_that_ended_badly_records_the_reason(self):
        trace, _ = record(turn_end(reason="max_iterations"))
        assert trace.outcome == "max_iterations"
        assert trace.failed is True

    def test_a_clean_turn_with_a_failed_tool_counts_as_failed(self):
        """The cheap filter for finding candidates -- a person still says
        which ones were failures."""
        trace, _ = record(tool_executed(is_error=True), turn_end())
        assert trace.outcome == "end_turn" and trace.failed is True

    def test_a_clean_turn_is_not_failed(self):
        trace, _ = record(tool_executed(), turn_end())
        assert trace.failed is False


class TestTheFile:
    def _log(self, tmp_path):
        return TrajectoryLog(tmp_path / "deep" / "turns.jsonl")

    def test_a_turn_round_trips(self, tmp_path):
        log = self._log(tmp_path)
        trace, _ = record(tool_executed(), turn_end())
        trace_id = log.record(trace)
        back = log.read()[0]
        assert back.id == trace_id
        assert back.task == trace.task
        assert back.tools_used == ["read_file"]
        assert back.tokens == 120

    def test_turns_append_rather_than_replace(self, tmp_path):
        log = self._log(tmp_path)
        for _ in range(3):
            log.record(record(tool_executed(), turn_end())[0])
        assert len(log.read()) == 3

    def test_a_truncated_last_line_does_not_cost_the_file(self, tmp_path):
        """What a killed process leaves behind."""
        log = self._log(tmp_path)
        for _ in range(2):
            log.record(record(tool_executed(), turn_end())[0])
        with log.path.open("a", encoding="utf-8") as handle:
            handle.write('{"format": "yantra.trace.v1", "id": "half')
        assert len(log.read()) == 2
        assert log.unreadable == 1

    def test_a_line_from_a_format_this_build_cannot_read_is_skipped(self,
                                                                    tmp_path):
        log = self._log(tmp_path)
        log.record(record(tool_executed(), turn_end())[0])
        with log.path.open("a", encoding="utf-8") as handle:
            handle.write('{"format": "yantra.trace.v99", "id": "later"}\n')
        assert len(log.read()) == 1 and log.unreadable == 1

    def test_a_missing_file_says_how_to_make_one(self, tmp_path):
        with pytest.raises(ConfigError, match="--trace"):
            TrajectoryLog(tmp_path / "never.jsonl").read()

    def test_a_turn_is_found_by_id_prefix(self, tmp_path):
        """Nobody types thirty-two characters off a terminal."""
        log = self._log(tmp_path)
        trace_id = log.record(record(tool_executed(), turn_end())[0])
        assert log.get(trace_id[:8]).id == trace_id

    def test_an_ambiguous_prefix_is_an_error_not_a_guess(self, tmp_path):
        log = self._log(tmp_path)
        first, _ = record(tool_executed(), turn_end())
        second, _ = record(tool_executed(), turn_end())
        first.id = "aaaa1111" + first.id[8:]
        second.id = "aaaa2222" + second.id[8:]
        log.record(first)
        log.record(second)
        with pytest.raises(ConfigError, match="matches 2 turns"):
            log.get("aaaa")

    def test_an_unknown_id_names_the_file(self, tmp_path):
        log = self._log(tmp_path)
        log.record(record(tool_executed(), turn_end())[0])
        with pytest.raises(ConfigError, match="no turn in"):
            log.get("ffffffff")


class TestTheFossil:
    def test_a_recorded_turn_becomes_a_case(self):
        trace, _ = record(tool_executed("outline"), tool_executed("read_file"),
                          turn_end())
        case = case_from_trajectory(trace, "answered without citing")
        assert case.user_message == "do the thing"
        assert case.required_tools == ["outline", "read_file"]
        assert case.max_tokens == 180            # 120 x 1.5
        assert case.description == "answered without citing"

    def test_a_repeated_tool_is_asserted_once(self):
        """required_tools is a set of claims, not a sequence."""
        trace, _ = record(tool_executed("outline"), tool_executed("outline"),
                          turn_end())
        assert case_from_trajectory(trace, "why").required_tools == ["outline"]

    def test_the_case_asserts_nothing_the_agent_read(self, tmp_path):
        """A case built on the contents of a file goes red when somebody
        edits that file, which is the opposite of a regression test."""
        trace, _ = record(tool_executed(), turn_end(), detail=FULL)
        case = case_from_trajectory(trace, "why")
        rendered = repr(case)
        assert "secrets.txt" not in rendered
        assert "the file said things" not in rendered

    def test_it_round_trips_through_the_suite_format(self, tmp_path):
        from yantra.eval_suite import load_cases, render_case

        trace, _ = record(tool_executed(), turn_end())
        case = case_from_trajectory(trace, "the failure")
        path = tmp_path / "cases.toml"
        path.write_text(render_case(case))
        (loaded,) = load_cases(path)
        assert loaded.user_message == case.user_message
        assert loaded.required_tools == case.required_tools
        assert loaded.max_tokens == case.max_tokens


class TestThroughTheCli:
    def _run(self, argv, monkeypatch):
        import yantra.cli.main as cli_main
        return cli_main.main(argv)

    def test_fossil_needs_the_file_that_recorded_the_turn(self, monkeypatch,
                                                          capsys):
        assert self._run(["--fossil", "abc123"], monkeypatch) == 2
        assert "--trace FILE" in capsys.readouterr().err

    def test_trace_full_without_trace_says_so(self, monkeypatch, capsys):
        assert self._run(["--trace-full", "--prompt", "hi"], monkeypatch) == 2
        assert "--trace FILE" in capsys.readouterr().err

    def test_fossil_prints_a_case_block_to_stdout(self, tmp_path, monkeypatch,
                                                  capsys):
        """Printed, not appended: a suite is the author's file."""
        log = TrajectoryLog(tmp_path / "t.jsonl")
        trace, _ = record(tool_executed(), turn_end())
        log.record(trace)
        rc = self._run(["--fossil", trace.id[:8], "--trace",
                        str(log.path)], monkeypatch)
        out, err = capsys.readouterr()
        assert rc == 0
        assert out.startswith("[[case]]")
        assert "do the thing" in out
        assert "SHAPE of that turn" in err        # advice never in stdout

    def test_an_unknown_id_is_an_error(self, tmp_path, monkeypatch, capsys):
        log = TrajectoryLog(tmp_path / "t.jsonl")
        log.record(record(tool_executed(), turn_end())[0])
        rc = self._run(["--fossil", "nope", "--trace", str(log.path)],
                       monkeypatch)
        assert rc == 2
        assert "no turn in" in capsys.readouterr().err
