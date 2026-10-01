"""A learned skill's script, promoted to a tool (notes/98).

The BIAS these tests encode: a promoted tool is a script that runs on
every call, so each door it opens is checked shut -- no shell between
the model and the script, no option smuggled in as an argument, no call
that skips the permission gate, no tool that outlives its skill being
pulled or set aside, and no promotion the person did not say yes to.
The convenience is checked second: one call does the task, counts as a
use, and survives a repair that did not change its arguments.
"""

from __future__ import annotations

import json

import pytest

from conftest import assistant_text, assistant_tool_call
from test_learned_skills import (
    SCRIPT,
    followed_and_failed,
    learner_after,
    make_agent,
    remembering,
    reply,
    run,
    saved_skill,
)
from yantra.errors import ToolError
from yantra.permissions import refuse
from yantra.sandbox import BwrapSandbox, SubprocessSandbox
from yantra.skills import SKILL_FILE, SkillError, discover
from yantra.skills.learn import enable_learning, record_use, set_tool_line
from yantra.skills.loader import PROMOTE_AFTER, LearnedRecord, load_skill, parse_learned
from yantra.tools.base import ToolContext
from yantra.skills.promote import (
    TOOL_FILE,
    ScriptTool,
    fits,
    load_tool_def,
    parse_proposal,
    parse_tool_def,
)


