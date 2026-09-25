"""Raw token-by-token streaming, no REPL machinery.

Run:
    uv run python examples/stream_demo.py "Tell me a haiku"          # local Ollama
    uv run python examples/stream_demo.py --provider anthropic "Tell me a haiku"

Watch tokens arrive as the provider sends them -- that's all "streaming"
is: the HTTP body arrives in chunks, and we print each text fragment the
moment the SSE parser extracts it.
"""

from __future__ import annotations

import argparse

from yantra.config import default_model, load_settings
from yantra.providers import get_provider
from yantra.types import EndEvent, Message, StartEvent, TextBlock, TextDelta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default="ollama",
                        choices=["anthropic", "openai", "ollama"])
    parser.add_argument("prompt", nargs="*",
                        default=["Write a haiku about rivers."])
    args = parser.parse_args()
    prompt = " ".join(args.prompt)
    model = default_model(args.provider)
    provider = get_provider(args.provider, load_settings(args.provider))
    messages = [Message("user", [TextBlock(prompt)])]

    print(f"[model: {model}]\n")
    for event in provider.stream(
        messages=messages, system=None, tools=[], model=model, max_tokens=1024
    ):
        match event:
            case TextDelta(text=fragment):
                print(fragment, end="", flush=True)
            case StartEvent():
                pass  # could render a header here
            case EndEvent(stop_reason=reason, usage=usage):
                print(
                    f"\n\n[done: {reason} | {usage.input_tokens} in / "
                    f"{usage.output_tokens} out]"
                )


if __name__ == "__main__":
    main()
