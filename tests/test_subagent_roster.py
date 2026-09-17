"""Roster assertions that can see a declared sub-agent's tool list.

The bias here is the vacuous pass. Every failure these tests are designed
against looks identical from the outside -- a green suite -- and each one
is a different way for a gate to check nothing: a case naming a sub-agent
that was renamed last week, a case naming a child in a package that
declares none, a child asserted about while a tool it needs is absent, an
empty pattern list that reads as caution.

So almost every test below asserts on the FAILURE TEXT, not just on the
boolean. "It went red" is satisfied by a gate that goes red for the wrong
reason, and the wrong reason is what sends an author to the wrong file.

The second bias is the ceiling/floor confusion. A child is built from the
parent's registry, so the parent's own lacks_tools was always a ceiling
over the whole package; what was missing is per-child narrowing, and a
test that only proved "the assertion fires" would not notice if it were
grading the parent's list twice.
"""

from __future__ import annotations

import pytest

from yantra.agent import Agent
from yantra.errors import ConfigError, ToolError
from yantra.eval_suite import CASES, SUITE_DIR, load_cases
from yantra.evals import (
    EvalCase,
    declared_rosters,
    recording_registry,
    roster_failures,
    subagent_failures,
)
from yantra.permissions import yolo
from yantra.spec import AgentSpec
from yantra.subagent import (
    DeclaredSubagent,
    SubagentSpawner,
    SubagentSpec,
    resolve_child_tools,
)
from yantra.tools.base import Tool, ToolRegistry

from conftest import ScriptedProvider


# ---- fixtures ---------------------------------------------------------------


class Look(Tool):
    name = "look"
    description = "look at something"
    parameters = {"type": "object", "properties": {}}
    read_only = True

    def summary(self, args, ctx):
        return "look()"

    def run(self, args, ctx):
        return "looked"


class Fetch(Look):
    name = "web_fetch"
    description = "fetch a page"


class Note(Look):
    name = "note"
    description = "write something down"
    read_only = False


def registry() -> ToolRegistry:
    reg = ToolRegistry()
    for tool in (Look(), Fetch(), Note()):
        reg.register(tool)
    return reg


def parent_with(*children: SubagentSpec, reg: ToolRegistry | None = None):
    """An agent carrying declared children, built the way spec.build does."""
    reg = reg if reg is not None else registry()
    agent = Agent(ScriptedProvider([]), model="m", tools=reg, permissions=yolo)
    spawner = SubagentSpawner(agent)
    agent.subagents = spawner
    for child in children:
        reg.register(DeclaredSubagent(child, spawner))
    return agent


def checker(**overrides) -> SubagentSpec:
    fields = dict(name="fact_checker", description="Check one claim.",
                  instructions="You check claims.", tools=("look",))
    fields.update(overrides)
    return SubagentSpec(**fields)


def case(**overrides) -> EvalCase:
    fields = dict(id="c", description="", user_message="")
    fields.update(overrides)
    return EvalCase(**fields)


def graded(agent, **case_kwargs) -> list[str]:
    return subagent_failures(case(**case_kwargs), declared_rosters(agent))


# ---- one definition of a child's roster -------------------------------------


class TestResolvingAChildsTools:
    def test_a_disabled_tool_is_unreachable_not_present(self):
        """names() lists it and get() raises: the gap this function closes."""
        reg = registry()
        reg.disable("note")
        offered, unreachable = resolve_child_tools(reg, ["look", "note"])
        assert offered == ["look"]
        assert unreachable == ["note"]

    def test_an_unknown_tool_is_unreachable(self):
        offered, unreachable = resolve_child_tools(registry(), ["teleport"])
        assert (offered, unreachable) == ([], ["teleport"])

    def test_duplicates_collapse_in_first_mention_order(self):
        offered, _ = resolve_child_tools(registry(), ["note", "look", "note"])
        assert offered == ["note", "look"]

    def test_a_disabled_tool_no_longer_crashes_read_only(self):
        """It used to raise KeyError from inside a permission check -- the
        worst possible place for an exception about configuration."""
        reg = registry()
        agent = parent_with(checker(tools=("look",)), reg=reg)
        tool = agent.registry.get("fact_checker")
        assert tool.read_only is True
        reg.disable("look")
        assert tool.read_only is False

    def test_a_child_that_cannot_be_built_says_so_as_a_tool_error(self):
        reg = registry()
        agent = parent_with(checker(tools=("look",)), reg=reg)
        tool = agent.registry.get("fact_checker")
        reg.disable("look")
        with pytest.raises(ToolError, match="disabled by the operator") as caught:
            tool.run({"task": "check this"}, agent.ctx)
        # and it does not offer the disabled tool back as an alternative
        assert "look" not in str(caught.value).split("available:")[1]


# ---- what the assertion can see ---------------------------------------------


