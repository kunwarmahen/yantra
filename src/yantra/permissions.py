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

Beside the reason, every refusal carries a CODE -- a short machine token
naming the cause. The reason is prose for the model; the code is a token
for whoever wired the gates up, and the two answer different questions.
"nobody answered in time" and "your policy forbids this" produce the same
shape of English and demand different handling, and a caller that had to
tell them apart by matching on a sentence would break the day someone
improved the wording. The codes here are constants; the field is a plain
string, so a host naming its own refusals needs no patch to this module.

And a gate may carry a DEADLINE. ``with_deadline`` puts a clock on a gate
that suspends, and it will not be built without being told what happens
when the clock runs out -- ``on_timeout`` has no default. A deadline is a
stopwatch, which is the library's business; what the silence MEANS is a
policy, which is the conversation owner's. Bundling the two would have
this module quietly deciding that unanswered means no.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from dataclasses import dataclass
from typing import Any
from collections.abc import Awaitable, Callable

#: The two runtime modes a session can sit in. "ask" defers to whatever
#: gate the frontend supplied (y/n/e terminal prompt, browser modal);
#: "yolo" approves everything.
MODES = ("ask", "yolo")

#: The refusal codes this harness issues. A code is a machine token, not
#: a sentence: callers branch on it (a service retries a timeout and does
#: not retry a policy), and prose that improves would break that branch.
#: The field is a plain ``str`` -- these are the ones shipped here, and a
#: host with refusals of its own names them without touching this module.
REFUSED_USER = "user"  # a human was asked and said no
REFUSED_UNATTENDED = "unattended"  # nobody was there to ask
REFUSED_TIMEOUT = "timeout"  # somebody was asked and did not answer in time
REFUSED_POLICY = "policy"  # a rule refused before any human saw it
REFUSED_UNSPECIFIED = "unspecified"  # a gate said no and named no cause
REFUSED_OUT_OF_TIME = "out_of_time"  # this turn's budget for waiting was gone
                                     # before the question could be asked

#: What a code is allowed to look like. Lower-case identifier-ish, so it
#: can be a dict key, a database column value and a metric label without
#: anybody quoting it.
_CODE = re.compile(r"[a-z][a-z0-9_]*\Z")


@dataclass(slots=True)
class PermissionRequest:
    """Everything a human needs to judge one tool call.

    Mutable ON PURPOSE, within two narrow contracts: gates may rewrite
    ``arguments`` (and refresh ``summary``) while deciding, and may write
    ``reason`` and ``code`` to explain a refusal. Everything else about
    the request is the loop's business.

    ``call_id`` is the loop's business in the other direction: it is the
    only thing here that lets a host correlate a DECISION with the
    ``ToolExecuted`` that decision produced. Everything else describes
    what is being asked; this says which asking it is.
    """

    tool_name: str
    arguments: dict[str, Any]
    summary: str  # built by the tool itself: the literal command / diff
    read_only: bool
    #: The id of the ``ToolCall`` being decided, so a gate's answer can be
    #: matched to the ``ToolExecuted`` it produces. A host that only
    #: approves or refuses needs nothing from this; a host that RECORDS
    #: what happened does.
    #:
    #: NOT BECAUSE ORDER WOULD NOT WORK -- BECAUSE IT WOULD. Gates run
    #: once per call in submission order and ``ToolExecuted`` is emitted
    #: in submission order, so a host could count. That is three
    #: invariants of somebody else's loop (gates sequential, one gate per
    #: call, results in submission order), none of them promised to
    #: callers, and a drift in any of them does not raise -- it silently
    #: files one person's approval against a different call. An id is one
    #: string and cannot drift.
    #:
    #: Defaulted rather than required: a ``PermissionRequest`` built by a
    #: test or by a host driving a gate directly is still a valid one, and
    #: "" reads as what it is -- no call behind this.
    call_id: str = ""
    #: Which TURN this call belongs to. The gate layer has no other way to
    #: know: it sees a stream of questions and nothing marks where one
    #: thing the agent was asked to do ends and the next begins. Told for
    #: the same reason ``call_id`` is told -- a wrapper could infer turns
    #: from timing or from call ordering, and inferring silently produces
    #: a budget that resets at the wrong moment rather than an error.
    #:
    #: "" is a request with no turn behind it (a test, a host driving a
    #: gate directly). ``with_wait_budget`` treats each of those as its
    #: own turn, because letting an unstamped question eat a real turn's
    #: allowance would be worse than not budgeting it at all.
    turn_id: str = ""
    #: tool.summary(args, ctx) with the context pre-bound by the loop, so
    #: an edit-and-reapprove UI can re-render the preview for amended args.
    #: None => the UI falls back to showing raw JSON.
    summarize: Callable[[dict[str, Any]], str] | None = None
    #: Why the gate answered the way it did, in a sentence addressed to
    #: the MODEL. Only a refusal's reason is ever used -- it replaces the
    #: default denial text in the error ToolResult. None => the default.
    reason: str | None = None
    #: The same answer as a machine token, addressed to whoever WIRED the
    #: gates up rather than to the model. Never shown to the model, and
    #: never rendered in place of the reason: it exists so an outer gate,
    #: a renderer or a host can branch without matching on English.
    #: None => ``REFUSED_UNSPECIFIED`` (see ``denial_code``).
    code: str | None = None


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


