"""How many children may run at once, and what their failures are called.

Two biases, both about a child being a conversation rather than a file
read.

The first is arithmetic nobody does until it hurts: a batch may hold
eight tool calls, and if eight of them are delegations then eight whole
conversations run at once, sharing one spawn budget and one dollar
meter. The tests below measure PEAK CONCURRENCY rather than asserting a
number in a constructor, because a semaphore that is held around the
wrong span passes the second kind of test and fails the first.

The second is the spawn budget itself, which is a check followed by an
increment -- two operations, and therefore a race the moment a batch
runs on a thread pool. The test for it drives real threads at a budget
of five and asserts that exactly five children were allowed, because a
lock that is missing here does not raise: it quietly spends more of
somebody's money than they authorised.

Failures get a CODE beside the prose for note 39's reason: "provider
error" and "hit its iteration cap" want opposite handling (retry the
first, never the second), and telling them apart by substring breaks the
day the sentence improves.
"""

from __future__ import annotations

import asyncio
import threading
import time


from conftest import ScriptedProvider, assistant_text
from yantra.agent import Agent
from yantra.errors import ProviderError, ToolError
from yantra.subagent import (
    CHILD_ITERATION_CAP,
    CHILD_PROVIDER_ERROR,
    SubagentSpawner,
)
from yantra.tools.base import ToolRegistry


class Watcher:
    """Counts how many children are inside ``run`` at the same moment."""

    def __init__(self) -> None:
        self.live = 0
        self.peak = 0
        self.lock = threading.Lock()

    def enter(self) -> None:
        with self.lock:
            self.live += 1
            self.peak = max(self.peak, self.live)

    def leave(self) -> None:
        with self.lock:
            self.live -= 1


def spawner(parent=None, **kwargs) -> SubagentSpawner:
    parent = parent or Agent(ScriptedProvider([assistant_text("x")]),
                             model="m", tools=ToolRegistry())
    return SubagentSpawner(parent, **kwargs)


def slow_child(watcher: Watcher, delay: float = 0.05):
    class Child:
        history: list = []
        total_usage = type("U", (), {"input_tokens": 0, "output_tokens": 0})()

        def run(self, objective):
            watcher.enter()
            time.sleep(delay)
            watcher.leave()
            return None
    return Child()


def slow_async_child(watcher: Watcher, delay: float = 0.05):
    class Child:
        history: list = []
        total_usage = type("U", (), {"input_tokens": 0, "output_tokens": 0})()

        async def run(self, objective):
            watcher.enter()
            await asyncio.sleep(delay)
            watcher.leave()
            return None
    return Child()


class TestChildrenInFlight:
    def test_the_sync_path_holds_the_line_at_two(self):
        """Eight delegations in a batch is eight conversations; the
        ceiling that used to apply was sized for file reads."""
        watcher = Watcher()
        sp = spawner(max_parallel=2)
        threads = [threading.Thread(target=sp._run,
                                    args=(slow_child(watcher), "go"))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert watcher.peak == 2

    def test_the_async_path_holds_the_same_line(self):
        watcher = Watcher()
        sp = spawner(max_parallel=2)

        async def scenario():
            await asyncio.gather(*[sp._arun(slow_async_child(watcher), "go")
                                   for _ in range(8)])
        asyncio.run(scenario())
        assert watcher.peak == 2

    def test_the_ceiling_is_the_host_s_to_raise(self):
        watcher = Watcher()
        sp = spawner(max_parallel=4)
        threads = [threading.Thread(target=sp._run,
                                    args=(slow_child(watcher), "go"))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert watcher.peak == 4

    def test_all_eight_children_still_run(self):
        """A bound is a queue, not a refusal: nothing is dropped."""
        watcher = Watcher()
        sp = spawner(max_parallel=2)
        threads = [threading.Thread(target=sp._run,
                                    args=(slow_child(watcher, 0.01), "go"))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(sp.results) == 8


class TestTheSpawnBudgetUnderThreads:
    def test_exactly_the_budget_is_spent_however_many_ask(self):
        """Check-then-increment is two operations; a batch on a thread
        pool is how two children both read '4 of 5 used'."""
        sp = spawner(max_per_session=5)
        start = threading.Barrier(12)
        allowed, refused = [], []

        def ask():
            start.wait()
            try:
                sp._charge_one()
                allowed.append(1)
            except ToolError:
                refused.append(1)

        threads = [threading.Thread(target=ask) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(allowed) == 5
        assert len(refused) == 7
        assert sp.spawned == 5


class TestTheWordForAChildsFailure:
    def _child_that_raises(self, exc):
        class Child:
            history: list = []
            total_usage = type("U", (),
                               {"input_tokens": 3, "output_tokens": 4})()

            def run(self, objective):
                raise exc
        return Child()

    def test_a_provider_failure_carries_its_code(self):
        result = spawner()._run(
            self._child_that_raises(ProviderError("502 from upstream")), "go")
        assert result.code == CHILD_PROVIDER_ERROR
        assert "provider error" in result.error

    def test_an_iteration_cap_carries_a_different_one(self):
        """The distinction that matters: retry the first, never the
        second -- the child does not need another go at the same task."""
        result = spawner()._run(
            self._child_that_raises(RuntimeError("max_iterations reached")),
            "go")
        assert result.code == CHILD_ITERATION_CAP

    def test_a_child_that_finished_has_no_code(self):
        watcher = Watcher()
        result = spawner()._run(slow_child(watcher, 0.0), "go")
        assert result.code is None
        assert result.error is None

    def test_the_prose_is_still_what_the_model_reads(self):
        """The code is for whoever wired the delegation up; the model
        gets a sentence it can act on."""
        result = spawner()._run(
            self._child_that_raises(ProviderError("502")), "go")
        assert "provider error inside sub-agent: 502" == result.error


class TestThroughTheSpec:
    def test_a_host_can_raise_the_ceiling_at_build_time(self):
        from yantra.spec import AgentSpec
        from yantra.subagent import SubagentSpec

        spec = AgentSpec(model="m", subagents=(
            SubagentSpec(name="checker", description="d", instructions="i",
                         tools=("read_file",)),))
        agent = spec.build(provider=ScriptedProvider([]),
                           provider_name="anthropic",
                           max_parallel_children=5)
        assert agent.subagents.max_parallel == 5

    def test_the_default_is_two(self):
        from yantra.spec import AgentSpec
        from yantra.subagent import SubagentSpec

        spec = AgentSpec(model="m", subagents=(
            SubagentSpec(name="checker", description="d", instructions="i",
                         tools=("read_file",)),))
        agent = spec.build(provider=ScriptedProvider([]),
                           provider_name="anthropic")
        assert agent.subagents.max_parallel == 2
