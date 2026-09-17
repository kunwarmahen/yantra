"""A deadline on a permission gate, and a word for why a call was refused.

Run (works against a local Ollama model -- no key, no spend):

    uv run python examples/gate_deadline_demo.py
    uv run python examples/gate_deadline_demo.py --provider anthropic
    uv run python examples/gate_deadline_demo.py --model gemma4:12b

The same question is asked twice, of the same absent owner, who answers
neither time. The only difference between the two runs is one word:

    with_deadline(owner.gate, seconds=4, on_timeout="deny")
    with_deadline(owner.gate, seconds=4, on_timeout="allow")

``on_timeout`` has no default, and that is the whole feature. A deadline
is two things welded together in most harnesses -- a stopwatch, which is
mechanism, and a verdict on silence, which is policy. The first belongs
to the library; the second belongs to whoever owns the conversation. A
deploy wants silence to mean no. An overnight batch was given a deadline
precisely so it would go ahead. Nothing here is entitled to choose
between them, so the wrapper cannot be constructed without being told.

The second thing on show is the CODE. Every refusal now carries a short
machine token beside the sentence -- ``timeout`` here, ``unattended``
from the default gate, ``user`` from the y/n prompt. The sentence is
written for the model and may be reworded any day; the token is for
whoever wired the gates up, and the host below reads it off the event
stream to print its own line. Watch the model in run one: it is told
nobody refused, only that nobody answered, and it goes looking for a
read-only route instead of arguing with a person who was never there.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from yantra import AsyncAgent, default_registry, get_provider
from yantra.agent import ToolExecuted
from yantra.config import load_settings
from yantra.permissions import PermissionRequest, with_deadline

START = time.monotonic()


def log(who: str, what: str) -> None:
    print(f"[{time.monotonic() - START:5.1f}s] {who:9s} {what}")


class AbsentOwner:
    """A person who is not at their desk, and does not become one.

    Stands in for whatever a real host does here -- a chat message, a
    push notification, a web socket. The only thing that matters to the
    harness is that the answer never comes, and that waiting for it
    SUSPENDS: a deadline only binds a gate that yields, because a gate
    blocking on input() has already answered by the time anyone could
    have timed it.
    """

    def __init__(self) -> None:
        self.withdrawn: list[str] = []

    async def gate(self, request: PermissionRequest) -> bool:
        log("owner", f"a question about {request.tool_name}() goes out ...")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # The deadline cancels the pending question, so a chat window
            # can take the buttons away rather than offering to approve a
            # call that can no longer happen.
            self.withdrawn.append(request.tool_name)
            log("owner", "the question expired and was withdrawn")
            raise
        return True


async def one_run(provider, model: str, on_timeout: str,
                  seconds: float) -> None:
    owner = AbsentOwner()
    agent = AsyncAgent(
        provider, model=model, tools=default_registry(),
        permissions=with_deadline(owner.gate, seconds, on_timeout=on_timeout),
        system="You are a shell assistant. Answer briefly.")

    print(f"\n--- on_timeout={on_timeout!r}, deadline {seconds:g}s "
          f"----------------------------------")
    async for event in agent.run_streaming(
            "Run the shell command: echo hello. "
            "If you cannot, say exactly what you were told."):
        if isinstance(event, ToolExecuted):
            # The host reads the TOKEN. It never has to parse the
            # sentence, which was written for the model.
            if event.refusal is not None:
                log("host", f"{event.call.name}() refused, code="
                            f"{event.refusal!r}")
            else:
                log("host", f"{event.call.name}() ran: "
                            f"{event.result.content.strip()[:40]!r}")
    reply = agent.history[-1].text().strip()
    log("model", f"final answer: {reply[:160]!r}")


async def main(args: argparse.Namespace) -> None:
    provider = get_provider(args.provider, load_settings(args.provider))
    model = args.model or ("qwen3.8:latest" if args.provider == "ollama"
                           else "claude-sonnet-4-5")
    print(f"· {args.provider}/{model} -- one absent owner, two policies")

    await one_run(provider, model, "deny", args.seconds)
    await one_run(provider, model, "allow", args.seconds)

    print("\nSame silence, opposite outcomes, and the library chose neither.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default="ollama",
                        help="ollama (default, local and free) / anthropic / openai")
    parser.add_argument("--model", default=None)
    parser.add_argument("--seconds", type=float, default=4.0,
                        metavar="SECONDS",
                        help="how long the question stays open (default 4)")
    asyncio.run(main(parser.parse_args()))
