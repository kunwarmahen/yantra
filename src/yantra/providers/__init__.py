"""Provider registry -- the only module the CLI/config code imports directly."""

from __future__ import annotations

import httpx

from yantra.config import canonical_provider

from yantra.providers.anthropic import AnthropicProvider
from yantra.providers.base import Provider, ProviderSettings
from yantra.providers.ollama import OllamaProvider
from yantra.providers.openai import OpenAIProvider
from yantra.providers.responses import ResponsesProvider

_REGISTRY: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    # OpenAI's Responses API -- chat-completions' successor wire format.
    "responses": ResponsesProvider,
    # local models: OpenAI dialect against localhost (no key needed)
    "ollama": OllamaProvider,
}


def get_provider(
    name: str,
    settings: ProviderSettings,
    transport: httpx.BaseTransport | None = None,
    **options,
) -> Provider:
    """Build a provider by name ("anthropic" | "openai" | "responses" | "ollama").

    Extra keyword options (retry=..., cache_control=...) forward to the
    provider constructor; unsupported options fail loudly at construction.
    """
    try:
        # Canonical first: "local" is a word for the keyless road, and a
        # host that passes it through should not have to know that the
        # thing at the other end is called Ollama (config.py).
        cls = _REGISTRY[canonical_provider(name)]
    except KeyError:
        raise KeyError(
            f"unknown provider {name!r}; known: {sorted(_REGISTRY)}"
        ) from None
    return cls(settings, transport=transport, **options)
