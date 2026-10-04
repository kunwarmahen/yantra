"""A turn compaction cut in two is still read as one (skills/learn.py).

On a small context window (qwen3.8:latest reports 8192) compaction can
run inside a turn. Read afterwards, the turn started at the summary: a
five-call solve read as three, with the summary as its task, so the
long turns most worth learning from were never looked at.

Designed against:

* **The summary taken for the person's question.** It is a user message
  with text; it is never a prompt.
* **The first half lost.** The turn so far is carried from just before
  compaction and joined to what came after, by call id.
* **A carried turn outliving its turn.** Once the person types again,
  the carried one is done.
"""

from __future__ import annotations

from types import SimpleNamespace

from yantra.context import SUMMARY_MARK, compact_history, is_summary
from yantra.skills.learn import carry_turn, current_turn, read_turn
from yantra.types import Message, TextBlock, ToolCall, ToolResult


def ask(text):
    return Message("user", [TextBlock(text)])


def call(i):
    return [Message("assistant", [ToolCall(f"c{i}", "bash", {"command": f"step {i}"})]),
            Message("user", [ToolResult(f"c{i}", f"output {i} " + "x" * 400)])]


def summary():
    return Message("user", [TextBlock(SUMMARY_MARK + "they asked to convert a CSV")])


def agent(history):
    return SimpleNamespace(history=history)


def steps(turn):
    return [s.call_id for s in turn.steps]


QUESTION = "convert people.csv to json, step by step"


class TestOneTurnInTwoHalves:
    def test_a_question_folded_into_the_summary(self):
        older = [ask("an older question"), Message("assistant", [TextBlock("done")])]
        a = agent([*older, ask(QUESTION), *call(1), *call(2), *call(3)])
        carry_turn(a)
        # compaction: the question and the first two calls become a summary
        a.history[:] = [older[0], summary(), *a.history[-2:]]
        a.history += [*call(4), *call(5), Message("assistant", [TextBlock("all done")])]
        # history alone pins the second half on the OLDER question
        assert read_turn(a.history).task == "an older question"
        turn = current_turn(a)
        assert turn.task == QUESTION
        assert steps(turn) == ["c1", "c2", "c3", "c4", "c5"]
        assert turn.answer == "all done"

    def test_a_first_turn_whose_question_stays(self):
        a = agent([ask(QUESTION), *call(1), *call(2), *call(3)])
        carry_turn(a)
        a.history[:] = [a.history[0], summary(), *a.history[-2:]]
        a.history += [*call(4), Message("assistant", [TextBlock("all done")])]
        assert steps(current_turn(a)) == ["c1", "c2", "c3", "c4"]

    def test_twice_in_one_turn(self):
        older = [ask("an older question"), Message("assistant", [TextBlock("done")])]
        a = agent([*older, ask(QUESTION), *call(1), *call(2)])
        carry_turn(a)
        a.history[:] = [older[0], summary(), *a.history[-2:]]
        a.history += [*call(3), *call(4)]
        carry_turn(a)
        a.history[:] = [older[0], summary(), *a.history[-2:]]
        a.history += [*call(5), Message("assistant", [TextBlock("all done")])]
        turn = current_turn(a)
        assert turn.task == QUESTION and steps(turn) == ["c1", "c2", "c3", "c4", "c5"]

    def test_the_next_turn_is_its_own(self):
        older = [ask("an older question"), Message("assistant", [TextBlock("done")])]
        a = agent([*older, ask(QUESTION), *call(1), *call(2)])
        carry_turn(a)
        a.history[:] = [older[0], summary(), *a.history[-2:]]
        a.history += [Message("assistant", [TextBlock("all done")]),
                      ask("thanks, now count them"), *call(9)]
        turn = current_turn(a)
        assert turn.task == "thanks, now count them" and steps(turn) == ["c9"]


class TestWithRealCompaction:
    def test_compact_history_on_a_small_window(self):
        older = [ask("an older question"), Message("assistant", [TextBlock("done")])]
        a = agent([*older, ask(QUESTION)])
        for i in range(1, 8):
            a.history += call(i)
        carry_turn(a)
        a.history[:], stats = compact_history(
            a.history, summarize=lambda text: "the CSV work so far",
            context_window=600, max_tokens=100)
        assert stats["summarized"] and any(is_summary(m) for m in a.history)
        assert not any(m.text() == QUESTION for m in a.history)   # folded away
        a.history += [*call(8), Message("assistant", [TextBlock("all done")])]
        turn = current_turn(a)
        assert turn.task == QUESTION
        assert steps(turn) == [f"c{i}" for i in range(1, 9)]
        # the carried steps keep their full results, not the masked stubs
        assert turn.steps[0].result.startswith("output 1")
