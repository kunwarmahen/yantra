"""The web server: one local browser page driving the SAME agent loop the
REPL drives, with the human interactions (permission prompts, ask_user)
round-tripping over a websocket instead of the terminal.

Architecture in three moves:

* THE LOOP STAYS SYNC. Each turn runs on a worker thread pulling the same
  ``run_streaming`` generator the REPL pulls -- identical semantics for
  gates, batches, cancellation, and resumable history. The server is a
  skin, not a second harness.

* A QUEUE BRIDGE crosses the sync/async boundary. The worker thread is
  blocking by design; the event loop must never. Every human interaction
  is a ``_Pending`` object with its own ``queue.Queue``: the worker blocks
  in ``get(timeout=...)`` polling for an answer AND for cancellation; the
  websocket handler (async side) delivers answers with ``put_nowait``.
  Outbound events fan out to every connected client the same way, via
  ``loop.call_soon_threadsafe`` captured per-connection.

* EVERYTHING IS ENVELOPES. One websocket (/ws) carries tagged JSON both
  ways -- stream deltas, tool cards, permission requests, asks, state --
  so the frontend is a dumb renderer and the session survives reconnects
  (a fresh tab receives ``state`` and any still-pending interaction, then
  fetches the transcript replay from /api/history -- ONE road, so nothing
  is drawn twice). REST endpoints cover plain request/response controls.

Cancellation mirrors Ctrl-C exactly: the cancel flag is honored while
streaming (between events), between tool executions, and while blocked on
a human answer -- each path closes the generator so the agent's
resumable-history synthesis runs. Because the loop polls the flag INSIDE
the model stream too (Agent.interrupt_check), a Stop click lands within
one SSE event of a long generation -- the one gap terminal Ctrl-C had
that a signal-less worker thread used to keep open.

Mutating REST endpoints refuse to run mid-turn (409): the REPL serves its
slash commands between turns too -- same single-operator assumption.
"""

from __future__ import annotations

import asyncio
import base64
import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from yantra.agent import Agent, BudgetWarning, ToolExecuted, TurnEnd
from yantra.config import default_model, load_settings
from yantra.context import estimate_history
from yantra.errors import ConfigError, ProviderError, UserUnavailable
from yantra.images import image_block_from_bytes
from yantra.memory.reflect import mark_reviewed
from yantra.permissions import (
    ON_TIMEOUT,
    REFUSED_OUT_OF_TIME,
    REFUSED_TIMEOUT,
    REFUSED_USER,
    PermissionRequest,
    approval_notice,
    refuse,
    wait_spent,
)
from yantra.pricing import is_free, session_cost
from yantra.prompt import recompose
from yantra.providers import get_provider
from yantra.session import SessionStore, apply_payload
from yantra.skills.loader import SkillError
from yantra.hold import check_answers, held_task
from yantra.trace import flagged, watch, why_flagged
from yantra.types import (
    EndEvent,
    ImageBlock,
    RedactedThinking,
    RedactedThinkingBlock,
    StartEvent,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolCallStart,
    ToolResult,
)

CANCELLED = object()  # sentinel pushed into pending queues on cancel


def _budget_meter(agent: Agent) -> dict[str, Any] | None:
    """The per-turn ceiling as numbers, or None when there is none.

    Separate from the ``budget`` sentence rather than parsed out of it:
    the sentence is written for a person reading one line, and a bar
    needs two floats and the answer to "can this ever move?".
    """
    budget = getattr(agent, "budget", None)
    if budget is None:
        return None
    return {"spent": round(budget.spent, 6), "max_usd": budget.max_usd,
            "metered": budget.metered, "tells_agent": budget.notify_agent,
            # The sub-agents' share of ``spent`` (notes/64): already in
            # it, and the one thing a single bar could not say.
            "delegated": round(budget.delegated, 6)}


def cost_line(agent: Agent) -> str:
    """The dollars footer, same honesty rules as the REPL's _cost_line:
    local models are free, unknown slugs show NO figure -- never $0."""
    buckets = agent.usage_by_model
    if not buckets:
        return ""
    if all(is_free(agent.provider.name, m) for m in buckets):
        return "$0.00 (local model)"
    total, complete = session_cost(buckets)
    if total == 0.0 and not complete:
        return ""
    suffix = "" if complete else " (priced models only)"
    return f"~${total:.4f}{suffix}"


# ---------------------------------------------------------------------------
# Session: owns the bridge between worker threads and websocket clients
# ---------------------------------------------------------------------------


#: How many recorded turns the page's panel lists, newest first.
TURNS_SHOWN = 100


def _held_view(agent) -> dict[str, Any] | None:
    """The held turn as the page shows it (notes/88), or None: what each
    waiting call would do, and how long ago the turn stopped -- an
    approval given an hour late runs against the world as it is now."""
    held = getattr(agent, "held", None)
    if held is None:
        return None
    return {"at": held.at, "age": round(time.time() - held.at),
            "calls": [{"id": r.call_id, "tool_name": r.tool_name,
                       "summary": r.summary, "arguments": r.arguments}
                      for r in held.waiting]}


def _refusal_tally(agent) -> dict[str, int]:
    """This turn's refusals counted by cause -- the terminal's per-turn
    tally (notes/52), for the page's turn footer.

    Counted from ``turn_refusals`` rather than from the envelopes the page
    was sent: a tab that joined mid-turn missed some of the cards, and
    the footer should still add up to what the gate did.
    """
    counts: dict[str, int] = {}
    for code in getattr(agent, "turn_refusals", {}).values():
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


@dataclass
class _Client:
    """One connected browser tab: its inbound event queue plus the loop that
    queue belongs to (call_soon_threadsafe needs the RIGHT loop)."""
    loop: asyncio.AbstractEventLoop
    events: asyncio.Queue


class _Expired(Exception):
    """The turn's waiting allowance ran out while a question was up."""


@dataclass
class _Pending:
    """One open question to the human (permission or ask), answered by id."""
    kind: str  # "permission" | "ask"
    envelope: dict[str, Any]  # what was broadcast; re-sent to late tabs
    answers: queue.Queue = field(default_factory=queue.Queue)  # dict|CANCELLED


