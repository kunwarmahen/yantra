"""The permission gate -- how a harness asks a human before acting.

A gate is just ``Callable[[PermissionRequest], bool]``. Keeping it a
plain function (not a class hierarchy) is deliberate: the CLI supplies
one that renders a rich y/n/e prompt; tests supply lambdas.

Every dangerous call produces EXACTLY one gate invocation, and whatever
it answers becomes data: denied calls turn into ``is_error`` ToolResults
the model can read and react to ("the user said no, try another way"),
never into exceptions.

Approve-with-edits: a gate may REPLACE ``request.arguments`` before
answering True (amend the bash command, fix the path). The agent loop
notices the swap (identity check) and adopts the edited dict as the
call's arguments -- so what runs is what was approved, edits included.
``summarize`` lets an editing UI re-render the preview for the amended
args without knowing anything about the tool.

Denial-with-a-reason is the same move in the other direction: a gate may
write ``request.reason`` before answering False, and that sentence is
what the model reads instead of "Permission denied by user." A gate with
no human behind it -- ``allow_read_only`` in a headless run, a policy in
a service -- was otherwise telling the model something untrue about why
it was refused, and the model cannot route around a reason it was given
wrong.

A gate may also be ASYNC. ``adecide`` awaits an awaitable answer, so a
gate that has to reach a human over a channel suspends instead of
blocking the event loop and every other conversation on it. ``decide``
is the synchronous twin, and it REFUSES an awaitable rather than
approving one: a coroutine object is truthy, so the obvious "just call
it" would auto-approve every dangerous call in the session.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any
from collections.abc import Awaitable, Callable

#: The two runtime modes a session can sit in. "ask" defers to whatever
#: gate the frontend supplied (y/n/e terminal prompt, browser modal);
#: "yolo" approves everything.
MODES = ("ask", "yolo")


@dataclass(slots=True)
class PermissionRequest:
    """Everything a human needs to judge one tool call.

    Mutable ON PURPOSE, within two narrow contracts: gates may rewrite
    ``arguments`` (and refresh ``summary``) while deciding, and may write
    ``reason`` to explain a refusal. Everything else about the request is
    the loop's business.
    """

    tool_name: str
    arguments: dict[str, Any]
    summary: str  # built by the tool itself: the literal command / diff
    read_only: bool
    #: tool.summary(args, ctx) with the context pre-bound by the loop, so
    #: an edit-and-reapprove UI can re-render the preview for amended args.
    #: None => the UI falls back to showing raw JSON.
    summarize: Callable[[dict[str, Any]], str] | None = None
    #: Why the gate answered the way it did, in a sentence addressed to
    #: the MODEL. Only a refusal's reason is ever used -- it replaces the
    #: default denial text in the error ToolResult. None => the default.
    reason: str | None = None


#: A gate receives one request per dangerous call; True => allow. The
#: answer may be awaited: a gate that asks a human over a channel returns
#: a coroutine, and only ``adecide`` (so only AsyncAgent) can take one.
PermissionFn = Callable[[PermissionRequest], bool | Awaitable[bool]]

#: What the model is told when a gate says no and offers no reason. Kept
#: here rather than in either agent so the two twins cannot drift.
DENIED = "Permission denied by user."


def denial_text(request: PermissionRequest) -> str:
    """The sentence the MODEL reads for a refused call."""
    return request.reason or DENIED


def allow_read_only(request: PermissionRequest) -> bool:
    """Auto-approve tools that declared read_only; deny everything else.

    The default gate, which means it is what a HEADLESS run gets: no
    human was asked, so the refusal says so rather than blaming a user
    who was never there.
    """
    if request.read_only:
        return True
    request.reason = (
        f"{request.tool_name} was denied: this session runs "
        f"unattended and auto-approves read-only tools only. Nobody is "
        f"available to ask.")
    return False


def yolo(request: PermissionRequest) -> bool:
    """Allow everything (--yolo). You trust the model; you accept the risk."""
    return True


def deny_all(request: PermissionRequest) -> bool:
    """Deny everything. Useful for dry-runs and tests."""
    request.reason = (f"{request.tool_name} was denied: this session "
                      f"denies every tool call.")
    return False


def decide(gate: PermissionFn, request: PermissionRequest) -> bool:
    """Put one request to ``gate`` from SYNCHRONOUS code.

    AN AWAITABLE ANSWER IS AN ERROR, NOT AN APPROVAL. A coroutine object
    is truthy, so a plain ``bool(gate(request))`` here would approve every
    dangerous call in the session the moment someone handed Agent an
    ``async def`` gate -- silently, and in the one place where silence is
    most expensive. Raising lands in the loop's own handler, which turns
    it into a refused call the model can read. The coroutine is closed
    first so the failure is one error, not an error plus a warning about
    a coroutine nobody awaited.
    """
    answer = gate(request)
    if inspect.isawaitable(answer):
        close = getattr(answer, "close", None)
        if callable(close):
            close()
        name = getattr(gate, "__qualname__", None) or type(gate).__name__
        raise TypeError(
            f"permission gate {name} answered with an awaitable; a gate "
            f"that suspends needs AsyncAgent (Agent is synchronous and "
            f"cannot await one)")
    return bool(answer)


async def adecide(gate: PermissionFn, request: PermissionRequest) -> bool:
    """Put one request to ``gate`` from a COROUTINE, awaiting if it suspends.

    Awaitable-TOLERANT, not async-only: every plain function written
    against the old signature keeps working unchanged, which is what
    lets one gate serve both agents and both frontends.
    """
    answer = gate(request)
    if inspect.isawaitable(answer):
        answer = await answer
    return bool(answer)


def trust_sandbox(inner: PermissionFn, sandbox) -> PermissionFn:
    """Auto-approve bash ONLY while it runs in a kernel-confined sandbox.

    The bridge between the two orthogonal layers (book ch14): gates
    decide, sandboxes contain -- this decorator lets containment EARN
    approval for exactly one tool, delegating everything else to
    ``inner`` unchanged. Reads ``sandbox.confined`` (True only for
    BwrapSandbox), so a downgrade to SubprocessSandbox silently restores
    prompting: convenience confinement never counts as trust.

    Wrappers like this one PASS THE ANSWER THROUGH without inspecting it,
    which is the whole reason an async gate needed no change here: an
    awaitable travels out to ``adecide`` intact.
    """
    def gate(request: PermissionRequest) -> bool | Awaitable[bool]:
        if request.tool_name == "bash" and sandbox.confined:
            return True
        return inner(request)
    return gate


@dataclass
class SwitchableGate:
    """A gate whose policy flips between "ask" and "yolo" MID-SESSION.

    The CLI wires one of these around the frontend's real ask-gate
    (confirm prompt or browser modal), so /yolo in the REPL and the mode
    chip in the web UI are just ``set_mode`` calls -- the agent loop is
    untouched, it keeps calling this object like any other gate. A flip
    mid-turn simply applies to every not-yet-approved call afterwards;
    the plain attribute read needs no lock (one writer, GIL-atomic).

    Kept callable rather than adding a mode parameter to Agent on
    purpose: gates are plain functions by design here, and embedders who
    pass a bare function get today's fixed-gate behavior unchanged.
    """

    ask: PermissionFn  # what runs while mode == "ask"
    mode: str = "ask"

    def __call__(self, request: PermissionRequest) -> bool | Awaitable[bool]:
        if self.mode == "yolo":
            return yolo(request)
        return self.ask(request)  # may be awaitable; passed through untouched

    def set_mode(self, mode: str) -> None:
        """Flip the policy; unknown names are a loud error, not a silent ask."""
        if mode not in MODES:
            raise ValueError(f"unknown permission mode {mode!r} "
                             f"(want one of {', '.join(MODES)})")
        self.mode = mode

    def toggle(self) -> str:
        """ask -> yolo -> ask ...; returns the mode now in force."""
        self.set_mode("ask" if self.mode == "yolo" else "yolo")
        return self.mode
