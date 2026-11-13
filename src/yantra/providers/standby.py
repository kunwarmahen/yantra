"""A model standing by: when yours doesn't answer, another one does -- and you hear which.

``fallback.py`` has had the mechanism since notes/21: try providers left to
right, commit to the first that opens. Nothing could switch it on, and it
had two gaps that kept it a library piece. It sends the SAME model name to
every backend, so falling from Ollama's ``qwen3.8`` to a cloud provider
asks the cloud for a model it has never heard of. And it fails over
silently, so a person whose words just went to a different company's
servers was never told.

THE STANDBY IS THE OWNER'S, AND IT IS NAMED, NEVER GUESSED.
``YANTRA_FALLBACK`` (or ``--fallback``) lists models to try when the
primary doesn't answer, in order, each as ``PROVIDER:MODEL``:

    YANTRA_FALLBACK="anthropic:claude-sonnet-5-5"
    YANTRA_FALLBACK="ollama:gemma4:12b, anthropic:claude-haiku-5-5"

The model is everything after the first colon (``gemma4:12b`` keeps its
own). Each provider's settings come from the environment as they would
for the primary (``ANTHROPIC_API_KEY``, ``OLLAMA_BASE_URL``); ``@ADDRESS``
after the model points that one elsewhere, which is how a second computer
running Ollama stands by for this one:

    YANTRA_FALLBACK="ollama:qwen3.8:latest@http://other-box:11434/v1"

A standby whose key isn't set is left out with a line saying so; it never
stops the start.

AN AGENT MAY SAY WAIT. ``[model] when_down = "wait"`` in a package keeps
that agent on its own model whatever the house has standing by. An agent
that reads your mail has good reason never to send it anywhere else, and
for it "the model is down, ask again later" is the right answer. The
default, ``"switch"``, uses the standby when there is one.

YOU HEAR WHICH. Every switch is written down as it happens ("qwen3.8:latest
(ollama) didn't answer -- connection refused -- so gemma4:12b (ollama)
answered"), and the agent puts the turn's switches on its ``TurnEnd``
(``switched``), so each host can say it under the answer.

A BILLED MODEL ISN'T ASKED ONCE THE HOUSE'S DAY IS SPENT. With
``YANTRA_HOUSE_DAY_USD`` set (usage_log.py), each billed model is checked
before it is asked, and when the house's dollars for today are gone it is
passed over the way a down one is: a local model standing by answers, and
the switch says so ("claude-sonnet-5-5 (anthropic) wasn't asked: the house's
$2.00 for today is spent ($2.03 so far), so gemma4:12b (ollama) answered").
With nothing local standing by, the turn ends with that sentence, never a
bill nobody agreed to. An agent that says ``wait`` is held to the ceiling too:
it keeps its own model, and the ceiling still applies to it.

THE SAME COMMIT RULE AS FALLBACK. A switch happens only before the first
event of a stream, so nothing is ever said twice. The taxonomy of what is
worth switching for is fallback.py's: a backend that is unreachable,
throttled, refusing the key, or answering 5xx. A request-shaped failure
(context overflow, an ordinary 400) is the same everywhere and is not
retried elsewhere.

A COMPOSITE THAT STILL LOOKS LIKE ITS PRIMARY. ``name``, ``settings`` and
every other attribute are the primary's, because session files store a
provider by name, and the picture checks ask the provider in use what it
can see. The standby changes which backend answered a request, and
nothing else about the agent.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

from yantra import usage_log
from yantra.errors import ProviderError
from yantra.providers.fallback import _can_fail_over

ENV = "YANTRA_FALLBACK"
MODES = ("switch", "wait")


class DaySpent(ProviderError):
    """A billed model passed over: the house's dollars for today are gone."""


def parse(raw: str | None) -> list[tuple[str, str, str]]:
    """``"anthropic:claude-sonnet-5-5, ollama:gemma4:12b@http://x:11434/v1"``
    as ``[(provider, model, address), ...]`` (address "" for the
    environment's); ValueError in words for a bad entry."""
    out: list[tuple[str, str, str]] = []
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        provider, _, rest = entry.partition(":")
        model, at, address = rest.partition("@")
        if not provider.strip() or not model.strip() or (
                at and not address.strip().startswith(("http://", "https://"))):
            raise ValueError(
                f"{ENV} entry {entry!r} is PROVIDER:MODEL, or PROVIDER:MODEL@ADDRESS, "
                f"like anthropic:claude-sonnet-5-5 or "
                f"ollama:gemma4:12b@http://other-box:11434/v1")
        out.append((provider.strip(), model.strip(), address.strip()))
    return out


