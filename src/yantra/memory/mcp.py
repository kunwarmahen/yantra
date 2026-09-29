"""A memory store reached over MCP: any server, four verbs, one map.

The built-in ``local`` store is sqlite and word overlap. A store with
embeddings, a graph, or a team behind it is somebody else's project, and
it plugs in from ITS side: it runs an MCP server, and ``[memory] via``
names that server. Yantra already speaks MCP (mcp.py), so there is no
second protocol, no Yantra-owned HTTP spec, and nothing of the store's
installed in Yantra's environment.

THE VERB MAP. The four verbs of ``MemoryStore`` are Yantra's words; the
server's tools are named whatever its author chose. ``verbs`` says which
tool is which, and a verb left out is taken to be a tool of the same name
(``list`` is ``list_memories``, since a tool called ``list`` says nothing).
A verb whose tool the server does not have is UNSUPPORTED, not an error:
a store without ``forget`` still remembers and recalls, and ``/memory``
says what it cannot do instead of failing the session.

WHAT IS SENT. Each verb calls its tool with fixed argument names, so a
server that wants to plug in knows what arrives:

=========  ===============================================
remember   ``statement``, ``user_id``, ``metadata`` (kind, package)
recall     ``query``, ``limit``, ``user_id``
forget     ``memory_id``, ``user_id``
list       ``limit``, ``user_id``
=========  ===============================================

``user_id`` is ALWAYS sent. Whose memories these are was settled when the
session started (memory/__init__.py), and a server that fell back on its
own default user would put every end user of a service into one person's
memory -- the exact leak "no identity, no memory" is there to prevent.

WHAT COMES BACK is read leniently, because it is the server's shape, not
ours: JSON, with an id under ``id`` / ``memory_id`` / ``event_id`` and the
text under ``statement`` / ``content`` / ``text`` / ``raw_text``; a list
of those at the top level or under ``memories`` / ``results`` / ``items``
/ ``events``. ``forget`` answers ``forgotten`` or ``deleted``.

BOUND LATE. Memory is attached while the agent is built; MCP servers
connect after, in the host. So the store starts unbound, and the host
calls ``bind`` once its servers are up (``bind_memory_server``). A call
before that, or after the server drops, is a ``MemoryStoreError`` -- which
the memory layer turns into a notice and carries on (fails open).

ONE ROAD TO MEMORY. The server's own tools would reach the model too, as
``mcp__<server>__remember`` and so on, beside Yantra's ``remember`` --
two ways to write the same thing, one of which skips the identity check.
Binding takes the mapped tools out of the model's roster; the server's
other tools stay.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from yantra.memory import MemoryItem, MemoryStoreError

VERBS = ("remember", "recall", "forget", "list")
#: A verb the map leaves out calls the tool of this name.
DEFAULT_TOOLS = {"remember": "remember", "recall": "recall",
                 "forget": "forget", "list": "list_memories"}
#: A server name, as ``--memory`` or ``[memory] via`` would give it.
SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

_ID_KEYS = ("id", "memory_id", "event_id")
_TEXT_KEYS = ("statement", "content", "text", "raw_text")
_LIST_KEYS = ("memories", "results", "items", "events")


def check_verbs(verbs: dict[str, str] | None) -> dict[str, str]:
    """The full map, defaults filled in. Raises ValueError on a bad one."""
    verbs = dict(verbs or {})
    unknown = sorted(set(verbs) - set(VERBS))
    if unknown:
        raise ValueError(f"unknown memory verb(s): {', '.join(unknown)} "
                         f"(known: {', '.join(VERBS)})")
    for verb, tool in verbs.items():
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError(f"memory verb {verb!r} must name a tool")
    return {**DEFAULT_TOOLS, **{v: t.strip() for v, t in verbs.items()}}


class McpStore:
    """``MemoryStore`` over one connected MCP server's tools."""

    def __init__(self, server: str, verbs: dict[str, str] | None = None, *,
                 timeout: float = 20.0) -> None:
        self.name = server
        self.server = server
        self.tools = check_verbs(verbs)
        # A slow store taxes one call, not the session: retrieval on local
        # hardware has been seen at 8s, so the ceiling sits above that.
        self.timeout = timeout
        self._session: Callable[[], Any] = lambda: None
        #: Verbs the connected server has no tool for. Known once bound.
        self.missing: frozenset[str] = frozenset()
        self.bound = False

    # ---- binding (the host, once its servers are up) -----------------------

    def bind(self, session: Callable[[], Any],
             offered: set[str] | None = None) -> None:
        """Point at a live session. ``session`` is asked on every call, so
        a reconnected server is picked up; ``offered`` is the server's tool
        names, when known, to tell which verbs it cannot do."""
        self._session = session
        self.bound = True
        if offered is not None:
            self.missing = frozenset(v for v, tool in self.tools.items()
                                     if tool not in offered)

    def supports(self, verb: str) -> bool:
        return verb not in self.missing

    # ---- the four verbs ------------------------------------------------------

    def remember(self, user: str, statement: str, meta: dict[str, Any]) -> str:
        metadata = {k: v for k, v in meta.items() if v is not None}
        data = self._call("remember", {"statement": statement,
                                       "user_id": user, "metadata": metadata})
        found = _first(data, _ID_KEYS) if isinstance(data, dict) else None
        if found is None and isinstance(data, (str, int)) and str(data).strip():
            found = data                        # a server that answers the id
        if found is None:
            raise MemoryStoreError(f"{self.server} stored it but gave no id "
                                   f"back ({_short(data)})")
        return str(found)

    def recall(self, user: str, query: str, limit: int) -> list[MemoryItem]:
        return _items(self._call("recall", {"query": query, "limit": limit,
                                            "user_id": user}))[:limit]

    def forget(self, user: str, memory_id: str) -> bool:
        data = self._call("forget", {"memory_id": str(memory_id).strip(),
                                     "user_id": user})
        if isinstance(data, dict):
            for key in ("forgotten", "deleted", "ok"):
                if key in data:
                    return bool(data[key])
        if isinstance(data, bool):
            return data
        return True                             # it ran and did not complain

    def list(self, user: str, limit: int) -> list[MemoryItem]:
        return _items(self._call("list", {"limit": limit,
                                          "user_id": user}))[:limit]

    # ---- the wire ------------------------------------------------------------

    def _call(self, verb: str, args: dict[str, Any]) -> Any:
        if not self.supports(verb):
            raise MemoryStoreError(
                f"{self.server} has no tool for {verb!r} (looked for "
                f"{self.tools[verb]!r}; map it in [memory] verbs)")
        session = self._session()
        if session is None:
            raise MemoryStoreError(f"mcp server {self.server!r} is not "
                                   f"connected")
        from yantra.mcp import MCPError
        try:
            text, is_error = session.call_tool(self.tools[verb], args,
                                               timeout=self.timeout)
        except MCPError as exc:
            raise MemoryStoreError(str(exc)) from None
        if is_error:
            raise MemoryStoreError(text or f"{self.tools[verb]} failed")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text