class WebSession:
    """Shared state for one served session. Created BEFORE the Agent (the
    CLI needs this object's permission gate at construction), attached after.

    Also IS the ask_user ``UserChannel`` -- one bridge serves both kinds of
    human interaction.
    """

    def __init__(self) -> None:
        self.agent: Agent | None = None
        self.store: SessionStore | None = None
        self.mcp: Any | None = None  # MCPManager; optional -- tests may omit
        self._clients: list[_Client] = []
        self._clients_lock = threading.Lock()
        self._pending: _Pending | None = None
        self._cancel = threading.Event()
        self.turn_active = False
        #: A TrajectoryLog when the operator passed --trace (notes/63). The
        #: browser drives the same agent through a different loop from the
        #: terminal's, so the recorder has to be teed in here as well --
        #: a flag that recorded one frontend and silently not the other
        #: would be a store with holes nobody could see.
        self.trace: Any | None = None
        #: How long ONE TURN may spend, in total, waiting for approvals
        #: (notes/78) -- ``with_wait_budget``'s rule, kept here because
        #: this gate blocks a worker thread and cannot be wrapped. None is
        #: no budget: every question waits as long as the person takes.
        self.wait_budget: float | None = None
        #: What an unanswered question means once the allowance is gone:
        #: "deny", "allow" or "hold" (notes/88). No default, for
        #: with_deadline's reason.
        self.on_timeout: str | None = None
        self._wait_left: float | None = None
        #: How the last turn ended, for a save asked for by hand when no
        #: Learner watched it (learning off). "" before the first turn.
        self._last_end = ""
        #: The Setu sign-in running from the Connections panel, if any
        #: (setu_link.SignIn), and whether its result still has to reach
        #: the tools -- held while a turn runs (notes/99).
        self.signin: Any | None = None
        self._setu_sync_due = False
        self._setu_lock = threading.Lock()

    def set_wait_budget(self, seconds: float, *, on_timeout: str) -> None:
        """Give each turn ``seconds`` of waiting for approvals (notes/78)."""
        if on_timeout not in ON_TIMEOUT:
            raise ValueError(f"unknown on_timeout {on_timeout!r} "
                             f"(want one of {', '.join(ON_TIMEOUT)})")
        if seconds <= 0:
            raise ValueError(f"a turn's waiting budget must be positive, "
                             f"got {seconds!r}")
        self.wait_budget, self.on_timeout = seconds, on_timeout

    def approval_notice(self, turn_id: str) -> str | None:
        """What the model is told about this turn's approval time
        (notes/80): ``with_wait_budget``'s sentence, from this clock."""
        if self._wait_left is None or self.wait_budget is None:
            return None
        return approval_notice(self._wait_left, self.wait_budget)

    # ---- wiring -------------------------------------------------------------

    def attach(self, agent: Agent, store: SessionStore | None,
               mcp: Any | None = None) -> None:
        self.agent = agent
        # The clock is kept here, so the model's view of it comes from
        # here too (notes/80). Without a budget it answers None.
        agent.approval_notice = self.approval_notice
        self.store = store
        if mcp is not None:
            self.mcp = mcp
        # Raw StreamEvents are PUSHED here while each response streams (they
        # cannot be yielded -- collect() owns that pull). Same wiring as the
        # REPL's renderer: without this, text/thinking deltas vanish.
        agent.on_stream_event = self._emit
        # The Stop button's teeth: the loop polls this flag between stream
        # events and between tool calls, so a click lands mid-model-call --
        # as close to terminal Ctrl-C as a signal-less worker can get.
        agent.interrupt_check = self._cancel.is_set
        # A memory store that failed says so on the page, as a banner in
        # the conversation it failed for (memory/__init__.py: fails open,
        # out loud).
        if getattr(agent, "memory", None) is not None:
            agent.memory.on_notice = lambda text: self.broadcast(
                {"type": "memory_notice", "text": text})

    @property
    def channel(self) -> WebSession:
        """The ask_user channel: this object (it implements .ask below)."""
        return self

    # ---- outbound fan-out ---------------------------------------------------

    def connect(self, loop: asyncio.AbstractEventLoop,
                events: asyncio.Queue) -> _Client:
        client = _Client(loop=loop, events=events)
        with self._clients_lock:
            self._clients.append(client)
        return client

    def disconnect(self, client: _Client) -> None:
        with self._clients_lock:
            if client in self._clients:
                self._clients.remove(client)

    def broadcast(self, envelope: dict[str, Any]) -> None:
        """Thread-safe fan-out. Called from workers AND async handlers."""
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.loop.call_soon_threadsafe(
                    client.events.put_nowait, envelope)
            except RuntimeError:
                pass  # that tab's loop is gone; disconnect() will reap it

    # ---- inbound answers ------------------------------------------------------

    def deliver(self, message: dict[str, Any]) -> None:
        """Route a websocket message arriving from the browser."""
        mtype = message.get("type")
        if mtype == "cancel":
            self.cancel_turn()
            return
        if mtype == "answer" and self._pending is not None:
            self._pending.answers.put_nowait(message)

    def cancel_turn(self) -> None:
        """The Stop button / ctrl-c equivalent. Honored between stream
        events -- i.e. mid-model-call, within one SSE event -- between
        tools, and while a human interaction is pending (that queue gets
        the sentinel immediately)."""
        self._cancel.set()
        pending = self._pending
        if pending is not None:
            pending.answers.put_nowait(CANCELLED)

    def _wait_for_answer(self, pending: _Pending,
                         deadline: float | None = None) -> dict[str, Any]:
        """Worker-side block: poll the answer queue AND the cancel flag.
        Polling (not a bare blocking get) is what lets cancel interrupt a
        wait without anyone knowing which thread notices first.

        ``deadline`` (a ``time.monotonic()``) is the turn's waiting
        allowance running out; reaching it raises ``_Expired``."""
        while True:
            try:
                item = pending.answers.get(timeout=0.2)
            except queue.Empty:
                if self._cancel.is_set():
                    # turn-cancel, REPL semantics; the empty poll is
                    # the timer, not the cause
                    raise KeyboardInterrupt from None
                if deadline is not None and time.monotonic() >= deadline:
                    raise _Expired from None
                continue
            if item is CANCELLED:
                raise KeyboardInterrupt
            return item

    def _ask_human(self, envelope: dict[str, Any],
                   deadline: float | None = None) -> dict[str, Any]:
        """Broadcast a question, block for its answer, clean up after.
        The ``resolved`` in the finally is also what withdraws a question
        whose time ran out: the page closes it rather than leaving a
        prompt up that has already been decided."""
        pending = _Pending(kind=envelope["type"], envelope=envelope)
        self._pending = pending
        try:
            self.broadcast(envelope)
            return self._wait_for_answer(pending, deadline)
        finally:
            self._pending = None
            self.broadcast({"type": "resolved", "id": envelope.get("id")})

    # ---- the two interactive surfaces ----------------------------------------

    def permission_gate(self):
        """PermissionFn for the browser: approve / deny / deny-with-a-reason
        / edit, with full edit round-trips -- mirroring cli/repl.py's
        confirm_gate loop beat for beat, including the sentence a person
        types instead of a bare no (notes/56)."""

        def gate(request: PermissionRequest) -> bool:
            if request.read_only:
                # Same contract as the terminal gate: a tool that declared
                # itself side-effect-free has nothing to confirm.
                return True
            edited = False
            while True:
                if self._wait_left is not None and self._wait_left <= 0:
                    # NOTHING IS ASKED ONCE THE ALLOWANCE IS GONE: a
                    # question nobody will wait for is not posted
                    # (with_wait_budget's rule, notes/51 and 71).
                    return wait_spent(request, self.wait_budget,
                                      REFUSED_OUT_OF_TIME,
                                      on_timeout=self.on_timeout)
                started = time.monotonic()
                deadline = (started + self._wait_left
                            if self._wait_left is not None else None)
                envelope = {
                    "type": "permission_request",
                    "id": uuid.uuid4().hex[:8],
                    "tool_name": request.tool_name,
                    "summary": request.summary,
                    "arguments": request.arguments,
                    "edited": edited,
                }
                if self._wait_left is not None:
                    # Seconds, not a timestamp: the page's clock and
                    # this one need not agree.
                    envelope["wait_left"] = round(self._wait_left, 1)
                try:
                    answer = self._ask_human(envelope, deadline)
                except _Expired:
                    return wait_spent(request, self.wait_budget,
                                      REFUSED_TIMEOUT,
                                      on_timeout=self.on_timeout)
                finally:
                    # Deducted however the wait ended -- an answer, the
                    # clock, or a Stop -- because the time was spent.
                    if self._wait_left is not None:
                        self._wait_left = max(
                            0.0, self._wait_left
                            - (time.monotonic() - started))
                decision = answer.get("decision")
                if decision == "approve":
                    return True
                if decision == "deny":
                    # Same three shapes as the terminal gate: a bare no,
                    # or a no with a sentence the person typed, which
                    # reaches the model attributed and verbatim in place
                    # of "Permission denied by user." (notes/56).
                    said = answer.get("reason")
                    said = said.strip() if isinstance(said, str) else ""
                    if said:
                        return refuse(request,
                                      f"{request.tool_name} was denied. The "
                                      f"person said: {said}",
                                      code=REFUSED_USER)
                    return refuse(request,
                                  f"{request.tool_name} was denied: you "
                                  f"said no at the approval prompt.",
                                  code=REFUSED_USER)
                if decision == "edit":
                    amended = answer.get("edited_args")
                    if not isinstance(amended, dict):
                        continue  # unusable payload -- just ask again
                    # Same contract as the terminal editor: swap args,
                    # refresh the preview through the tool's own summary(),
                    # re-ask. What you approve is what runs.
                    request.arguments = amended
                    try:
                        request.summary = (request.summarize(amended)
                                           if request.summarize else
                                           f"{request.tool_name}"
                                           f"({json.dumps(amended)})")
                    except Exception:
                        request.summary = (
                            f"{request.tool_name}({json.dumps(amended)})")
                    edited = True

        return gate

    def ask(self, question: str, choices: list[str],
            context: str = "") -> str:
        """UserChannel implementation: ask_user blocks here until the tab
        answers. Empty/malformed answers count as a cancel, not an answer."""
        answer = self._ask_human({
            "type": "ask",
            "id": uuid.uuid4().hex[:8],
            "question": question,
            "context": context,
            "choices": choices,
        })
        text = answer.get("text")
        if not isinstance(text, str) or not text.strip():
            raise KeyboardInterrupt
        return text

    # ---- turns ---------------------------------------------------------------

    def start_turn(self, text: str, images: list[ImageBlock]) -> None:
        """Spawn the worker thread. Caller has already refused concurrent
        turns; this is single-operator, one-turn-at-a-time by design."""
        self._begin(lambda: self.agent.run_streaming(
            text, images=images or None), text)

    def start_resume(self, answers: dict[str, Any]) -> None:
        """Answer a held turn from the page and carry it on (notes/88).

        Checked HERE, before a thread starts, so a stale or partial answer
        is a 400 the page can show rather than a turn_error after the fact.
        """
        held = check_answers(self.agent.held, answers, self.agent.history)
        # Read off the hold and the history, not off this session: a hold
        # loaded from a checkpoint was made by a process that is gone.
        self._begin(lambda: self.agent.resume(answers),
                    held_task(self.agent.history), resumes=held.recorded)

    def _begin(self, stream_of, task: str, resumes: str | None = None
               ) -> None:
        self._cancel.clear()
        self.turn_active = True
        # The allowance is per TURN, and this is the one place a turn
        # starts -- no inferring it from gaps between questions. A resume
        # is a turn too: the person is here now.
        self._wait_left = self.wait_budget
        self.broadcast({"type": "turn_started"})
        threading.Thread(target=self._run_turn,
                         args=(stream_of, task, resumes),
                         daemon=True, name="yantra-turn").start()

    def _run_turn(self, stream_of, task: str,
                  resumes: str | None = None) -> None:
        """The REPL's run_turn, transplanted: pull the generator, forward
        envelopes, honor cancel at every yield boundary."""
        agent = self.agent
        stream = stream_of()
        if self.trace is not None:
            stream = watch(task, stream, self._record,
                           provider=getattr(agent.provider, "name", ""),
                           model=agent.model, detail=self.trace.detail,
                           spawner=getattr(agent, "subagents", None),
                           resumes=resumes)
        ended = False  # a natural TurnEnd went out -- don't double-report
        cancelled = False
        end: TurnEnd | None = None
        try:
            for event in stream:
                ended = ended or isinstance(event, TurnEnd)
                if isinstance(event, TurnEnd):
                    end = event
                self._emit(event)
                if self._cancel.is_set():
                    cancelled = True
                    stream.close()  # runs the outstanding-call synthesis
                    break
        except UserUnavailable as exc:
            self.broadcast({"type": "turn_error", "message": str(exc)})
        except KeyboardInterrupt:
            cancelled = True  # cancel landed while blocked on the human
        except ProviderError as exc:
            wait = (f" retry after {exc.retry_after:.0f}s"
                    if exc.retry_after else "")
            self.broadcast({"type": "turn_error",
                            "message": f"{type(exc).__name__}: {exc}{wait}"})
        except Exception as exc:
            self.broadcast({"type": "turn_error",
                            "message": f"{type(exc).__name__}: {exc}"})
        if cancelled and not ended:
            self.broadcast({"type": "turn_cancelled"})
        if end is not None and not cancelled:
            self._offer_pending_memories()
            self._learn(end)
        self.turn_active = False
        self._cancel.clear()
        self._setu_settle()
        self.broadcast({"type": "state", **self.state()})
        self.broadcast({"type": "turn_done"})

    def _offer_pending_memories(self) -> None:
        """What a look back before compaction found mid-turn goes to the
        page when the turn ends; the page answers through
        /api/memory/keep, like the button's look back."""
        memory = getattr(self.agent, "memory", None)
        if memory is None or not memory.pending:
            return
        found, memory.pending = list(memory.pending), []
        self.broadcast({"type": "memory_offer",
                        "candidates": [{"statement": c.statement, "kind": c.kind}
                                       for c in found]})

    # ---- learning (notes/96) ---------------------------------------------------

    def _learn(self, end: TurnEnd | None, *, forced: bool = False) -> None:
        """The terminal's after-turn offer, on the page: count learned
        skills the turn used, then offer to save what it solved.

        Runs on the turn's worker, before ``turn_done``, so the input stays
        busy while the question is up -- the same order the terminal keeps.
        Nothing here may cost the answer already on the page: a failure is
        a banner, and Stop is a no.
        """
        if end is not None:
            # Kept even with learning off: a save asked for later by hand
            # needs to know this turn finished.
            self._last_end = end.reason
        learner = getattr(self.agent, "learner", None)
        if learner is None and forced:
            from yantra.skills.learn import Learner
            learner = Learner(self.agent, "ask")
            learner.last_reason = self._last_end
        if learner is None:
            return
        try:
            if end is not None:
                for name, worked in learner.after_turn(end):
                    self.broadcast({"type": "learn_counted", "name": name,
                                    "worked": worked})
                if end.reason != "end_turn":
                    return
            offer = learner.consider(
                forced=forced,
                progress=lambda text: self.broadcast(
                    {"type": "learn_status", "text": text}))
            if offer is None:
                if forced:
                    self.broadcast({"type": "learn_skipped",
                                    "reason": learner.last_skip})
                return
            if learner.mode == "auto" and not forced:
                skill = learner.save(offer)
                self.broadcast({"type": "learned", "name": skill.name,
                                "path": str(skill.directory)})
                return
            self._ask_to_save(learner, offer)
        except KeyboardInterrupt:
            self.broadcast({"type": "learn_skipped", "reason": "not saved"})
        except Exception as exc:
            self.broadcast({"type": "learn_skipped",
                            "reason": f"learning skipped: {exc}"})

    def _ask_to_save(self, learner: Any, offer: Any) -> None:
        """Put the offer to the page; save, re-ask with the error, or drop."""
        error = ""
        while True:
            envelope = {"type": "learn_offer", "id": uuid.uuid4().hex[:8],
                        **offer.view(self.agent.ctx.cwd, home=learner.home),
                        "error": error}
            try:
                answer = self._ask_human(envelope)
            except KeyboardInterrupt:
                learner.discard(offer)
                raise
            if answer.get("decision") != "save":
                learner.discard(offer)
                self.broadcast({"type": "learn_skipped", "reason": "not saved"})
                return
            scope = answer.get("scope")
            text = answer.get("skill_md")
            script = answer.get("script")
            try:
                skill = learner.save(
                    offer, scope=scope if scope in ("user", "project") else None,
                    skill_md=text if isinstance(text, str) else None,
                    script=script if isinstance(script, str) else None)
            except (SkillError, OSError, ValueError) as exc:
                # What the person wrote stays in the offer they see again:
                # the staged files ARE the offer (Offer.view reads them).
                offer.keep_edits(text if isinstance(text, str) else None,
                                 script if isinstance(script, str) else None)
                error = str(exc)
                continue
            self.broadcast({"type": "learned", "name": skill.name,
                            "path": str(skill.directory)})
            return

    def start_learn(self) -> None:
        """``POST /api/learn``: save the last turn because the person asked.
        A turn of its own for the page -- the input is busy until it ends."""
        self._cancel.clear()
        self.turn_active = True
        self.broadcast({"type": "turn_started"})

        def work() -> None:
            try:
                self._learn(None, forced=True)
            finally:
                self.turn_active = False
                self._cancel.clear()
                self._setu_settle()
                self.broadcast({"type": "state", **self.state()})
                self.broadcast({"type": "turn_done"})

        threading.Thread(target=work, daemon=True, name="yantra-learn").start()

    def start_promote(self, name: str) -> None:
        """``POST /api/skills/{name}/tool``: the page's ``/skills tool``.
        Write the tool, test it once through the gate, ask -- a turn of
        its own for the page, like a save asked for by hand."""
        self._cancel.clear()
        self.turn_active = True
        self.broadcast({"type": "turn_started"})

        def work() -> None:
            try:
                self._promote(name)
            except KeyboardInterrupt:
                self.broadcast({"type": "learn_skipped",
                                "reason": "not made a tool"})
            except Exception as exc:
                self.broadcast({"type": "learn_skipped",
                                "reason": f"not made a tool: {exc}"})
            finally:
                self.turn_active = False
                self._cancel.clear()
                self._setu_settle()
                self.broadcast({"type": "state", **self.state()})
                self.broadcast({"type": "turn_done"})

        threading.Thread(target=work, daemon=True, name="yantra-promote").start()

    def _promote(self, name: str) -> None:
        from yantra.skills.learn import Learner

        learner = getattr(self.agent, "learner", None) or Learner(self.agent, "ask")
        offer = learner.propose_tool(
            name, progress=lambda text: self.broadcast(
                {"type": "learn_status", "text": text}))
        if offer is None:
            self.broadcast({"type": "learn_skipped",
                            "reason": f"not made a tool: {learner.last_skip}"})
            return
        answer = self._ask_human({"type": "tool_offer",
                                  "id": uuid.uuid4().hex[:8], **offer.view()})
        if answer.get("decision") != "make" or not offer.passed:
            self.broadcast({"type": "learn_skipped", "reason": "not made a tool"})
            return
        skill = learner.save_tool(offer)
        self.broadcast({"type": "promoted", "skill": skill.name,
                        "tool": skill.tool_name})

    # ---- connections (notes/99) ---------------------------------------------------

    def connections_state(self, *, local: bool = False) -> dict[str, Any]:
        """The Connections panel: Setu's report (never a secret, by its
        contract), what this session runs, and the sign-in in progress."""
        setu = getattr(self.agent, "setu", None)
        base = ({"mode": "off", "found": False, "connections": [], "connectors": [],
                 "setup": {}, "problems": [], "error": ""}
                if setu is None else setu.describe(self.mcp))
        signin = self.signin
        return {**base, "local": local,
                "signin": ({"ref": signin.ref, "running": signin.running,
                            **{k: v for k, v in signin.last.items()
                               if k in ("event", "url", "message", "email",
                                        "level_label")}}
                           if signin is not None else None)}

    def setu_sync(self) -> dict[str, Any]:
        """Ask Setu again and make the tools match. Called when no turn is
        running: a turn's tool list and prompt do not change under it."""
        setu = getattr(self.agent, "setu", None)
        if setu is None:
            return {"connected": {}, "dropped": [], "notes": []}
        with self._setu_lock:
            self._setu_sync_due = False
            setu.refresh()
            done = setu.sync(self.mcp, self.agent) if self.mcp is not None else None
        result = ({"connected": done.connected, "dropped": done.dropped,
                   "notes": done.notes} if done is not None
                  else {"connected": {}, "dropped": [], "notes": []})
        self.broadcast({"type": "connections", **self.connections_state(), "sync": result})
        self.broadcast({"type": "state", **self.state()})
        return result

    def _setu_settle(self) -> None:
        """A sign-in that finished during a turn reaches the tools now."""
        if self._setu_sync_due and not self.turn_active:
            try:
                self.setu_sync()
            except Exception as exc:
                self.broadcast({"type": "setu_signin", "event": "error",
                                "message": f"connected, but the tools did not "
                                           f"update: {exc}"})

    def start_signin(self, connector: str, account: str, level: str) -> None:
        """Run ``setu connect --json`` and relay it to the page."""
        from yantra.setu_link import SignIn

        setu = self.agent.setu

        def relay(event: dict[str, Any]) -> None:
            self.broadcast({"type": "setu_signin", **event})
            if event.get("event") == "connected":
                self._setu_sync_due = True
                if not self.turn_active:
                    self._setu_settle()

        self.signin = SignIn(setu.program, connector, account, level, relay)

    def _record(self, trajectory) -> None:
        """The recorder's sink: write the line, then tell the page its id.

        The id is what a mark is written against (notes/77). It goes out
        AFTER the line is on disk, so a page can never offer to judge a
        turn the file does not have yet -- and before ``turn_done``,
        because ``watch`` hands the turn over as the stream closes.
        """
        self.trace.record(trajectory)
        held = getattr(self.agent, "held", None)
        if trajectory.outcome == "held" and held is not None:
            held.recorded = trajectory.id
        self.broadcast({"type": "recorded", "id": trajectory.id})

    def mark(self, trace_id: str, verdict: str,
             why: str | None = None) -> dict[str, Any]:
        """A person's verdict from the page: ``--mark`` without leaving it.

        The same ``TrajectoryLog.mark`` the terminal command calls, so the
        line a button writes is the line ``--mark`` would have written.
        ``clear`` takes it back and gives a suite's verdict back.
        """
        passed = {"good": True, "bad": False, "clear": None}[verdict]
        turn = self.trace.mark(trace_id, passed=passed,
                               why=why if passed is not None else None)
        return {"id": turn.id, "passed": turn.passed,
                "judged_by": turn.judged_by, "why": turn.why}

    def turns(self, limit: int = TURNS_SHOWN) -> dict[str, Any]:
        """The recording, newest first: ``--turns`` for the page (notes/81).

        The end-of-turn mark row only exists for turns recorded while the
        page was open. A reload, a second tab, or yesterday's session all
        leave turns the page never saw, and marking them meant going to a
        terminal. This reads the same file ``--turns`` reads and flags
        with the same rule (``trace.flagged``), so the two lists agree.

        Each row carries the turn's answer when the line was written with
        ``--trace-full``: a verdict given without reading what the agent
        said is a guess about the task, not a judgement of the turn.

        Newest first and capped, because the turn a person wants to judge
        is almost always a recent one, and a year of recording should not
        become one very long page. ``total`` says how many there are.
        """
        turns = self.trace.read()
        rows = [{
            "id": t.id, "at": t.at, "task": " ".join(t.task.split())[:200],
            "tools": len(t.steps), "case": t.case,
            "flagged": flagged(t), "flag_why": why_flagged(t),
            "passed": t.passed, "judged_by": t.judged_by, "why": t.why,
            # What is being judged. Only a --trace-full line keeps it, and
            # it was clipped and redacted when it was written, so it goes
            # out as the file has it. A withheld line says why instead.
            "answer": None if t.withheld else t.answer,
            "withheld": t.withheld, "outcome": t.outcome,
        } for t in reversed(turns[-limit:])]
        return {"path": str(self.trace.path), "total": len(turns),
                "flagged": sum(1 for t in turns if flagged(t)),
                "unreadable": self.trace.unreadable, "turns": rows}

    def _emit(self, event) -> None:
        """AgentEvent/StreamEvent -> envelope(s). Mirrors render.py's match."""
        match event:
            case StartEvent(model=model):
                self.broadcast({"type": "start", "model": model})
            case TextDelta(text=text):
                self.broadcast({"type": "delta", "text": text})
            case ThinkingDelta(index=_, text=text, signature=_):
                if text:  # signature fragments accumulate silently
                    self.broadcast({"type": "thinking_delta", "text": text})
            case RedactedThinking(index=_, data=data):
                self.broadcast({"type": "redacted_thinking", "chars": len(data)})
            case ToolCallStart(index=_, id=_, name=name):
                self.broadcast({"type": "tool_start", "name": name})
            case ToolExecuted(call=call, result=result, refusal=refusal):
                self.broadcast({
                    "type": "tool_result",
                    "name": call.name,
                    "arguments": call.arguments,
                    "output": result.content,
                    "is_error": result.is_error,
                    # A REFUSAL IS NOT A CRASH. Both arrive as error
                    # results (agent.py), and a browser that draws them
                    # the same way tells somebody their tool broke when
                    # what happened is that they said no -- or that
                    # nobody answered in time, which is a third thing
                    # again (notes/52).
                    "refusal": refusal,
                    # Pressure moves during a turn too (each iteration
                    # refills the window); the header bar follows along
                    # instead of waiting for the end-of-turn state.
                    "utilization": self.agent.utilization(),
                    # The meter moves DURING a turn -- every model call
                    # charges it -- so the header follows along instead of
                    # jumping at the end. A tool result is the right
                    # moment: the call that asked for this tool has been
                    # billed by the time we get here.
                    "budget_meter": _budget_meter(self.agent),
                })
            case EndEvent(stop_reason=_, usage=_):
                pass  # per-call usage; TurnEnd.usage carries the turn's total
            case BudgetWarning(detail=detail, spent=spent, max_usd=max_usd):
                # The numbers ride along as numbers, not only inside the
                # sentence: a browser can draw a bar, a terminal cannot.
                self.broadcast({"type": "budget_warning", "detail": detail,
                                "spent": spent, "max_usd": max_usd,
                                "budget_meter": _budget_meter(self.agent)})
            case TurnEnd(reason="end_turn", response=response, iterations=n,
                         usage=turn_usage):
                # The turn's calls summed, as the REPL footer shows them --
                # not the last call's, which the page used to print.
                usage = turn_usage or (response.usage
                                       if response is not None else None)
                self.broadcast({
                    "type": "turn_end",
                    "reason": "end_turn",
                    "stop_reason": response.stop_reason if response else "",
                    "text": response.message.text() if response is not None else "",
                    "input_tokens": usage.input_tokens if usage else 0,
                    "output_tokens": usage.output_tokens if usage else 0,
                    "cost_line": cost_line(self.agent),
                    "iterations": n,
                    "budget_meter": _budget_meter(self.agent),
                    "refused": _refusal_tally(self.agent),
                })
            case TurnEnd(reason=reason, response=response, iterations=n,
                         detail=detail):
                # detail carries the numbers the reason word cannot --
                # "over_budget" is not an answer to "over what?" (budget.py).
                # A capped reply (notes/76) arrives WITH its response: the
                # text so far is kept, and the reason says why it stops.
                self.broadcast({"type": "turn_end", "reason": reason,
                                "detail": detail or "",
                                "text": (response.message.text()
                                         if response is not None else ""),
                                "iterations": n, "cost_line": "",
                                "refused": _refusal_tally(self.agent),
                                "held": _held_view(self.agent)})

    # ---- snapshots -------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        agent = self.agent
        u = agent.total_usage
        return {
            # The turn waiting for approval, if any (notes/88), so a page
            # opened or reloaded after the hold still shows the questions.
            "held": _held_view(agent),
            "provider": agent.provider.name,
            "model": agent.model,
            "cwd": str(agent.ctx.cwd),
            # "ask" | "yolo" -- agents built with a bare gate (tests,
            # embedders) report the fixed default rather than crashing.
            "mode": getattr(agent.permissions, "mode", "ask"),
            # Session awareness snapshot ([notes/29]); None when the host
            # built the agent without an EnvContext.
            "env_context": (agent.env_context.describe()
                            if hasattr(agent, "env_context") else None),
            # Skills snapshot ([notes/30]); None when the host built the
            # agent without a SkillRegistry (--no-skills, embedders).
            "skills": (agent.skills.describe()
                       if hasattr(agent, "skills") else None),
            # Memory about the person (memory/): which store, whose, how
            # many went into this conversation's prompt. None when off.
            "memory": (agent.memory.describe()
                       if getattr(agent, "memory", None) is not None else None),
            "tools": agent.registry.names(),
            # Runtime-disabled subset of ``tools`` (the /api/tools panel's
            # toggles); empty for a stock session.
            "disabled_tools": agent.registry.disabled_names(),
            "usage": {"input": u.input_tokens, "output": u.output_tokens,
                      "cache_read": u.cache_read_tokens,
                      "cache_write": u.cache_write_tokens},
            # The per-turn dollar ceiling, or None when there is none.
            # A ceiling nobody can see is a ceiling nobody trusts, and the
            # inert case (a local model) has to say so rather than imply
            # protection by staying quiet ([notes/34]).
            "budget": (agent.budget.describe()
                       if getattr(agent, "budget", None) is not None else None),
            # The same ceiling AS NUMBERS, for the header's meter. A
            # terminal can only print the sentence; a browser can draw
            # what is left, which is the readout [notes/36] said belonged
            # beside the context-pressure bar rather than in the event
            # stream. None where there is no ceiling, and ``metered``
            # false where one exists and can never fire (a local model) --
            # a full bar that will never move is worse than no bar.
            "budget_meter": _budget_meter(agent),
            # Where turns are being written, or None (notes/64). The
            # terminal banner says so once at startup; the PAGE is what
            # somebody at a shared machine actually looks at.
            # The per-turn allowance for approvals (notes/78), so the page
            # can say why a prompt carries a clock.
            "wait_budget": ({"seconds": self.wait_budget,
                             "on_timeout": self.on_timeout}
                            if self.wait_budget is not None else None),
            "recording": ({"path": str(self.trace.path),
                           "detail": self.trace.detail,
                           "redacting": getattr(self.trace,
                                                "redact_count", 0),
                           # A count, never the entries (notes/86).
                           "redacting_words": getattr(self.trace,
                                                      "redact_words", 0),
                           # The local model reading for names (notes/89),
                           # by tag; what it finds is never shown.
                           "reading_names": getattr(
                               getattr(self.trace, "reader", None),
                               "model", None)}
                          if self.trace is not None else None),
            "utilization": agent.utilization(),
            # The honest numbers behind the pressure bar: what the last
            # request actually filled and how big the window is at all.
            # Before the first response arrives there is no provider
            # figure -- fall back to the chars/4 estimate and SAY so.
            "context_estimated": agent.last_context_tokens == 0,
            "context_tokens": (agent.last_context_tokens or
                               estimate_history(agent.history)),
            "context_window": agent.context_window,
            "cost_line": cost_line(agent),
            "turn_active": self.turn_active,
        }

    def history_envelopes(self) -> list[dict[str, Any]]:
        """Replayable transcript for a freshly (re)connected tab, rendered in
        almost the same shapes the live feed uses, minus streaming."""
        results_by_id: dict[str, dict] = {}
        for message in self.agent.history:
            if message.role != "user":
                continue
            for block in message.content:
                if isinstance(block, ToolResult):
                    results_by_id[block.tool_call_id] = {
                        "output": block.content, "is_error": block.is_error}

        out: list[dict[str, Any]] = []
        for message in self.agent.history:
            if message.role == "user":
                texts = [b.text for b in message.content
                         if isinstance(b, TextBlock)]
                images = sum(1 for b in message.content
                             if isinstance(b, ImageBlock))
                if texts or images:  # pure tool-result carriers are skipped
                    out.append({"type": "user_message",
                                "text": "\n".join(texts), "images": images})
                continue
            thinking = "".join(b.thinking for b in message.content
                               if isinstance(b, ThinkingBlock))
            redacted = sum(1 for b in message.content
                           if isinstance(b, RedactedThinkingBlock))
            if thinking:
                out.append({"type": "thinking_done", "text": thinking})
            if redacted:
                out.append({"type": "redacted_thinking",
                            "chars": sum(len(b.data) for b in
                                         message.content
                                         if isinstance(b, RedactedThinkingBlock))})
            if message.text().strip():
                out.append({"type": "assistant_text",
                            "text": message.text()})
            held = getattr(self.agent, "held", None)
            waiting = set(held.ids) if held is not None else set()
            for call in message.tool_calls():
                if call.id in waiting:
                    # Still waiting (notes/88): the held panel shows it,
                    # and a card here would draw a call that has not run
                    # as one that ran and printed nothing.
                    continue
                result = results_by_id.get(call.id, {})
                # No "refusal" key on a replayed call: the code lives on
                # the EVENT, not in history, and a reconnect that invented
                # one would be worse than a reconnect that shows the error
                # result the model actually saw.
                out.append({"type": "tool_result", "name": call.name,
                            "arguments": call.arguments,
                            "output": result.get("output", ""),
                            "is_error": result.get("is_error", False)})
        return out


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