@pytest.fixture(autouse=True)
def _no_ambient_skills(monkeypatch, tmp_path):
    monkeypatch.delenv("YANTRA_SKILLS_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "not-your-home"))


GREET = {"name": "greet_person",
         "description": "Greet someone by name through the greeting script.",
         "parameters": {"type": "object",
                        "properties": {"who": {"type": "string",
                                               "description": "who to greet"}},
                        "required": ["who"]},
         "argv": ["{who}"]}

FIVE = "2026-09-28 · worked 5 · failed 0 · last ok 2026-09-29"


def promoted(tmp_path, line=FIVE, definition=GREET):
    """greet-someone saved AND promoted: tool.json + the tool: line."""
    folder = saved_skill(tmp_path, line)
    (folder / TOOL_FILE).write_text(json.dumps(definition))
    path = folder / SKILL_FILE
    path.write_text(set_tool_line(path.read_text(),
                                  f"{definition['name']} scripts/greet.py"))
    return folder


def no_to(tool: str):
    """A gate that refuses calls to one tool and allows the rest."""
    def gate(request):
        return refuse(request, "not now") if request.tool_name == tool else True
    return gate


def proposal(test=None, **overrides) -> str:
    data = {**GREET, "test": test if test is not None else {"who": "world"},
            **overrides}
    return f"=== TOOL ===\n{json.dumps(data)}\n=== END ===\n"


# ---- the counter line and the suggestion -----------------------------------


class TestStreak:
    def test_a_line_with_no_failure_counts_every_success(self):
        assert parse_learned("2026-09-28 · worked 5 · failed 0").in_a_row == 5

    def test_a_failure_zeroes_it_and_it_is_written_once_it_grows(self, tmp_path):
        folder = saved_skill(tmp_path, FIVE)
        path = folder / SKILL_FILE
        record_use(load_skill(path), False, today="2026-10-01")
        assert load_skill(path).learned.in_a_row == 0
        record_use(load_skill(path), True, today="2026-10-02")
        record_use(load_skill(path), True, today="2026-10-03")
        again = load_skill(path).learned
        assert again.in_a_row == 2
        assert "· in a row 2" in again.render()
        assert parse_learned(again.render()) == again

    def test_the_suggestion_waits_for_five_in_a_row(self, tmp_path):
        folder = saved_skill(tmp_path, "2026-09-28 · worked 4 · failed 0")
        path = folder / SKILL_FILE
        assert not load_skill(path).suggest_tool
        record_use(load_skill(path), True)
        assert load_skill(path).suggest_tool

    def test_no_script_no_suggestion(self, tmp_path):
        folder = saved_skill(tmp_path, FIVE)
        (folder / "scripts/greet.py").unlink()
        assert not load_skill(folder / SKILL_FILE).suggest_tool

    def test_a_promoted_skill_is_not_suggested_again(self, tmp_path):
        assert not load_skill(promoted(tmp_path) / SKILL_FILE).suggest_tool


class TestToolLine:
    def test_it_parses_into_a_name_and_a_script(self, tmp_path):
        skill = load_skill(promoted(tmp_path) / SKILL_FILE)
        assert (skill.tool_name, skill.tool_script) == ("greet_person", "scripts/greet.py")

    @pytest.mark.parametrize("line", ["Greet scripts/greet.py", "greet_person",
                                      "greet_person ../x.py", "greet_person /bin/sh"])
    def test_a_malformed_one_breaks_the_skill_loudly(self, tmp_path, line):
        folder = saved_skill(tmp_path, FIVE)
        path = folder / SKILL_FILE
        path.write_text(set_tool_line(path.read_text(), line))
        with pytest.raises(SkillError, match="tool:"):
            load_skill(path)

    def test_a_hand_written_skill_cannot_declare_one(self, tmp_path):
        folder = tmp_path / "skills" / "mine"
        folder.mkdir(parents=True)
        (folder / SKILL_FILE).write_text(
            "---\nname: mine\ndescription: A skill a person wrote by hand. "
            "Use when.\ntool: mine_tool scripts/x.py\n---\n\nbody\n")
        found = discover(tmp_path, home=tmp_path / "home")
        assert found.get("mine") is None
        assert "for learned skills" in found.broken[0].reason


# ---- the definition --------------------------------------------------------------


class TestDefinition:
    def test_words_and_groups_become_a_command_line(self):
        tool = parse_tool_def({
            "name": "fan_control", "description": "Set a Home Assistant fan.",
            "parameters": {"type": "object", "properties": {
                "fan": {"type": "string"}, "percentage": {"type": "integer"},
                "dry_run": {"type": "boolean"}}, "required": ["fan"]},
            "argv": ["ha.env", "{fan}", ["--percent={percentage}"],
                     ["--dry-run", "{dry_run}"]]})
        assert tool.command({"fan": "office"}) == ["ha.env", "office"]
        assert tool.command({"fan": "office", "percentage": 50, "dry_run": True}) \
            == ["ha.env", "office", "--percent=50", "--dry-run"]
        assert tool.command({"fan": "office", "dry_run": False}) == ["ha.env", "office"]

    @pytest.mark.parametrize("change, error", [
        ({"name": "Greet"}, "snake_case"),
        ({"argv": ["{nobody}"]}, "not a parameter"),
        ({"argv": []}, "never reaches the script"),
        ({"parameters": {"type": "object", "properties": {
            "who": {"type": "array"}}, "required": ["who"]}}, "needs a type"),
        ({"parameters": {"type": "object", "properties": {
            "who": {"type": "string"}}}}, "must sit in a group"),
        ({"parameters": {"type": "object", "properties": {
            "who": {"type": "string"}, "ha_token": {"type": "string"}},
            "required": ["who", "ha_token"]},
          "argv": ["{who}", "{ha_token}"]}, "would pass a secret"),
    ])
    def test_a_definition_that_cannot_mean_what_it_says_is_refused(self, change, error):
        with pytest.raises(SkillError, match=error):
            parse_tool_def({**GREET, **change})

    def test_arguments_are_checked_before_anything_runs(self):
        tool = parse_tool_def(GREET)
        with pytest.raises(ToolError, match="missing required"):
            tool.check_args({})
        with pytest.raises(ToolError, match="unknown argument"):
            tool.check_args({"who": "x", "shell": "rm -rf /"})
        with pytest.raises(ToolError, match="must be a string"):
            tool.check_args({"who": 3})

    def test_an_option_cannot_be_smuggled_in_as_a_value(self):
        with pytest.raises(ToolError, match="may not start with '-'"):
            parse_tool_def(GREET).check_args({"who": "--help"})


# ---- running it --------------------------------------------------------------------


class Recording:
    """A sandbox that says what it was asked to run."""

    confined = False
    describe = "recording"

    def __init__(self):
        self.calls = []

    def execute(self, command, *, cwd, timeout, read_only=()):
        self.calls.append((command, read_only))
        return SubprocessSandbox().execute(command, cwd=cwd, timeout=timeout)


class TestRunning:
    def agent(self, tmp_path, script=()):
        promoted(tmp_path)
        return make_agent(tmp_path, list(script))

    def test_the_tool_is_registered_and_the_roster_says_so(self, tmp_path):
        agent = self.agent(tmp_path)
        assert "greet_person" in agent.registry
        roster = agent.skills.render_roster()
        assert "- greet-someone [tool: greet_person]:" in roster
        assert "call NAME directly" in roster

    def test_one_call_runs_the_script_with_no_shell(self, tmp_path):
        agent = self.agent(tmp_path)
        tool = agent.registry.get("greet_person")
        sandbox = Recording()
        agent.registry._tools["bash"].sandbox = sandbox
        out = tool.run({"who": "world; rm -rf ~"}, agent.ctx)
        assert out == "greeted world; rm -rf ~"        # one argument, not two commands
        command, read_only = sandbox.calls[0]
        assert command[0] == "python3" and command[1].endswith("scripts/greet.py")
        assert read_only == ()                  # this skill is inside the workspace
        work = tmp_path / "work"
        work.mkdir()
        tool.run({"who": "x"}, ToolContext(cwd=work))
        assert sandbox.calls[1][1] == (tool.directory.resolve(),)

    def test_a_script_that_fails_is_a_tool_error(self, tmp_path):
        agent = self.agent(tmp_path)
        (agent.skills.get("greet-someone").directory / "scripts/greet.py").write_text(
            "import sys\nsys.exit('no such person')\n")
        with pytest.raises(ToolError, match="no such person"):
            agent.registry.get("greet_person").run({"who": "x"}, agent.ctx)

    def test_every_call_goes_through_the_gate(self, tmp_path):
        promoted(tmp_path)
        agent = make_agent(tmp_path, [
            assistant_tool_call("t", "greet_person", {"who": "mars"}),
            assistant_text("I was not allowed.")], permissions=no_to("greet_person"))
        run(agent, "greet mars")
        result = agent.history[-2].content[0]
        assert result.is_error and "greeted" not in result.content

    def test_pulling_the_skill_takes_the_tool_with_it(self, tmp_path):
        agent = self.agent(tmp_path)
        agent.skills.disable("greet-someone")
        agent.skills.reapply()
        assert agent.registry.is_disabled("greet_person")
        assert "greet_person" not in [s.name for s in agent.registry.specs()]
        agent.skills.enable("greet-someone")
        agent.skills.reapply()
        assert not agent.registry.is_disabled("greet_person")

    def test_a_stale_skill_sets_its_tool_aside(self, tmp_path):
        promoted(tmp_path, "2026-09-28 · worked 5 · failed 3 · failing 3")
        agent = make_agent(tmp_path, [])
        assert agent.registry.is_disabled("greet_person")

    def test_a_broken_tool_json_leaves_the_recipe_working(self, tmp_path):
        folder = promoted(tmp_path)
        (folder / TOOL_FILE).write_text("{not json")
        agent = make_agent(tmp_path, [])
        assert "greet_person" not in agent.registry
        assert "greet-someone" in agent.skills.available()
        assert "not valid JSON" in agent.skills.tool_errors["greet-someone"]
        assert "[tool:" not in agent.skills.render_roster()

    def test_a_name_already_taken_is_not_registered_over(self, tmp_path):
        promoted(tmp_path, definition={**GREET, "name": "bash"})
        agent = make_agent(tmp_path, [])
        assert agent.registry.get("bash").__class__.__name__ == "Bash"
        assert "already exists" in agent.skills.tool_errors["greet-someone"]

    def test_bubblewrap_mounts_the_skill_folder_read_only(self, tmp_path):
        sandbox = BwrapSandbox.__new__(BwrapSandbox)
        sandbox.bwrap, sandbox.allow_network = "/usr/bin/bwrap", False
        argv = sandbox._argv(["true"], tmp_path, (tmp_path / "skill",))
        at = argv.index(str(tmp_path / "skill"))
        assert argv[at - 1] == "--ro-bind"


# ---- what it is filled with --------------------------------------------------------


class TestInputsFromMemory:
    """A promoted tool is called without load_skill, so the facts load_skill
    would have brought ride in its description. The BIAS is against a tool
    the model fills by guessing what the person already said -- and
    against memory ever getting in the way of the call."""

    def agent(self, tmp_path, *, inputs=True, facts=(), store_fails=False):
        folder = promoted(tmp_path)
        if inputs:
            text = (folder / SKILL_FILE).read_text()
            (folder / SKILL_FILE).write_text(text.replace(
                "origin: learned", "origin: learned\ninputs: who (the name to greet)"))
        agent = make_agent(tmp_path, [assistant_text("done")] * 3)
        memory = remembering(agent, tmp_path)
        for fact in facts:
            memory.remember(fact)
        if store_fails:
            def broken(*a, **k):
                raise OSError("disk gone")
            memory.store.recall = broken
        return agent, memory

    @staticmethod
    def described(agent) -> str:
        return next(spec.description for spec in agent.registry.specs()
                    if spec.name == "greet_person")

    def test_matching_facts_reach_the_schema_the_model_fills(self, tmp_path):
        agent, _ = self.agent(tmp_path, facts=["Their sister is called Mira.",
                                               "Prefers tea to coffee."])
        assert "may fill" not in self.described(agent)   # nothing before a turn
        run(agent, "greet my sister")
        head, tail = self.described(agent).split("may fill its arguments", 1)
        assert head.startswith(GREET["description"])
        assert "- Their sister is called Mira." in tail
        assert "tea" not in tail

    def test_nothing_is_added_without_inputs_or_without_a_match(self, tmp_path):
        agent, _ = self.agent(tmp_path, inputs=False,
                              facts=["Their sister is called Mira."])
        run(agent, "greet my sister")
        assert self.described(agent) == GREET["description"]
        agent, _ = self.agent(tmp_path / "b", facts=["Prefers tea to coffee."])
        run(agent, "greet my sister")
        assert self.described(agent) == GREET["description"]

    def test_a_new_conversation_looks_again(self, tmp_path):
        agent, memory = self.agent(tmp_path, facts=["Their sister is called Mira."])
        run(agent, "greet my sister")
        memory.forget(memory.list()[0].id)
        run(agent, "greet my sister again")           # same conversation: kept
        assert "Mira" in self.described(agent)
        agent.history.clear()
        run(agent, "greet my sister")
        assert self.described(agent) == GREET["description"]

    def test_a_failing_store_leaves_the_tool_working(self, tmp_path):
        agent, _ = self.agent(tmp_path, facts=["Their sister is called Mira."],
                              store_fails=True)
        run(agent, "greet my sister")
        assert self.described(agent) == GREET["description"]
        assert agent.registry.get("greet_person").run(
            {"who": "Mira"}, agent.ctx) == "greeted Mira"


# ---- a call is a use -------------------------------------------------------------


class TestCounting:
    def test_calling_the_tool_counts_as_using_the_skill(self, tmp_path):
        promoted(tmp_path)
        agent = make_agent(tmp_path, [
            assistant_tool_call("t", "greet_person", {"who": "mars"}),
            assistant_text("Greeted mars.")])
        learner = learner_after(agent, "greet mars")
        assert learner.last_counted == [("greet-someone", True)]
        assert agent.skills.get("greet-someone").learned.worked == 6

    def test_a_failing_call_counts_as_failed(self, tmp_path):
        promoted(tmp_path)
        agent = make_agent(tmp_path, [
            assistant_tool_call("t", "greet_person", {"who": "--help"}),
            assistant_text("It did not work.")])
        learner = learner_after(agent, "greet mars")
        assert learner.last_counted == [("greet-someone", False)]

    def test_a_turn_that_used_the_tool_is_not_learned_again(self, tmp_path):
        promoted(tmp_path)
        agent = make_agent(tmp_path, [
            *[assistant_tool_call(f"c{i}", "bash", {"command": "echo x"})
              for i in range(4)],
            assistant_tool_call("t", "greet_person", {"who": "mars"}),
            assistant_text("Greeted mars.")])
        learner = learner_after(agent, "greet mars")
        assert learner.consider() is None
        assert "already followed a skill (greet-someone)" in learner.last_skip


# ---- the promotion ----------------------------------------------------------------


class TestPromotion:
    def test_propose_test_and_make(self, tmp_path):
        folder = saved_skill(tmp_path, FIVE)
        agent = make_agent(tmp_path, [assistant_text(proposal())])
        learner = enable_learning(agent, "ask", home=tmp_path / "home")
        offer = learner.propose_tool("greet-someone")
        assert offer is not None, learner.last_skip
        sent = agent.provider.last_request()
        assert sent["tools"] == [] and len(sent["messages"]) == 1
        assert 'print("greeted", sys.argv[1])' in sent["messages"][0].text()
        assert "learned:" not in sent["messages"][0].text()
        assert offer.passed and offer.test_output == "greeted world"
        assert "greet_person" not in agent.registry          # nothing yet
        assert not (folder / TOOL_FILE).exists()
        skill = learner.save_tool(offer)
        assert skill.tool == "greet_person scripts/greet.py"
        assert load_tool_def(skill).name == "greet_person"
        assert "greet_person" in agent.registry

    def test_the_test_run_goes_through_the_gate(self, tmp_path):
        saved_skill(tmp_path, FIVE)
        agent = make_agent(tmp_path, [assistant_text(proposal())],
                           permissions=no_to("greet_person"))
        learner = enable_learning(agent, "ask", home=tmp_path / "home")
        assert learner.propose_tool("greet-someone") is None
        assert learner.last_skip.startswith("its test did not run")
        assert "not now" in learner.last_skip
        assert "greet_person" not in agent.registry

    def test_a_failing_test_is_shown_and_cannot_be_made(self, tmp_path):
        folder = saved_skill(tmp_path, FIVE)
        (folder / "scripts/greet.py").write_text("import sys\nsys.exit('nope')\n")
        agent = make_agent(tmp_path, [assistant_text(proposal())])
        learner = enable_learning(agent, "ask", home=tmp_path / "home")
        offer = learner.propose_tool("greet-someone")
        assert offer is not None and not offer.passed and "nope" in offer.test_output

    @pytest.mark.parametrize("setup, why", [
        (lambda f: (f / "scripts/greet.py").unlink(), "no python or bash script"),
        (lambda f: (f / "scripts/other.py").write_text(SCRIPT), "2 python or bash scripts"),
    ])
    def test_it_needs_exactly_one_script(self, tmp_path, setup, why):
        setup(saved_skill(tmp_path, FIVE))
        agent = make_agent(tmp_path, [])
        learner = enable_learning(agent, "ask", home=tmp_path / "home")
        assert learner.propose_tool("greet-someone") is None
        assert why in learner.last_skip

    def test_a_broken_definition_gets_one_fix_then_stops(self, tmp_path):
        saved_skill(tmp_path, FIVE)
        loose = proposal(argv=["{who}", "--loud", "{loud}"], parameters={
            "type": "object", "properties": {"who": {"type": "string"},
                                             "loud": {"type": "string"}},
            "required": ["who"]})
        agent = make_agent(tmp_path, [assistant_text(loose), assistant_text(proposal())])
        learner = enable_learning(agent, "ask", home=tmp_path / "home")
        offer = learner.propose_tool("greet-someone")
        assert offer is not None and offer.passed
        fix = agent.provider.last_request()["messages"][0].text()
        assert "must sit in a group" in fix and '"--loud"' in fix

        saved_skill(tmp_path / "again", FIVE)
        agent = make_agent(tmp_path / "again", [assistant_text(loose)] * 2)
        learner = enable_learning(agent, "ask", home=tmp_path / "again/home")
        assert learner.propose_tool("greet-someone") is None
        assert "must sit in a group" in learner.last_skip

    def test_a_proposal_that_does_not_fit_its_own_test_is_refused(self):
        with pytest.raises(SkillError, match="does not fit"):
            parse_proposal(proposal(test={"nobody": 1}))

    def test_skip_carries_its_reason(self):
        assert parse_proposal("VERDICT: skip\nREASON: it needs a person mid-way") \
            == "it needs a person mid-way"

    def test_the_terminal_asks_and_no_is_the_default(self, tmp_path):
        import io

        from rich.console import Console

        from yantra.cli.repl import Repl

        folder = saved_skill(tmp_path, FIVE)
        agent = make_agent(tmp_path, [assistant_text(proposal())])
        out = io.StringIO()
        repl = Repl(agent, Console(file=out, width=120), input_fn=lambda p: "")
        repl._skills_command("tool greet-someone")
        text = out.getvalue()
        assert "Make this a tool?" in text and "tested: passed" in text
        assert "not made a tool" in text
        assert not (folder / TOOL_FILE).exists()

    def test_the_panel_suggests_it(self, tmp_path):
        import io

        from rich.console import Console

        from yantra.cli.repl import Repl

        saved_skill(tmp_path, FIVE)
        agent = make_agent(tmp_path, [])
        out = io.StringIO()
        repl = Repl(agent, Console(file=out, width=160), input_fn=lambda p: "")
        repl._skills_command("")
        assert "make it a tool? /skills tool greet-someone" in out.getvalue()
        assert agent.skills.describe()["skills"][0]["suggest_tool"] is True

    def test_the_page_asks_over_the_socket(self, tmp_path):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from yantra.web.server import WebSession, make_app

        folder = saved_skill(tmp_path, FIVE)
        session = WebSession()
        agent = make_agent(tmp_path, [assistant_text(proposal())])
        session.attach(agent, None)
        client = TestClient(make_app(session))
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            assert client.post("/api/skills/greet-someone/tool").status_code == 200
            seen = []
            while not seen or seen[-1]["type"] != "tool_offer":
                seen.append(ws.receive_json())
            offer = seen[-1]
            assert offer["name"] == "greet_person" and offer["test"]["passed"]
            ws.send_json({"type": "answer", "id": offer["id"], "decision": "make"})
            while seen[-1]["type"] != "turn_done":
                seen.append(ws.receive_json())
        assert any(e["type"] == "promoted" for e in seen)
        assert (folder / TOOL_FILE).exists()


# ---- repair ------------------------------------------------------------------------


class TestRepairKeepsOrDrops:
    def turn(self, tmp_path, script: str, test: str):
        folder = promoted(tmp_path, "2026-09-28 · worked 7 · failed 0")
        steps = [assistant_tool_call("t", "greet_person", {"who": "mars"}),
                 *followed_and_failed()[1:]]
        draft = reply(script, test=test).replace("VERDICT: save", "VERDICT: update")
        agent = make_agent(tmp_path, [*steps, assistant_text(draft)])
        (folder / "scripts/greet.py").write_text("import sys\nsys.exit('broken')\n")
        learner = learner_after(agent, "greet mars")
        return folder, agent, learner

    def test_same_arguments_keep_the_tool(self, tmp_path):
        fixed = SCRIPT.replace('"greeted"', '"said hello to"')
        folder, agent, learner = self.turn(
            tmp_path, fixed, 'python3 "$SKILL_DIR/scripts/greet.py" mars')
        assert learner.last_counted == [("greet-someone", False)]
        offer = learner.consider()
        assert offer is not None, learner.last_skip
        assert offer.tool == "greet_person" and offer.keeps_tool
        assert "tool:" not in offer.diff()
        skill = learner.save(offer)
        assert skill.tool_name == "greet_person"
        assert (folder / TOOL_FILE).exists()
        assert agent.registry.get("greet_person").run(
            {"who": "you"}, agent.ctx) == "said hello to you"
        assert skill.learned.in_a_row == 0      # the fix proves itself afresh

    def test_changed_arguments_drop_it_back_to_a_recipe(self, tmp_path):
        two = "import sys\nprint('greeted', sys.argv[1], 'in', sys.argv[2])\n"
        folder, agent, learner = self.turn(
            tmp_path, two, 'python3 "$SKILL_DIR/scripts/greet.py" mars french')
        offer = learner.consider()
        assert offer is not None and offer.tool and not offer.keeps_tool
        skill = learner.save(offer)
        assert not skill.tool and not (folder / TOOL_FILE).exists()
        assert agent.registry.is_disabled("greet_person")
        assert "[tool:" not in agent.skills.render_roster()


class TestFits:
    def test_a_test_command_reads_back_into_arguments(self):
        tool = parse_tool_def({**GREET, "parameters": {"type": "object", "properties": {
            "who": {"type": "string"}, "times": {"type": "integer"}},
            "required": ["who"]}, "argv": ["{who}", ["--times", "{times}"]]})
        script = "scripts/greet.py"
        assert fits(tool, 'python3 "$SKILL_DIR/scripts/greet.py" mars', script) \
            == {"who": "mars"}
        assert fits(tool, 'python3 "$SKILL_DIR/scripts/greet.py" mars --times 3',
                    script) == {"who": "mars", "times": 3}
        assert fits(tool, 'python3 "$SKILL_DIR/scripts/greet.py" mars fr', script) is None
        assert fits(tool, 'python3 "$SKILL_DIR/scripts/other.py" mars', script) is None

    def test_a_probe_is_never_left_registered(self, tmp_path):
        folder = saved_skill(tmp_path, FIVE)
        agent = make_agent(tmp_path, [assistant_text(proposal())])
        tool = ScriptTool(agent.skills, load_skill(folder / SKILL_FILE),
                          parse_tool_def(GREET))
        assert tool.run_checks
        learner = enable_learning(agent, "ask", home=tmp_path / "home")
        learner.propose_tool("greet-someone")
        assert "greet_person" not in agent.registry


def test_the_record_type_still_round_trips_without_a_streak():
    record = LearnedRecord(since="2026-09-28", worked=PROMOTE_AFTER)
    assert parse_learned(record.render()) == record
