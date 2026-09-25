"""A local model as a second reader of what the recorder is about to write.

``--trace-redact-words`` (notes/86) scrubs the names an operator LISTS.
A customer nobody listed still reaches the file. Note 86 designed the
next step and refused to build it until it was measured: a model asked
for the names in each recorded turn, whose answer is scrubbed too. The
measurement is ``examples/name_recall_trial.py``, and notes/89 has the
numbers. This module is what it measured.

Four rules, each from note 86 and each enforced here rather than
promised:

**LOCAL ONLY.** The text being scrubbed is the text that must not leave
the machine, so it is never sent to a cloud model to find out what is
in it. The reader is an Ollama model, and there is no flag for any other.

**IT ADDS TO THE LIST; IT NEVER REMOVES FROM IT.** The operator's
patterns and list run first, exactly as without a reader; the model is
shown what they left and whatever it returns is scrubbed after. A model
that misses a name the list has costs nothing; a model that finds one
the list lacks is the point. (After, not before: a name found inside
an email address would otherwise break the address up so the ``email``
pattern no longer saw one.)

**EACH WORD OF A NAME IS SCRUBBED TOO.** The one pattern the measurement
found: in a long thread the model lists "Dmitri Nkosi" and not the bare
"Dmitri" three lines later. So every word of a multi-word name is added
alongside it. The price is a few ordinary words hidden with a name that
is also a word ("will" beside "Will Turner"), which costs a word of
evidence where a miss costs somebody's privacy.

**A READER THAT FAILS WRITES NOTHING, NEVER EVERYTHING.** An error, an
answer cut off by the token limit, or an answer that is not a list of
names means this turn's contents are withheld from the file. The line is
still written, with the shape of the turn and a note saying why. The
only silent failure left is the one no check can see -- a name the model
read past -- and that is the number the measurement exists to report.
"""

from __future__ import annotations

from collections.abc import Iterable

from yantra.types import Message, TextBlock

#: What is asked. Written for recall: a name the model hesitates over
#: should be listed, because an extra scrub costs a word of evidence and a
#: missed one costs somebody's privacy.
SYSTEM = (
    "You find the names of people in text, for a tool that hides them "
    "before the text is saved. List every person's name that appears, "
    "copied exactly as written, one per line. Include first names or "
    "surnames on their own, names in lower case, and names inside other "
    "text such as email addresses. Leave out companies, products, places "
    "and job titles. If you are unsure whether something is a person's "
    "name, list it. If there are none, write NONE. Write nothing else.")

#: The most text sent in one question. The measurement's longest text was
#: 6.6 KB (a 150-row export and 40-message threads); nothing past what was
#: measured is sent at once.
CHUNK_CHARS = 8000

#: Longer than any name, so a line this long is the model writing prose
#: instead of a list -- an answer that did not follow the question.
MAX_NAME_CHARS = 200


class ReaderFailed(Exception):
    """The model could not be relied on for this text; say why."""


def read_names(provider, model: str, text: str) -> list[str]:
    """The names one call finds in ``text``, as the model wrote them.

    Raises ``ReaderFailed`` rather than returning a short list: a list cut
    off part-way looks exactly like a list with fewer names in it.
    """
    try:
        reply = provider.complete(
            messages=[Message("user", [TextBlock(text)])], system=SYSTEM,
            tools=[], model=model, max_tokens=16384, temperature=0)
    except Exception as exc:  # noqa: BLE001 -- any failure fails closed
        raise ReaderFailed(f"{type(exc).__name__}: {exc}") from None
    if reply.stop_reason == "max_tokens":
        raise ReaderFailed("the answer was cut off by the token limit")
    lines = [line.strip().strip("-*•").strip()
             for line in reply.message.text().splitlines()]
    names = [line for line in lines if line and line.upper() != "NONE"]
    if any(len(name) > MAX_NAME_CHARS for name in names):
        raise ReaderFailed("the answer was prose, not a list of names")
    return names


def with_each_word(names: Iterable[str]) -> list[str]:
    """Every name, and every word of a name that has more than one."""
    out: list[str] = []
    for name in names:
        out.append(name)
        words = name.split()
        if len(words) > 1:
            out.extend(words)
    return [name for name in out if len(name) >= 2]


def chunks(texts: Iterable[str], limit: int = CHUNK_CHARS) -> list[str]:
    """The texts, packed into pieces no longer than ``limit``.

    Whole texts where they fit; one longer than the limit is cut at line
    breaks, then hard at the limit, so a name is only ever split by a
    text that has no line break in eight thousand characters.
    """
    pieces: list[str] = []
    current = ""
    for text in texts:
        for part in _split(text, limit):
            if current and len(current) + 2 + len(part) > limit:
                pieces.append(current)
                current = ""
            current = f"{current}\n\n{part}" if current else part
    if current:
        pieces.append(current)
    return pieces


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text] if text.strip() else []
    parts, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            parts.append(current + line[:limit - len(current)])
            line, current = line[limit - len(current):], ""
        if len(current) + len(line) > limit:
            parts.append(current)
            current = ""
        current += line
    if current.strip():
        parts.append(current)
    return parts


class NameReader:
    """One local model, asked for the names in each recorded turn."""

    provider_name = "ollama"

    def __init__(self, model: str, *, provider=None) -> None:
        if not model or not model.strip():
            raise ValueError("a name reader needs an Ollama model tag, "
                             "such as qwen3.8:latest")
        self.model = model.strip()
        #: Injected by tests. Otherwise a provider is opened per turn and
        #: closed after it: a recorder has no shutdown to hang a close
        #: on, and a connection pool is cheap beside a model call.
        self._provider = provider

    def names(self, texts: Iterable[str]) -> list[str]:
        """Every name in ``texts``, each word included. Raises
        ``ReaderFailed`` if any piece could not be read."""
        pieces = chunks(texts)
        if not pieces:
            return []
        if self._provider is not None:
            return self._read(self._provider, pieces)
        from yantra.config import load_settings
        from yantra.providers import get_provider
        with get_provider(self.provider_name,
                          load_settings(self.provider_name)) as provider:
            return self._read(provider, pieces)

    def _read(self, provider, pieces: list[str]) -> list[str]:
        found: list[str] = []
        for piece in pieces:
            found.extend(read_names(provider, self.model, piece))
        return with_each_word(found)