class TestGradingAChildsList:
    def test_the_hole_note_40_left_is_closed(self):
        """Widening fact_checker to include web_fetch turns a gate red."""
        narrow = parent_with(checker(tools=("look",)))
        assert graded(narrow,
                      subagent_lacks_tools={"fact_checker": ["web_fetch"]}) == []

        widened = parent_with(checker(tools=("look", "web_fetch")))
        (failure,) = graded(
            widened, subagent_lacks_tools={"fact_checker": ["web_fetch"]})
        assert failure == ("on fact_checker's roster and should not be: "
                           "web_fetch")

    def test_has_tools_names_the_child_and_shows_its_list(self):
        agent = parent_with(checker(tools=("look",)))
        (failure,) = graded(agent,
                            subagent_has_tools={"fact_checker": ["grep"]})
        assert failure == "not on fact_checker's roster: grep (fact_checker: look)"

    def test_patterns_work_in_the_values(self):
        agent = parent_with(checker(tools=("look", "web_fetch")))
        (failure,) = graded(agent,
                            subagent_lacks_tools={"fact_checker": ["web_*"]})
        assert failure == ("on fact_checker's roster and should not be: "
                           "web_* matches web_fetch")

    def test_the_parents_roster_is_a_ceiling_and_not_a_floor(self):
        """The distinction the whole feature rests on: the parent offers
        web_fetch, the child does not, and only one of the two assertions
        can tell the difference."""
        agent = parent_with(checker(tools=("look",)))
        assert roster_failures(case(has_tools=["web_fetch"]),
                               sorted(agent.registry.names())) == []
        assert graded(agent,
                      subagent_lacks_tools={"fact_checker": ["web_fetch"]}) == []


class TestAChildNamedMustExist:
    def test_a_renamed_child_turns_its_assertions_red(self):
        """The alarm. Renaming without updating the case must not read as
        'the child cannot use web_fetch, because there is no child'."""
        agent = parent_with(checker(name="verifier"))
        (failure,) = graded(
            agent, subagent_lacks_tools={"fact_checker": ["web_fetch"]})
        assert failure == ("no sub-agent called 'fact_checker' is declared "
                           "(declared: verifier)")

    def test_a_package_with_no_children_fails_a_wildcard_assertion(self):
        agent = parent_with()
        (failure,) = graded(agent, subagent_lacks_tools={"*": ["note"]})
        assert "checks nothing" in failure

    def test_a_child_the_admission_policy_dropped_is_not_a_child(self):
        """A declared sub-agent left out of tools.allow never registers, and
        an assertion about it is about a delegation that cannot happen."""
        reg = registry()
        reg.admit_only(allow=["look", "web_fetch", "note"])
        agent = parent_with(checker(), reg=reg)
        assert "fact_checker" not in agent.registry.names()
        (failure,) = graded(agent,
                            subagent_has_tools={"fact_checker": ["look"]})
        assert "no sub-agent called 'fact_checker' is declared" in failure

    def test_a_disabled_child_is_not_a_child_either(self):
        agent = parent_with(checker())
        agent.registry.disable("fact_checker")
        (failure,) = graded(agent, subagent_has_tools={"fact_checker": ["look"]})
        assert "no sub-agent called 'fact_checker' is declared" in failure


class TestEveryChild:
    def test_the_wildcard_covers_children_added_later(self):
        """The strongest form of the alarm: a case written against one
        child grades the second one somebody adds."""
        agent = parent_with(checker(), checker(name="summariser",
                                               tools=("look", "note")))
        (failure,) = graded(agent, subagent_lacks_tools={"*": ["note"]})
        assert failure == "on summariser's roster and should not be: note"

    def test_the_wildcard_grades_children_in_name_order(self):
        agent = parent_with(checker(name="b", tools=("note",)),
                            checker(name="a", tools=("note",)))
        failures = graded(agent, subagent_lacks_tools={"*": ["note"]})
        assert failures == ["on a's roster and should not be: note",
                            "on b's roster and should not be: note"]


class TestAChildThatCannotBeBuilt:
    def test_grading_a_broken_child_reports_the_break_not_the_list(self):
        """Its offered list is a fiction: nothing it holds will reach a
        model, so 'it lacks web_fetch' would be true and useless."""
        reg = registry()
        agent = parent_with(checker(tools=("look", "note")), reg=reg)
        reg.disable("note")
        (failure,) = graded(agent,
                            subagent_lacks_tools={"fact_checker": ["web_fetch"]})
        assert failure == ("fact_checker cannot be built: it declares note, "
                           "which this agent does not offer")

    def test_one_broken_child_named_twice_says_so_once(self):
        reg = registry()
        agent = parent_with(checker(tools=("look", "note")), reg=reg)
        reg.disable("note")
        failures = graded(agent,
                          subagent_has_tools={"fact_checker": ["look"]},
                          subagent_lacks_tools={"fact_checker": ["web_fetch"]})
        assert len(failures) == 1