def denial_code(request: PermissionRequest) -> str:
    """The token a CALLER reads for a refused call.

    Total, like ``denial_text``: a gate written before codes existed
    still answers something, and "unspecified" is the honest word for a
    refusal that named no cause. Never derived from the reason -- a
    guess dressed as a token is worse than an admission.
    """
    return request.code or REFUSED_UNSPECIFIED


def refuse(request: PermissionRequest, reason: str, *,
           code: str = REFUSED_POLICY) -> bool:
    """Record a refusal on ``request`` and answer no. ALWAYS returns False.

    The return value is the point: a gate ends ``return refuse(request,
    "...")`` and cannot write a reason and then accidentally approve.
    (``reason`` is advisory in the other direction too -- nothing stops a
    gate setting the attribute directly and returning True -- but nothing
    in this repo does, and the shape above is why.)

    A CODE IS A TOKEN, NOT A SENTENCE, and this is where that is
    enforced: a full stop or a capital letter in the code field means
    somebody put the explanation in the wrong argument, and the mistake
    is invisible afterwards -- it surfaces as a caller whose ``==``
    comparison silently never matches.
    """
    if not _CODE.match(code):
        raise ValueError(
            f"refusal code {code!r} is not a token; a code is matched with "
            f"== by callers (want lower-case letters, digits and "
            f"underscores -- the sentence goes in reason)")
    request.reason = reason
    request.code = code
    return False


def allow_read_only(request: PermissionRequest) -> bool:
    """Auto-approve tools that declared read_only; deny everything else.

    The default gate, which means it is what a HEADLESS run gets: no
    human was asked, so the refusal says so rather than blaming a user
    who was never there.
    """
    if request.read_only:
        return True
    return refuse(
        request,
        f"{request.tool_name} was denied: this session runs "
        f"unattended and auto-approves read-only tools only. Nobody is "
        f"available to ask.",
        code=REFUSED_UNATTENDED)


def yolo(request: PermissionRequest) -> bool:
    """Allow everything (--yolo). You trust the model; you accept the risk."""
    return True


def deny_all(request: PermissionRequest) -> bool:
    """Deny everything. Useful for dry-runs and tests."""
    return refuse(request,
                  f"{request.tool_name} was denied: this session "
                  f"denies every tool call.")


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


#: What a deadline may be told to do when it expires. Two words, and
#: neither is a default: see ``with_deadline``.
ON_TIMEOUT = ("deny", "allow")


def with_deadline(inner: PermissionFn, seconds: float, *,
                  on_timeout: str) -> PermissionFn:
    """Put a clock on a gate that suspends, and say what the silence means.

    ``on_timeout`` HAS NO DEFAULT, and that is the whole design. A
    deadline is two separate things welded together in most harnesses: a
    stopwatch, which is mechanism and belongs here, and a verdict on
    silence, which is policy and belongs to whoever owns the
    conversation. A library that shipped ``on_timeout="deny"`` as the
    default would be deciding, on everyone's behalf, that an unanswered
    question is a refusal -- true for a deploy, wrong for an overnight
    batch whose owner set the deadline precisely so it would proceed.
    So the wrapper cannot be constructed without being told.

    A DEADLINE ONLY BINDS A GATE THAT SUSPENDS. If ``inner`` answers
    inline -- any plain function, including one blocking on ``input()``
    -- the answer is already in hand by the time this wrapper sees it and
    there is nothing left to time. That is not a gap that can be closed
    from here: interrupting a blocking call means a thread and a
    cancellation story the callee never agreed to. Deadlines are for
    gates that reach a person over a channel, which are exactly the gates
    that had to become awaitable anyway.

    On expiry the inner awaitable is CANCELLED, so a gate holding a
    pending question learns its question is dead and can withdraw it.
    An outer cancellation -- the turn dropped, the connection gone --
    passes straight through as ``CancelledError`` and never becomes a
    denial, the same rule the gate itself follows: nobody said no.
    """
    if on_timeout not in ON_TIMEOUT:
        raise ValueError(f"unknown on_timeout {on_timeout!r} "
                         f"(want one of {', '.join(ON_TIMEOUT)})")
    if seconds <= 0:
        raise ValueError(
            f"a permission deadline must be positive, got {seconds!r}; "
            f"0 does not mean 'no deadline' here -- it means every "
            f"question expires before it can be answered (for no "
            f"deadline, do not wrap the gate)")

    async def wait(answer: Awaitable[bool],
                   request: PermissionRequest) -> bool:
        try:
            async with asyncio.timeout(seconds):
                return bool(await answer)
        except TimeoutError:
            if on_timeout == "allow":
                return True
            return refuse(
                request,
                f"{request.tool_name} was denied: the approval request "
                f"went unanswered for {seconds:g} seconds and expired. "
                f"Nobody refused it -- try a read-only route, or say what "
                f"you need and let the person answer later.",
                code=REFUSED_TIMEOUT)

    def gate(request: PermissionRequest) -> bool | Awaitable[bool]:
        answer = inner(request)
        if not inspect.isawaitable(answer):
            return answer  # answered inline; there was nothing to wait for
        return wait(answer, request)

    return gate


