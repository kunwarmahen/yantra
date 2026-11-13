"""Every turn's model use, in one file the whole house writes.

Each program in the house uses a model: Yantra's page, the terminal, each
person's turns through Dvara, Samay's runs, Prahari's questions and the
pictures it looks at, Vidhi's steps. Each kept its own count, or none:
Dvara's runs know their cost, the page shows a session's, and a watch's
yes/no question vanished without a trace. "What did the house use today,
and on what?" had no answer anywhere, and a floor or an alert could only
ever meter one program at a time.

ONE LINE PER TURN, WHEREVER IT RAN. Every program runs its model through
Yantra, so the agent writes one JSON line as each turn ends, to
``$XDG_STATE_HOME/yantra/usage.jsonl`` (``YANTRA_USAGE_FILE`` elsewhere,
``YANTRA_USAGE=off`` for none). Sarathi's containers share that folder, so
one file holds the whole house. Each line is under the size an append
writes whole, so programs writing at the same moment never mix their
lines.

WHO ASKED, NOT WHAT WAS SAID. A line keeps:

* ``by``, the program (``YANTRA_USAGE_BY``: dvara, samay, prahari, vidhi;
  the terminal and the page say yantra);
* ``who``, a label the host gives (a person's id, a schedule's), never a
  message;
* the provider, the model that answered, and whether it's local
  (``ollama``) or billed;
* the tokens, the dollars (None when the model has no list price, never a
  guessed 0), the seconds, the iterations, how the turn ended, and a
  standby's switch.

Nothing anyone typed or read is in it. It's a meter, and a meter's file is
one an owner may hand to anybody.

A CEILING FOR THE WHOLE HOUSE, READ FROM THE SAME FILE.
``YANTRA_HOUSE_DAY_USD`` (Sarathi's ``[usage] day_usd``) is what the house's
billed models may come to in a day, every program together. Each program
already writes its turns here, so the total is this file's lines since
local midnight, read backwards from the end and stopped at yesterday: no
second meter to disagree with this one. The standby layer
(providers/standby.py) asks ``day_spent`` before each billed call, so once
the day's dollars are gone a billed model isn't asked, a local one standing
by answers instead, and with none the turn ends saying why. It's a stop, not
a cap, like a turn's own budget (budget.py): the call that crosses the line
is the last one, and the day can end a few cents over.

A LINE THAT CAN'T BE WRITTEN COSTS THE TURN NOTHING. A full disk, or a
folder that isn't there, is skipped silently: an answer is never lost
because its receipt was.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

FORMAT = "yantra.usage.v1"
#: The house's billed dollars a day, every program together (day_spent).
DAY_USD = "YANTRA_HOUSE_DAY_USD"
#: Programs that bill nothing (a local model).
LOCAL = frozenset({"ollama", "local"})


def path() -> Path | None:
    if os.environ.get("YANTRA_USAGE", "").strip().lower() in ("off", "0", "no"):
        return None
    named = os.environ.get("YANTRA_USAGE_FILE", "").strip()
    if named:
        return Path(named).expanduser()
    state = os.environ.get("XDG_STATE_HOME", "").strip() or str(Path.home() / ".local/state")
    return Path(state) / "yantra" / "usage.jsonl"


def line(agent: Any, event: Any, seconds: float) -> dict[str, Any]:
    """The turn that just ended, as one record."""
    from yantra.pricing import cost_of, price_for

    usage = getattr(event, "usage", None)
    response = getattr(event, "response", None)
    model = (getattr(response, "model", None) or str(getattr(agent, "model", "") or ""))
    provider = str(getattr(getattr(agent, "provider", None), "name", "") or "")
    switched = list(getattr(event, "switched", ()) or ())
    if switched:
        # a standby answered: the backend that did, so local is the truth
        # about it ("standby" when a host's provider can't say)
        provider = str(getattr(getattr(agent, "provider", None), "answered", "")
                       or "standby")
    price = price_for(model) if model else None
    usd = round(cost_of(usage, price), 6) if usage is not None and price is not None else None
    return {
        "format": FORMAT,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "by": os.environ.get("YANTRA_USAGE_BY", "").strip() or "yantra",
        # the host's label, else who a program said asked (Samay's, Prahari's
        # YANTRA_ASKED_BY: "Samay: Morning brief (bfaea182) · mahen")
        "who": str(getattr(agent, "usage_who", "") or os.environ.get("YANTRA_USAGE_WHO", "")
                   or os.environ.get("YANTRA_ASKED_BY", "")).strip()[:80],
        "provider": provider,
        "model": model,
        "local": provider in LOCAL,
        "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
        "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
        "cache_read_tokens": getattr(usage, "cache_read_tokens", 0) if usage else 0,
        "usd": usd,
        "seconds": round(seconds, 2),
        "iterations": getattr(event, "iterations", 0),
        "reason": getattr(event, "reason", ""),
        "switched": switched[:2],
    }


def record(agent: Any, event: Any, started: float | None) -> None:
    """Append the turn's line; never raises."""
    where = path()
    if where is None:
        return
    try:
        seconds = time.monotonic() - started if started else 0.0
        data = json.dumps(line(agent, event, seconds), separators=(",", ":")) + "\n"
        where.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(where, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 -- a receipt never costs an answer
        return


def read(where: Path | None = None, since: float | None = None) -> list[dict]:
    """Every line kept (newer than ``since``, epoch seconds), skipping any
    that can't be read."""
    from datetime import datetime

    where = where or path()
    out: list[dict] = []
    if where is None:
        return out
    try:
        handle = where.open(encoding="utf-8")
    except OSError:
        return out
    with handle:
        for raw in handle:
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("format") != FORMAT:
                continue
            if since is not None:
                try:
                    if datetime.strptime(row["at"], "%Y-%m-%dT%H:%M:%S%z").timestamp() < since:
                        continue
                except (KeyError, ValueError):
                    continue
            out.append(row)
    return out


def day_ceiling() -> float | None:
    """``YANTRA_HOUSE_DAY_USD`` as dollars; None when unset, 0 or unreadable
    (a ceiling nobody could read stops nothing, and says nothing either)."""
    raw = os.environ.get(DAY_USD, "").strip()
    try:
        value = float(raw) if raw else 0.0
    except ValueError:
        return None
    return value if value > 0 else None


def _midnight(now: float) -> float:
    day = time.localtime(now)
    return time.mktime((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0, 0, 0, -1))


def spent_today(where: Path | None = None, now: float | None = None) -> float:
    """The billed dollars in today's lines (since local midnight). The file
    is read from its end and the reading stops at the first line from
    before midnight, so a year of lines costs what today's do."""
    from datetime import datetime

    where = where or path()
    if where is None:
        return 0.0
    since = _midnight(now if now is not None else time.time())
    total, tail = 0.0, b""
    try:
        with where.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            at = handle.tell()
            while at > 0:
                step = min(65536, at)
                at -= step
                handle.seek(at)
                block = handle.read(step) + tail
                rows = block.split(b"\n")
                # the first piece may be half a line: kept for the next block
                tail, rows = (rows[0], rows[1:]) if at > 0 else (b"", rows)
                for raw in reversed(rows):
                    try:
                        row = json.loads(raw)
                        stamp = datetime.strptime(row["at"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
                    except (ValueError, KeyError, TypeError):
                        continue
                    if stamp < since:
                        return round(total, 6)
                    if row.get("format") == FORMAT and row.get("usd") is not None:
                        total += float(row["usd"])
    except OSError:
        return 0.0
    return round(total, 6)


def day_spent(provider_name: str) -> str | None:
    """Why a billed model isn't asked: the house's day is spent. None for a
    local model, with no ceiling, or with money still left."""
    ceiling = day_ceiling()
    if ceiling is None or provider_name in LOCAL:
        return None
    spent = spent_today()
    if spent < ceiling:
        return None
    return f"the house's ${ceiling:.2f} for today is spent (${spent:.2f} so far)"