def standbys(raw: str | None = None, *, say=None) -> list[tuple[Any, str]]:
    """The standby models, built: ``[(provider, model), ...]``. One that
    can't be built (no key) is left out, and ``say`` is told why."""
    from yantra.config import load_settings
    from yantra.errors import ConfigError
    from yantra.providers import get_provider

    from dataclasses import replace

    built: list[tuple[Any, str]] = []
    for name, model, address in parse(os.environ.get(ENV) if raw is None else raw):
        try:
            settings = load_settings(name)
            if address:
                settings = replace(settings, base_url=address)
            built.append((get_provider(name, settings), model))
        except (ConfigError, KeyError, ValueError) as exc:
            if say is not None:
                say(f"standby {name}:{model} left out: {exc}")
    return built


def wrap(primary: Any, model: str, standing_by: list[tuple[Any, str]],
         when_down: str | None = None) -> Any:
    """The primary, or the primary with models standing by. ``wait`` (an
    agent's own ``when_down``) and an empty list give the primary back --
    unless the house has a day's ceiling, which holds a billed primary to
    it with nothing standing by."""
    if isinstance(primary, Standby):
        return primary
    if (when_down or "switch") == "wait":
        standing_by = []
    if not standing_by and usage_log.day_ceiling() is None:
        return primary
    return Standby(primary, model, standing_by)


def label(provider: Any, model: str) -> str:
    """"qwen3.8:latest (ollama)", or with its address when it isn't the
    usual one's: "gemma4:12b (ollama at http://other-box:11434/v1)"."""
    name = getattr(provider, "name", "?")
    address = getattr(getattr(provider, "settings", None), "base_url", "")
    usual = os.environ.get(f"{str(name).upper()}_BASE_URL", "")
    where = f" at {address}" if address and usual and address.rstrip("/") != usual.rstrip("/") \
        else ""
    return f"{model} ({name}{where})"


def _why(exc: Exception) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    if "Connection refused" in text or "ConnectError" in type(exc).__name__:
        return "nothing is listening there"
    if "timed out" in text.lower() or "Timeout" in type(exc).__name__:
        return "it took too long to answer"
    return text[:160]


