"""Giving the connection pools back.

A provider opens TWO httpx pools in its constructor -- one sync, one
async -- and for a long time gave neither back. A CLI that exits does not
care; a process that does not exit does, and the only thing it could do
was reach into ``.client`` and ``.aclient``, which was nobody's promise.

The bias in these tests: a leak is invisible by construction, so nothing
here asserts on a log line or a return value. They assert that the pool
object reports itself CLOSED, that a closed provider refuses to send,
that closing twice is not an error (shutdown paths run twice more often
than anyone plans for), and that a composite reaches every provider it
holds rather than only the one that happened to answer.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from yantra.errors import ProviderError
from yantra.providers.anthropic import AnthropicProvider
from yantra.providers.base import ProviderSettings
from yantra.providers.fallback import FallbackProvider
from yantra.types import Message, TextBlock


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "msg_1", "type": "message", "role": "assistant",
        "model": "m", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })


def provider(**kw) -> AnthropicProvider:
    return AnthropicProvider(
        ProviderSettings(api_key="k", base_url="https://mock.local"),
        transport=httpx.MockTransport(ok), **kw)


def a_turn(p) -> None:
    p.complete(messages=[Message("user", [TextBlock("hi")])],
               system=None, tools=[], model="m")


# ---- the sync pool ----------------------------------------------------------


def test_close_gives_back_the_sync_pool():
    p = provider()
    a_turn(p)                       # a pool that has actually been used
    assert p.client.is_closed is False
    p.close()
    assert p.client.is_closed is True


def test_closing_twice_is_not_an_error():
    """Shutdown paths run twice more often than anyone plans for."""
    p = provider()
    p.close()
    p.close()


def test_a_closed_provider_refuses_to_send():
    """Better than a pool that quietly reopens: using a provider after
    shutdown is a bug in the host, and it should say so at the call."""
    p = provider()
    p.close()
    with pytest.raises((RuntimeError, ProviderError)):
        a_turn(p)


def test_the_context_manager_closes_on_the_way_out():
    with provider() as p:
        a_turn(p)
    assert p.client.is_closed is True


def test_it_closes_even_when_the_body_raises():
    p = provider()
    with pytest.raises(ZeroDivisionError):
        with p:
            raise ZeroDivisionError("the body blew up")
    assert p.client.is_closed is True


# ---- both pools -------------------------------------------------------------


def test_aclose_gives_back_both_pools():
    """The one an async host wants: one line at shutdown, not two."""
    p = provider()

    async def _scenario():
        await p.acomplete(messages=[Message("user", [TextBlock("hi")])],
                          system=None, tools=[], model="m")
        await p.aclose()

    asyncio.run(_scenario())
    assert p.aclient.is_closed is True
    assert p.client.is_closed is True


def test_the_async_context_manager_closes_both():
    p = provider()

    async def _scenario():
        async with p:
            pass

    asyncio.run(_scenario())
    assert p.aclient.is_closed is True
    assert p.client.is_closed is True


def test_close_cannot_touch_the_async_pool_and_says_so_by_leaving_it():
    """Documented limitation, pinned so it cannot change by accident: an
    AsyncClient can only be closed from inside an event loop, so the
    synchronous close() closes what it can and no more."""
    p = provider()
    p.close()
    assert p.client.is_closed is True
    assert p.aclient.is_closed is False


# ---- a composite owns everything it holds -----------------------------------


def test_the_fallback_closes_every_provider_it_holds():
    """Including the one it never fell back to -- which is the pool a
    hand-written shutdown forgets."""
    primary, secondary = provider(), provider()
    fallback = FallbackProvider(primary, secondary)
    fallback.close()
    assert primary.client.is_closed is True
    assert secondary.client.is_closed is True


def test_the_fallback_acloses_every_provider_it_holds():
    primary, secondary = provider(), provider()
    fallback = FallbackProvider(primary, secondary)
    asyncio.run(fallback.aclose())
    assert primary.aclient.is_closed is True
    assert secondary.aclient.is_closed is True
    assert primary.client.is_closed is True


def test_the_fallback_context_manager_closes_its_children():
    primary, secondary = provider(), provider()
    with FallbackProvider(primary, secondary):
        pass
    assert primary.client.is_closed is True
    assert secondary.client.is_closed is True