def with_wait_budget(inner: PermissionFn, seconds: float, *,
                     on_timeout: str) -> PermissionFn:
    """A ceiling on how long ONE TURN may spend waiting for a person.

    ``with_deadline`` puts a clock on each question, which is the right
    unit for the question and the wrong unit for the turn: five dangerous
    calls in one batch, a thirty-second deadline, and a turn can sit for
    two and a half minutes without anybody having refused anything. The
    person who set "thirty seconds" was describing their patience, and
    patience does not multiply by the number of things the model decided
    to try.

    So the allowance is spent DOWN across a turn. Each awaited answer is
    timed against whatever is left, and what remains becomes the next
    question's deadline. The turn is the unit because a turn is one thing
    the agent was asked to do -- the same unit the dollar ceiling uses
    (notes/34), for the same reason.

    NOTHING IS ASKED ONCE THE ALLOWANCE IS GONE. The verdict is returned
    without calling ``inner`` at all, and the refusal code says which
    happened: ``timeout`` means somebody was asked and did not answer,
    ``out_of_time`` means nobody was asked because this turn had no
    waiting left to do. Posting a question the wrapper will not wait for
    is how a person ends up answering a prompt that has already been
    decided against them.

    The tradeoff, stated: a question that would have been answered in one
    second can be refused because earlier questions in the same turn ate
    the budget. That is what a ceiling IS, and the alternative -- a fresh
    deadline per call -- is the behaviour this exists to replace.

    ``on_timeout`` has no default, for ``with_deadline``'s reason exactly:
    a stopwatch is mechanism and belongs here, a verdict on silence is
    policy and belongs to whoever owns the conversation.

    Like ``with_deadline``, this only binds a gate that SUSPENDS. An
    answer that arrives inline was never waited for, so it costs nothing
    and cannot be timed out.
    """
    if on_timeout not in ON_TIMEOUT:
        raise ValueError(f"unknown on_timeout {on_timeout!r} "
                         f"(want one of {', '.join(ON_TIMEOUT)})")
    if seconds <= 0:
        raise ValueError(
            f"a turn's waiting budget must be positive, got {seconds!r}; "
            f"0 does not mean 'no budget' here -- it means no question in "
            f"any turn is ever asked (for no budget, do not wrap the gate)")

    #: One mutable cell, closed over: which turn the remaining allowance
    #: belongs to, and how much of it is left.
    state: dict[str, Any] = {"turn": None, "left": seconds}

    def spent_out(request: PermissionRequest, code: str) -> bool:
        """The verdict, with the two causes phrased as the two things they
        are. Reached only when the allowance is gone, which is why both
        sentences can say so."""
        if on_timeout == "allow":
            return True
        asked = ("the approval request went unanswered"
                 if code == REFUSED_TIMEOUT else
                 "nobody was asked, because this turn had no waiting left")
        return refuse(
            request,
            f"{request.tool_name} was denied: {asked}. This turn may spend "
            f"{seconds:g} seconds in total waiting for approval, and that "
            f"is now spent. Nobody refused it -- try a read-only route, or "
            f"say what you need and let the person answer in their own "
            f"time.",
            code=code)

    async def wait(answer: Awaitable[bool], request: PermissionRequest,
                   allowance: float) -> bool:
        started = time.monotonic()
        try:
            async with asyncio.timeout(allowance):
                return bool(await answer)
        except TimeoutError:
            return spent_out(request, REFUSED_TIMEOUT)
        finally:
            # Deducted in a finally so a question that was CANCELLED from
            # outside -- the turn dropped, the connection gone -- still
            # costs what it actually waited. An outer cancellation is not
            # a denial and passes straight through (with_deadline's rule).
            state["left"] = max(0.0, state["left"]
                                - (time.monotonic() - started))

    def gate(request: PermissionRequest) -> bool | Awaitable[bool]:
        # An unstamped request is its own turn: see PermissionRequest.
        if request.turn_id != state["turn"] or not request.turn_id:
            state["turn"], state["left"] = request.turn_id, seconds
        if state["left"] <= 0:
            return spent_out(request, REFUSED_OUT_OF_TIME)
        answer = inner(request)
        if not inspect.isawaitable(answer):
            return answer  # answered inline; nothing was waited for
        return wait(answer, request, state["left"])

    return gate


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
