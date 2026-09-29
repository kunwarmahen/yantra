"""Learned skills: a solved task, written down, tested, and saved on a yes.

The BIAS these tests encode is against the three ways phase-zero saving
went wrong, and the one way it could go worse:

* COST -- the write-up re-sent the whole session. So the distil call
  must see one small message and no tools, whatever the history holds.
* INVENTION -- the model wrote pitfalls that never happened. What it
  sees of a failed call is one line; everything else is the prompt's job
  and the person's read before saving.
* UNBOUNDED TESTING -- the model tested its script ten times. Here it
  runs at most twice, and a draft that never passes is never offered.
* LEAKS -- a skill is a file that lives forever and may be shared. No
  secret reaches the model, and a draft that carries one anyway is
  dropped rather than offered.

Everything runs against a real Agent, the real bash tool and the real
permission gate; only the model is scripted.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.agent import Agent, TurnEnd
from yantra.permissions import refuse, yolo
from yantra.skills import SKILL_FILE, SkillError, discover, enable_skills
from yantra.skills.learn import (
    MAX_TEST_RUNS,
    Learner,
    LearnError,
    digest,
    enable_learning,
    learn_mode,
    parse_reply,
    read_turn,
    record_use,
    scrub,
    set_learned_line,
    why_not,
)
from yantra.skills.loader import LearnedRecord, parse_learned
from yantra.tools.base import ToolRegistry
from yantra.tools.shell import Bash
from yantra.types import Usage


@pytest.fixture(autouse=True)
def _no_ambient_skills(monkeypatch, tmp_path):
    """Saving writes to a home folder. Whatever a test forgets to pass,
    the real one is never it."""
    monkeypatch.delenv("YANTRA_SKILLS_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "not-your-home"))


SECRET = "eyJfake.ha.longlived.token.7f3a"

SCRIPT = """\
import sys
print("greeted", sys.argv[1])
"""

BROKEN_SCRIPT = """\
import sys
sys.exit("no such greeting")
"""


def reply(script: str = SCRIPT, *, name: str = "greet-someone",
          test: str = 'python3 "$SKILL_DIR/scripts/greet.py" world',
          scope: str = "user", fenced: bool = False) -> str:
    body = f"```python\n{script}```\n" if fenced else script
    return f"""VERDICT: save
NAME: {name}
DESCRIPTION: Greet someone by name through the greeting script. Use when
SCOPE: {scope}
INPUTS: who (the name the person gives)
NEEDS: none
SCRIPT: scripts/greet.py
TEST: {test}
=== INSTRUCTIONS ===
# Greet someone

