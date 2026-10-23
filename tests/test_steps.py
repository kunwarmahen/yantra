"""A turn, step by step (steps.py): the run viewer.

The BIAS these tests encode is against a viewer that tells a different
story from the turn it shows:

* the WRONG STEP -- a picture, a time or a "who said yes" filed under a
  step it did not belong to, which is worse than none, because it sends
  whoever is chasing a bug to the wrong tap;
* WHAT A PERSON SAID YES TO SHOWN AS THE TOOL'S TIME -- a minute waiting
  for an answer is not a slow tool;
* AN OLD CONVERSATION THAT WILL NOT OPEN -- one from before the notes
  must still show its steps, only without the times;
* A NOTE THAT OUTLIVES ITS CONVERSATION -- after /clear, the next
  conversation's payload must not carry the last one's.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys

import pytest
from rich.console import Console

from conftest import ScriptedProvider, assistant_text, assistant_tool_call

from yantra import steps
from yantra.agent import Agent
from yantra.permissions import refuse
from yantra.session import SessionStore, apply_payload, snapshot
from yantra.tools.base import Tool, ToolOutput, ToolRegistry
from yantra.types import ImageBlock


class Look(Tool):
    name = "phone_look"
    description = "the phone's screen"
    parameters = {"type": "object", "properties": {}}
    read_only = True

    def summary(self, args, ctx):
        return "look at the phone"

    def run(self, args, ctx):
        return ToolOutput("1 Orders  2 Returns", [ImageBlock("image/png", "AAAA")])


class Tap(Tool):
    name = "phone_tap"
    description = "tap"
    parameters = {"type": "object", "properties": {"n": {"type": "integer"}}}
    read_only = False

    def summary(self, args, ctx):
        return f"tap {args.get('n')}"

    def run(self, args, ctx):
        return f"tapped {args.get('n')}"


def person(request):
    """A gate that asks somebody: yes to everything but a tap on 9."""
    if request.read_only:
        return True
    request.answered_by = "you, on the page"
    if request.arguments.get("n") == 9:
        return refuse(request, "no", code="refused_user")
    return True


def _agent(script):
    registry = ToolRegistry()
    registry.register(Look())
    registry.register(Tap())
    return Agent(ScriptedProvider(script), model="m", tools=registry,
                 permissions=person)


JOB = [
    assistant_tool_call("c1", "phone_look", {}),
    assistant_tool_call("c2", "phone_tap", {"n": 2}),
    assistant_tool_call("c3", "phone_tap", {"n": 9}),
    assistant_text("Opened Returns."),
]


class TestWhatIsNoted:
    def test_each_step_has_its_own_time_answer_and_pictures(self):
        agent = _agent(JOB)
        agent.run("open my returns")

        view = steps.timeline(snapshot(agent, "scripted"))

        look, tap, refused = view["steps"]
        assert look["tool"] == "phone_look" and look["pictures"][0]["data"] == "AAAA"
        assert look["answered_by"] is None and look["ran"] is not None
        assert tap["arguments"] == {"n": 2} and tap["result"] == "tapped 2"
        assert tap["answered_by"] == "you, on the page" and tap["pictures"] == []
        assert refused["refused"] == "refused_user" and refused["ran"] is None
        assert view["answer"] == "Opened Returns." and view["outcome"] == "end_turn"
        assert view["message"] == "open my returns"

    def test_waiting_for_a_yes_is_not_the_tools_time(self):
        import time

        def slow_person(request):
            if not request.read_only:
                time.sleep(0.3)
                request.answered_by = "you, in the terminal"
            return True
        agent = _agent([assistant_tool_call("c1", "phone_tap", {"n": 2}),
                        assistant_text("done")])
        agent.permissions = slow_person
        agent.run("tap two")

        [tap] = steps.timeline(snapshot(agent, "scripted"))["steps"]
        assert tap["waited"] >= 0.3 and tap["ran"] < 0.3

    def test_a_host_names_the_turn_and_finds_it_by_that_name(self):
        agent = _agent(JOB)
        agent.turn_tag = "run-42"
        agent.run("open my returns")
        agent.turn_tag = ""
        agent.provider.script = [assistant_text("hi")]
        agent.run("thanks")

        payload = snapshot(agent, "scripted")
        assert steps.timeline(payload, "run-42")["turn"] == 1
        assert steps.timeline(payload)["turn"] == 2
        assert steps.timeline(payload, 1)["keys"] == ["run-42"]
        with pytest.raises(LookupError):
            steps.timeline(payload, 3)

    def test_notes_survive_a_checkpoint_and_join_the_next_turn(self):
        agent = _agent(JOB)
        agent.run("open my returns")
        again = _agent([assistant_tool_call("c9", "phone_look", {}),
                        assistant_text("same screen")])
        apply_payload(again, snapshot(agent, "scripted"), history_only=True)
        again.run("look again")

        payload = snapshot(again, "scripted")
        assert steps.timeline(payload, 1)["steps"][1]["answered_by"] == "you, on the page"
        assert steps.timeline(payload, 2)["steps"][0]["ran"] is not None

    def test_a_cleared_conversation_takes_its_notes_with_it(self):
        agent = _agent(JOB)
        agent.run("open my returns")
        agent.history.clear()
        agent.provider.script = [assistant_text("hello")]
        agent.run("hi")

        payload = snapshot(agent, "scripted")
        assert "steps" not in payload
        assert [t["message"] for t in payload["turns"].values()] == ["hi"]


class TestOlderConversations:
    def test_one_from_before_the_notes_still_shows_its_steps(self):
        agent = _agent(JOB)
        agent.run("open my returns")
        payload = snapshot(agent, "scripted")
        del payload["steps"], payload["turns"]

        view = steps.timeline(payload)

        assert [s["tool"] for s in view["steps"]] == ["phone_look", "phone_tap", "phone_tap"]
        assert view["steps"][0]["pictures"][0]["data"] == "AAAA"
        assert view["steps"][0]["at"] is None and view["started"] is None


class TestInWords:
    def test_the_bug_report_says_first_what_it_holds(self):
        agent = _agent(JOB)
        agent.run("open my returns")
        text = steps.markdown(steps.timeline(snapshot(agent, "scripted")))

        assert text.startswith("> This file holds what the agent's tools were given")
        assert '"n": 2' in text and "tapped 2" in text
        assert "1 picture(s) not included" in text

    def test_a_step_line_says_who_and_how_long(self):
        line = steps.step_line({"n": 3, "tool": "phone_tap", "at": None, "waited": 12.0,
                                "ran": 1.4, "answered_by": "you, on the page",
                                "refused": None, "error": False, "pictures": []})
        assert line == "3. phone_tap  asked you, on the page, 12.0s · ran 1.4s"
        assert steps.took(0.034) == "34ms"


class TestRoads:
    def test_steps_reads_a_job_file_with_no_model(self, tmp_path):
        agent = _agent(JOB)
        agent.run("open my returns")
        store = SessionStore(tmp_path / "job.sqlite")
        store.save(agent, provider_name="scripted", session_id="job")

        done = subprocess.run([sys.executable, "-m", "yantra.cli.main", "--steps",
                               str(tmp_path / "job.sqlite"), "--json"],
                              capture_output=True, text=True, timeout=60)

        view = json.loads(done.stdout)
        assert view["format"] == steps.FORMAT and len(view["steps"]) == 3

    def test_the_terminal_shows_this_conversations_last_turn(self):
        from yantra.cli.repl import Repl
        agent = _agent(JOB)
        out = io.StringIO()
        feeder = iter(["open my returns", "/steps", "/quit"])
        Repl(agent, Console(file=out, width=160),
             input_fn=lambda _p: next(feeder)).run()

        assert "2. phone_tap" in out.getvalue() and "-> tapped 2" in out.getvalue()


fastapi = pytest.importorskip("fastapi")


class TestPage:
    def test_the_page_shows_a_turn_and_gives_it_as_a_file(self, tmp_path):
        from fastapi.testclient import TestClient

        from yantra.history import ConversationLog
        from yantra.web.server import WebSession, make_app
        log = ConversationLog(tmp_path / "c.sqlite")
        agent = _agent(JOB)
        agent.run("open my returns")
        log.record("old", agent, provider_name="scripted", road="page")
        session = WebSession()
        session.log = log
        session.attach(_agent([]), None)
        client = TestClient(make_app(session))

        view = client.get("/api/chats/old/steps").json()
        assert view["steps"][1]["answered_by"] == "you, on the page"
        got = client.get("/api/chats/old/steps/export?turn=1")
        assert got.headers["content-type"].startswith("text/markdown")
        assert "steps-turn-1.md" in got.headers["content-disposition"]
        assert client.get("/api/steps").status_code == 404     # nothing said here yet
        assert client.get("/api/chats/old/steps?turn=7").status_code == 404
