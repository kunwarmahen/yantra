"""A job stopped by a restart, carried on from its last step (carry.py).

The BIAS these tests encode is against three ways a long job goes wrong
when the process under it dies:

* LOST -- the steps done (a form filled, a message typed) leave no
  trace, so nothing can pick the job up; a checkpoint must exist after
  every step, not only when the turn ends;
* DONE TWICE -- carried on means continued from the last saved step,
  never the prompt run again from the top;
* CARRIED ON BLIND -- the model must be told it stopped and that the
  screen may have moved, and a job the PERSON stopped (Ctrl-C, Stop) is
  not offered back as if a restart had.

And against a carry-on that slips past the outside-text guard: what the
job had read before the restart is still read.
"""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from conftest import ScriptedProvider, assistant_text, assistant_tool_call

from yantra import carry
from yantra.agent import Agent
from yantra.async_agent import AsyncAgent
from yantra.history import ConversationLog
from yantra.session import SessionStore, apply_payload, snapshot
from yantra.tools.base import Tool, ToolRegistry


def allow_all(request) -> bool:
    return True


class Tap(Tool):
    """A step on a screen: counts how often it really ran."""

    name = "tap"
    description = "tap a button"
    parameters = {"type": "object", "properties": {"what": {"type": "string"}}}
    read_only = True

    def __init__(self, on_run=None):
        self.ran: list[str] = []
        self.on_run = on_run

    def summary(self, args, ctx):
        return f"tap {args.get('what')}"

    def run(self, args, ctx):
        self.ran.append(args.get("what"))
        if self.on_run is not None:
            self.on_run(args.get("what"))
        return f"tapped {args.get('what')}"


def _agent(script, tap=None):
    registry = ToolRegistry()
    tap = tap or Tap()
    registry.register(tap)
    agent = Agent(ScriptedProvider(script), model="m", tools=registry,
                  permissions=allow_all)
    return agent, tap


def _steps(agent):
    saved = []
    agent.on_step = lambda a: saved.append(snapshot(a, "scripted"))
    return saved


# ---- every step is kept, while the turn is open ---------------------------------


class TestCheckpoints:
    def test_each_step_is_saved_with_how_far_the_job_got(self):
        agent, _ = _agent([
            assistant_tool_call("c1", "tap", {"what": "Orders"}),
            assistant_tool_call("c2", "tap", {"what": "Return"}),
            assistant_text("Return started."),
        ])
        saved = _steps(agent)

        agent.run("return the blue kettle")

        # the message, then one per step, each still open; then the end
        assert [s[carry.KEY]["steps"] for s in saved[:-1]] == [0, 1, 2]
        assert saved[2][carry.KEY]["tools"] == ["tap", "tap"]
        assert saved[2][carry.KEY]["message"] == "return the blue kettle"
        # and the turn that ended says nothing of it, nor its last checkpoint
        assert carry.KEY not in saved[-1]
        assert carry.KEY not in snapshot(agent, "scripted")

    def test_a_turn_ended_by_its_iteration_cap_is_not_unfinished(self):
        agent, _ = _agent([assistant_tool_call("c1", "tap", {"what": "x"})])
        agent.max_iterations = 1

        list(agent.run_streaming("go"))

        assert not agent.turn_open
        assert carry.KEY not in snapshot(agent, "scripted")

    def test_ctrl_c_ends_the_job_rather_than_leaving_it_to_carry_on(self):
        def stop(what):
            raise KeyboardInterrupt
        agent, _ = _agent([assistant_tool_call("c1", "tap", {"what": "x"})],
                          tap=Tap(on_run=stop))

        with pytest.raises(KeyboardInterrupt):
            agent.run("go")

        assert carry.KEY not in snapshot(agent, "scripted")

    def test_a_cancelled_task_is_a_shutdown_and_stays_open(self):
        started = asyncio.Event()

        class Slow(Tap):
            async def arun(self, args, ctx):
                started.set()
                await asyncio.sleep(60)
                return "never"

        registry = ToolRegistry()
        registry.register(Slow())
        agent = AsyncAgent(ScriptedProvider([
            assistant_tool_call("c1", "tap", {"what": "Orders"})]),
            model="m", tools=registry, permissions=allow_all)

        async def go():
            task = asyncio.create_task(agent.run("return the kettle"))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(go())

        assert snapshot(agent, "scripted")[carry.KEY]["message"] == "return the kettle"