1. Run python3 scripts/greet.py WHO from this skill's folder.
=== SCRIPT ===
{body}=== END ===
"""


def solved_turn(extra: list | None = None) -> list:
    """Four working calls and an answer: a turn worth a look."""
    calls = [assistant_tool_call(f"c{i}", "bash", {"command": f"echo step {i}"},
                                 usage=Usage(1000, 50))
             for i in range(1, 5)]
    return [*calls, *(extra or []),
            assistant_text("Greeted world.", usage=Usage(1000, 20))]


def make_agent(tmp_path: Path, script: list, *, permissions=yolo) -> Agent:
    registry = ToolRegistry()
    registry.register(Bash())
    agent = Agent(ScriptedProvider(script), model="m", tools=registry,
                  cwd=tmp_path, permissions=permissions)
    enable_skills(agent, tmp_path, home=tmp_path / "home")
    return agent


def run(agent: Agent, prompt: str = "greet world") -> TurnEnd:
    end = None
    for event in agent.run_streaming(prompt):
        if isinstance(event, TurnEnd):
            end = event
    assert end is not None
    return end


def learner_after(agent: Agent, prompt: str = "greet world") -> Learner:
    learner = enable_learning(agent, "ask", home=agent.ctx.cwd / "home")
    learner.after_turn(run(agent, prompt))
    return learner


# ---- the format -------------------------------------------------------------


class TestFormat:
    def test_learned_keys_parse_and_round_trip(self, tmp_path):
        folder = tmp_path / ".yantra" / "skills" / "learned" / "home-fan"
        folder.mkdir(parents=True)
        (folder / SKILL_FILE).write_text(
            "---\nname: home-fan\ndescription: Turn a Home Assistant fan on "
            "or off. Use when asked to switch a fan.\norigin: learned\n"
            "needs: a Home Assistant URL and token\ninputs: fan name, speed\n"
            "learned: 2026-09-28 · worked 6 · failed 1 · last ok 2026-10-02\n"
            "---\n\n1. Run the script.\n")
        skill = discover(tmp_path, home=tmp_path / "home").get("home-fan")
        assert skill.is_learned and skill.source == "learned-local"
        assert skill.needs == "a Home Assistant URL and token"
        assert skill.learned == LearnedRecord("2026-09-28", 6, 1, "2026-10-02")

    def test_a_malformed_counter_line_is_an_error_not_a_zero(self):
        with pytest.raises(SkillError, match="learned:"):
            parse_learned("six times")

    def test_an_unknown_origin_is_refused(self, tmp_path):
        folder = tmp_path / "skills" / "x-skill"
        folder.mkdir(parents=True)
        (folder / SKILL_FILE).write_text(
            "---\ndescription: A skill that claims a strange origin story.\n"
            "origin: downloaded\n---\n\nbody\n")
        found = discover(tmp_path, home=tmp_path / "home")
        assert "unknown origin" in found.broken[0].reason

    def test_a_hand_written_skill_wins_its_name_over_a_learned_one(self, tmp_path):
        text = ("---\ndescription: Greet someone by name, politely. Use when "
                "asked.\n---\n\n{}\n")
        for root, body in ((tmp_path / "skills", "hand"),
                           (tmp_path / ".yantra/skills/learned", "learned")):
            (root / "greet").mkdir(parents=True)
            (root / "greet" / SKILL_FILE).write_text(text.format(body))
        found = discover(tmp_path, home=tmp_path / "home")
        assert found.get("greet").body == "hand"
        assert [name for name, _ in found.shadowed] == ["greet"]

    def test_the_web_editor_keeps_a_learned_skills_own_keys(self, tmp_path):
        agent = make_agent(tmp_path, [])
        folder = tmp_path / ".yantra/skills/learned/home-fan"
        folder.mkdir(parents=True)
        (folder / SKILL_FILE).write_text(
            "---\ndescription: Turn a Home Assistant fan on or off. Use when "
            "asked.\norigin: learned\nneeds: Home Assistant\n"
            "learned: 2026-09-28 · worked 2 · failed 0\n---\n\nold\n")
        agent.skills.reload()
        saved = agent.skills.write("home-fan", "Turn a Home Assistant fan on "
                                   "or off. Use when asked.", "new body")
        assert saved.body == "new body"
        assert saved.origin == "learned" and saved.needs == "Home Assistant"
        assert saved.learned.worked == 2


# ---- noticing -----------------------------------------------------------------


class TestNotice:
    def test_a_turn_with_real_effort_is_worth_a_look(self, tmp_path):
        agent = make_agent(tmp_path, solved_turn())
        run(agent)
        turn = read_turn(agent.history)
        assert turn.task == "greet world" and len(turn.steps) == 4
        assert why_not(turn, "end_turn") is None

    def test_a_short_turn_is_not(self, tmp_path):
        agent = make_agent(tmp_path, [
            assistant_tool_call("c1", "bash", {"command": "echo hi"}),
            assistant_text("done")])
        run(agent)
        assert "fewer than 4" in why_not(read_turn(agent.history), "end_turn")

    def test_an_unfinished_turn_is_not(self, tmp_path):
        agent = make_agent(tmp_path, solved_turn())
        run(agent)
        assert "did not finish" in why_not(read_turn(agent.history),
                                           "max_iterations")

    def test_a_turn_that_followed_a_skill_is_reuse_not_learning(self, tmp_path):
        turn = read_turn([])
        assert "no finished turn" in why_not(turn, "end_turn")
        agent = make_agent(tmp_path, [
            assistant_tool_call("s", "load_skill", {"name": "greet"}),
            *solved_turn()])
        run(agent)
        # load_skill is not registered (no skills), so it failed -- but it
        # was still an attempt to follow a recipe, and says so
        assert "followed a skill" in why_not(read_turn(agent.history), "end_turn")


# ---- what the model is shown ------------------------------------------------------


class TestScrub:
    def test_env_file_values_are_removed_and_caught_again_bare(self):
        found: set[str] = set()
        text = scrub(f"HA_URL=http://127.0.0.1:8123\nHA_TOKEN={SECRET}\n"
                     f"curl -H 'X: {SECRET}' /api", found)
        assert SECRET not in text and SECRET in found
        assert "HA_TOKEN=[redacted]" in text
        assert "http://127.0.0.1:8123" in text   # an address is an input, not a secret

    def test_authorization_headers_lose_their_value(self):
        text = scrub('curl -H "Authorization: Bearer abcdefgh12345678xyz" /api')
        assert "abcdefgh12345678xyz" not in text

    def test_code_that_reads_a_token_is_left_alone(self):
        code = "token = args.token\nTOKEN = os.environ['HA_TOKEN']\n"
        assert scrub(code) == code

    def test_the_digest_holds_failures_as_one_line(self, tmp_path):
        agent = make_agent(tmp_path, solved_turn(extra=[
            assistant_tool_call("bad", "bash", {"command": "exit 3"})]))
        run(agent)
        text = digest(read_turn(agent.history))
        assert "TASK: greet world" in text
        assert text.count("-> ok:") == 4 and text.count("-> FAILED:") == 1
        assert "FINAL ANSWER TO THE PERSON:\nGreeted world." in text


# ---- the reply --------------------------------------------------------------------


class TestReply:
    def test_skip_carries_its_reason(self):
        assert parse_reply("VERDICT: skip\nREASON: a one-off question") == \
            "a one-off question"

    def test_a_save_becomes_a_draft(self):
        draft = parse_reply(reply(fenced=True))
        assert draft.name == "greet-someone" and draft.scope == "user"
        assert draft.script_name == "scripts/greet.py"
        assert draft.script == SCRIPT      # the fence the model added is gone
        assert "origin: learned" in draft.skill_md()

    def test_thinking_left_in_the_text_is_ignored(self):
        assert parse_reply("<think>VERDICT: save</think>\nVERDICT: skip\n"
                           "REASON: nope") == "nope"

    def test_a_script_may_not_reach_outside_scripts(self):
        text = reply().replace("SCRIPT: scripts/greet.py", "SCRIPT: ../../.bashrc")
        with pytest.raises(LearnError, match="not a plain file name"):
            parse_reply(text)

    def test_a_reply_in_no_shape_at_all_is_an_error(self):
        with pytest.raises(LearnError, match="format"):
            parse_reply("Sure! Here is a great skill for you.")


# ---- the whole loop -----------------------------------------------------------------


class TestConsider:
    def test_the_distil_call_is_one_small_message_with_no_tools(self, tmp_path):
        agent = make_agent(tmp_path, [*solved_turn(), assistant_text(
            reply(), usage=Usage(900, 300))])
        learner = learner_after(agent)
        before = agent.total_usage.input_tokens
        offer = learner.consider()
        assert offer is not None, learner.last_skip
        request = agent.provider.last_request()
        assert len(request["messages"]) == 1 and request["tools"] == []
        assert request["system"] is None
        # its cost is the offer's AND the session's
        assert (offer.spent.input_tokens, offer.spent.output_tokens) == (900, 300)
        assert agent.total_usage.input_tokens == before + 900

    def test_a_passing_script_is_offered_once_tested(self, tmp_path):
        agent = make_agent(tmp_path, [*solved_turn(), assistant_text(reply())])
        offer = learner_after(agent).consider()
        assert offer.tested is True and offer.test_runs == 1
        assert "greeted world" in offer.test_output
        view = offer.view(tmp_path, home=tmp_path / "home")
        assert view["scopes"]["user"]["path"].endswith("learned/greet-someone")

    def test_a_failing_script_gets_one_repair_and_then_passes(self, tmp_path):
        agent = make_agent(tmp_path, [
            *solved_turn(), assistant_text(reply(BROKEN_SCRIPT)),
            assistant_text(f"=== SCRIPT ===\n{SCRIPT}=== END ===")])
        offer = learner_after(agent).consider()
        assert offer.tested is True and offer.test_runs == 2
        assert offer.draft.script == SCRIPT

    def test_a_script_that_never_passes_is_never_offered(self, tmp_path):
        agent = make_agent(tmp_path, [
            *solved_turn(), assistant_text(reply(BROKEN_SCRIPT)),
            assistant_text(f"=== SCRIPT ===\n{BROKEN_SCRIPT.replace('no such', 'still no')}"
                           f"=== END ===")])
        learner = learner_after(agent)
        assert learner.consider() is None
        assert f"failed its test {MAX_TEST_RUNS} time(s)" in learner.last_skip
        assert agent.provider.script == []   # and no third attempt was asked for

    def test_the_test_goes_through_the_sessions_gate(self, tmp_path):
        asked = []

        def gate(request):
            asked.append(request.summary)
            if "SKILL_DIR" in request.arguments["command"]:
                return refuse(request, "not now")
            return True

        agent = make_agent(tmp_path, [*solved_turn(), assistant_text(reply())],
                           permissions=gate)
        learner = learner_after(agent)
        assert learner.consider() is None
        assert "SKILL_DIR" in asked[-1]
        # refused is not broken: no repair is asked for, one run is counted
        assert learner.last_skip.startswith("its test did not run")
        assert "not now" in learner.last_skip
        assert not (tmp_path / ".yantra/learning/greet-someone").exists()

    def test_a_secret_in_the_draft_means_no_offer(self, tmp_path):
        leaky = reply(SCRIPT.replace('"greeted"', '"sk-abcdefghijklmnopqrstuv"'))
        agent = make_agent(tmp_path, [*solved_turn(), assistant_text(leaky)])
        learner = learner_after(agent)
        assert learner.consider() is None
        assert "secret" in learner.last_skip

    def test_the_model_never_sees_a_secret_the_turn_read(self, tmp_path):
        (tmp_path / "ha.env").write_text(f"HA_TOKEN={SECRET}\n")
        agent = make_agent(tmp_path, [
            assistant_tool_call("r", "bash", {"command": "cat ha.env"}),
            *solved_turn(), assistant_text("VERDICT: skip\nREASON: one-off")])
        learner = learner_after(agent)
        assert learner.consider() is None
        sent = agent.provider.last_request()["messages"][0].text()
        assert SECRET not in sent and "HA_TOKEN=[redacted]" in sent
        assert learner.last_skip == "not worth keeping: one-off"

    def test_learn_by_name_waives_the_effort_count(self, tmp_path):
        agent = make_agent(tmp_path, [
            assistant_tool_call("c1", "bash", {"command": "echo hi"}),
            assistant_text("done"), assistant_text(reply())])
        learner = learner_after(agent)
        assert learner.consider() is None and "fewer" in learner.last_skip
        assert learner.consider(forced=True) is not None
        assert "person asked" in agent.provider.last_request()["messages"][0].text()

    def test_a_hand_written_name_is_kept_and_the_draft_renamed(self, tmp_path):
        (tmp_path / "skills" / "greet-someone").mkdir(parents=True)
        (tmp_path / "skills" / "greet-someone" / SKILL_FILE).write_text(
            "---\ndescription: The team's own greeting procedure. Use when "
            "asked.\n---\n\nhand\n")
        agent = make_agent(tmp_path, [*solved_turn(), assistant_text(reply())])
        offer = learner_after(agent).consider()
        assert offer.draft.name == "greet-someone-learned"
        assert offer.renamed_from == "greet-someone"


# ---- saving and counting ----------------------------------------------------------------


class TestSave:
    def offer(self, tmp_path, **kw):
        agent = make_agent(tmp_path, [*solved_turn(), assistant_text(reply(**kw))])
        learner = learner_after(agent)
        return agent, learner, learner.consider()

    def test_saving_writes_the_folder_and_the_roster_learns_it(self, tmp_path):
        agent, learner, offer = self.offer(tmp_path)
        skill = learner.save(offer)
        folder = tmp_path / "home/.yantra/skills/learned/greet-someone"
        assert skill.path == folder / SKILL_FILE and skill.is_learned
        assert os.access(folder / "scripts/greet.py", os.X_OK)
        assert skill.learned.worked == 0 and skill.learned.failed == 0
        assert "greet-someone" in agent.skills.render_roster()
        assert not offer.staging.exists()

    def test_project_scope_stays_under_the_private_folder(self, tmp_path):
        _, learner, offer = self.offer(tmp_path)
        skill = learner.save(offer, scope="project")
        assert skill.source == "learned-local"
        assert skill.path.is_relative_to(tmp_path / ".yantra/skills/learned")

    def test_the_persons_edit_is_what_is_saved(self, tmp_path):
        _, learner, offer = self.offer(tmp_path)
        edited = offer.draft.skill_md().replace("# Greet someone", "# Say hello")
        skill = learner.save(offer, skill_md=edited, script="print('hi')\n")
        assert "# Say hello" in skill.body
        assert (skill.directory / "scripts/greet.py").read_text() == "print('hi')\n"

    def test_an_edit_that_breaks_the_rules_saves_nothing(self, tmp_path):
        _, learner, offer = self.offer(tmp_path)
        renamed = offer.draft.skill_md().replace("name: greet-someone", "name: other")
        with pytest.raises(SkillError, match="does not match its folder"):
            learner.save(offer, skill_md=renamed)
        assert not (tmp_path / "home/.yantra/skills/learned/greet-someone").exists()

    def test_no_means_nothing_is_kept(self, tmp_path):
        _, learner, offer = self.offer(tmp_path)
        learner.discard(offer)
        assert not offer.staging.exists()
        assert not (tmp_path / "home/.yantra/skills/learned").exists()


class TestCounters:
    def saved(self, tmp_path, turn: list):
        """A saved greet-someone, then one more turn that loads it."""
        agent = make_agent(tmp_path, [*solved_turn(), assistant_text(reply()),
                                      assistant_tool_call("l", "load_skill",
                                                          {"name": "greet-someone"}),
                                      *turn])
        learner = learner_after(agent)
        learner.save(learner.consider())
        counted = learner.after_turn(run(agent, "greet mars"))
        return agent, counted

    def test_a_clean_reuse_counts_as_worked(self, tmp_path):
        agent, counted = self.saved(tmp_path, [
            assistant_tool_call("g", "bash", {"command": "echo greeted mars"}),
            assistant_text("Greeted mars.")])
        assert counted == [("greet-someone", True)]
        record = agent.skills.get("greet-someone").learned
        assert (record.worked, record.failed) == (1, 0) and record.last_ok

    def test_a_failed_call_after_the_load_counts_as_failed(self, tmp_path):
        agent, counted = self.saved(tmp_path, [
            assistant_tool_call("g", "no_such_tool", {}),
            assistant_text("It did not work.")])
        assert counted == [("greet-someone", False)]
        assert agent.skills.get("greet-someone").learned.failed == 1

    def test_a_command_that_exits_non_zero_is_a_failure_too(self, tmp_path):
        # bash reports the exit code as data, not as a tool error; for
        # "did the recipe work" it is a failed step all the same
        _, counted = self.saved(tmp_path, [
            assistant_tool_call("g", "bash", {"command": "exit 1"}),
            assistant_text("It did not work.")])
        assert counted == [("greet-someone", False)]

    def test_the_counter_line_is_rewritten_in_place(self, tmp_path):
        path = tmp_path / "s" / SKILL_FILE
        path.parent.mkdir()
        path.write_text("---\ndescription: A learned skill that has been used. "
                        "Use when.\n---\n\nbody\n")
        skill = discover(tmp_path, home=tmp_path / "h")  # nothing: wrong root
        assert not len(skill)
        from yantra.skills.loader import load_skill
        record_use(load_skill(path), True, today="2026-10-02")
        record_use(load_skill(path), False, today="2026-10-03")
        again = load_skill(path)
        assert again.learned.render() == \
            "2026-10-02 · worked 1 · failed 1 · last ok 2026-10-02"
        assert again.origin == "learned"
        text = path.read_text()
        assert text.count("learned:") == 1 and text.count("origin:") == 1
        assert set_learned_line("no frontmatter", again.learned) == "no frontmatter"


class TestMode:
    def test_flag_beats_env_beats_the_default(self, monkeypatch):
        assert learn_mode() == "ask"
        monkeypatch.setenv("YANTRA_LEARN", "off")
        assert learn_mode() == "off"
        assert learn_mode("auto") == "auto"
        with pytest.raises(ValueError):
            learn_mode("sometimes")

    def test_off_attaches_nothing(self, tmp_path):
        agent = make_agent(tmp_path, [])
        assert enable_learning(agent, "off") is None and agent.learner is None


# ---- the terminal's question ---------------------------------------------------------


class TestTerminal:
    """The save question in the REPL. The default is no: an Enter, a typo
    or a Ctrl-C leaves nothing on disk."""

    def repl(self, tmp_path, answers: list[str], script: list, mode="ask"):
        import io

        from rich.console import Console

        from yantra.cli.repl import Repl

        agent = make_agent(tmp_path, script)
        enable_learning(agent, mode, home=tmp_path / "home")
        feeder = iter(answers)
        out = io.StringIO()
        repl = Repl(agent, Console(file=out, width=120),
                    input_fn=lambda prompt: next(feeder))
        return agent, repl, out

    def test_save_after_changing_scope(self, tmp_path):
        agent, repl, out = self.repl(tmp_path, ["c", "s"],
                                     [*solved_turn(), assistant_text(reply())])
        repl.run_turn("greet world")
        text = out.getvalue()
        assert "Save this as a skill?" in text and 'print("greeted"' in text
        assert "tested: passed (1 run)" in text
        assert agent.skills.get("greet-someone").source == "learned-local"
        assert "saved skill greet-someone" in text

    def test_enter_is_no(self, tmp_path):
        agent, repl, out = self.repl(tmp_path, [""],
                                     [*solved_turn(), assistant_text(reply())])
        repl.run_turn("greet world")
        assert agent.skills.get("greet-someone") is None
        assert "not saved" in out.getvalue()
        assert not (tmp_path / ".yantra/learning/greet-someone").exists()

    def test_an_edit_made_before_saving_is_what_lands(self, tmp_path):
        agent, repl, _ = self.repl(tmp_path, ["e", "", "s"],
                                   [*solved_turn(), assistant_text(reply())])
        staged = tmp_path / ".yantra/learning/greet-someone/SKILL.md"
        original = repl._edit_offer

        def edit(offer):
            staged.write_text(staged.read_text().replace("# Greet someone",
                                                         "# Hello there"))
            original(offer)   # no $EDITOR under test: prints paths, waits

        repl._edit_offer = edit
        repl.run_turn("greet world")
        assert "# Hello there" in agent.skills.get("greet-someone").body

    def test_auto_saves_without_asking(self, tmp_path):
        agent, repl, out = self.repl(tmp_path, [],
                                     [*solved_turn(), assistant_text(reply())],
                                     mode="auto")
        repl.run_turn("greet world")
        assert agent.skills.get("greet-someone") is not None

    def test_learn_by_hand_works_with_learning_off(self, tmp_path):
        agent, repl, out = self.repl(tmp_path, ["s"], [
            assistant_tool_call("c1", "bash", {"command": "echo hi"}),
            assistant_text("done"), assistant_text(reply())], mode="off")
        repl.run_turn("greet world")
        assert agent.provider.script != []     # no offer after the turn
        repl._command("/learn")
        assert agent.skills.get("greet-someone") is not None

    def test_learn_explains_itself_when_there_is_nothing_to_save(self, tmp_path):
        _, repl, out = self.repl(tmp_path, [], [], mode="off")
        repl._command("/learn")
        assert "nothing saved: there is no finished turn" in out.getvalue()


# ---- the page's question -----------------------------------------------------------------


class TestPage:
    """The same offer over the socket: after the turn's answer, before
    turn_done, so the input stays busy while the question is up."""

    def serve(self, tmp_path, script: list, mode="ask"):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app

        session = WebSession()
        agent = make_agent(tmp_path, script)
        session.attach(agent, None)
        enable_learning(agent, mode, home=tmp_path / "home")
        return agent, TestClient(make_app(session))

    @staticmethod
    def until(ws, kind: str, limit: int = 60) -> list[dict]:
        seen = []
        while len(seen) < limit:
            seen.append(ws.receive_json())
            if seen[-1]["type"] == kind:
                return seen
            if kind != "turn_done" and seen[-1]["type"] == "turn_done":
                raise AssertionError(f"turn ended without {kind}: {seen}")
        raise AssertionError(f"never got {kind}")

    def test_offer_edit_and_save(self, tmp_path):
        agent, client = self.serve(tmp_path, [*solved_turn(), assistant_text(reply())])
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/message", json={"text": "greet world"})
            seen = self.until(ws, "learn_offer")
            offer = seen[-1]
            assert "turn_done" not in [e["type"] for e in seen]
            assert any(e["type"] == "learn_status" for e in seen)
            assert offer["test"]["passed"] is True and offer["script"] == SCRIPT
            ws.send_json({"type": "answer", "id": offer["id"], "decision": "save",
                          "scope": "project",
                          "skill_md": offer["skill_md"].replace("# Greet someone",
                                                                "# Greet anyone"),
                          "script": offer["script"]})
            learned = self.until(ws, "learned")[-1]
            self.until(ws, "turn_done")
        skill = agent.skills.get("greet-someone")
        assert learned["name"] == "greet-someone" and skill.source == "learned-local"
        assert "# Greet anyone" in skill.body

    def test_a_broken_edit_is_asked_again_with_the_edit_kept(self, tmp_path):
        agent, client = self.serve(tmp_path, [*solved_turn(), assistant_text(reply())])
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/message", json={"text": "greet world"})
            offer = self.until(ws, "learn_offer")[-1]
            broken = offer["skill_md"].replace("name: greet-someone", "name: nope")
            ws.send_json({"type": "answer", "id": offer["id"], "decision": "save",
                          "skill_md": broken, "script": offer["script"]})
            again = self.until(ws, "learn_offer")[-1]
            assert "does not match its folder" in again["error"]
            assert again["skill_md"] == broken
            ws.send_json({"type": "answer", "id": again["id"], "decision": "no"})
            self.until(ws, "turn_done")
        assert agent.skills.get("greet-someone") is None

    def test_save_last_turn_by_hand(self, tmp_path):
        agent, client = self.serve(tmp_path, [
            assistant_tool_call("c1", "bash", {"command": "echo hi"}),
            assistant_text("done"), assistant_text(reply())], mode="off")
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            client.post("/api/message", json={"text": "greet world"})
            assert "learn_offer" not in [e["type"] for e in self.until(ws, "turn_done")]
            assert client.post("/api/learn").status_code == 200
            offer = self.until(ws, "learn_offer")[-1]
            ws.send_json({"type": "answer", "id": offer["id"], "decision": "save",
                          "skill_md": offer["skill_md"], "script": offer["script"]})
            self.until(ws, "turn_done")
        assert agent.skills.get("greet-someone") is not None
        state = client.get("/api/skills").json()["skills"][0]
        assert state["learned"] is True and state["counters"]["worked"] == 0


class TestRecipeShape:
    def test_a_list_under_a_header_key_is_kept(self):
        text = reply().replace("INPUTS: who (the name the person gives)",
                               "INPUTS:\n- who: the name the person gives\n- how loud")
        assert parse_reply(text).inputs == "- who: the name the person gives - how loud"

    def test_load_skill_hands_a_learned_recipe_its_real_folder(self, tmp_path):
        from yantra.skills.tools import LoadSkill

        agent = make_agent(tmp_path, [*solved_turn(), assistant_text(reply().replace(
            "1. Run python3 scripts/greet.py WHO from this skill's folder.",
            '1. Run python3 "$SKILL_DIR/scripts/greet.py" WHO.'))])
        learner = learner_after(agent)
        skill = learner.save(learner.consider())
        text = LoadSkill(agent.skills).run({"name": skill.name}, agent.ctx)
        assert f'"{skill.directory}/scripts/greet.py"' in text
        assert "$SKILL_DIR" not in text
