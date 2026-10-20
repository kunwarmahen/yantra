"""A lesson: a job taught one step per message, saved as one skill.

The BIAS these tests encode is against the walk-through being learned
the way a single turn is:

* ONLY THE LAST STEP -- ``/learn`` reads the last turn, so a job taught
  in five messages was learned as its fifth. A lesson's write-up must see
  every step since ``/learn start``, with the person's words above them.
* HALFWAY OFFERS -- the look after a turn would offer "tap Mom" as a skill
  of its own. While a lesson is open, nothing is offered.
* RUNNING AHEAD -- an agent not told it is being taught finishes the job
  its own way. The opening says so, in the conversation.
* LOST ON A REBUILD -- Dvara builds its agent per message; a lesson that
  lives only in memory is gone by the second step. It round-trips a dict.
* ONE PHONE'S NAMES -- a lesson taught on an iPhone wrote down its bundle
  id and the home-screen folder the app sat in, so the skill could not
  run on the Android phone. The write-up is told which phone it was, and
  to open apps by their everyday name.
* A WORD THAT ISN'T THERE -- people reach for ``/learn clear`` (from
  ``/clear``) to drop a lesson. It is ``stop``, not "unknown word".

Only the model is scripted; the agent, its tools and the learner are real.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent
from yantra.permissions import yolo
from yantra.skills import enable_skills
from yantra.skills.learn import Learner, current_turn, digest, enable_learning
from yantra.skills.lesson import USAGE, Lesson, parse
from yantra.tools.base import ToolRegistry
from yantra.tools.shell import Bash


@pytest.fixture(autouse=True)
def _no_ambient_skills(monkeypatch, tmp_path):
    monkeypatch.delenv("YANTRA_SKILLS_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "not-your-home"))


LESSON_REPLY = """VERDICT: save
NAME: send-a-whatsapp-message
DESCRIPTION: Send someone a WhatsApp message on the phone. Use when asked to message someone.
SCOPE: user
INPUTS: who (a contact's name), message (the words to send)
NEEDS: the phone
SCRIPT: none
TEST: none
=== INSTRUCTIONS ===
# Send a WhatsApp message

1. Open the WhatsApp app. The screen shows the Chats list.
2. Tap the chat whose name is WHO. The chat opens.
3. Type MESSAGE in the message box, then tap Send (it waits for a yes).
=== END ===
"""


def step(n: int, command: str) -> object:
    return assistant_tool_call(f"c{n}", "bash", {"command": command})


def make_agent(tmp_path: Path, script: list) -> Agent:
    registry = ToolRegistry()
    registry.register(Bash())
    agent = Agent(ScriptedProvider(script), model="m", tools=registry,
                  cwd=tmp_path, permissions=yolo)
    enable_skills(agent, tmp_path, home=tmp_path / "home")
    return agent


def run(agent: Agent, prompt: str) -> None:
    for _ in agent.run_streaming(prompt):
        pass


#: A walk of three messages after the opening: one tool call each.
WALK = [assistant_text("Ready. What's first?"),
        step(1, "echo opened whatsapp"), assistant_text("WhatsApp is open. Next?"),
        step(2, "echo tapped mom"), assistant_text("Mom's chat is open. Next?"),
        step(3, "echo typed running late"), assistant_text("Typed it. Next?")]


def taught(agent: Agent, lesson: Lesson) -> None:
    for words in (lesson.opening(), "open WhatsApp", "tap Mom", "type running late"):
        run(agent, words)
        lesson.add(current_turn(agent))


class TestTheLesson:
    def test_every_step_since_the_start_is_one_turn(self, tmp_path):
        agent = make_agent(tmp_path, WALK)
        lesson = Lesson(task="send a WhatsApp message")
        taught(agent, lesson)
        turn = lesson.as_turn()
        assert turn.task == "send a WhatsApp message"
        assert [s.arguments["command"] for s in turn.steps] == [
            "echo opened whatsapp", "echo tapped mom", "echo typed running late"]
        assert turn.said == [(0, "open WhatsApp"), (1, "tap Mom"),
                             (2, "type running late")]
        assert lesson.told() == 3 and lesson.steps == 3

    def test_the_persons_words_sit_above_the_steps_they_asked_for(self, tmp_path):
        agent = make_agent(tmp_path, WALK)
        lesson = Lesson(task="send a WhatsApp message")
        taught(agent, lesson)
        text = digest(lesson.as_turn())
        assert text.index("PERSON: tap Mom") < text.index("echo tapped mom")
        assert text.index("echo opened whatsapp") < text.index("PERSON: tap Mom")
        assert "I'm going to teach you" not in text      # the opening is the task

    def test_words_with_no_step_after_them_are_still_shown(self):
        lesson = Lesson(task="x")
        from yantra.skills.learn import Turn
        lesson.turns = [Turn("opening", [], "ready"), Turn("that's it, thanks", [], "ok")]
        assert "PERSON: that's it, thanks" in digest(lesson.as_turn())

    def test_the_opening_tells_the_agent_to_do_only_what_it_is_told(self):
        said = Lesson(task="order my usual coffee").opening()
        assert "order my usual coffee" in said and "ONLY the step I tell you" in said
        shown = Lesson(task="x", watching=True).opening()
        assert "watched" in shown and "Don't touch the phone" in shown

    def test_a_lesson_survives_a_rebuilt_agent(self, tmp_path):
        agent = make_agent(tmp_path, WALK)
        lesson = Lesson(task="send a WhatsApp message", watching=True)
        taught(agent, lesson)
        again = Lesson.from_dict(lesson.to_dict())
        assert again.as_turn() == lesson.as_turn() and again.watching

    def test_a_long_result_is_clipped_as_it_is_kept(self):
        from yantra.skills.learn import MAX_RESULT_CHARS, Step, Turn
        lesson = Lesson(task="x")
        lesson.add(Turn("look", [Step("a", "look", {}, "x" * 20000, True)], ""))
        assert len(lesson.turns[0].steps[0].result) < MAX_RESULT_CHARS + 100

    def test_the_words_after_learn(self):
        assert parse("") == ("", "")
        assert parse("start send a WhatsApp") == ("start", "send a WhatsApp")
        assert parse("DONE") == ("done", "")
        assert "/learn done" in USAGE

    def test_clear_is_another_word_for_stop(self):
        assert parse("clear") == ("stop", "")
        assert parse("CLEAR") == ("stop", "")
        assert "/learn clear" in USAGE

    def test_a_page_is_told_what_is_taught_and_how_far(self, tmp_path):
        agent = make_agent(tmp_path, WALK)
        lesson = Lesson(task="send a WhatsApp message", watching=True)
        taught(agent, lesson)
        seen = lesson.describe()
        assert seen["task"] == "send a WhatsApp message" and seen["watching"]
        assert seen["told"] == 3 and seen["steps"] == 3
        assert seen["status"].startswith("You're showing me")


def on_a_phone(agent: Agent, kind: str) -> None:
    """The phone layer sparsh_link writes for one phone of ``kind``."""
    from yantra.prompt import attach_prompt
    from yantra.sparsh_link import prompt_text

    serial = "http://10.0.0.5:8100" if kind == "iPhone" else "0123ABC"
    data = {"phones": [{"serial": serial, "model": "x", "state": "device"}], "adb": "ok"}
    prompt = attach_prompt(agent)
    prompt.set("phone", prompt_text(data))
    prompt.apply()


class TestBothPhones:
    def test_the_phone_layer_names_the_phone(self, tmp_path):
        from yantra.sparsh_link import phone_kind

        agent = make_agent(tmp_path, [])
        assert phone_kind(agent) == ""
        on_a_phone(agent, "iPhone")
        assert phone_kind(agent) == "iPhone"
        on_a_phone(agent, "Android phone")
        assert phone_kind(agent) == "Android phone"

    def test_a_lesson_keeps_the_phone_it_was_taught_on(self, tmp_path):
        agent = make_agent(tmp_path, WALK)
        on_a_phone(agent, "iPhone")
        lesson = Lesson(task="send a WhatsApp message")
        for words in (lesson.opening(), "open WhatsApp", "tap Mom", "type running late"):
            run(agent, words)
            lesson.add(current_turn(agent), agent)
        assert lesson.phone == "iPhone"
        assert Lesson.from_dict(lesson.to_dict()).phone == "iPhone"
        assert "TAUGHT ON: the person's iPhone" in digest(lesson.as_turn())

    def test_with_two_phones_it_keeps_the_one_its_steps_named(self, tmp_path):
        from yantra.prompt import attach_prompt
        from yantra.skills.learn import Step, Turn
        from yantra.sparsh_link import prompt_text

        agent = make_agent(tmp_path, [])
        both = {"adb": "ok", "phones": [
            {"serial": "0123ABC", "state": "device", "model": "x", "name": "nexus"},
            {"serial": "http://10.0.0.5:8100", "state": "device", "model": "x",
             "name": "iphone"}],
            "named": [{"name": "nexus", "where": "0123ABC", "kind": "Android"},
                      {"name": "iphone", "where": "http://10.0.0.5:8100", "kind": "iPhone"}]}
        prompt = attach_prompt(agent)
        prompt.set("phone", prompt_text(both))
        prompt.apply()
        lesson = Lesson(task="read the battery")
        step = Step("c1", "mcp__sparsh__open_app", {"name": "settings", "phone": "iphone"},
                    "Phone: iphone", True)
        lesson.add(Turn(task="open settings", steps=[step], answer="ok"), agent)
        assert lesson.phone == "iPhone"
        assert "TAUGHT ON: the person's iPhone" in digest(lesson.as_turn())

    def test_a_lesson_with_no_phone_says_nothing_of_one(self, tmp_path):
        agent = make_agent(tmp_path, WALK)
        lesson = Lesson(task="x")
        for words in (lesson.opening(), "open WhatsApp"):
            run(agent, words)
            lesson.add(current_turn(agent), agent)
        assert lesson.phone == "" and "TAUGHT ON" not in digest(lesson.as_turn())

    def test_the_write_up_opens_apps_by_name_for_either_phone(self, tmp_path):
        agent = make_agent(tmp_path, [*WALK, assistant_text(LESSON_REPLY)])
        lesson = Lesson(task="x")
        taught(agent, lesson)
        Learner(agent, "ask", home=tmp_path / "home").consider(lesson=lesson)
        prompt = agent.provider.requests[-1]["messages"][0].text()
        assert "Open an app with open_app and its everyday name" in prompt
        assert "package or bundle id" in prompt and "If TAUGHT ON names the phone" in prompt


class TestTheWriteUp:
    def test_done_writes_one_skill_from_the_whole_walk(self, tmp_path):
        agent = make_agent(tmp_path, [*WALK, assistant_text(LESSON_REPLY)])
        lesson = Lesson(task="send a WhatsApp message")
        taught(agent, lesson)
        learner = Learner(agent, "ask", home=tmp_path / "home")
        offer = learner.consider(lesson=lesson)
        assert offer is not None and offer.draft.name == "send-a-whatsapp-message"
        prompt = agent.provider.requests[-1]["messages"][0].text()
        assert "TAUGHT you this task" in prompt
        assert "Never write a line number" in prompt
        for words in ("PERSON: open WhatsApp", "PERSON: tap Mom",
                      "PERSON: type running late"):
            assert words in prompt

    def test_a_lesson_with_one_step_is_still_worth_writing(self, tmp_path):
        agent = make_agent(tmp_path, [assistant_text("Ready."), step(1, "echo hi"),
                                      assistant_text("Done. Next?"),
                                      assistant_text(LESSON_REPLY)])
        lesson = Lesson(task="say hi")
        for words in (lesson.opening(), "say hi"):
            run(agent, words)
            lesson.add(current_turn(agent))
        assert Learner(agent, "ask", home=tmp_path / "home").consider(lesson=lesson)

    def test_a_lesson_nothing_was_done_in_says_so(self, tmp_path):
        agent = make_agent(tmp_path, [assistant_text("Ready.")])
        lesson = Lesson(task="x")
        run(agent, lesson.opening())
        lesson.add(current_turn(agent))
        learner = Learner(agent, "ask", home=tmp_path / "home")
        assert learner.consider(lesson=lesson) is None
        assert "the lesson took 0 tool call(s)" in learner.last_skip


class TestTheTerminal:
    def repl(self, tmp_path, script):
        from rich.console import Console

        from yantra.cli.repl import Repl

        agent = make_agent(tmp_path, script)
        enable_learning(agent, "ask", home=tmp_path / "home")
        out = io.StringIO()
        answers = iter(["s"])
        return agent, Repl(agent, Console(file=out, width=200),
                           input_fn=lambda prompt: next(answers)), out

    def test_start_steps_done_saves_the_walk_and_nothing_halfway(self, tmp_path):
        agent, repl, out = self.repl(tmp_path, [*WALK, assistant_text(LESSON_REPLY)])
        repl._command("/learn start send a WhatsApp message")
        for words in ("open WhatsApp", "tap Mom", "type running late"):
            repl.run_turn(words)
        assert "Save this" not in out.getvalue()      # no step offered alone
        assert repl.lesson is not None and repl.lesson.told() == 3
        repl._command("/learn done")
        assert repl.lesson is None
        assert agent.skills.get("send-a-whatsapp-message") is not None

    def test_stop_drops_the_lesson(self, tmp_path):
        _, repl, out = self.repl(tmp_path, [assistant_text("Ready.")])
        repl._command("/learn start x")
        repl._command("/learn stop")
        assert repl.lesson is None and "dropped the lesson" in out.getvalue()

    def test_learn_clear_drops_it_too(self, tmp_path):
        _, repl, out = self.repl(tmp_path, [assistant_text("Ready.")])
        repl._command("/learn start x")
        repl._command("/learn clear")
        assert repl.lesson is None and "dropped the lesson" in out.getvalue()

    def test_done_with_no_lesson_and_start_with_no_task(self, tmp_path):
        _, repl, out = self.repl(tmp_path, [])
        repl._command("/learn done")
        repl._command("/learn start")
        assert "no lesson is open" in out.getvalue()
        assert "/learn start and what it's for" in out.getvalue()


class TestThePage:
    def serve(self, tmp_path, script):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app

        session = WebSession()
        agent = make_agent(tmp_path, script)
        session.attach(agent, None)
        enable_learning(agent, "ask", home=tmp_path / "home")
        return session, agent, TestClient(make_app(session))

    @staticmethod
    def until(ws, kind: str, limit: int = 80) -> list[dict]:
        seen = []
        while len(seen) < limit:
            seen.append(ws.receive_json())
            if seen[-1]["type"] == kind:
                return seen
        raise AssertionError(f"never got {kind}: {seen}")

    def test_learn_typed_in_the_chat_runs_a_lesson(self, tmp_path):
        session, _, client = self.serve(tmp_path, [*WALK, assistant_text(LESSON_REPLY)])
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/message", json={"text": "/learn start send a WhatsApp message"})
            self.until(ws, "turn_done")
            for words in ("open WhatsApp", "tap Mom", "type running late"):
                client.post("/api/message", json={"text": words})
                seen = self.until(ws, "turn_done")
                assert "look_status" not in [e["type"] for e in seen]
            lesson = session.state()["lesson"]
            assert lesson["task"] == "send a WhatsApp message" and lesson["told"] == 3
            assert lesson["status"].startswith("You're teaching me")
            client.post("/api/message", json={"text": "/learn done"})
            offer = self.until(ws, "learn_offer")[-1]
        assert offer["name"] == "send-a-whatsapp-message"
        assert session.lesson is None

    def test_stop_on_the_page_is_a_line_not_a_turn(self, tmp_path):
        session, _, client = self.serve(tmp_path, [])
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/message", json={"text": "/learn stop"})
            said = self.until(ws, "lesson")[-1]
        assert said["text"] == "No lesson is open."

    def test_a_lesson_opened_shows_at_once_and_clear_hides_it(self, tmp_path):
        # The Teach button types /learn start; the bar comes with the
        # state sent before the opening turn, and goes with the one
        # after /learn clear.
        session, _, client = self.serve(tmp_path, [assistant_text("Ready.")])
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/message", json={"text": "/learn watch check my battery"})
            seen = self.until(ws, "turn_started")
            states = [e for e in seen if e["type"] == "state"]
            assert states and states[-1]["lesson"]["watching"]
            self.until(ws, "turn_done")
            client.post("/api/message", json={"text": "/learn clear"})
            seen = self.until(ws, "state")
        assert seen[-1]["lesson"] is None and session.lesson is None


class TestTheMixUps:
    """Met on a real iPhone through Telegram: a second start was answered
    with the first's status (0 steps) and taken for the new one; the steps
    were then done by hand in a lesson that learns only what it's told;
    and /new had dropped the opening while the lesson stayed open."""

    def repl(self, tmp_path, script):
        return TestTheTerminal.repl(self, tmp_path, script)

    def test_a_start_over_an_empty_lesson_replaces_it(self, tmp_path):
        _, repl, out = self.repl(tmp_path, [assistant_text("Ready."), assistant_text("Ready.")])
        repl._command("/learn start coinbase current portfolio value")
        repl._command("/learn start check my Coinbase portfolio value")
        assert repl.lesson.task == "check my Coinbase portfolio value"
        assert "You're teaching me" not in out.getvalue()

    def test_a_start_over_a_lesson_with_steps_says_so_and_how_to_restart(self, tmp_path):
        _, repl, out = self.repl(tmp_path, WALK[:3])
        repl._command("/learn start x")
        repl.run_turn("open WhatsApp")
        repl._command("/learn start y")
        assert repl.lesson.task == "x" and "/learn stop, then start again" in out.getvalue()

    def test_done_with_nothing_taught_explains_start_and_watch_and_keeps_it(self, tmp_path):
        _, repl, out = self.repl(tmp_path, [assistant_text("Ready.")])
        repl._command("/learn start x")
        repl._command("/learn done")
        said = out.getvalue()
        assert "haven't shown me any steps" in said and "/learn watch" in said
        assert repl.lesson is not None            # still open: they may go on

    def test_clear_drops_the_lesson_its_opening_went_with(self, tmp_path):
        _, repl, out = self.repl(tmp_path, [assistant_text("Ready.")])
        repl._command("/learn start x")
        repl._command("/clear")
        assert repl.lesson is None and "lesson you'd started was dropped" in out.getvalue()


class TestYours:
    """A taught Coinbase skill sat in the roster unmarked, and qwen3.8 went
    to coinbase.com, as its package's prompt ("you check web pages") said."""

    def test_the_persons_own_skill_is_marked_and_preferred(self, tmp_path):
        agent = make_agent(tmp_path, [*WALK, assistant_text(LESSON_REPLY)])
        lesson = Lesson(task="send a WhatsApp message")
        taught(agent, lesson)
        learner = Learner(agent, "ask", home=tmp_path / "home")
        learner.save(learner.consider(lesson=lesson))
        agent.skills.reload()
        roster = agent.skills.render_roster()
        assert "- send-a-whatsapp-message [yours]:" in roster
        assert "instead of finding another way (a website" in roster

    def test_a_skill_that_came_with_the_agent_is_not_marked(self, tmp_path):
        folder = tmp_path / "skills" / "x-latest-post"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            "---\nname: x-latest-post\ndescription: The newest post of an X account. "
            "Use when asked for one.\norigin: learned\n---\n\nSteps.\n")
        roster = make_agent(tmp_path, []).skills.render_roster()
        assert "- x-latest-post:" in roster and "[yours]" not in roster

    def test_a_lesson_asks_for_where_and_the_ways_they_ask(self, tmp_path):
        agent = make_agent(tmp_path, [*WALK, assistant_text(LESSON_REPLY)])
        lesson = Lesson(task="x")
        taught(agent, lesson)
        Learner(agent, "ask", home=tmp_path / "home").consider(lesson=lesson)
        prompt = agent.provider.requests[-1]["messages"][0].text()
        assert "The DESCRIPTION says where the job is done" in prompt
