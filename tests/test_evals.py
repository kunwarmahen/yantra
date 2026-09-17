"""Evals: the METADATA machinery (completion / process / cost checks,
recording proxies, fossil conversion, reporting) is deterministic and
gets unit tests here. Only live-model correctness is left to real runs
(examples/run_evals.py) -- exactly the tests-vs-evals split ch19 draws.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar

from conftest import ScriptedProvider, assistant_text, assistant_tool_call

from yantra.evals import (
    AsyncEvalRunner,
    CaseOutcome,
    EvalCase,
    EvalResult,
    EvalRunner,
    case_from_trace,
    judge,
    spawn_setup,
    summarize,
)
from yantra.errors import ProviderError
from yantra.permissions import yolo
from yantra.subagent import SPAWN_TOOL_NAME
from yantra.tools.base import Tool, ToolRegistry
from yantra.types import Usage


class EchoTool(Tool):
    name: ClassVar[str] = "echo"
    description: ClassVar[str] = "returns its text argument"
    parameters: ClassVar[dict] = {"type": "object",
                                  "properties": {"text": {"type": "string"}},
                                  "required": ["text"]}
    read_only: ClassVar[bool] = True

    def summary(self, args: dict[str, Any], ctx) -> str:
        return f"echo {args.get('text', '')!r}"

    def run(self, args: dict[str, Any], ctx) -> str:
        return str(args.get("text", ""))


def make_runner(script: list) -> tuple[EvalRunner, ScriptedProvider]:
    provider = ScriptedProvider(script)
    registry = ToolRegistry()
    registry.register(EchoTool())
    return (EvalRunner(provider, "m", tools=registry, permissions=yolo),
            provider)


def case(**overrides: Any) -> EvalCase:
    base = dict(id="c1", description="d", user_message="do the thing",
                required_tools=[], forbidden_tools=[])
    return EvalCase(**{**base, **overrides})


class TestChecks:
    def test_happy_trajectory_passes_and_records(self):
        runner, _ = make_runner([
            assistant_tool_call("t1", "echo", {"text": "hi"}),
            assistant_text("all done",
                           usage=Usage(input_tokens=10, output_tokens=5)),
        ])
        result = runner.run_case(case(check_answer=lambda a: "done" in a))
        assert result.passed and result.failures == []
        assert result.tool_calls_seen == ["echo"]
        assert result.tokens_used == 15
        assert result.iterations_used == 2

    def test_failures_accumulate_instead_of_first_fail(self):
        runner, _ = make_runner([assistant_text(
            "no tools here", usage=Usage(input_tokens=100, output_tokens=50))])
        result = runner.run_case(case(
            required_tools=["echo"],
            forbidden_tools=["bash"],
            check_answer=lambda a: False,
            max_tokens=10,
        ))
        assert not result.passed
        joined = "; ".join(result.failures)
        # ALL of these visible at once is how you diagnose a case
        assert "answer check failed" in joined
        assert "required tool not used: echo" in joined
        assert "over token budget: 150 > 10" in joined
        assert "forbidden tool used" not in joined  # bash never ran

    def test_forbidden_tool_used_is_caught(self):
        runner, _ = make_runner([
            assistant_tool_call("t1", "echo", {"text": "x"}),
            assistant_text("ok"),
        ])
        result = runner.run_case(case(forbidden_tools=["echo"]))
        assert not result.passed
        assert "forbidden tool used: echo" in result.failures


class TestCompletion:
    def test_crash_becomes_failed_result_not_an_exception(self):
        class Exploding(ScriptedProvider):
            def stream(self, **kwargs):
                raise ProviderError("503 upstream", status=503)
                yield  # pragma: no cover

        provider = Exploding([])
        registry = ToolRegistry()
        registry.register(EchoTool())
        runner = EvalRunner(provider, "m", tools=registry, permissions=yolo)
        result = runner.run_case(case())
        assert not result.passed
        assert result.error.startswith("ProviderError")
        assert any(f.startswith("crashed:") for f in result.failures)

    def test_fresh_agent_per_case_no_history_bleed(self):
        runner, provider = make_runner([
            assistant_text("first"),
            assistant_text("second"),
        ])
        r1 = runner.run_case(case(id="a"))
        r2 = runner.run_case(case(id="b"))
        assert r1.passed and r2.passed
        # each request carried ONLY its own user message: one case's
        # history never leaked into the next
        for request in provider.requests:
            assert len(request["messages"]) == 1
        assert r2.tool_calls_seen == []  # seen-list reset between cases


class TestSetupSeam:
    def test_setup_gets_each_fresh_agent(self):
        runner, _ = make_runner([
            assistant_text("first"),
            assistant_text("second"),
        ])
        agents = []
        runner.run_case(case(id="a", setup=agents.append))
        runner.run_case(case(id="b", setup=agents.append))
        assert len(agents) == 2
        assert agents[0] is not agents[1]  # fresh wiring per case, not shared

    def test_setup_registered_tools_are_recorded_exactly_once(self):
        # idempotent re-wrapping: originals keep ONE recording layer even
        # though the registry is wrapped twice (before + after setup)
        runner, _ = make_runner([
            assistant_tool_call("t1", "echo", {"text": "x"}),
            assistant_tool_call("t2", "extra", {"text": "y"}),
            assistant_text("done"),
        ])

        class Extra(EchoTool):
            name: ClassVar[str] = "extra"

        def setup(agent) -> None:
            agent.registry.register(Extra())

        result = runner.run_case(case(
            required_tools=["echo", "extra"], setup=setup))

        assert result.passed, result.failures
        assert result.tool_calls_seen.count("echo") == 1
        assert result.tool_calls_seen.count("extra") == 1

    def test_spawn_case_delegation_is_assertable_and_conflated(self):
        script = [
            assistant_tool_call("p1", SPAWN_TOOL_NAME, {
                "objective": "have a child echo something",
                "output_format": "one line",
                "tools_allowed": ["echo"],
                "justification": "isolated window"}),
            assistant_tool_call("c1", "echo", {"text": "child worked"}),
            assistant_text("child done"),
            assistant_text("parent relays"),
        ]
        provider = ScriptedProvider(script)
        registry = ToolRegistry()
        registry.register(EchoTool())
        runner = EvalRunner(provider, "m", tools=registry, permissions=yolo)

        result = runner.run_case(case(
            id="spawn",
            required_tools=[SPAWN_TOOL_NAME, "echo"],
            check_answer=lambda a: "relays" in a,
            setup=spawn_setup(),
        ))

        assert result.passed, result.failures
        # delegation itself is in the record...
        assert result.tool_calls_seen.count(SPAWN_TOOL_NAME) == 1
        # ...and so is the child's execution of the shared tool instance
        # (documented conflation: delegation is the parent's doing)
        assert result.tool_calls_seen.count("echo") == 1


class TestJudge:
    def test_pass_fail_and_fail_closed(self):
        for verdict, expected in [("PASS because complete", True),
                                  ("FAIL missing detail", False),
                                  ("unsure, honestly", False)]:
            _, provider = make_runner([assistant_text(verdict)])
            ok, raw = judge(provider, "m", question="q?", answer="a")
            assert ok is expected
            assert raw == verdict

    def test_judge_prompt_carries_the_material(self):
        _, provider = make_runner([assistant_text("PASS x")])
        judge(provider, "m", question="What is 2+2?",
              answer="five", reference="four", criteria="arithmetic only")
        request = provider.requests[0]
        assert "impartial grader" in request["system"]
        blob = str(request["messages"])
        for fragment in ("What is 2+2?", "five", "four", "arithmetic only"):
            assert fragment in blob


class TestAsyncRunner:
    """The async twin: same cases, same grading rules, one loop driving
    several trajectories. These tests pin the CONTRACT of the twin --
    parity with sync scoring, submission-order results, per-case seen
    isolation, bounded concurrency -- against the same ScriptedProvider.
    """

    @staticmethod
    def make_async_runner(script: list, *, concurrency: int = 4
                          ) -> tuple[AsyncEvalRunner, ScriptedProvider]:
        provider = ScriptedProvider(script)
        registry = ToolRegistry()
        registry.register(EchoTool())
        return (AsyncEvalRunner(provider, "m", tools=registry,
                                permissions=yolo, concurrency=concurrency),
                provider)

    def test_grading_matches_the_sync_twin_field_for_field(self):
        script = [
            assistant_tool_call("t1", "echo", {"text": "hi"}),
            assistant_text("all done",
                           usage=Usage(input_tokens=10, output_tokens=5)),
        ]
        the_case = case(id="c1", check_answer=lambda a: "done" in a)

        sync_runner, _ = make_runner(script)
        sync_result = sync_runner.run_case(the_case)

        async def _async_one():
            runner, _ = self.make_async_runner(script)
            return await runner.run_case(the_case)

        async_result = asyncio.run(_async_one())
        for field in ("passed", "failures", "final_answer", "tokens_used",
                      "iterations_used", "tool_calls_seen"):
            assert getattr(sync_result, field) == getattr(async_result, field), \
                f"{field} diverged between twins"

    def test_results_align_with_submission_order_not_finish_order(self):
        # three one-request cases whose responses land in REVERSE order;
        # gather must still hand back results indexed like `cases`
        delays = iter([0.15, 0.10, 0.05])

        class Slow(ScriptedProvider):
            async def astream(self, **kwargs):
                await asyncio.sleep(next(delays))
                async for event in super().astream(**kwargs):
                    yield event

        provider = Slow([assistant_text(f"done {i}") for i in range(3)])
        registry = ToolRegistry()
        registry.register(EchoTool())
        runner = AsyncEvalRunner(provider, "m", tools=registry,
                                 permissions=yolo)
        cases = [case(id=cid) for cid in ("a", "b", "c")]

        results = asyncio.run(runner.run_all(cases))
        assert [r.case_id for r in results] == ["a", "b", "c"]
        assert all(r.passed for r in results)

    def test_concurrent_cases_do_not_share_their_seen_lists(self):
        # exactly ONE of the two trajectories executes echo; if they
        # shared a seen-list (the sync runner's cleared singleton),
        # BOTH results would record it
        script = [
            assistant_tool_call("t1", "echo", {"text": "x"}),
            assistant_text("a done"),
            assistant_text("b done"),
        ]

        async def _two():
            runner, _ = self.make_async_runner(script)
            return await asyncio.gather(runner.run_case(case(id="a")),
                                        runner.run_case(case(id="b")))

        ra, rb = asyncio.run(_two())
        seen_lists = sorted([ra.tool_calls_seen, rb.tool_calls_seen])
        assert seen_lists == [[], ["echo"]]

    def test_crash_becomes_failed_result_even_under_gather(self):
        class Exploding(ScriptedProvider):
            def stream(self, **kwargs):
                raise ProviderError("503 upstream", status=503)
                yield  # pragma: no cover

        provider = Exploding([])
        registry = ToolRegistry()
        registry.register(EchoTool())
        runner = AsyncEvalRunner(provider, "m", tools=registry,
                                 permissions=yolo)

        results = asyncio.run(runner.run_all([case(id="boom"), case(id="also-boom")]))
        assert len(results) == 2  # one broken case did not sink the batch
        for r in results:
            assert not r.passed
            assert r.error.startswith("ProviderError")
            assert any(f.startswith("crashed:") for f in r.failures)

    def test_concurrency_cap_bounds_inflight_cases(self):
        inflight = 0
        peak = 0

        class Tracked(ScriptedProvider):
            async def astream(self, **kwargs):
                nonlocal inflight, peak
                inflight += 1
                peak = max(peak, inflight)
                try:
                    await asyncio.sleep(0.02)  # hold the slot long enough to overlap
                    async for event in super().astream(**kwargs):
                        yield event
                finally:
                    inflight -= 1

        provider = Tracked([assistant_text(f"done {i}") for i in range(6)])
        registry = ToolRegistry()
        registry.register(EchoTool())
        runner = AsyncEvalRunner(provider, "m", tools=registry,
                                 permissions=yolo, concurrency=2)

        start = time.monotonic()
        results = asyncio.run(
            runner.run_all([case(id=f"c{i}") for i in range(6)]))
        elapsed = time.monotonic() - start

        assert len(results) == 6 and all(r.passed for r in results)
        assert peak <= 2, f"semaphore leaked: {peak} cases ran at once"
        assert peak > 1, "cases never overlapped -- nothing was concurrent"
        # 6 x 0.02s serial would be >= 0.12s; two-wide overlap finishes faster
        assert elapsed < 0.12

    def test_delegate_case_runs_through_the_async_twin_unchanged(self):
        # spawn_setup wires a SubagentSpawner onto an ASYNC agent here,
        # and the spawn is AWAITED -- so the child is an AsyncAgent on the
        # same loop, which is the shape a service gets and therefore the
        # shape a suite has to grade (notes/40)
        script = [
            assistant_tool_call("p1", SPAWN_TOOL_NAME, {
                "objective": "have a child echo something",
                "output_format": "one line",
                "tools_allowed": ["echo"],
                "justification": "isolated window"}),
            assistant_tool_call("c1", "echo", {"text": "child worked"}),
            assistant_text("child done"),
            assistant_text("parent relays"),
        ]

        async def _spawn():
            runner, _ = self.make_async_runner(script)
            return await runner.run_case(case(
                id="spawn",
                required_tools=[SPAWN_TOOL_NAME, "echo"],
                check_answer=lambda a: "relays" in a,
                setup=spawn_setup(),
            ))

        result = asyncio.run(_spawn())
        assert result.passed, result.failures
        assert result.tool_calls_seen.count(SPAWN_TOOL_NAME) == 1
        # child's execution through the shared tool instance lands in
        # THIS case's own seen-list (conflation documented on spawn_setup)
        assert result.tool_calls_seen.count("echo") == 1


class TestTheSuiteOverRepeatedRuns:
    """--repeat, on both twins. A trajectory is a die roll, so the thing
    under test here is that n rolls are reported as n rolls: the right
    number of real requests, the union of reasons with counts, and a
    threshold that does not quietly lower the bar.
    """

    def test_a_repeated_case_makes_one_request_per_run(self):
        runner, provider = make_runner([assistant_text("done")] * 4)
        outcome = runner.evaluate(case(), repeat=4)
        assert outcome.attempts == 4 and len(provider.requests) == 4
        assert outcome.tokens_used == sum(r.tokens_used for r in outcome.runs)

    def test_each_run_lands_as_it_happens(self):
        """n runs of a live case is minutes of silence otherwise, and the
        operator who paid for the evidence should watch it arrive."""
        runner, _ = make_runner([assistant_text("done")] * 3)
        landed: list[bool] = []
        runner.evaluate(case(), repeat=3, on_result=lambda r: landed.append(r.passed))
        assert landed == [True, True, True]

    def test_the_async_twin_grades_a_repeated_case_identically(self):
        script = [assistant_text("done"), assistant_text("nope"),
                  assistant_text("done")]
        the_case = case(check_answer=lambda a: "done" in a, min_pass_rate=0.6)

        sync = make_runner(list(script))[0].evaluate(the_case, repeat=3)

        async def _async_one():
            runner, _ = TestAsyncRunner.make_async_runner(list(script),
                                                          concurrency=1)
            return await runner.evaluate(the_case, repeat=3)

        # concurrency=1 so the async twin consumes the same script in the
        # same order -- the point is the VERDICT, not the interleaving
        asynchronous = asyncio.run(_async_one())
        for attr in ("attempts", "passes", "passed", "marks", "failures"):
            assert getattr(sync, attr) == getattr(asynchronous, attr), attr
        assert sync.passed and sync.marks == "✓✗✓"

    def test_the_unit_of_async_concurrency_is_a_run_not_a_case(self):
        """Five repeats of one case is five independent trajectories;
        bounding by case would leave the semaphore empty."""
        inflight = 0
        peak = 0

        class Tracked(ScriptedProvider):
            async def astream(self, **kwargs):
                nonlocal inflight, peak
                inflight += 1
                peak = max(peak, inflight)
                try:
                    await asyncio.sleep(0.02)
                    async for event in super().astream(**kwargs):
                        yield event
                finally:
                    inflight -= 1

        provider = Tracked([assistant_text("done")] * 5)
        registry = ToolRegistry()
        registry.register(EchoTool())
        runner = AsyncEvalRunner(provider, "m", tools=registry,
                                 permissions=yolo, concurrency=3)
        outcome, = asyncio.run(runner.run_suite([case(id="one")], repeat=5))
        assert outcome.attempts == 5 and outcome.passed
        assert peak > 1, "repeats of a single case never overlapped"
        assert peak <= 3, f"semaphore leaked: {peak} runs at once"

    def test_async_outcomes_come_back_in_submission_order(self):
        delays = iter([0.15, 0.10, 0.05])

        class Slow(ScriptedProvider):
            async def astream(self, **kwargs):
                await asyncio.sleep(next(delays))
                async for event in super().astream(**kwargs):
                    yield event

        provider = Slow([assistant_text("done")] * 3)
        registry = ToolRegistry()
        registry.register(EchoTool())
        runner = AsyncEvalRunner(provider, "m", tools=registry,
                                 permissions=yolo)
        finished: list[str] = []
        outcomes = asyncio.run(runner.run_suite(
            [case(id=cid) for cid in ("a", "b", "c")],
            on_outcome=lambda o: finished.append(o.case_id)))
        # a report you diff between models must not reorder itself when
        # the network is slow; the LIVE feed may, and does
        assert [o.case_id for o in outcomes] == ["a", "b", "c"]
        assert finished == ["c", "b", "a"]

    def test_a_case_that_fails_every_run_reports_the_count(self):
        runner, _ = make_runner([assistant_text("nope")] * 3)
        outcome = runner.evaluate(case(required_tools=["echo"]), repeat=3)
        assert not outcome.passed
        assert outcome.failures == ["required tool not used: echo "
                                     "(3 of 3 runs)"]

    def test_a_flaky_case_below_its_rate_is_still_red(self):
        outcome = CaseOutcome.of(
            case(min_pass_rate=0.9),
            [_result("c1", i < 8) for i in range(10)])
        assert (outcome.passes, outcome.required_passes) == (8, 9)
        assert not outcome.passed and outcome.pass_rate == 0.8

    def test_summarize_reports_the_rate_when_there_were_repeats(self):
        outcome = CaseOutcome.of(case(id="flaky", min_pass_rate=0.5),
                                 [_result("flaky", True),
                                  _result("flaky", False, ["answer check failed"])])
        report = summarize([outcome])
        assert "✓ flaky: 1/2 runs · 14tok" in report
        assert "answer check failed (1 of 2 runs)" in report
        assert report.endswith("1/1 passed")


class TestFossilRuleAndReporting:
    def test_case_from_trace_budget_is_observed_times_15(self):
        c = case_from_trace("abcdef123456", "hallucinated a path",
                            "where is X defined?", tokens_used=10000,
                            required_tools=["grep"])
        assert c.id == "trace-abcdef12"
        assert c.description == "hallucinated a path"
        assert c.max_tokens == 15000
        assert c.required_tools == ["grep"]

    def test_summarize_lines_and_aggregate(self):
        results = [_result("c-ok", True), _result("c-bad", False,
                                                  ["answer check failed"])]
        report = summarize(results)
        assert report.count("\n") == 2
        assert "✓ c-ok" in report
        assert "✗ c-bad: 7tok · 1it" in report
        assert "-- answer check failed" in report
        assert report.endswith("1/2 passed")


def _result(case_id: str, passed: bool,
            failures: list[str] | None = None) -> EvalResult:
    return EvalResult(case_id=case_id, passed=passed, failures=failures or [],
                      final_answer="", tokens_used=7, iterations_used=1,
                      tool_calls_seen=[], duration_seconds=0.25)
