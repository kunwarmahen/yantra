"""What a turn cost is every call it made, not the last one.

The bias is UNDERCOUNTING, and it hides well. A turn that uses tools is
several model calls, each re-sending the whole conversation, so the last
call's usage is one slice of the bill -- and it is the slice that sits
on ``TurnEnd.response``, where anything reading the stream looks first.
Three readers did exactly that: the recorder wrote it as the turn's
``tokens``, the terminal footer printed it beside "8 iteration(s)", and
the page printed it under the turn. Measured on ``qwen3.8:latest``, an
eight-call turn recorded 4,569 tokens and billed 27,385.

The cost is not only a wrong number on screen. A case made from a
recording takes its token ceiling from that number (``case_from_trace``,
x1.5), while the grader counts the whole turn (``total_usage``) -- so a
fossil of a multi-call turn could fail on the very budget it came from.
The last test here is that argument, run.

Designed against, besides the headline:

* **A turn that ends with no response** -- held, capped, out of
  iterations -- still made calls. It must not record zero.
* **The second turn carrying the first one's bill.** The total is per
  turn, not per session; ``total_usage`` is the session's.
"""

from __future__ import annotations

import asyncio
import io

from rich.console import Console

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent, TurnEnd
from yantra.async_agent import AsyncAgent
from yantra.cli.render import Renderer
from yantra.evals import case_from_trajectory
from yantra.permissions import yolo
from yantra.tools.base import Tool, ToolRegistry
from yantra.trace import watch
from yantra.types import Usage


class Echo(Tool):
    name = "echo"
    description = "echo the text back"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    read_only = True

    def summary(self, args, ctx):
        return f"echo({args.get('text')!r})"

    def run(self, args, ctx):
        return f"echo:{args.get('text', '')}"


def three_call_script():
    """Two tool rounds and an answer: 100+200+300 in, 10+20+30 out."""
    return [
        assistant_tool_call("c1", "echo", {"text": "a"},
                            usage=Usage(input_tokens=100, output_tokens=10)),
        assistant_tool_call("c2", "echo", {"text": "b"},
                            usage=Usage(input_tokens=200, output_tokens=20)),
        assistant_text("done", usage=Usage(input_tokens=300, output_tokens=30)),
    ]


def make_agent(script, cls=Agent, **kwargs):
    registry = ToolRegistry()
    registry.register(Echo())
    return cls(ScriptedProvider(script), model="scripted-model",
               tools=registry, permissions=yolo, **kwargs)


def turn_end(events) -> TurnEnd:
    end = events[-1]
    assert isinstance(end, TurnEnd)
    return end


class TestTheAgentSumsTheTurn:
    def test_turn_end_carries_every_call_the_turn_made(self):
        end = turn_end(list(make_agent(three_call_script()).run_streaming("go")))
        assert (end.usage.input_tokens, end.usage.output_tokens) == (600, 60)
        # the last call alone, which is what readers used to take
        assert end.response.usage.input_tokens == 300

    def test_the_async_agent_sums_the_same_way(self):
        agent = make_agent(three_call_script(), cls=AsyncAgent)

        async def drain():
            return [event async for event in agent.run_streaming("go")]

        end = turn_end(asyncio.run(drain()))
        assert (end.usage.input_tokens, end.usage.output_tokens) == (600, 60)

    def test_a_turn_with_no_response_still_says_what_it_spent(self):
        """Out of iterations: response is None, but two calls were billed."""
        agent = make_agent(three_call_script()[:2], max_iterations=2)
        end = turn_end(list(agent.run_streaming("go")))
        assert end.reason == "max_iterations" and end.response is None
        assert (end.usage.input_tokens, end.usage.output_tokens) == (300, 30)

    def test_the_second_turn_counts_only_its_own_calls(self):
        script = three_call_script() + [
            assistant_text("again", usage=Usage(input_tokens=50,
                                                output_tokens=5))]
        agent = make_agent(script)
        list(agent.run_streaming("go"))
        end = turn_end(list(agent.run_streaming("and again")))
        assert (end.usage.input_tokens, end.usage.output_tokens) == (50, 5)
        assert agent.total_usage.input_tokens == 650   # the session's


class TestTheReadersUseIt:
    def record(self, script):
        agent = make_agent(script)
        kept = []
        list(watch("go", agent.run_streaming("go"), kept.append,
                   provider="scripted", model="scripted-model"))
        return kept[0], agent

    def test_a_recorded_turn_counts_every_call(self):
        trajectory, _ = self.record(three_call_script())
        assert trajectory.tokens == 660

    def test_a_turn_that_ran_out_of_iterations_is_not_recorded_as_free(self):
        agent = make_agent(three_call_script()[:2], max_iterations=2)
        kept = []
        list(watch("go", agent.run_streaming("go"), kept.append))
        assert kept[0].outcome == "max_iterations"
        assert kept[0].tokens == 330

    def test_the_footer_prints_the_turns_tokens(self):
        end = turn_end(list(make_agent(three_call_script()).run_streaming("go")))
        console = Console(file=io.StringIO(), width=200)
        Renderer(console)(end)
        footer = console.file.getvalue()
        assert "600 in / 60 out" in footer
        assert "3 iteration(s)" in footer

    def test_a_case_fossilized_from_a_recording_fits_its_own_replay(self):
        """The ceiling a recording hands a case must cover what the grader
        will count for the same run. With the last call's figure it was
        330 x 1.5 = 495 against a replay of 660."""
        trajectory, agent = self.record(three_call_script())
        case = case_from_trajectory(trajectory, "echo twice then answer")
        graded = agent.total_usage.input_tokens + agent.total_usage.output_tokens
        assert case.max_tokens >= graded