def _first(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if data.get(key) not in (None, ""):
            return data[key]
    return None


def _items(data: Any) -> list[MemoryItem]:
    rows = data
    if isinstance(data, dict):
        rows = next((data[k] for k in _LIST_KEYS
                     if isinstance(data.get(k), list)), [])
    if not isinstance(rows, list):
        return []
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        memory_id, text = _first(row, _ID_KEYS), _first(row, _TEXT_KEYS)
        if memory_id is None or not isinstance(text, str):
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        items.append(MemoryItem(
            id=str(memory_id), statement=" ".join(text.split()),
            created=str(row.get("created_at") or row.get("created") or ""),
            package=meta.get("package"), kind=row.get("kind") or meta.get("kind")))
    return items


def _short(data: Any) -> str:
    text = data if isinstance(data, str) else json.dumps(data, default=str)
    return text if len(text) <= 80 else text[:77] + "..."


def bind_memory_server(agent: Any, manager: Any,
                       configs: list[Any] = ()) -> str | None:
    """The host's half: connect the store's server if it is not up yet
    (from ``configs``, a package's ``[[mcp]]``), bind the store to it, and
    take the mapped tools out of the model's roster.

    Returns a sentence saying what is wrong, or None when bound. Never
    raises: memory fails open, like everywhere else.
    """
    memory = getattr(agent, "memory", None)
    store = getattr(memory, "store", None)
    if not isinstance(store, McpStore):
        return None
    server = store.server
    if server not in manager.sessions:
        declared = next((c for c in configs if c.name == server), None)
        if declared is None:
            return (f"memory: mcp server {server!r} is not connected "
                    f"(add it with --mcp-config, the page's MCP panel, or "
                    f"[[mcp]] in agent.toml)")
        from yantra.mcp import MCPError
        try:
            manager.connect(declared)
        except (MCPError, ValueError) as exc:
            return f"memory: mcp server {server!r} unavailable ({exc})"
    prefix = f"mcp__{server}__"
    offered = {name.removeprefix(prefix)
               for name in manager.tool_names.get(server, [])}
    store.bind(lambda: manager.sessions.get(server), offered)
    for verb, tool in store.tools.items():
        if verb not in store.missing:
            agent.registry.unregister(prefix + tool)
    return None


__all__ = ["DEFAULT_TOOLS", "McpStore", "SERVER_NAME", "VERBS",
           "bind_memory_server", "check_verbs"]