def make_app(session: WebSession, static_dir: Path | None = None,
             settings_loader=None, provider_factory=None) -> FastAPI:
    """Build the app around a WebSession. The session is injected rather
    than constructed here so tests can drive ScriptedProvider agents
    offline through the exact production routes. The load-path provider
    factories are injectable too, on the same principle as
    ``session.apply_payload`` (a scripted provider has no real settings)."""
    static_dir = static_dir or Path(__file__).parent / "static"

    app = FastAPI(title="yantra", docs_url=None, redoc_url=None)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        await sock.accept()
        events: asyncio.Queue = asyncio.Queue()
        client = session.connect(asyncio.get_running_loop(), events)
        reader = asyncio.create_task(_ws_read(sock))
        writer = asyncio.create_task(_ws_write(sock, events))
        try:
            await sock.send_json({"type": "state", **session.state()})
            pending = session._pending
            if pending is not None:  # a question was already on screen
                await sock.send_json(pending.envelope)
            # No transcript here: the page fetches /api/history on its first
            # state envelope. Sending it on both roads drew every turn twice.
            done, _ = await asyncio.wait({reader, writer},
                                         return_when=asyncio.FIRST_COMPLETED)
            for task in done:  # surface a read/write failure, if any
                if task.exception() is not None:
                    raise task.exception()
        except Exception:
            pass  # disconnect path -- finally does the cleanup either way
        finally:
            reader.cancel()
            writer.cancel()
            session.disconnect(client)

    async def _ws_read(sock: WebSocket) -> None:
        while True:
            session.deliver(await sock.receive_json())

    async def _ws_write(sock: WebSocket, events: asyncio.Queue) -> None:
        while True:
            await sock.send_json(await events.get())

    # ---- REST ----

    def require_ready() -> None:
        if session.agent is None:
            raise HTTPException(500, "no agent attached")

    def require_idle() -> None:
        require_ready()
        if session.turn_active:
            raise HTTPException(409, "a turn is running -- wait or cancel")

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        require_ready()
        return session.state()

    @app.get("/api/history")
    def history() -> list[dict[str, Any]]:
        """Replayable transcript — the client rebuilds its view from this on
        connect/reload, so the server's history stays the single truth."""
        require_ready()
        return session.history_envelopes()

    @app.post("/api/message")
    async def message(req: Request) -> dict[str, Any]:
        require_idle()
        body = await req.json()
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(400, "text is required")
        images: list[ImageBlock] = []
        for img in body.get("images") or []:
            try:
                raw = base64.b64decode(img["data_base64"])
                images.append(image_block_from_bytes(img["filename"], raw))
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(400, f"bad image: {exc}") from exc
        session.start_turn(text, images)
        return {"ok": True}

    @app.post("/api/resume")
    async def resume(req: Request) -> dict[str, Any]:
        """Answers for a held turn (notes/88): one per waiting call, each
        ``{"decision": "approve"|"deny"|"edit", "reason": ...,
        "edited_args": {...}}`` -- the approval modal's own vocabulary."""
        require_idle()
        body = await req.json()
        raw = body.get("answers")
        if not isinstance(raw, dict):
            raise HTTPException(400, "answers is required: {call_id: "
                                     "{decision: approve|deny|edit}}")
        answers: dict[str, Any] = {}
        for call_id, answer in raw.items():
            decision = answer.get("decision") if isinstance(answer, dict) else None
            if decision == "approve":
                answers[call_id] = True
            elif decision == "deny":
                said = answer.get("reason")
                answers[call_id] = (said.strip() if isinstance(said, str)
                                    and said.strip() else False)
            elif decision == "edit" and isinstance(answer.get("edited_args"),
                                                   dict):
                answers[call_id] = answer["edited_args"]
            else:
                raise HTTPException(400, f"answer for {call_id}: decision "
                                         f"must be approve, deny or edit "
                                         f"(edit with edited_args)")
        try:
            session.start_resume(answers)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/model")
    async def set_model(req: Request) -> dict[str, Any]:
        require_idle()
        slug = (await req.json()).get("model")
        if not isinstance(slug, str) or not slug.strip():
            raise HTTPException(400, "model slug required")
        session.agent.model = slug.strip()
        return session.state()

    @app.post("/api/provider")
    async def set_provider(req: Request) -> dict[str, Any]:
        require_idle()
        name = (await req.json()).get("provider")
        outgoing = session.agent.provider
        try:
            settings = load_settings(name)
            session.agent.provider = get_provider(name, settings)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from exc
        outgoing.close()  # require_idle() above: nothing is mid-request
        # Model slugs are per-provider namespaces (same rule as the REPL's
        # /provider): carrying the old slug over would ask provider B for a
        # model it may not have.
        try:
            session.agent.model = default_model(name)
        except Exception:
            pass  # keep the old slug; the operator sets one explicitly
        return session.state()

    @app.post("/api/permissions")
    async def set_permissions(req: Request) -> dict[str, Any]:
        """Flip the permission mode ("ask" <-> "yolo"). Deliberately NO
        require_idle(): unlike model/provider swaps, flipping mid-turn is
        safe and is the point -- it applies to every not-yet-approved call
        of the running turn (rescues a turn stuck in approval modals).
        Broadcasts state so every open tab's mode chip follows along."""
        require_ready()
        gate = session.agent.permissions
        if not hasattr(gate, "set_mode"):
            raise HTTPException(400, "permission mode is fixed for this "
                                     "session (built without a switchable gate)")
        try:
            gate.set_mode((await req.json()).get("mode"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        session.broadcast({"type": "state", **session.state()})
        return session.state()

    @app.post("/api/env-context")
    async def set_env_context(req: Request) -> dict[str, Any]:
        """Flip session awareness (off/local/full). Deliberately NO
        require_idle(): the composed system is consulted at each iteration's
        request, so a flip lands on the running turn's next model call --
        same argument as the permission flip. The flip itself runs in a
        worker thread (to_thread): upgrading to 'full' may do the one-time
        geo lookup, which must never block the event loop. Broadcasts state
        so every open tab's chip follows along."""
        require_ready()
        ctx = getattr(session.agent, "env_context", None)
        if ctx is None:
            raise HTTPException(400, "this session has no env context "
                                     "(built without session awareness)")
        mode = (await req.json()).get("mode")
        try:
            await asyncio.to_thread(ctx.flip, mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        session.broadcast({"type": "state", **session.state()})
        return session.state()

    @app.get("/api/tools")
    def tools_list() -> list[dict[str, Any]]:
        """The tools panel's data: everything registered, with enough
        detail to decide what to pull -- description, whether it prompts,
        and its current enabled state."""
        require_ready()
        registry = session.agent.registry
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "read_only": tool.read_only,
                "enabled": not registry.is_disabled(tool.name),
            }
            for tool in sorted(registry, key=lambda t: t.name)
        ]

    @app.post("/api/tools")
    async def tools_toggle(req: Request) -> dict[str, Any]:
        """Enable/disable one tool by exact name. Deliberately NO
        require_idle(): like the permission flip, mid-turn is the POINT --
        pulling a tool applies to the running turn's next call (the loop
        re-consults the registry per call), so a runaway `bash` chain can
        be cut without waiting it out. Unknown names are a clean 400."""
        require_ready()
        body = await req.json()
        name, enabled = body.get("name"), body.get("enabled")
        if not isinstance(name, str) or not isinstance(enabled, bool):
            raise HTTPException(400, "name (str) and enabled (bool) required")
        registry = session.agent.registry
        if name not in registry:
            raise HTTPException(404, f"no such tool: {name!r}")
        if enabled:
            registry.enable(name)
        else:
            registry.disable(name)
        # Broadcast so every open tab's chip count follows along.
        session.broadcast({"type": "state", **session.state()})
        return session.state()

    # ---- skills ---------------------------------------------------------------

    def require_skills() -> Any:
        skills = getattr(session.agent, "skills", None)
        if skills is None:
            raise HTTPException(400, "skills are off for this session")
        return skills

    @app.get("/api/skills")
    def skills_list() -> dict[str, Any]:
        """The skills panel's data: the set, what broke, what was loaded."""
        require_ready()
        return require_skills().describe()

    @app.get("/api/skills/{name}")
    def skill_body(name: str) -> dict[str, Any]:
        """One skill's instructions, for reading in the panel. Reading your
        own file should not cost a model turn -- same rule as /skills NAME
        in the REPL, so this does NOT mark the skill as loaded."""
        require_ready()
        skill = require_skills().get(name)
        if skill is None:
            raise HTTPException(404, f"no such skill: {name!r}")
        return {"name": skill.name, "description": skill.description,
                "source": skill.source, "path": str(skill.path),
                "allowed_tools": list(skill.allowed_tools), "body": skill.body,
                # The delegated shape too: the editor PUTs back everything
                # it was given, so a field missing here is a field silently
                # erased on the next save.
                "mode": skill.mode, "output_format": skill.output_format,
                "max_iterations": skill.max_iterations}

    @app.post("/api/skills")
    async def skills_toggle(req: Request) -> dict[str, Any]:
        """Enable/disable one skill by exact name -- the panel's switch, and
        the twin of POST /api/tools. It DOES take require_idle(), which the
        tools toggle deliberately does not: the roster is a system-prompt
        layer, and rewriting the prompt under a running turn changes the
        request in flight."""
        require_ready()
        require_idle()
        skills = require_skills()
        body = await req.json()
        name, enabled = body.get("name"), body.get("enabled")
        if not isinstance(name, str) or not isinstance(enabled, bool):
            raise HTTPException(400, "name (str) and enabled (bool) required")
        if not (skills.enable(name) if enabled else skills.disable(name)):
            raise HTTPException(404, f"no such skill: {name!r}")
        skills.reapply()
        session.broadcast({"type": "state", **session.state()})
        return session.state()

    @app.put("/api/skills/{name}")
    async def skill_write(name: str, req: Request) -> dict[str, Any]:
        """Create or update a skill from the panel's editor.

        This is a HUMAN writing a file in their own project, which is the
        same trust level /api/mcp add already assumes (that one can start
        an arbitrary process). It is still narrow on purpose: the name is
        re-validated against the loader's grammar, so nothing here can
        address a path outside a skills root, and the text is parsed
        before it is written -- the editor cannot persist a broken skill.

        require_idle for the same reason reload takes it: this rewrites
        the roster, which is part of the system prompt.
        """
        require_ready()
        require_idle()
        skills = require_skills()
        body = await req.json()
        if not isinstance(body.get("body"), str) or \
                not isinstance(body.get("description"), str):
            raise HTTPException(400, "description (str) and body (str) required")
        tools = body.get("allowed_tools") or []
        if not isinstance(tools, list) or \
                not all(isinstance(t, str) for t in tools):
            raise HTTPException(400, "allowed_tools must be a list of strings")
        try:
            skill = skills.write(
                name,
                body["description"],
                body["body"],
                mode=str(body.get("mode") or "inline"),
                allowed_tools=tools,
                output_format=str(body.get("output_format") or ""),
                max_iterations=int(body.get("max_iterations") or 0),
            )
        except SkillError as exc:
            # The editor's whole job is showing this back to the author.
            raise HTTPException(422, str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(400, f"could not write skill: {exc}") from exc
        session.broadcast({"type": "state", **session.state()})
        return {"name": skill.name, "path": str(skill.path),
                **session.state()}

    @app.post("/api/learn")
    def learn() -> dict[str, Any]:
        """Save what the last turn did as a skill -- the page's ``/learn``.
        The offer arrives over the socket like any other question."""
        require_ready()
        require_idle()
        require_skills()
        session.start_learn()
        return {"ok": True}

    @app.post("/api/skills/{name}/tool")
    def skills_promote(name: str) -> dict[str, Any]:
        """Make a learned skill's script a tool of its own. The proposal
        arrives over the socket as a ``tool_offer`` question; nothing is
        written until the person says make it."""
        require_ready()
        require_idle()
        require_skills()
        session.start_promote(name)
        return {"ok": True}

    @app.post("/api/skills/reload")
    def skills_reload() -> dict[str, Any]:
        """Re-scan every root after editing a SKILL.md. require_idle: the
        roster is a system-prompt layer, and rewriting the prompt under a
        running turn changes the request mid-flight."""
        require_ready()
        require_idle()
        require_skills().reload()
        session.broadcast({"type": "state", **session.state()})
        return session.state()

    # ---- memory about the person (memory/) -----------------------------------

    def require_memory() -> Any:
        require_ready()
        memory = getattr(session.agent, "memory", None)
        if memory is None:
            raise HTTPException(400, "memory is off for this session "
                                "(--memory off, a package that did not ask, "
                                "or no identity -- set YANTRA_USER)")
        return memory

    def memory_view(memory: Any) -> dict[str, Any]:
        try:
            # A store that cannot list (memory/mcp.py) still opens the
            # panel: empty, with ``cannot`` saying why.
            items = memory.list(200) if "list" not in memory.cannot() else []
        except Exception as exc:  # the store's failure, said plainly
            raise HTTPException(503, f"memory ({memory.store.name}): {exc}") from None
        shown = {item.id for item in memory.in_prompt}
        return {**memory.describe(),
                "items": [{"id": i.id, "statement": i.statement,
                           "created": i.created, "package": i.package,
                           "kind": i.kind, "in_prompt": i.id in shown}
                          for i in items]}

    @app.get("/api/memory")
    def memory_list() -> dict[str, Any]:
        """The memory panel: everything remembered about this person, and
        which of it this conversation's prompt carries. No model turn."""
        return memory_view(require_memory())

    @app.post("/api/memory/forget")
    async def memory_forget(req: Request) -> dict[str, Any]:
        """Delete one. Takes effect from the next conversation: the prompt
        layer is filled once, so the cached prefix is not rewritten."""
        memory = require_memory()
        memory_id = str((await req.json()).get("id") or "").strip()
        if not memory_id:
            raise HTTPException(400, "which memory? send its id")
        try:
            forgotten = memory.forget(memory_id)
        except Exception as exc:  # no forget verb, or the store is down
            raise HTTPException(503, f"memory ({memory.store.name}): {exc}") from None
        if not forgotten:
            raise HTTPException(404, f"no memory #{memory_id}")
        return memory_view(memory)

    @app.post("/api/memory/add")
    async def memory_add(req: Request) -> dict[str, Any]:
        """The person telling it something directly -- their words, no
        model in between, so no approval either."""
        from yantra.memory import MemoryStoreError
        memory = require_memory()
        statement = str((await req.json()).get("statement") or "")
        try:
            memory.remember(statement, kind="fact")
        except MemoryStoreError as exc:
            raise HTTPException(400, str(exc)) from None
        return memory_view(memory)

    @app.post("/api/memory/reflect")
    async def memory_reflect(req: Request) -> dict[str, Any]:
        """Look back over this conversation (memory/reflect.py).

        ``ending`` is the page ending a conversation -- clear, or restoring
        another -- and follows --reflect: off looks at nothing, auto keeps
        what it finds. Without it this is the person's button, which looks
        whatever the mode and always asks. The candidates come back to be
        shown; nothing is kept until /api/memory/keep.
        """
        from yantra.memory.reflect import keep, reflect
        require_idle()
        memory = require_memory()
        ending = bool((await req.json()).get("ending"))
        if ending and memory.reflect == "off":
            return {**memory_view(memory), "candidates": [], "kept": 0}
        found, memory.pending = list(memory.pending), []
        try:
            found += [c for c in await asyncio.to_thread(reflect, session.agent)
                      if c not in found]
        except Exception as exc:
            raise HTTPException(503, f"looking back failed: {exc}") from None
        if ending and memory.reflect == "auto":
            kept = len(keep(memory, found))
            return {**memory_view(memory), "candidates": [], "kept": kept}
        return {**memory_view(memory), "kept": 0,
                "candidates": [{"statement": c.statement, "kind": c.kind}
                               for c in found]}

    @app.post("/api/memory/keep")
    async def memory_keep(req: Request) -> dict[str, Any]:
        """The person's answer to a look back: what to keep, what to drop.
        Dropped ones are not offered again this session."""
        from yantra.memory.reflect import Candidate, decline, keep
        memory = require_memory()
        body = await req.json()

        def candidates(key: str) -> list[Candidate]:
            return [Candidate(str(c.get("statement") or ""),
                              str(c.get("kind") or "fact"))
                    for c in body.get(key) or [] if isinstance(c, dict)]
        decline(memory, candidates("drop"))
        try:
            kept = keep(memory, [c for c in candidates("keep") if c.statement])
        except Exception as exc:
            raise HTTPException(400, f"memory ({memory.store.name}): {exc}") from None
        return {**memory_view(memory), "kept": len(kept)}

    # ---- connections, through Setu (notes/99) --------------------------------

    #: A page opened on this computer. Only there can a sign-in's redirect
    #: (to a port Setu opens on THIS machine) come back to it.
    LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost")

    def is_local(req: Request) -> bool:
        return bool(req.client) and req.client.host in LOCAL_HOSTS

    def require_setu() -> Any:
        require_ready()
        setu = getattr(session.agent, "setu", None)
        if setu is None:
            raise HTTPException(400, "Setu is off for this session (--no-setu)")
        return setu

    def setu_program(setu: Any) -> str:
        program = setu.program
        if not program:
            raise HTTPException(400, setu.error or "Setu was not found: install it, or "
                                "start Yantra with --setu /path/to/setu")
        return program

    @app.get("/api/connections")
    def connections_list(req: Request) -> dict[str, Any]:
        """The Connections panel's data -- Setu's report, never a key."""
        require_ready()
        return session.connections_state(local=is_local(req))

    @app.post("/api/connections/refresh")
    def connections_refresh(req: Request) -> dict[str, Any]:
        """Ask Setu again: a connection made in a terminal shows up, and its
        tools with it. require_idle: the tools and prompt change."""
        require_idle()
        require_setu()
        sync = session.setu_sync()
        return {**session.connections_state(local=is_local(req)), "sync": sync}

    @app.post("/api/connections/connect")
    async def connections_connect(req: Request) -> dict[str, Any]:
        """Start a sign-in. Only from a page on this computer; anywhere else
        the answer is the command to run here instead."""
        setu = require_setu()
        body = await req.json()
        connector = str(body.get("connector", ""))
        account = str(body.get("account", "") or "personal").strip()
        level = str(body.get("level", ""))
        known = setu.link.connectors if setu.link is not None else {}
        spec = known.get(connector)
        if spec is None:
            raise HTTPException(404, f"no installed connector {connector!r}")
        if level not in [lv["name"] for lv in spec.get("levels", [])]:
            raise HTTPException(400, f"{connector} has no access level {level!r}")
        command = f"setu connect {connector} --as {account} --level {level}"
        if not is_local(req):
            raise HTTPException(403, "signing in only works from a page on the computer "
                                     f"running Yantra -- there, run: {command}")
        if not spec.get("ready", True):
            raise HTTPException(409, spec.get("not_ready") or "not ready to sign in")
        if session.signin is not None and session.signin.running:
            raise HTTPException(409, f"a sign-in to {session.signin.ref} is already "
                                     "waiting -- finish or cancel it first")
        setu_program(setu)
        from yantra.setu_link import SetuLinkError
        try:
            session.start_signin(connector, account, level)
        except SetuLinkError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True, "ref": session.signin.ref}

    @app.post("/api/connections/cancel")
    def connections_cancel() -> dict[str, Any]:
        require_ready()
        if session.signin is not None:
            session.signin.cancel()
        return {"ok": True}

    @app.post("/api/connections/disconnect")
    async def connections_disconnect(req: Request) -> dict[str, Any]:
        """Revoke and forget one connection (Setu does both), then pull its
        server and tools. require_idle, like every tool-list change."""
        require_idle()
        setu = require_setu()
        ref = str((await req.json()).get("ref", ""))
        if ref not in [row["ref"] for row in (setu.link.connections if setu.link else [])]:
            raise HTTPException(404, f"no connection {ref!r}")
        from yantra.setu_link import run_setu
        ok, said = await run_in_threadpool(run_setu, setu_program(setu), "disconnect", ref)
        if not ok:
            raise HTTPException(502, said or "setu disconnect failed")
        sync = await run_in_threadpool(session.setu_sync)
        return {**session.connections_state(local=is_local(req)), "said": said,
                "sync": sync}

    @app.post("/api/connections/client-file")
    async def connections_client_file(req: Request) -> dict[str, Any]:
        """Tell Setu where the Google OAuth client file is. Setu checks it
        and remembers the PATH; the file is never read by Yantra."""
        require_idle()
        setu = require_setu()
        path = str((await req.json()).get("path", "")).strip()
        if not path or path.startswith("-"):
            raise HTTPException(400, "give the path to the client file")
        from yantra.setu_link import run_setu
        ok, said = await run_in_threadpool(run_setu, setu_program(setu), "config",
                                           "client-file", path)
        if not ok:
            raise HTTPException(400, said.splitlines()[-1] if said else "setu refused it")
        await run_in_threadpool(session.setu_sync)
        return session.connections_state(local=is_local(req))

    # ---- MCP server management ----------------------------------------------

    def require_mcp() -> Any:
        require_ready()
        if session.mcp is None:
            raise HTTPException(400, "this session has no mcp manager")
        return session.mcp

    @app.get("/api/mcp")
    def mcp_list() -> dict[str, list[dict[str, Any]]]:
        """The servers section of the tools panel: transport, liveness,
        tool counts (incl. how many are soft-disabled), remembered flag."""
        return {"servers": require_mcp().servers()}

    @app.post("/api/mcp/toggle")
    async def mcp_toggle(req: Request) -> dict[str, Any]:
        """Soft-switch one server's whole toolset. Deliberately NO
        require_idle(), same argument as /api/tools: the disabled set is
        consulted per call, so cutting a server off lands on its very
        next call of a running turn. The process stays warm."""
        mcp = require_mcp()
        body = await req.json()
        name, enabled = body.get("name"), body.get("enabled")
        if not isinstance(name, str) or not isinstance(enabled, bool):
            raise HTTPException(400, "name (str) and enabled (bool) required")
        try:
            mcp.set_enabled(name, enabled)
        except Exception as exc:  # unknown server (MCPError) -> clean 404
            raise HTTPException(404, str(exc)) from exc
        session.broadcast({"type": "state", **session.state()})
        return session.state()

    @app.post("/api/mcp/add")
    async def mcp_add(req: Request) -> dict[str, Any]:
        """Connect a new server mid-session. Two shapes:

        * fields -- {name, command|url, args?, env?, headers?, remember?}:
          what the panel's simple form sends. ``headers`` is http-only
          (the stdio equivalent is ``env``) and its values may reference
          the environment as ``${VAR}``;
        * {config: "<json>"} -- the panel's paste-JSON mode, same
          ``{"servers": {...}}`` syntax as --mcp-config files; may add
          several at once.

        DOES take require_idle(): connecting spawns processes and
        mutates the registry, which must not interleave with a turn's
        batch iteration. Per-server results come back so one bad entry
        doesn't hide the others."""
        from yantra.mcp import MCPServerConfig, parse_mcp_text

        require_idle()
        mcp = require_mcp()
        body = await req.json()
        results: list[dict[str, Any]] = []

        def attempt(cfg) -> None:
            try:
                names = mcp.connect(cfg, remember=bool(body.get("remember")))
                results.append({"name": cfg.name, "ok": True,
                                "tools": len(names)})
            except Exception as exc:
                results.append({"name": cfg.name, "ok": False,
                                "error": str(exc)})

        if isinstance(body.get("config"), str):
            try:
                configs = parse_mcp_text(body["config"])
            except Exception as exc:  # MCPError from the shared parser
                raise HTTPException(400, str(exc)) from exc
            for cfg in configs:
                attempt(cfg)
        else:
            name = body.get("name")
            command, url = body.get("command"), body.get("url")
            if not isinstance(name, str) or not name.strip():
                raise HTTPException(400, "name is required")
            if (command is None) == (url is None):
                raise HTTPException(400, "exactly one of 'command' (stdio)"
                                         " or 'url' (http) is required")
            args = body.get("args") or []
            env = body.get("env") or None
            headers = body.get("headers") or None
            if not isinstance(args, list) or \
                    not all(isinstance(a, str) for a in args):
                raise HTTPException(400, "args must be a list of strings")
            if env is not None and (not isinstance(env, dict) or not all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in env.items())):
                raise HTTPException(400, "env must map strings to strings")
            if headers is not None and (
                    not isinstance(headers, dict) or not all(
                        isinstance(k, str) and isinstance(v, str)
                        for k, v in headers.items())):
                raise HTTPException(400,
                                    "headers must map strings to strings")
            if headers and url is None:
                raise HTTPException(400, "headers need a 'url' -- a stdio "
                                         "server takes 'env' instead")
            attempt(MCPServerConfig(
                name=name.strip(),
                command=command if command is None else str(command),
                args=args, url=url if url is None else str(url),
                env=env, headers=headers))
        session.broadcast({"type": "state", **session.state()})
        return {"results": results, **session.state()}

    @app.post("/api/mcp/login")
    async def mcp_login(req: Request) -> dict[str, Any]:
        """Run the OAuth walk for one server, then reconnect it.

        Takes require_idle() for the same reason /add does: it ends by
        re-registering the server's tools, and the browser step can sit
        for minutes while a human types a password.

        The browser opens on the machine running the SERVER, which is the
        same machine as the panel in the localhost case this UI is built
        for. When it isn't, ``authorize_url`` comes back in the response
        so the operator can open it themselves.
        """
        from yantra.mcp import (MCPAuthRequired, MCPError, MCPHttpSession,
                                 MCPServerConfig)
        from yantra.mcp_oauth import MCPAuthError, forget_token, login

        require_idle()
        mcp = require_mcp()
        body = await req.json()
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(400, "name is required")
        name = name.strip()
        # The common case is a server that 401'd and therefore never
        # connected: it has no session and no row, so the url has to
        # arrive with the request rather than be looked up.
        session_for_name = mcp.sessions.get(name)
        if session_for_name is not None:
            config = session_for_name.config
        else:
            url = body.get("url")
            if not isinstance(url, str) or not url.strip():
                raise HTTPException(
                    404, f"{name!r} is not connected, so its url must come "
                         "with the request")
            config = MCPServerConfig(name=name, url=url.strip())
        if not config.url:
            raise HTTPException(400, f"{name!r} is a stdio server -- there "
                                     "is nothing to log in to; its "
                                     "credentials belong in 'env'")

        # The challenge must come FROM the server: it names the metadata
        # document, and guessing that URL is how clients break when a
        # vendor moves it.
        probe = MCPHttpSession(config, timeout=20.0)
        try:
            probe.start()
        except MCPAuthRequired as exc:
            challenge = exc.challenge
        except MCPError as exc:
            raise HTTPException(502, str(exc)) from None
        else:
            return {"ok": True, "already": True, **session.state()}
        finally:
            probe.close()

        shown: list[str] = []
        try:
            await run_in_threadpool(
                login, name, config.url, challenge,
                on_url=shown.append)
        except MCPAuthError as exc:
            return {"ok": False, "error": str(exc),
                    "authorize_url": shown[0] if shown else None,
                    **session.state()}

        # Reconnect so the tools register against the fresh token.
        if mcp.sessions.get(name) is not None:
            try:
                mcp.disconnect(name)
            except MCPError:
                pass
        try:
            names = mcp.connect(config)
        except MCPError as exc:
            forget_token(name)  # a token that cannot connect is noise
            return {"ok": False, "error": str(exc), **session.state()}
        session.broadcast({"type": "state", **session.state()})
        return {"ok": True, "tools": len(names), **session.state()}

    @app.post("/api/mcp/remove")
    async def mcp_remove(req: Request) -> dict[str, Any]:
        """Disconnect one server and pull its tools (a saved entry is
        forgotten too). require_idle() like add: closing transports and
        unregistering must not race a running turn."""
        from yantra.mcp import MCPError

        require_idle()
        mcp = require_mcp()
        name = (await req.json()).get("name")
        if not isinstance(name, str):
            raise HTTPException(400, "name (str) required")
        try:
            removed = mcp.disconnect(name)
        except MCPError as exc:
            raise HTTPException(404, str(exc)) from exc
        session.broadcast({"type": "state", **session.state()})
        return {"removed": removed, **session.state()}

    @app.get("/api/turns")
    def turns_list() -> dict[str, Any]:
        """The turns panel's data (notes/81). Readable mid-turn: a read of
        an append-only file races nothing, and the recorder's line lands
        whole or not at all."""
        if session.trace is None:
            raise HTTPException(400, "nothing is being recorded -- start "
                                     "the server with --trace FILE")
        if not session.trace.path.exists():
            # --trace names a file the first turn creates: before then
            # there is simply nothing recorded yet, which is not an error.
            return {"path": str(session.trace.path), "total": 0,
                    "flagged": 0, "unreadable": 0, "turns": []}
        try:
            return session.turns()
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/mark")
    async def mark(req: Request) -> dict[str, Any]:
        """A person's verdict on a recorded turn (notes/77). Idle only:
        the recorder writes as a turn ends, and a rewrite racing an append
        is exactly the swap that must not lose a line."""
        require_idle()
        if session.trace is None:
            raise HTTPException(400, "nothing is being recorded -- start "
                                     "the server with --trace FILE")
        body = await req.json()
        trace_id, verdict = body.get("id"), body.get("verdict")
        if not isinstance(trace_id, str) or not trace_id:
            raise HTTPException(400, "id is required")
        if verdict not in ("good", "bad", "clear"):
            raise HTTPException(400, "verdict must be good, bad or clear")
        why = body.get("why")
        why = why.strip() if isinstance(why, str) and why.strip() else None
        try:
            return session.mark(trace_id, verdict, why)
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/save")
    async def save(req: Request) -> dict[str, Any]:
        require_idle()
        if session.store is None:
            raise HTTPException(500, "no session store configured")
        name = (await req.json()).get("name") or "default"
        version = session.store.save(session.agent,
                                     provider_name=session.agent.provider.name,
                                     session_id=name)
        return {"saved": name, "version": version}

    @app.post("/api/load")
    async def load(req: Request) -> dict[str, Any]:
        require_idle()
        if session.store is None:
            raise HTTPException(500, "no session store configured")
        name = (await req.json()).get("name") or "default"
        payload = session.store.load_latest(name)
        # A hold belongs to the conversation being replaced (notes/88).
        session.agent.held = None
        if payload is None:
            raise HTTPException(404, f"no checkpoint named '{name}'")
        try:
            apply_payload(session.agent, payload,
                          settings_loader=settings_loader or load_settings,
                          provider_factory=provider_factory or get_provider)
        except Exception as exc:
            raise HTTPException(400, f"restore failed: {exc}") from exc
        # Same rule as the REPL's load path: checkpoints store the COMPOSED
        # system, so rebuild it from the live prompt layers.
        recompose(session.agent)
        mark_reviewed(session.agent)
        return session.state()

    @app.post("/api/compact")
    def compact() -> dict[str, Any]:
        require_idle()
        stats = session.agent.compact()
        return {"stats": stats, **session.state()}

    @app.post("/api/clear")
    def clear() -> dict[str, Any]:
        require_idle()
        session.agent.held = None     # nothing left to resume it into
        session.agent.history.clear()
        return session.state()

    return app


def launch(session: WebSession, agent: Agent, store: SessionStore | None,
           *, host: str = "127.0.0.1", port: int = 8321,
           mcp: Any | None = None) -> int:
    """Blocking entrypoint for `yantra --web`: attach, banner, serve."""
    import uvicorn

    session.attach(agent, store, mcp=mcp)
    app = make_app(session)
    servers = f" · mcp servers={len(mcp.sessions)}" if mcp else ""
    recording = (f"  recording turns -> {session.trace.path} "
                 f"({session.trace.label})\n" if session.trace else "")
    waiting = (f"  approvals: {session.wait_budget:g}s of waiting per turn, "
               f"then {session.on_timeout}\n"
               if session.wait_budget is not None else "")
    print(f"\n  yantra web UI -> http://{host}:{port}\n"
          f"  provider={agent.provider.name} · model={agent.model} · "
          f"tools={len(agent.registry)}{servers} · cwd={agent.ctx.cwd}\n"
          f"{recording}{waiting}"
          "  ctrl-c stops the server\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0