# ---- carried on, not run again ---------------------------------------------------


class TestCarryOn:
    def _stopped_after_one_step(self):
        agent, tap = _agent([
            assistant_tool_call("c1", "tap", {"what": "Orders"}),
            assistant_tool_call("c2", "tap", {"what": "Return"}),
        ])
        saved = _steps(agent)
        with pytest.raises(AssertionError, match="script exhausted"):
            agent.run("return the blue kettle")   # dies at the third model call
        return saved[2], tap                      # after the second step

    def test_it_goes_on_from_the_last_step_and_does_nothing_twice(self):
        payload, first_tap = self._stopped_after_one_step()
        assert first_tap.ran == ["Orders", "Return"]

        agent, tap = _agent([assistant_text("Return started for the kettle.")])
        apply_payload(agent, payload, history_only=True)
        assert agent.unfinished["steps"] == 2

        events = list(agent.carry_on())

        assert events[-1].response.message.text() == "Return started for the kettle."
        assert tap.ran == []                      # nothing tapped again
        sent = agent.provider.requests[0]["messages"]
        assert "stopped here" in sent[-1].text()  # told, before it acts
        assert "Look again" in sent[-1].text()
        assert agent.unfinished is None and not agent.turn_open

    def test_with_nothing_stopped_there_is_nothing_to_carry_on(self):
        agent, _ = _agent([assistant_text("hi")])
        agent.run("hello")

        with pytest.raises(ValueError, match="nothing to carry on"):
            list(agent.carry_on())

    def test_a_new_message_leaves_the_stopped_job(self):
        payload, _ = self._stopped_after_one_step()
        agent, _ = _agent([assistant_text("ok, something else")])
        apply_payload(agent, payload, history_only=True)

        agent.run("never mind, what's the weather?")

        assert agent.unfinished is None

    def test_what_it_had_read_from_outside_is_still_read(self):
        agent, _ = _agent([assistant_tool_call("c1", "tap", {"what": "x"})])
        saved = _steps(agent)
        agent.outside_read.append("an email from helpdesk@example")
        with pytest.raises(AssertionError):
            agent.run("deal with my email")
        stopped = saved[-1]
        stopped[carry.KEY]["read_outside"] = ["an email from helpdesk@example"]

        fresh, _ = _agent([assistant_text("done")])
        apply_payload(fresh, stopped, history_only=True)
        list(fresh.carry_on())

        assert fresh.outside_read == ["an email from helpdesk@example"]

    def test_the_async_twin_carries_on_too(self):
        payload, _ = self._stopped_after_one_step()
        registry = ToolRegistry()
        registry.register(Tap())
        agent = AsyncAgent(ScriptedProvider([assistant_text("done")]), model="m",
                           tools=registry, permissions=allow_all)
        apply_payload(agent, payload, history_only=True)

        async def go():
            return [e async for e in agent.carry_on()]

        assert asyncio.run(go())[-1].response.message.text() == "done"


# ---- what a person reads ---------------------------------------------------------


class TestWords:
    def test_describe_says_what_and_how_far(self):
        line = carry.describe({"message": "return the blue kettle", "steps": 2,
                               "tools": ["phone_open_app", "phone_tap", "phone_tap"]})
        assert line == ("“return the blue kettle” (2 steps done: "
                        "phone_open_app, phone_tap)")

    def test_a_job_stopped_before_its_first_step_says_so(self):
        assert "before its first step" in carry.describe({"message": "hi", "steps": 0})

    def test_the_note_asks_it_to_stop_and_say_when_things_moved(self):
        text = carry.note({"saved": "2026-10-23T10:00:00+00:00", "tools": ["phone_tap"]},
                          now="2026-10-23T10:05:00+00:00")
        assert "phone_tap" in text and "don't guess" in text


# ---- the roads: --job, the terminal, the page -------------------------------------


