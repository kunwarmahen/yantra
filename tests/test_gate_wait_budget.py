"""A ceiling on how long ONE TURN may keep somebody waiting.

The bias here is that a per-call deadline reads like a promise it does
not make. Somebody who writes "thirty seconds" is describing their own
patience; a turn with five dangerous calls then waits two and a half
minutes, and nobody refused anything. The tests below spend the
allowance down across several questions and assert the total rather than
the individual waits.

Two failures specifically designed against:

* **Asking a question nobody will wait for.** Once the allowance is
  gone, ``inner`` must not be called at all -- a person answering a
  prompt that was already decided against them is worse than a refusal.
  The tests count invocations, not verdicts.
* **A budget that leaks across turns.** The wrapper resets when the turn
  id changes, and an UNSTAMPED request is its own turn, so a host
  driving a gate directly can never eat a real turn's allowance.

Every refusal here is also checked for its CODE: "timeout" means
somebody was asked and did not answer; "out_of_time" means nobody was
asked. Those demand different handling and cannot be told apart from
prose.
"""

from __future__ import annotations

import asyncio

import pytest

from yantra.permissions import (
    REFUSED_OUT_OF_TIME,
    REFUSED_TIMEOUT,
    PermissionRequest,
    adecide,
    denial_code,
    with_wait_budget,
)


def request(turn: str = "t1", tool_name: str = "bomb") -> PermissionRequest:
    return PermissionRequest(tool_name=tool_name, arguments={},
                             summary=f"{tool_name}()", read_only=False,
                             turn_id=turn)


def slow_gate(delay: float, answer: bool = True, log: list | None = None):
    """A gate that reaches a person over a channel -- i.e. one that
    suspends, which is the only kind a deadline can bind."""
    async def answer_later(request):
        if log is not None:
            log.append(request.tool_name)
        await asyncio.sleep(delay)
        return answer
    return answer_later


class TestTheAllowanceIsSpentDown:
    def test_one_quick_answer_leaves_the_rest_of_the_budget(self):
        async def scenario():
            gate = with_wait_budget(slow_gate(0.01), 0.3, on_timeout="deny")
            assert await adecide(gate, request()) is True
            assert await adecide(gate, request()) is True
            assert await adecide(gate, request()) is True

        asyncio.run(scenario())

    def test_the_second_question_inherits_what_the_first_left(self):
        """A per-call deadline would let each of these wait 0.2s; the
        budget is 0.2s for the whole turn, so the second one expires."""
        async def scenario():
            gate = with_wait_budget(slow_gate(0.15), 0.2, on_timeout="deny")
            assert await adecide(gate, request()) is True
            second = request()
            assert await adecide(gate, second) is False
            assert denial_code(second) == REFUSED_TIMEOUT

        asyncio.run(scenario())

    def test_once_it_is_gone_nobody_is_asked_at_all(self):
        """Posting a question the wrapper will not wait for is how a
        person ends up answering a prompt already decided against them."""
        async def scenario():
            asked: list[str] = []
            gate = with_wait_budget(slow_gate(0.15, log=asked), 0.1,
                                    on_timeout="deny")
            assert await adecide(gate, request()) is False   # asked, expired
            third = request()
            assert await adecide(gate, third) is False
            assert asked == ["bomb"]              # not asked the second time
            assert denial_code(third) == REFUSED_OUT_OF_TIME

        asyncio.run(scenario())

    def test_the_two_refusals_are_different_codes(self):
        """Somebody was asked, versus nobody was asked: the same shape of
        English, opposite handling."""
        async def scenario():
            gate = with_wait_budget(slow_gate(0.2), 0.05, on_timeout="deny")
            first, second = request(), request()
            await adecide(gate, first)
            await adecide(gate, second)
            assert denial_code(first) == REFUSED_TIMEOUT
            assert denial_code(second) == REFUSED_OUT_OF_TIME

        asyncio.run(scenario())

    def test_the_reason_the_model_reads_names_the_turn_ceiling(self):
        async def scenario():
            gate = with_wait_budget(slow_gate(0.2), 0.05, on_timeout="deny")
            req = request()
            await adecide(gate, req)
            assert "This turn may spend 0.05 seconds" in req.reason
            assert "that is now spent" in req.reason

        asyncio.run(scenario())


class TestTurns:
    def test_a_new_turn_gets_a_fresh_allowance(self):
        async def scenario():
            gate = with_wait_budget(slow_gate(0.15), 0.1, on_timeout="deny")
            assert await adecide(gate, request("t1")) is False
            spent = request("t1")
            await adecide(gate, spent)
            assert denial_code(spent) == REFUSED_OUT_OF_TIME
            # A new turn is a new thing the agent was asked to do, so the
            # next question is ASKED (and expires) rather than refused
            # out of hand.
            fresh = request("t2")
            assert await adecide(gate, fresh) is False
            assert denial_code(fresh) == REFUSED_TIMEOUT

        asyncio.run(scenario())

    def test_an_unstamped_request_is_its_own_turn(self):
        """A host driving a gate directly may not eat a real turn's
        allowance, and the wrapper cannot tell which turn it belongs
        to."""
        async def scenario():
            asked: list[str] = []
            gate = with_wait_budget(slow_gate(0.01, log=asked), 0.1,
                                    on_timeout="deny")
            for _ in range(5):
                assert await adecide(gate, request(turn="")) is True
            assert len(asked) == 5

        asyncio.run(scenario())


class TestWhatItRefusesToDecideForYou:
    def test_it_cannot_be_built_without_a_verdict_on_silence(self):
        with pytest.raises(TypeError):
            with_wait_budget(slow_gate(0.01), 1.0)

    def test_an_unknown_verdict_is_refused_by_name(self):
        with pytest.raises(ValueError, match="unknown on_timeout"):
            with_wait_budget(slow_gate(0.01), 1.0, on_timeout="maybe")

    def test_zero_is_refused_rather_than_read_as_no_budget(self):
        with pytest.raises(ValueError, match="no question in any turn"):
            with_wait_budget(slow_gate(0.01), 0, on_timeout="deny")

    def test_allow_really_allows(self):
        """The overnight batch whose owner set the budget precisely so it
        would go ahead."""
        async def scenario():
            gate = with_wait_budget(slow_gate(0.2), 0.05, on_timeout="allow")
            assert await adecide(gate, request()) is True
            assert await adecide(gate, request()) is True  # and once spent

        asyncio.run(scenario())


class TestWhatItDoesNotBind:
    def test_an_answer_that_arrives_inline_costs_nothing(self):
        """Nothing was waited for, so there is nothing to time -- and a
        hundred inline answers never exhaust a turn."""
        async def scenario():
            gate = with_wait_budget(lambda request: True, 0.05,
                                    on_timeout="deny")
            for _ in range(100):
                assert await adecide(gate, request()) is True

        asyncio.run(scenario())

    def test_an_outer_cancellation_is_not_a_denial(self):
        """The turn dropped, the connection went: nobody said no."""
        async def scenario():
            gate = with_wait_budget(slow_gate(5.0), 10.0, on_timeout="deny")
            task = asyncio.create_task(adecide(gate, request()))
            await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())

    def test_a_cancelled_question_still_costs_what_it_waited(self):
        """Charged in a finally, so a dropped question does not refund the
        time the person actually spent waiting."""
        async def scenario():
            gate = with_wait_budget(slow_gate(5.0), 0.2, on_timeout="deny")
            task = asyncio.create_task(adecide(gate, request()))
            await asyncio.sleep(0.15)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            req = request()
            assert await adecide(gate, req) is False

        asyncio.run(scenario())