class Standby:
    """The primary, with models to try when it doesn't open."""

    def __init__(self, primary: Any, model: str, standing_by: list[tuple[Any, str]]) -> None:
        self.primary = primary
        self.primary_model = model
        self.standing_by = list(standing_by)
        #: What happened since the last ``take_switches``, in words.
        self.switches: list[str] = []
        #: The provider that answered last (its name: "ollama"), so the
        #: usage file says local or billed of the one that really did.
        self.answered: str = str(getattr(primary, "name", "") or "")

    # Everything not about choosing a backend is the primary's: its name
    # round-trips through a session file, its settings answer "can it see?".
    def __getattr__(self, attr: str) -> Any:
        return getattr(self.primary, attr)

    def take_switches(self) -> tuple[str, ...]:
        said, self.switches = tuple(self.switches), []
        return said

    def _order(self, model: str) -> list[tuple[Any, str]]:
        return [(self.primary, model), *self.standing_by]

    @staticmethod
    def _held(provider: Any, model: str) -> DaySpent | None:
        """The house's day spent, for a billed model (usage_log.day_spent)."""
        why = usage_log.day_spent(str(getattr(provider, "name", "") or ""))
        return DaySpent(why) if why else None

    def _note(self, failed: list[tuple[Any, str, Exception]], provider: Any,
              model: str) -> None:
        self.answered = str(getattr(provider, "name", "") or "")
        if not failed:
            return
        down = "; ".join(f"{label(p, m)} wasn't asked: {e}" if isinstance(e, DaySpent)
                         else f"{label(p, m)} didn't answer ({_why(e)})"
                         for p, m, e in failed)
        self.switches.append(f"{down}, so {label(provider, model)} answered")

    def _all_failed(self, failed: list[tuple[Any, str, Exception]]) -> ProviderError:
        spent = next((e for _, _, e in failed if isinstance(e, DaySpent)), None)
        if spent is not None and all(isinstance(e, DaySpent) for _, _, e in failed):
            self.switches.append(f"no model was asked ({spent})")
            return DaySpent(str(spent))
        down = "; ".join(f"{label(p, m)}: {_why(e)}" for p, m, e in failed)
        error = ProviderError(f"no model answered: {down}",
                              status=getattr(failed[-1][2], "status", None))
        self.switches.append(f"no model answered ({down})")
        return error

    def stream(self, *, messages, system, tools, model, max_tokens=16384,
               temperature=None) -> Iterator:
        failed: list[tuple[Any, str, Exception]] = []
        for provider, slug in self._order(model):
            held = self._held(provider, slug)
            if held is not None:
                failed.append((provider, slug, held))
                continue
            try:
                gen = provider.stream(messages=messages, system=system, tools=tools,
                                      model=slug, max_tokens=max_tokens,
                                      temperature=temperature)
                first = next(gen)                  # the commit point
            except StopIteration:
                return
            except Exception as exc:
                if not _can_fail_over(exc):
                    raise
                failed.append((provider, slug, exc))
                continue
            self._note(failed, provider, slug)
            yield first
            yield from gen
            return
        raise self._all_failed(failed) from failed[-1][2]

    async def astream(self, *, messages, system, tools, model, max_tokens=16384,
                      temperature=None) -> AsyncIterator:
        failed: list[tuple[Any, str, Exception]] = []
        for provider, slug in self._order(model):
            held = self._held(provider, slug)
            if held is not None:
                failed.append((provider, slug, held))
                continue
            try:
                agen = provider.astream(messages=messages, system=system, tools=tools,
                                        model=slug, max_tokens=max_tokens,
                                        temperature=temperature)
                first = await agen.__anext__()     # the commit point
            except StopAsyncIteration:
                return
            except Exception as exc:
                if not _can_fail_over(exc):
                    raise
                failed.append((provider, slug, exc))
                continue
            self._note(failed, provider, slug)
            yield first
            async for event in agen:
                yield event
            return
        raise self._all_failed(failed) from failed[-1][2]

    def complete(self, *, messages, system, tools, model, max_tokens=16384,
                 temperature=None):
        failed: list[tuple[Any, str, Exception]] = []
        for provider, slug in self._order(model):
            held = self._held(provider, slug)
            if held is not None:
                failed.append((provider, slug, held))
                continue
            try:
                answer = provider.complete(messages=messages, system=system, tools=tools,
                                           model=slug, max_tokens=max_tokens,
                                           temperature=temperature)
            except Exception as exc:
                if not _can_fail_over(exc):
                    raise
                failed.append((provider, slug, exc))
                continue
            self._note(failed, provider, slug)
            return answer
        raise self._all_failed(failed) from failed[-1][2]

    async def acomplete(self, **kwargs):
        failed: list[tuple[Any, str, Exception]] = []
        model = kwargs.pop("model")
        for provider, slug in self._order(model):
            held = self._held(provider, slug)
            if held is not None:
                failed.append((provider, slug, held))
                continue
            try:
                answer = await provider.acomplete(model=slug, **kwargs)
            except Exception as exc:
                if not _can_fail_over(exc):
                    raise
                failed.append((provider, slug, exc))
                continue
            self._note(failed, provider, slug)
            return answer
        raise self._all_failed(failed) from failed[-1][2]

    def close(self) -> None:
        for provider in [self.primary, *(p for p, _ in self.standing_by)]:
            provider.close()

    async def aclose(self) -> None:
        for provider in [self.primary, *(p for p, _ in self.standing_by)]:
            await provider.aclose()


def down_words(exc: BaseException) -> str | None:
    """A failed turn's error, in words for the person, when it was the
    MODEL that didn't answer; None for any other failure."""
    import httpx

    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, DaySpent):
            return (f"Nothing was done: {seen}, and no local model stands by. "
                    f"Billed models answer again after midnight.")
        if isinstance(seen, ProviderError) and str(seen).startswith("no model answered"):
            return (f"The model didn't answer, so nothing was done. "
                    f"{str(seen)[len('no model answered: '):]}. Ask again in a little while.")
        if isinstance(seen, httpx.TransportError):
            return (f"The model didn't answer ({_why(seen)}), so nothing was done. "
                    f"Ask again in a little while.")
        seen = seen.__cause__ or seen.__context__
    return None
