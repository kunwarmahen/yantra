"""A permission gate that waits for a person, without stopping the world.

Run (works against a local Ollama model -- no key, no spend):

    uv run python examples/async_gate_demo.py
    uv run python examples/async_gate_demo.py --provider anthropic
    uv run python examples/async_gate_demo.py --model gemma4:12b --approve

Two conversations share one process and one event loop.

  * AMARA asks for a shell command. Her gate does not decide anything
    itself -- it hands the question to "an owner" who is somewhere else
    entirely, and waits for an answer that takes several seconds.
  * BHASKAR asks an unrelated question WHILE that gate is waiting, and
    his whole turn finishes inside the wait.

The timestamps are the receipt. A gate is a plain callable, so the old
contract called it inline; an ``async def`` gate returned a coroutine
object, which is truthy, which meant a gate that intended to say NO was
read as a yes. Now the async loop awaits it (``permissions.adecide``) and
the synchronous loop REFUSES it rather than guessing
(``permissions.decide``).

The owner also writes ``request.reason`` before refusing. That sentence
is what the model reads, in place of the old hardcoded "Permission denied
by user." -- which, in a program with no user attached, was a guess about
somebody who was not there. Watch Amara's final answer quote it back.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from yantra import AsyncAgent, ToolRegistry, default_registry, get_provider
from yantra.config import load_settings
from yantra.permissions import PermissionRequest

START = time.monotonic()


def log(who: str, what: str) -> None:
    print(f"[{time.monotonic() - START:5.1f}s] {who:9s} {what}")


class Owner:
    """A person who is not at this keyboard.

    Stands in for whatever a real host does here -- a chat message, a
    push notification, a web socket. The only thing that matters to the
    harness is that answering takes TIME and happens somewhere else.
    """

    def __init__(self, *, thinks_for: float, approves: bool) -> None:
        self.thinks_for = thinks_for
        self.approves = approves
        self.asked = asyncio.Event()

    async def gate(self, request: PermissionRequest) -> bool:
        log("amara", f"gate: asking the owner about {request.tool_name}() ...")
        self.asked.set()
        await asyncio.sleep(self.thinks_for)   # a person, deciding
        if not self.approves:
            # The gate says why. The model reads THIS, not a sentence
            # about a user who was never consulted.
            request.reason = ("your owner refused this: bash is off for "
                              "chat sessions. Read the file instead if "
                              "you need what is in it.")
        log("amara", f"gate: the owner said {'yes' if self.approves else 'no'}")
        return self.approves


async def turn(name: str, agent: AsyncAgent, prompt: str,
               after: asyncio.Event | None = None) -> None:
    if after is not None:
        await after.wait()      # start this one INSIDE the other's wait
    log(name, "turn starts")
    reply = (await agent.run(prompt)).message.text().strip()
    log(name, f"turn ends: {reply[:100]!r}")


async def main(args: argparse.Namespace) -> None:
    provider = get_provider(args.provider, load_settings(args.provider))
    model = args.model or ("qwen3.8:latest" if args.provider == "ollama"
                           else "claude-sonnet-4-5")
    owner = Owner(thinks_for=args.thinks_for, approves=args.approve)

    amara = AsyncAgent(provider, model=model, tools=default_registry(),
                       permissions=owner.gate,
                       system="You are a shell assistant. Answer briefly.")
    # No tools at all: Bhaskar is here to prove the loop still turns.
    bhaskar = AsyncAgent(provider, model=model, tools=ToolRegistry(),
                         system="Answer in one short sentence.")

    print(f"· {args.provider}/{model} -- two conversations, one event loop\n")
    await asyncio.gather(
        turn("amara", amara,
             "Run the shell command: echo hello. "
             "If you are refused, say what you were told."),
        turn("bhaskar", bhaskar, "What is the capital of Senegal?",
             after=owner.asked),
    )
    print("\nBhaskar's whole turn happened inside Amara's wait. Before an\n"
          "awaitable gate, those seconds were a stopped event loop: every\n"
          "conversation in the process frozen behind one person deciding\n"
          "about one echo.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default="ollama",
                        help="ollama (default, local and free) / anthropic / openai")
    parser.add_argument("--model", default=None)
    parser.add_argument("--thinks-for", type=float, default=6.0,
                        metavar="SECONDS",
                        help="how long the owner takes to answer (default 6)")
    parser.add_argument("--approve", action="store_true",
                        help="the owner says yes, and the command runs")
    asyncio.run(main(parser.parse_args()))