class TestThroughTheRunnersOwnRegistry:
    """Every tool a suite grades is a recording PROXY, and the first
    version of this feature looked for DeclaredSubagent instances that the
    proxy had swallowed. It found no children, reported none declared, and
    a lacks_ assertion about a child that was right there passed -- the
    vacuous pass, arriving through the one registry every real run uses.
    """

    def test_a_proxied_child_is_still_a_child(self):
        agent = parent_with(checker(tools=("look", "web_fetch")))
        agent.registry = recording_registry(agent.registry, [])
        assert set(declared_rosters(agent)) == {"fact_checker"}
        assert graded(agent, subagent_lacks_tools={"*": ["web_fetch"]}) == [
            "on fact_checker's roster and should not be: web_fetch"]


# ---- the case is still free -------------------------------------------------


class TestItCostsNothing:
    def test_a_child_assertion_alone_is_a_complete_case(self):
        built = case(subagent_lacks_tools={"fact_checker": ["web_fetch"]})
        assert built.grades_a_roster is True
        assert built.needs_a_model is False

    def test_a_case_with_nothing_to_do_is_still_refused(self):
        with pytest.raises(ValueError, match="nothing to do"):
            EvalCase(id="c", description="")


# ---- the manifest surface ---------------------------------------------------


def suite(tmp_path, body: str):
    """A package with an eval suite in it, as an author would lay it out."""
    root = tmp_path / "pkg"
    (root / SUITE_DIR).mkdir(parents=True, exist_ok=True)
    (root / "agent.toml").write_text('[agent]\nname = "pkg"\n')
    (root / SUITE_DIR / CASES).write_text(body, encoding="utf-8")
    return root


class TestTheManifestKeys:
    def test_a_child_table_parses(self, tmp_path):
        (parsed,) = load_cases(suite(tmp_path, '''
[[case]]
id = "the checker stays off the network"
subagent_lacks_tools = { fact_checker = ["web_*"] }
subagent_has_tools = { fact_checker = ["read_file"] }
'''))
        assert parsed.subagent_lacks_tools == {"fact_checker": ["web_*"]}
        assert parsed.subagent_has_tools == {"fact_checker": ["read_file"]}
        assert parsed.user_message == ""

    def test_a_list_instead_of_a_table_says_what_to_write(self, tmp_path):
        with pytest.raises(ConfigError, match="keyed by sub-agent name"):
            load_cases(suite(tmp_path, '''
[[case]]
id = "c"
subagent_lacks_tools = ["web_fetch"]
'''))

    def test_a_pattern_in_the_key_is_refused(self, tmp_path):
        """A key matching no child is the vacuous pass in disguise."""
        with pytest.raises(ConfigError, match="a sub-agent NAME, not a pattern"):
            load_cases(suite(tmp_path, '''
[[case]]
id = "c"
subagent_lacks_tools = { "fact_*" = ["web_fetch"] }
'''))

    def test_the_wildcard_key_is_allowed(self, tmp_path):
        (parsed,) = load_cases(suite(tmp_path, '''
[[case]]
id = "c"
subagent_lacks_tools = { "*" = ["bash"] }
'''))
        assert parsed.subagent_lacks_tools == {"*": ["bash"]}

    def test_an_empty_pattern_list_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="cannot fail"):
            load_cases(suite(tmp_path, '''
[[case]]
id = "c"
subagent_lacks_tools = { fact_checker = [] }
'''))

    def test_a_child_assertion_excuses_a_missing_user_message(self, tmp_path):
        (parsed,) = load_cases(suite(tmp_path, '''
[[case]]
id = "c"
subagent_has_tools = { fact_checker = ["read_file"] }
'''))
        assert parsed.needs_a_model is False

    def test_a_trajectory_key_with_no_task_still_names_the_roster_keys(
            self, tmp_path):
        with pytest.raises(ConfigError, match="subagent_lacks_tools"):
            load_cases(suite(tmp_path, '''
[[case]]
id = "c"
subagent_has_tools = { fact_checker = ["read_file"] }
required_tools = ["read_file"]
'''))


# ---- end to end, through a built package ------------------------------------


class TestThroughASpec:
    def test_a_package_built_from_a_spec_grades_its_declared_child(self):
        """The path an author actually takes: agent.toml -> AgentSpec ->
        build() -> the free half of the gate."""
        spec = AgentSpec(
            name="researcher",
            tool_allow=("look", "web_fetch", "fact_checker"),
            subagents=(checker(tools=("look",)),),
        )
        agent = spec.build(provider=ScriptedProvider([]), model="m",
                           provider_name="ollama",
                           registry=registry(), permissions=yolo)
        assert declared_rosters(agent) == {"fact_checker": (["look"], [])}
        assert graded(agent,
                      subagent_lacks_tools={"*": ["web_fetch"]}) == []

        widened = AgentSpec(
            name="researcher",
            tool_allow=("look", "web_fetch", "fact_checker"),
            subagents=(checker(tools=("look", "web_fetch")),),
        )
        agent = widened.build(provider=ScriptedProvider([]), model="m",
                              provider_name="ollama",
                              registry=registry(), permissions=yolo)
        assert graded(agent, subagent_lacks_tools={"*": ["web_fetch"]}) == [
            "on fact_checker's roster and should not be: web_fetch"]
