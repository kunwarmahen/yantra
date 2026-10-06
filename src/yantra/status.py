"""What Yantra can tell a program that starts it, without running a turn.

Sarathi lists the projects it puts together, and every other one of them
answers ``status --json`` in its own words: Setu its accounts, Samay its
clock, Dvara its door. Yantra could only be "found, here", which leaves
the two questions a person setting it up actually has: *which release is
this?* and *would it reach a model if I asked it something now?*

THE SHAPE IS THE FAMILY'S. A ``format`` field names the version
(``yantra.status.v1``) and a reader refuses one it does not know rather
than guess. The rest is what Yantra would do with no flags, in this
folder, with this environment: the provider ``guess_provider`` settles
on and why, the model it would ask for, which optional parts are
installed, which tool packs it could name, and whether a session would
find Setu and Samay -- asked the way a session asks them (``YANTRA_SETU``,
``YANTRA_SAMAY``, then what is installed), which only reads: each is
asked its own ``status --json``, and neither is started.

NO TURN, NO KEY. Nothing here builds an Agent or opens a provider. The
one thing it asks over the network is a LOCAL server's list of models,
because "qwen3.8:latest is not pulled" is the failure that half the
people running this hit first, and it is invisible until a turn fails
on it. A cloud provider is never asked anything: whether a key is good
is the provider's to say, and costs a call to find out.

NO SECRET IS IN IT. Which rung of the ladder chose the provider (a key
is set, ``YANTRA_PROVIDER`` says so), never the key itself.

A PROBLEM IS A LINE, NOT AN EXIT. "No provider found" is what a person
asking ``status`` most needs to read, so it is reported in ``problems``
and the command still exits 0 with a whole answer.
"""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec

import httpx

from yantra import samay_link, setu_link
from yantra.config import default_model, guess_provider, load_settings
from yantra.errors import ConfigError
from yantra.tools.discover import entry_point_packs

FORMAT = "yantra.status.v1"

#: How long a local server gets to list its models.
LOCAL_TIMEOUT = 2.0


def _version() -> str:
    try:
        return version("yantra")
    except PackageNotFoundError:
        return "unknown"


def _why(provider: str) -> str:
    """Which rung of guess_provider's ladder answered -- words, no values."""
    if os.environ.get("YANTRA_PROVIDER"):
        return "YANTRA_PROVIDER"
    if provider == "anthropic":
        return ("ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY")
                else "ANTHROPIC_AUTH_TOKEN")
    if provider == "ollama":
        return "an OLLAMA_* setting"
    return f"{provider.upper()}_API_KEY"


def _local(base_url: str, model: str) -> dict:
    """Is the local server answering, and has it pulled the model?"""
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        response = httpx.get(f"{root}/api/tags", timeout=LOCAL_TIMEOUT)
        listed = response.json().get("models", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return {"answering": False, "pulled": None}
    names = {m.get("name") for m in listed if isinstance(m, dict)} \
        if isinstance(listed, list) else set()
    return {"answering": True,
            "pulled": model in names or f"{model}:latest" in names}


def _setu(problems: list[str]) -> dict:
    """Would a session find Setu, and how many accounts would it bring?
    A count, never the accounts: those are Setu's to list."""
    try:
        link = setu_link.load(*setu_link.resolve_mode(None))
    except setu_link.SetuLinkError as exc:
        problems.append(f"setu: {exc}")
        return {"found": False, "road": None, "connections": 0}
    if link is None:
        return {"found": False, "road": None, "connections": 0}
    return {"found": True, "road": link.road, "connections": len(link.connections)}


def _samay(problems: list[str]) -> dict:
    """Would a session find Samay, and is its clock running?"""
    try:
        found = samay_link.load(*samay_link.resolve_mode(None))
    except samay_link.SamayLinkError as exc:
        problems.append(f"samay: {exc}")
        return {"found": False, "program": None, "serving": False}
    if found is None:
        return {"found": False, "program": None, "serving": False}
    data, program = found
    return {"found": True, "program": program, "serving": bool(data.get("serving"))}


def report() -> dict:
    problems: list[str] = []
    provider = model = base_url = why = None
    local = None
    try:
        provider = guess_provider()
    except ConfigError as exc:
        problems.append(str(exc))
    if provider is not None:
        why = _why(provider)
        model = default_model(provider)
        if provider == "ollama":
            base_url = load_settings(provider).base_url
            local = _local(base_url, model)
            if not local["answering"]:
                problems.append(f"the local server is not answering at "
                                f"{base_url} (start it: ollama serve)")
            elif not local["pulled"]:
                problems.append(f"{model} is not pulled "
                                f"(ollama pull {model})")
    return {
        "format": FORMAT,
        "version": _version(),
        "python": sys.executable,
        "provider": provider,
        "chosen_by": why,
        "model": model,
        "base_url": base_url,
        "local": local,
        "extras": {"web": find_spec("fastapi") is not None,
                   "browse": find_spec("playwright") is not None},
        "packs": sorted(entry_point_packs()),
        "setu": _setu(problems),
        "samay": _samay(problems),
        "problems": problems,
    }


def lines(data: dict) -> list[str]:
    """``yantra status`` without ``--json``: the same answer, for a person."""
    out = [f"yantra {data['version']} · {data['python']}"]
    if data["provider"]:
        out.append(f"would ask {data['provider']} for {data['model']} "
                   f"(chosen by {data['chosen_by']})")
    local = data["local"]
    if local and local["answering"] and local["pulled"]:
        out.append(f"local server answering at {data['base_url']}; "
                   f"{data['model']} is pulled")
    extras = [name for name, there in data["extras"].items() if there]
    missing = [name for name, there in data["extras"].items() if not there]
    out.append("extras: " + (", ".join(extras) or "none")
               + (f" (not installed: {', '.join(missing)})" if missing else ""))
    out.append("tool packs: " + (", ".join(data["packs"]) or "none"))
    setu, samay = data["setu"], data["samay"]
    out.append(f"setu: {setu['connections']} account(s), found by {setu['road']}"
               if setu["found"] else "setu: not found")
    out.append(("samay: " + ("clock running" if samay["serving"] else "clock not running")
                + f" ({samay['program']})") if samay["found"] else "samay: not found")
    out += [f"problem: {p}" for p in data["problems"]]
    return out