class TestJobFile:
    def test_a_job_file_with_a_stopped_run_is_carried_on(self, tmp_path):
        from yantra.cli.main import JOB_SESSION, _open_job
        payload, _ = TestCarryOn()._stopped_after_one_step()
        store = SessionStore(tmp_path / "job.sqlite")
        store.save_payload(payload, session_id=JOB_SESSION)
        agent, _ = _agent([])
        out = io.StringIO()

        carrying = _open_job(SimpleNamespace(job=str(tmp_path / "job.sqlite")),
                             agent, Console(file=out))

        assert carrying and agent.unfinished["steps"] == 2
        assert "carrying on" in out.getvalue()
        agent.on_step(agent)                      # and every step is saved there
        assert store.latest_version(JOB_SESSION) == 2
        agent.job_lock.close()

    def test_a_job_file_whose_run_finished_starts_over(self, tmp_path):
        from yantra.cli.main import JOB_SESSION, _open_job
        done, _ = _agent([assistant_text("all done")])
        done.run("check the mail")
        store = SessionStore(tmp_path / "job.sqlite")
        store.save(done, provider_name="scripted", session_id=JOB_SESSION)
        agent, _ = _agent([])

        assert not _open_job(SimpleNamespace(job=str(tmp_path / "job.sqlite")),
                             agent, Console(file=io.StringIO()))
        assert agent.history == []
        agent.job_lock.close()

    def test_a_job_another_yantra_holds_is_refused(self, tmp_path):
        from yantra.cli.main import _open_job
        first, _ = _agent([])
        _open_job(SimpleNamespace(job=str(tmp_path / "job.sqlite")), first,
                  Console(file=io.StringIO()))
        second, _ = _agent([])
        with pytest.raises(SystemExit, match="another Yantra is running this job"):
            _open_job(SimpleNamespace(job=str(tmp_path / "job.sqlite")), second,
                      Console(file=io.StringIO()))
        first.job_lock.close()


class TestSessionStore:
    def test_unfinished_lists_only_sessions_whose_newest_version_is_open(self, tmp_path):
        store = SessionStore(tmp_path / "s.sqlite")
        stopped, _ = TestCarryOn()._stopped_after_one_step()
        store.save_payload(stopped, session_id="cut")
        store.save_payload(stopped, session_id="finished-later")
        done, _ = _agent([assistant_text("done")])
        done.run("hi")
        store.save(done, provider_name="scripted", session_id="finished-later")

        assert [sid for sid, _ in store.unfinished()] == ["cut"]


class TestLog:
    def test_stopped_finds_the_last_conversation_a_restart_cut(self, tmp_path):
        log = ConversationLog(tmp_path / "c.sqlite")
        payload, _ = TestCarryOn()._stopped_after_one_step()
        log.keep("a", payload, road="page")

        found = log.stopped("page")

        assert found is not None and found[0] == "a"
        assert found[1]["message"] == "return the blue kettle"
        assert log.stopped("terminal") is None

    def test_a_finished_conversation_is_not_stopped(self, tmp_path):
        log = ConversationLog(tmp_path / "c.sqlite")
        agent, _ = _agent([assistant_text("hi")])
        agent.run("hello")
        log.record("a", agent, provider_name="scripted", road="page")

        assert log.stopped("page") is None


fastapi = pytest.importorskip("fastapi")


class TestPage:
    def _page(self, log, script):
        from fastapi.testclient import TestClient
        from yantra.web.server import WebSession, make_app
        session = WebSession()
        session.log = log                          # as launch() has it
        agent, tap = _agent(script)
        agent.permissions = session.permission_gate()
        session.attach(agent, None)
        return session, agent, tap, TestClient(make_app(session))

    def test_the_page_offers_the_stopped_job_and_carries_it_on(self, tmp_path):
        log = ConversationLog(tmp_path / "c.sqlite")
        payload, _ = TestCarryOn()._stopped_after_one_step()
        log.keep("cut", payload, road="page")
        session, agent, tap, client = self._page(log, [assistant_text("Return started.")])

        offered = client.get("/api/state").json()["carry"]
        assert offered["id"] == "cut" and not offered["here"]
        assert "return the blue kettle" in offered["text"]

        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            assert client.post("/api/carry").status_code == 200
            while ws.receive_json()["type"] != "turn_done":
                pass

        assert session.conversation_id == "cut"
        assert agent.history[-1].text() == "Return started."
        assert tap.ran == []
        assert client.get("/api/state").json()["carry"] is None
        assert log.stopped("page") is None          # kept as finished now

    def test_leave_it_takes_the_bar_away(self, tmp_path):
        log = ConversationLog(tmp_path / "c.sqlite")
        payload, _ = TestCarryOn()._stopped_after_one_step()
        log.keep("cut", payload, road="page")
        _, _, _, client = self._page(log, [])

        assert client.post("/api/carry/leave").json()["carry"] is None
        assert client.post("/api/carry").status_code == 400
