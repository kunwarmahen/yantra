"""A silent reply must say WHY it is silent.

The bias: "(no text in reply)" was written for a model that spent its turn
thinking. It said the same thing when the prompt had filled the local
server's window and left the reply twelve tokens -- the case where the
operator has something to FIX (a context window yantra believes is larger
than the server's, so compaction never fires). The tests below keep the
two apart.
"""

from __future__ import annotations

import io

from rich.console import Console

from yantra.agent import TurnEnd
from yantra.cli.render import Renderer, empty_reply_reason
from yantra.types import Message, ModelResponse, ThinkingBlock, Usage


def render_end(stop_reason) -> str:
    console = Console(file=io.StringIO(), width=300)
    response = ModelResponse(
        message=Message("assistant", [ThinkingBlock(thinking="Found the")]),
        stop_reason=stop_reason, usage=Usage(input_tokens=32756,
                                             output_tokens=12))
    Renderer(console)(TurnEnd(response=response, reason="end_turn",
                              iterations=4))
    return console.file.getvalue()


def test_silence_after_max_tokens_points_at_the_context_window():
    out = render_end("max_tokens")
    assert "ran out of room" in out
    assert "CONTEXT_WINDOW" in out


def test_silence_after_a_normal_stop_stays_the_plain_note():
    out = render_end("end_turn")
    assert "no text in reply" in out
    assert "ran out of room" not in out


def test_the_reason_names_the_setting_to_check():
    assert "<PROVIDER>_CONTEXT_WINDOW" in empty_reply_reason("max_tokens")
    assert empty_reply_reason(None) == "no text in reply"
