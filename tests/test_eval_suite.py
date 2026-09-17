"""A package's acceptance gate: the suite format, and what it grades.

The bias here is that a gate which passes when it should not is worse
than no gate, because someone will trust it. So most of these tests are
about the ways this one could quietly stop checking: a misspelled key
that turns an assertion into decoration, an empty suite reporting green,
two cases sharing an id, a grader reference that resolves to nothing.

The second bias is that the suite must grade THE PACKAGE. A runner that
built a bare agent would report a verdict on a prompt, skill set and
tool list that nobody ships -- so the tests that matter most here are
the ones proving the package's own prompt, its own tools, and its own
admission policy are what ran.

The third is that the free checks must stay free. A roster assertion that
quietly spent a model call, or a roster FAILURE that paid for a
trajectory nobody will read, would be the whole point of the key thrown
away -- so several of these assert on tokens being zero rather than on a
verdict.
"""

from __future__ import annotations

import pytest

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.errors import ConfigError
from yantra.eval_suite import (
    CASES,
    SUITE_DIR,
    find_suite,
    load_cases,
    render_case,
)
from yantra.evals import (
    CaseOutcome,
    EvalCase,
    EvalResult,
    EvalRunner,
    recording_registry,
    roster_failures,
    roster_of,
)
from yantra.package import MANIFEST, load_package
from yantra.permissions import allow_read_only, yolo
from yantra.tools import default_registry
from yantra.tools.base import Tool, ToolRegistry

ONE_CASE = '''
[[case]]
id = "reads-the-file"
description = "it reads before answering"
user_message = "what does README say?"
required_tools = ["read_file"]
forbidden_tools = ["bash"]
max_tokens = 5000
max_iterations = 4
'''

GRADERS = '''
def cites_a_file(answer):
    return "README" in answer

not_a_function = 3
'''


def _suite(tmp_path, cases_text: str, *, graders: str | None = None,
           package: str | None = None):
    """A package with an eval suite in it, as an author would lay it out."""
    root = tmp_path / "pkg"
    (root / SUITE_DIR).mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(package if package is not None
                                 else '[agent]\nname = "pkg"\n')
    (root / SUITE_DIR / CASES).write_text(cases_text)
    if graders is not None:
        (root / SUITE_DIR / "graders.py").write_text(graders)
    return root


class TestTheFormat:
    """cases.toml -> EvalCase, field for field."""

    def test_a_case_round_trips(self, tmp_path):
        case, = load_cases(_suite(tmp_path, ONE_CASE))
        assert case.id == "reads-the-file"
        assert case.description == "it reads before answering"
        assert case.user_message == "what does README say?"
        assert case.required_tools == ["read_file"]
        assert case.forbidden_tools == ["bash"]
        assert (case.max_tokens, case.max_iterations) == (5000, 4)
        assert case.check_answer is None

    def test_only_id_and_user_message_are_required(self, tmp_path):
        case, = load_cases(_suite(
            tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n'))
        assert (case.description, case.max_tokens) == ("", None)

    def test_a_single_case_table_is_accepted(self, tmp_path):
        """[case] instead of [[case]] is the mistake everyone makes once."""
        case, = load_cases(_suite(
            tmp_path, '[case]\nid = "x"\nuser_message = "hi"\n'))
        assert case.id == "x"

    def test_the_suite_is_found_by_convention(self, tmp_path):
        root = _suite(tmp_path, ONE_CASE)
        assert find_suite(root) == root / SUITE_DIR
        assert find_suite(root / SUITE_DIR) == root / SUITE_DIR
        assert find_suite(root / SUITE_DIR / CASES) == root / SUITE_DIR

    def test_a_package_without_a_suite_is_not_an_error_to_look_for(self, tmp_path):
        (tmp_path / "bare").mkdir()
        assert find_suite(tmp_path / "bare") is None


class TestWhatTheFormatRefuses:
    """Every one of these, unreported, is a gate that checks less than
    its author believes it checks."""

    def test_an_unknown_key_names_the_known_ones(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'user_message = "hi"\nforbiden_tools = ["bash"]\n'))
        assert "forbiden_tools" in str(exc.value)
        assert "known: check, description" in str(exc.value)

    def test_an_empty_suite_is_refused(self, tmp_path):
        """A file with no cases would report green forever."""
        with pytest.raises(ConfigError, match="empty suite"):
            load_cases(_suite(tmp_path, "# nothing here\n"))

    def test_duplicate_ids_are_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="duplicate case id 'x'"):
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'user_message = "a"\n\n[[case]]\nid = "x"\n'
                              'user_message = "b"\n'))

    def test_a_case_needs_a_user_message(self, tmp_path):
        with pytest.raises(ConfigError, match="has no user_message"):
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'))

    def test_a_case_needs_an_id(self, tmp_path):
        with pytest.raises(ConfigError, match=r"case\[0\] has no id"):
            load_cases(_suite(tmp_path, '[[case]]\nuser_message = "hi"\n'))

    def test_a_budget_must_be_a_positive_integer(self, tmp_path):
        with pytest.raises(ConfigError, match="max_tokens must be positive"):
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'user_message = "hi"\nmax_tokens = 0\n'))

    def test_required_tools_must_be_strings(self, tmp_path):
        with pytest.raises(ConfigError, match="must be a list of strings"):
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'user_message = "hi"\nrequired_tools = [7]\n'))

    def test_a_case_may_not_replace_the_prompt(self, tmp_path):
        """The package's prompt is the thing under test. A case that swapped
        it would grade a different agent and report it as this one."""
        with pytest.raises(ConfigError, match="grade some other agent"):
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'user_message = "hi"\nsystem = "you are bob"\n'))

    def test_check_answer_is_pointed_at_the_right_spelling(self, tmp_path):
        with pytest.raises(ConfigError, match="spell it 'check'"):
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'user_message = "hi"\ncheck_answer = "f"\n'))

    def test_an_unknown_table_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="unknown table"):
            load_cases(_suite(tmp_path, '[suite]\nmodel = "x"\n\n[[case]]\n'
                              'id = "x"\nuser_message = "hi"\n'))

    def test_bad_toml_names_the_file(self, tmp_path):
        with pytest.raises(ConfigError, match=f"{CASES}: invalid TOML"):
            load_cases(_suite(tmp_path, "[[case\n"))


class TestGraders:
    """The escape hatch to Python, and the rule that it opens at LOAD time.

    A grader reference that resolves to nothing on case nine has spent
    eight cases of real money to tell you something that was knowable
    before the first request went out.
    """

    def test_a_grader_resolves_to_the_function(self, tmp_path):
        case, = load_cases(_suite(
            tmp_path, ONE_CASE.replace('max_iterations = 4',
                                       'check = "graders:cites_a_file"'),
            graders=GRADERS))
        assert case.check_answer("see README.md") is True
        assert case.check_answer("trust me") is False

    def test_a_missing_module_fails_at_load(self, tmp_path):
        with pytest.raises(ConfigError, match="graders.py, which does not exist"):
            load_cases(_suite(tmp_path, ONE_CASE.replace(
                'max_iterations = 4', 'check = "graders:cites_a_file"')))

    def test_a_missing_function_lists_what_is_there(self, tmp_path):
        with pytest.raises(ConfigError) as exc:
            load_cases(_suite(tmp_path, ONE_CASE.replace(
                'max_iterations = 4', 'check = "graders:cites_a_url"'),
                graders=GRADERS))
        assert "defines no 'cites_a_url'" in str(exc.value)
        assert "it defines: cites_a_file" in str(exc.value)

    def test_a_reference_without_a_function_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="must be 'module:function'"):
            load_cases(_suite(tmp_path, ONE_CASE.replace(
                'max_iterations = 4', 'check = "graders"'), graders=GRADERS))

    def test_a_filename_is_corrected_to_a_module_name(self, tmp_path):
        with pytest.raises(ConfigError, match='write "graders:cites_a_file"'):
            load_cases(_suite(tmp_path, ONE_CASE.replace(
                'max_iterations = 4', 'check = "graders.py:cites_a_file"'),
                graders=GRADERS))

    def test_a_non_function_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="not callable"):
            load_cases(_suite(tmp_path, ONE_CASE.replace(
                'max_iterations = 4', 'check = "graders:not_a_function"'),
                graders=GRADERS))

    def test_the_wrong_signature_is_caught_before_the_run(self, tmp_path):
        """Arity is checkable for free, and un-checkable arity means the
        crash lands after the tokens are spent."""
        with pytest.raises(ConfigError, match=r"must take the answer as its "
                                              r"one argument"):
            load_cases(_suite(tmp_path, ONE_CASE.replace(
                'max_iterations = 4', 'check = "graders:two_args"'),
                graders="def two_args(a, b):\n    return True\n"))

    def test_a_grader_module_that_raises_names_the_file(self, tmp_path):
        with pytest.raises(ConfigError, match="graders.py: ZeroDivisionError"):
            load_cases(_suite(tmp_path, ONE_CASE.replace(
                'max_iterations = 4', 'check = "graders:x"'),
                graders="1 / 0\n"))


class TestRecordingSurvivesLateRegistration:
    """The proxy used to be applied in one sweep. Under a spec, the tools
    that matter most -- the package's own -- arrive after that sweep."""

    def test_a_tool_registered_later_is_still_recorded(self):
        seen: list[str] = []
        registry = recording_registry(ToolRegistry(), seen)

        class Late(Tool):
            name = "late"
            description = "registered after the sweep"
            parameters = {"type": "object", "properties": {},
                          "additionalProperties": False}
            read_only = True

            def summary(self, args, ctx):
                return "late"

            def run(self, args, ctx):
                return "ok"

        registry.register(Late())
        registry.get("late").run({}, None)
        assert seen == ["late"]

    def test_the_admission_policy_still_governs_a_recording_registry(self):
        """A package that excluded a tool must not get it back because
        something was watching."""
        seen: list[str] = []
        registry = recording_registry(default_registry(None), seen)
        registry.admit_only(("read_file",), ())
        assert registry.names() == ["read_file"]

    def test_recording_is_not_doubled_by_re_wrapping(self):
        seen: list[str] = []
        once = recording_registry(default_registry(None), seen)
        twice = recording_registry(once, seen)
        twice.get("read_file")
        assert type(twice.get("read_file")._inner).__name__ == "ReadFile"


class TestTheSuiteGradesThePackage:
    """The load-bearing property. A runner that built a bare agent would
    be grading a prompt and a tool list that nobody ships."""

    PACKAGE = (
        '[agent]\nname = "pkg"\n\n'
        '[model]\nmax_iterations = 9\n\n'
        '[tools]\nallow = ["read_file", "list_dir"]\n'
    )

    def _runner(self, tmp_path, script, **kw):
        root = _suite(tmp_path, ONE_CASE, package=self.PACKAGE)
        (root / "prompt.md").write_text("You are the package's own agent.")
        spec = load_package(root)
        runner = EvalRunner(
            ScriptedProvider(script), "m", tools=default_registry(None),
            permissions=allow_read_only, cwd=root, spec=spec,
            provider_name="anthropic", **kw)
        return runner, root

    def test_the_packages_prompt_and_ceilings_are_what_run(self, tmp_path):
        runner, _ = self._runner(tmp_path, [assistant_text("done")])
        captured: list = []
        original = EvalRunner._result

        def spy(self, case, agent, *args):
            captured.append(agent)
            return original(self, case, agent, *args)

        EvalRunner._result = spy
        try:
            runner.run_case(EvalCase(id="x", description="",
                                     user_message="hi"))
        finally:
            EvalRunner._result = original
        agent, = captured
        assert agent.prompt.get("agent") == "You are the package's own agent."
        assert agent.max_iterations == 9          # the package's, not the runner's
        assert agent.registry.names() == ["list_dir", "read_file"]

    def test_a_package_tool_is_registered_and_recorded(self, tmp_path):
        """The point of grading a package: its OWN code is in the trajectory."""
        root = _suite(tmp_path, ONE_CASE, package=(
            '[agent]\nname = "pkg"\n\n[tools]\nallow = ["shout"]\n'))
        (root / "tools").mkdir()
        (root / "tools" / "shout.py").write_text(
            'from yantra.tools.base import Tool\n\n'
            'class Shout(Tool):\n'
            '    name = "shout"\n'
            '    description = "shout"\n'
            '    parameters = {"type": "object", "properties": {},\n'
            '                  "additionalProperties": False}\n'
            '    read_only = True\n'
            '    def summary(self, args, ctx):\n'
            '        return "shout"\n'
            '    def run(self, args, ctx):\n'
            '        return "HEY"\n')
        runner = EvalRunner(
            ScriptedProvider([assistant_tool_call("1", "shout", {}),
                              assistant_text("said it")]),
            "m", tools=default_registry(None), permissions=allow_read_only,
            cwd=root, spec=load_package(root), provider_name="anthropic")
        result = runner.run_case(EvalCase(
            id="x", description="", user_message="shout",
            required_tools=["shout"]))
        assert result.passed, result.failures
        assert result.tool_calls_seen == ["shout"]

    def test_a_forbidden_tool_that_ran_fails_the_case(self, tmp_path):
        runner, _ = self._runner(tmp_path, [
            assistant_tool_call("1", "list_dir", {"path": "."}),
            assistant_text("done")])
        result = runner.run_case(EvalCase(
            id="x", description="", user_message="hi",
            forbidden_tools=["list_dir"]))
        assert not result.passed
        assert result.failures == ["forbidden tool used: list_dir"]

    def test_failures_accumulate(self, tmp_path):
        """A case that fails five ways reports five; first-fail
        short-circuiting throws away what you diagnose with."""
        runner, _ = self._runner(tmp_path, [assistant_text("nope")])
        result = runner.run_case(EvalCase(
            id="x", description="", user_message="hi",
            required_tools=["read_file"], max_iterations=0,
            check_answer=lambda a: False))
        assert len(result.failures) == 3


class TestRosterAssertionsInTheFormat:
    """has_tools / lacks_tools: the keys that grade the tool LIST.

    The reason they exist is in this file already: a forbidden_tools case
    stays green when the tool is added to tools.allow, because nothing
    executed. That is a real limit of trajectory checks, not a bug, and
    these are the keys that cover it.
    """

    def test_roster_keys_round_trip(self, tmp_path):
        case, = load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                                  'user_message = "hi"\n'
                                  'has_tools = ["read_file"]\n'
                                  'lacks_tools = ["bash"]\n'))
        assert case.has_tools == ["read_file"]
        assert case.lacks_tools == ["bash"]

    def test_a_roster_only_case_needs_no_user_message(self, tmp_path):
        """The whole point of the key: this case costs nothing to grade."""
        case, = load_cases(_suite(tmp_path, '[[case]]\nid = "cannot-write"\n'
                                  'lacks_tools = ["write_file"]\n'))
        assert case.user_message == ""
        assert case.needs_a_model is False

    def test_roster_keys_take_patterns(self, tmp_path):
        case, = load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                                  'lacks_tools = ["browser_*"]\n'))
        assert case.lacks_tools == ["browser_*"]

    def test_a_pattern_in_a_trajectory_key_is_refused(self, tmp_path):
        """A pattern would match nothing in a list of names that RAN, so the
        case would be green and would have checked nothing -- exactly the
        failure mode this format exists to prevent."""
        with pytest.raises(ConfigError) as exc:
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'user_message = "hi"\n'
                              'forbidden_tools = ["write_*"]\n'))
        assert "takes exact tool names, not patterns" in str(exc.value)
        assert "has_tools/lacks_tools" in str(exc.value)

    def test_a_trajectory_key_without_a_task_is_refused(self, tmp_path):
        """A message-less case never runs, so required_tools on one is a
        check that would never be performed."""
        with pytest.raises(ConfigError) as exc:
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'lacks_tools = ["bash"]\n'
                              'required_tools = ["read_file"]\n'
                              'max_tokens = 10\n'))
        assert "grade a trajectory that never happens" in str(exc.value)
        assert "max_tokens, required_tools" in str(exc.value)

    def test_a_case_with_neither_a_task_nor_a_roster_claim_is_refused(self,
                                                                     tmp_path):
        with pytest.raises(ConfigError, match="has no user_message"):
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'description = "asserts nothing"\n'))


class TestPassRateInTheFormat:
    """min_pass_rate: the author's claim, and what it may not be."""

    def test_a_rate_round_trips_and_defaults_to_one(self, tmp_path):
        loose, = load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                                   'user_message = "hi"\n'
                                   'min_pass_rate = 0.7\n'))
        assert loose.min_pass_rate == 0.7
        strict, = load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                                    'user_message = "hi"\n'))
        assert strict.min_pass_rate == 1.0

    def test_an_integer_one_is_a_rate_too(self, tmp_path):
        case, = load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                                  'user_message = "hi"\nmin_pass_rate = 1\n'))
        assert case.min_pass_rate == 1.0

    def test_a_rate_out_of_range_says_it_is_a_fraction(self, tmp_path):
        """'7 of 10' written as 7 is the mistake worth catching, because 7
        would otherwise read as a threshold nothing can ever meet."""
        with pytest.raises(ConfigError) as exc:
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'user_message = "hi"\nmin_pass_rate = 7\n'))
        assert "nine cases in ten is 0.9, not 9" in str(exc.value)

    def test_a_rate_of_zero_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="greater than 0"):
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'user_message = "hi"\nmin_pass_rate = 0\n'))

    def test_repeat_is_not_the_authors_key(self, tmp_path):
        """How many times the gate runs is paid for by whoever runs it."""
        with pytest.raises(ConfigError, match="operator's money"):
            load_cases(_suite(tmp_path, '[[case]]\nid = "x"\n'
                              'user_message = "hi"\nrepeat = 5\n'))


class TestTheRosterIsGradedFree:
    """Zero tokens, no model -- asserted on the provider, not on a verdict.

    ScriptedProvider records every request it is asked for, so an empty
    request log is proof that the gate reached no model at all. That is the
    property the key was added for; a test that only checked pass/fail
    would keep passing if the implementation started paying for it.
    """

    PACKAGE = ('[agent]\nname = "pkg"\n\n'
               '[tools]\nallow = ["read_file", "list_dir"]\n')

    def _runner(self, tmp_path, script=()):
        root = _suite(tmp_path, ONE_CASE, package=self.PACKAGE)
        provider = ScriptedProvider(list(script))
        runner = EvalRunner(provider, "m", tools=default_registry(None),
                            permissions=allow_read_only, cwd=root,
                            spec=load_package(root), provider_name="anthropic")
        return runner, provider

    def test_the_roster_is_what_the_model_is_offered(self):
        registry = default_registry(None)
        registry.admit_only(("read_file", "list_dir", "glob"), ())
        registry.disable("glob")

        class Fake:
            pass

        agent = Fake()
        agent.registry = registry
        # a disabled tool is registered but never reaches the model and
        # cannot be called, so counting it would make lacks_tools lie
        assert roster_of(agent) == ["list_dir", "read_file"]

    def test_a_satisfied_roster_case_makes_no_request(self, tmp_path):
        runner, provider = self._runner(tmp_path)     # empty script: a model
        result = runner.run_case(EvalCase(             # call would raise
            id="cannot-write", description="",
            lacks_tools=["write_file", "bash"], has_tools=["read_file"]))
        assert result.passed and result.failures == []
        assert (result.tokens_used, result.iterations_used) == (0, 0)
        assert result.ran_model is False
        assert provider.requests == []

    def test_a_failed_roster_case_is_not_paid_for(self, tmp_path):
        """A trajectory from an agent with the wrong tool list belongs to a
        different agent, so the run is not bought."""
        runner, provider = self._runner(tmp_path, [assistant_text("hi")])
        result = runner.run_case(EvalCase(
            id="x", description="", user_message="hi",
            lacks_tools=["read_file"]))
        assert not result.passed
        assert result.tokens_used == 0 and result.ran_model is False
        assert provider.requests == []
        assert result.failures == ["on the roster and should not be: read_file"]

    def test_a_missing_tool_names_the_roster_it_looked_at(self, tmp_path):
        runner, _ = self._runner(tmp_path)
        result = runner.run_case(EvalCase(
            id="x", description="", has_tools=["write_file"]))
        assert result.failures == [
            "not on the roster: write_file (roster: list_dir, read_file)"]

    def test_a_pattern_reports_every_tool_it_caught(self):
        case = EvalCase(id="x", description="", lacks_tools=["browser_*"])
        assert roster_failures(case, ["browser_click", "browser_open",
                                      "read_file"]) == [
            "on the roster and should not be: browser_* matches "
            "browser_click, browser_open"]
        assert roster_failures(case, ["read_file"]) == []

    def test_a_long_roster_is_truncated_in_the_message(self):
        case = EvalCase(id="x", description="", has_tools=["nope"])
        failure, = roster_failures(case, [f"t{i}" for i in range(12)])
        assert "+4 more" in failure

    def test_roster_failures_accumulate_like_every_other_check(self):
        case = EvalCase(id="x", description="", has_tools=["a", "b"],
                        lacks_tools=["c"])
        assert len(roster_failures(case, ["c"])) == 3

    def test_the_admission_policy_is_what_a_roster_case_grades(self, tmp_path):
        """The load-bearing claim: add the tool to tools.allow and the case
        goes red on its own -- no model, no prompt to talk out of it.

        This is the assertion the 'cannot write even when asked' case could
        not make (see the researcher package's own suite).
        """
        for allow, expect_pass in ((["read_file"], True),
                                   (["read_file", "write_file"], False)):
            root = _suite(tmp_path / str(len(allow)), ONE_CASE, package=(
                '[agent]\nname = "pkg"\n\n[tools]\nallow = '
                + repr(allow).replace("'", '"') + "\n"))
            runner = EvalRunner(ScriptedProvider([]), "m",
                                tools=default_registry(None),
                                permissions=allow_read_only, cwd=root,
                                spec=load_package(root),
                                provider_name="anthropic")
            result = runner.run_case(EvalCase(
                id="cannot-write", description="",
                lacks_tools=["write_file"]))
            assert result.passed is expect_pass, result.failures


class TestPassRatesOverRepeatedRuns:
    """n runs and a threshold: the statistically honest version of a gate.

    The bias: a repeated case must not become a way to LOWER the bar by
    accident. So the threshold rounds up, a case that fails its rate is
    red however many runs it won, and the default (1.0 over one run) is
    the old behaviour exactly.
    """

    def _runner(self, tmp_path, script):
        root = _suite(tmp_path, ONE_CASE,
                      package='[agent]\nname = "pkg"\n\n[tools]\n'
                              'allow = ["read_file", "list_dir"]\n')
        provider = ScriptedProvider(script)
        return EvalRunner(provider, "m", tools=default_registry(None),
                          permissions=allow_read_only, cwd=root,
                          spec=load_package(root),
                          provider_name="anthropic"), provider

    def test_three_runs_of_one_case_are_three_runs(self, tmp_path):
        runner, provider = self._runner(tmp_path, [assistant_text("done")] * 3)
        outcome = runner.evaluate(EvalCase(id="x", description="",
                                           user_message="hi"), repeat=3)
        assert outcome.attempts == 3 and outcome.passes == 3
        assert len(provider.requests) == 3       # three real trajectories
        assert outcome.passed and outcome.marks == "✓✓✓"

    def test_a_rate_below_one_tolerates_a_losing_run(self, tmp_path):
        """The point of the key: 'this holds seven times in ten' is a claim
        a gate can hold an agent to, and 'it passed once' is not."""
        runner, _ = self._runner(tmp_path, [
            assistant_tool_call("1", "read_file", {"path": "agent.toml"}),
            assistant_text("read it"),
            assistant_text("did not read it"),
            assistant_tool_call("2", "read_file", {"path": "agent.toml"}),
            assistant_text("read it"),
        ])
        case = EvalCase(id="x", description="", user_message="hi",
                        required_tools=["read_file"], min_pass_rate=0.6)
        outcome = runner.evaluate(case, repeat=3)
        assert (outcome.passes, outcome.attempts) == (2, 3)
        assert outcome.marks == "✓✗✓"
        assert outcome.passed              # 2/3 >= ceil(0.6 * 3) = 2
        assert outcome.failures == ["required tool not used: read_file "
                                     "(1 of 3 runs)"]

    def test_the_threshold_rounds_up(self):
        """A threshold that rounded down would pass a suite its author said
        should fail -- 0.7 of 10 is seven runs, not six."""
        case = EvalCase(id="x", description="", user_message="hi",
                        min_pass_rate=0.7)
        runs = [EvalResult(case_id="x", passed=i < 6, failures=[],
                           final_answer="", tokens_used=1, iterations_used=1,
                           tool_calls_seen=[], duration_seconds=0.0)
                for i in range(10)]
        outcome = CaseOutcome.of(case, runs)
        assert outcome.required_passes == 7
        assert outcome.passes == 6 and not outcome.passed

    def test_one_run_at_the_default_is_the_old_behaviour(self, tmp_path):
        runner, _ = self._runner(tmp_path, [assistant_text("done")])
        outcome = runner.evaluate(EvalCase(id="x", description="",
                                           user_message="hi"))
        assert outcome.attempts == 1 and outcome.passed
        assert outcome.failures == []          # verbatim, not annotated
        assert outcome.marks == "✓"

    def test_a_roster_only_case_is_never_repeated(self, tmp_path):
        """n copies of a free check is not a statistic. Reporting '0 of 5
        runs' for one deterministic fact would dress it up as one."""
        runner, provider = self._runner(tmp_path, [])
        outcome = runner.evaluate(EvalCase(id="x", description="",
                                           lacks_tools=["bash"]), repeat=5)
        assert outcome.attempts == 1 and outcome.passed
        assert provider.requests == []

    def test_a_failed_roster_check_collapses_instead_of_repeating(self, tmp_path):
        runner, _ = self._runner(tmp_path, [assistant_text("x")] * 4)
        outcome = runner.evaluate(EvalCase(
            id="x", description="", user_message="hi",
            lacks_tools=["read_file"]), repeat=4)
        assert outcome.attempts == 1 and not outcome.passed
        assert outcome.tokens_used == 0

    def test_run_suite_reports_every_case_once(self, tmp_path):
        runner, _ = self._runner(tmp_path, [assistant_text("done")] * 4)
        seen: list[str] = []
        outcomes = runner.run_suite(
            [EvalCase(id="a", description="", user_message="hi"),
             EvalCase(id="b", description="", user_message="hi")],
            repeat=2, on_outcome=lambda o: seen.append(o.case_id))
        assert [o.case_id for o in outcomes] == ["a", "b"] == seen
        assert all(o.attempts == 2 for o in outcomes)

    def test_a_case_cannot_be_built_with_a_nonsense_rate(self):
        with pytest.raises(ValueError, match="min_pass_rate"):
            EvalCase(id="x", description="", user_message="hi",
                     min_pass_rate=0)

    def test_a_case_cannot_be_built_with_nothing_to_do(self):
        """The library refuses it too, not only the file format: a case that
        asserts nothing is a green case forever."""
        with pytest.raises(ValueError, match="nothing to do"):
            EvalCase(id="x", description="")


class TestTheCliGate:
    """--eval, and the exit code being the whole product."""

    def _run(self, tmp_path, monkeypatch, argv, script=None):
        import yantra.cli.main as cli_main
        provider = ScriptedProvider(script or [assistant_text("done")])
        self.provider = provider   # for the tests that assert on REQUESTS
        monkeypatch.setattr(cli_main, "load_settings", lambda name: object())
        monkeypatch.setattr(cli_main, "get_provider", lambda *a, **k: provider)
        return cli_main.main(argv)

    def test_a_green_suite_exits_zero(self, tmp_path, monkeypatch):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic"])
        assert rc == 0

    def test_a_red_suite_exits_one(self, tmp_path, monkeypatch):
        """The whole feature: CI can tell, without reading the output."""
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n'
                                'required_tools = ["read_file"]\n')
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic"])
        assert rc == 1

    def test_a_broken_suite_exits_two_before_any_model_call(self, tmp_path,
                                                            monkeypatch):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n'
                                'check = "graders:nope"\n')
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic"], script=[])   # an empty script: a model
        assert rc == 2                             # call would raise here

    def test_a_package_without_a_suite_says_what_to_create(self, tmp_path,
                                                           monkeypatch,
                                                           capsys):
        root = tmp_path / "bare"
        root.mkdir()
        (root / MANIFEST).write_text('[agent]\nname = "bare"\n')
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic"])
        assert rc == 2
        assert f"{SUITE_DIR}/{CASES}" in capsys.readouterr().err

    def test_eval_without_a_package_is_refused(self, tmp_path, monkeypatch,
                                               capsys):
        rc = self._run(tmp_path, monkeypatch,
                       ["--cwd", str(tmp_path), "--eval", "--provider",
                        "anthropic"])
        assert rc == 2
        assert "--agent DIR" in capsys.readouterr().err

    def test_a_package_cannot_open_its_own_gate(self, tmp_path, monkeypatch):
        """An author who ships mode = "yolo" does not get their own
        acceptance run graded with the safety off."""
        import yantra.cli.main as cli_main
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n',
                      package='[agent]\nname = "pkg"\n\n'
                              '[permissions]\nmode = "yolo"\n')
        gates: list = []
        original = cli_main.EvalRunner

        def spy(*args, **kwargs):
            gates.append(kwargs["permissions"])
            return original(*args, **kwargs)

        monkeypatch.setattr(cli_main, "EvalRunner", spy)
        self._run(tmp_path, monkeypatch,
                  ["--agent", str(root), "--eval", "--provider", "anthropic"])
        assert gates == [allow_read_only]

    def test_yolo_is_the_operators_to_give(self, tmp_path, monkeypatch):
        import yantra.cli.main as cli_main
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        gates: list = []
        original = cli_main.EvalRunner
        monkeypatch.setattr(cli_main, "EvalRunner",
                            lambda *a, **k: (gates.append(k["permissions"]),
                                             original(*a, **k))[1])
        self._run(tmp_path, monkeypatch,
                  ["--agent", str(root), "--eval", "--yolo", "--provider",
                   "anthropic"])
        assert gates == [yolo]

    def test_a_roster_only_suite_is_a_gate_that_costs_nothing(self, tmp_path,
                                                              monkeypatch,
                                                              capsys):
        """The shape CI can afford on every push: a verdict about the tool
        list, no model, no tokens. The empty script is the proof -- any
        request at all would raise."""
        root = _suite(tmp_path, '[[case]]\nid = "cannot-write"\n'
                                'lacks_tools = ["write_file", "bash"]\n'
                                'has_tools = ["read_file"]\n',
                      package='[agent]\nname = "pkg"\n\n[tools]\n'
                              'allow = ["read_file", "list_dir"]\n')
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic"], script=[])
        assert rc == 0
        assert self.provider.requests == []
        out = capsys.readouterr().out
        assert "1 roster-only" in out
        assert "roster only · no model call · 0 tok" in out
        assert "cost nothing" in out

    def test_a_roster_assertion_can_turn_a_suite_red_for_free(self, tmp_path,
                                                              monkeypatch,
                                                              capsys):
        root = _suite(tmp_path, '[[case]]\nid = "cannot-write"\n'
                                'lacks_tools = ["read_file"]\n',
                      package='[agent]\nname = "pkg"\n\n[tools]\n'
                              'allow = ["read_file"]\n')
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic"], script=[])
        assert rc == 1
        assert self.provider.requests == []
        # a FAILED roster reads differently from a roster-only PASS: one is
        # a case that had nothing to run, the other a task never paid for
        assert "roster failed · no model call" in capsys.readouterr().out

    def test_repeat_buys_one_run_per_repeat(self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic", "--repeat", "3"],
                       script=[assistant_text("done")] * 3)
        assert rc == 0
        assert len(self.provider.requests) == 3
        out = capsys.readouterr().out
        assert "3 runs each" in out and "✓✓✓ 3/3 runs" in out
        assert "1/1 passed · 3 runs" in out

    def test_a_case_under_its_rate_is_red_over_repeats(self, tmp_path,
                                                       monkeypatch, capsys):
        """Two of three runs used the tool; the case claims nine in ten."""
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n'
                                'required_tools = ["read_file"]\n'
                                'min_pass_rate = 0.9\n')
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic", "--repeat", "3"],
                       script=[assistant_tool_call("1", "read_file",
                                                   {"path": "agent.toml"}),
                               assistant_text("read it"),
                               assistant_text("did not"),
                               assistant_tool_call("2", "read_file",
                                                   {"path": "agent.toml"}),
                               assistant_text("read it")])
        assert rc == 1
        out = capsys.readouterr().out
        assert "✓✗✓ 2/3 runs (needs 3)" in out
        assert "required tool not used: read_file (1 of 3 runs)" in out

    def test_a_declared_rate_at_one_run_says_it_cannot_be_honoured(
            self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n'
                                'min_pass_rate = 0.7\n')
        self._run(tmp_path, monkeypatch,
                  ["--agent", str(root), "--eval", "--provider", "anthropic"])
        assert "--repeat N buys the evidence" in capsys.readouterr().out

    def test_async_drives_the_same_suite_to_the_same_verdict(self, tmp_path,
                                                            monkeypatch,
                                                            capsys):
        root = _suite(tmp_path, '[[case]]\nid = "a"\nuser_message = "hi"\n\n'
                                '[[case]]\nid = "b"\nuser_message = "hi"\n'
                                'required_tools = ["read_file"]\n')
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic", "--async", "2"],
                       script=[assistant_text("done")] * 2)
        assert rc == 1                      # case b never read anything
        out = capsys.readouterr().out
        assert "mode: async, 2 trajectories at once" in out
        assert "1/2 passed" in out

    def test_async_takes_a_default_width(self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic", "--async"])
        assert rc == 0
        assert "async, 4 trajectories" in capsys.readouterr().out

    def test_repeat_below_one_is_refused(self, tmp_path, monkeypatch, capsys):
        """A suite that runs nothing passes everything."""
        root = _suite(tmp_path, ONE_CASE)
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic", "--repeat", "0"])
        assert rc == 2
        assert "--repeat must be at least 1" in capsys.readouterr().err

    def test_repeat_and_async_belong_to_the_gate(self, tmp_path, monkeypatch,
                                                capsys):
        rc = self._run(tmp_path, monkeypatch,
                       ["--cwd", str(tmp_path), "--repeat", "3", "--provider",
                        "anthropic", "hello"])
        assert rc == 2
        assert "belong to --eval" in capsys.readouterr().err

    def test_eval_is_its_own_mode(self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, ONE_CASE)
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic", "hello"])
        assert rc == 2
        assert "--eval runs the package's suite" in capsys.readouterr().err


class TestPointingTheGate:
    """--case, and the difference between a subset and a gate.

    The bias: a filtered run produces a green line that looks exactly like
    the package's verdict, and that line is what somebody pastes into a
    pull request. So these tests read the WORD, not just the exit code.
    """

    def _run(self, tmp_path, monkeypatch, argv, script=None):
        import yantra.cli.main as cli_main
        provider = ScriptedProvider(script or [assistant_text("done")])
        self.provider = provider
        monkeypatch.setattr(cli_main, "load_settings", lambda name: object())
        monkeypatch.setattr(cli_main, "get_provider", lambda *a, **k: provider)
        return cli_main.main(argv)

    TWO = ('[[case]]\nid = "alpha"\nuser_message = "hi"\n\n'
           '[[case]]\nid = "beta"\nuser_message = "hi"\n')

    def test_a_pattern_runs_only_the_cases_it_matches(self, tmp_path,
                                                      monkeypatch, capsys):
        root = _suite(tmp_path, self.TWO)
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic", "--case", "al*"],
                       script=[assistant_text("done")])
        assert rc == 0
        assert len(self.provider.requests) == 1   # beta never ran
        out = capsys.readouterr().out
        assert "alpha" in out and "beta" not in out

    def test_a_filtered_run_is_not_the_packages_gate(self, tmp_path,
                                                     monkeypatch, capsys):
        root = _suite(tmp_path, self.TWO)
        self._run(tmp_path, monkeypatch,
                  ["--agent", str(root), "--eval", "--provider", "anthropic",
                   "--case", "alpha"], script=[assistant_text("done")])
        out = capsys.readouterr().out
        assert "SUBSET GREEN" in out
        assert "SUITE GREEN" not in out
        assert "1 of 2 case(s)" in out
        assert "1 case(s) not run" in out

    def test_several_patterns_union(self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, self.TWO)
        self._run(tmp_path, monkeypatch,
                  ["--agent", str(root), "--eval", "--provider", "anthropic",
                   "--case", "alpha", "--case", "beta"],
                  script=[assistant_text("done")] * 2)
        assert len(self.provider.requests) == 2

    def test_a_pattern_that_matches_nothing_is_an_error_not_a_pass(
            self, tmp_path, monkeypatch, capsys):
        """A suite that runs nothing passes everything -- the same rule an
        empty cases.toml gets."""
        root = _suite(tmp_path, self.TWO)
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic", "--case", "gamma"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "no case matches" in err
        assert "alpha, beta" in err            # says what there is

    def test_the_point_of_it_is_repeat_on_one_case(self, tmp_path,
                                                   monkeypatch, capsys):
        """The leftover this closes: --repeat 10 used to drag every
        deterministic case along with the one that needed the evidence."""
        root = _suite(tmp_path, self.TWO)
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic", "--case", "beta", "--repeat", "3"],
                       script=[assistant_text("done")] * 3)
        assert rc == 0
        assert len(self.provider.requests) == 3   # three, not six

    def test_case_belongs_to_the_gate(self, tmp_path, monkeypatch, capsys):
        rc = self._run(tmp_path, monkeypatch,
                       ["--cwd", str(tmp_path), "--case", "x", "--provider",
                        "anthropic", "hello"])
        assert rc == 2
        assert "belong to --eval" in capsys.readouterr().err


class TestTheGateWithoutAKey:
    """A roster gate that costs zero tokens AND zero setup.

    The bias is the one note 35 admitted and could not fix: grading a
    roster means BUILDING the agent, and an agent takes a provider, so a
    suite that made no request still demanded a key. These tests assert
    that the resolution functions are never reached -- not that the run
    happened to succeed, which a cached key in the environment would also
    produce.
    """

    def _run_keyless(self, monkeypatch, argv):
        """No provider resolution available at all: any attempt explodes."""
        import yantra.cli.main as cli_main

        def refuse(*args, **kwargs):
            raise AssertionError("a roster-only run resolved a provider")

        monkeypatch.setattr(cli_main, "guess_provider", refuse)
        monkeypatch.setattr(cli_main, "load_settings", refuse)
        monkeypatch.setattr(cli_main, "get_provider", refuse)
        return cli_main.main(argv)

    def test_a_roster_only_suite_needs_no_provider_at_all(self, tmp_path,
                                                          monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "cannot-write"\n'
                                'lacks_tools = ["write_file", "bash"]\n'
                                'has_tools = ["read_file"]\n',
                      package='[agent]\nname = "pkg"\n\n[tools]\n'
                              'allow = ["read_file", "list_dir"]\n')
        rc = self._run_keyless(monkeypatch, ["--agent", str(root), "--eval"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "no provider resolved" in out
        assert "0 tokens" in out

    def test_it_can_still_go_red_without_a_key(self, tmp_path, monkeypatch,
                                               capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\n'
                                'lacks_tools = ["read_file"]\n',
                      package='[agent]\nname = "pkg"\n\n[tools]\n'
                              'allow = ["read_file"]\n')
        rc = self._run_keyless(monkeypatch, ["--agent", str(root), "--eval"])
        assert rc == 1

    def test_a_ceiling_does_not_block_a_run_that_spends_nothing(
            self, tmp_path, monkeypatch, capsys):
        """A ceiling refuses to be BUILT against a model nobody can price
        (notes/34) -- correct, and beside the point when there is no model."""
        root = _suite(tmp_path, '[[case]]\nid = "x"\n'
                                'has_tools = ["read_file"]\n',
                      package='[agent]\nname = "pkg"\n\n'
                              '[budget]\nmax_usd_per_turn = 0.5\n')
        rc = self._run_keyless(monkeypatch, ["--agent", str(root), "--eval"])
        assert rc == 0

    def test_filtering_down_to_roster_cases_drops_the_key_requirement(
            self, tmp_path, monkeypatch, capsys):
        """The two features compose: --case can turn a suite that needs a
        model into a run that does not."""
        root = _suite(tmp_path, '[[case]]\nid = "costly"\nuser_message = "hi"\n'
                                '\n[[case]]\nid = "free"\n'
                                'has_tools = ["read_file"]\n')
        rc = self._run_keyless(monkeypatch,
                               ["--agent", str(root), "--eval",
                                "--case", "free"])
        assert rc == 0
        assert "SUBSET GREEN" in capsys.readouterr().out

    def test_one_case_needing_a_model_still_needs_the_key(self, tmp_path,
                                                          monkeypatch, capsys):
        """No half measures: the resolution is per RUN, so one trajectory
        case brings the whole requirement back."""
        import yantra.cli.main as cli_main
        root = _suite(tmp_path, '[[case]]\nid = "costly"\nuser_message = "hi"\n'
                                '\n[[case]]\nid = "free"\n'
                                'has_tools = ["read_file"]\n')
        monkeypatch.setattr(cli_main, "guess_provider",
                            lambda: (_ for _ in ()).throw(
                                ConfigError("no API key found")))
        rc = cli_main.main(["--agent", str(root), "--eval"])
        assert rc == 2
        assert "no API key found" in capsys.readouterr().err

    def test_the_offline_provider_refuses_loudly_rather_than_answering(self):
        """If the accounting is ever wrong, it must be an error and not a
        mysteriously empty answer."""
        from yantra.evals import OfflineProvider
        with pytest.raises(ConfigError, match="resolved no provider"):
            OfflineProvider().complete(messages=[], system=None, tools=[],
                                       model="m")


class TestTheServersUnderTheGate:
    """Declared MCP servers, and why an unreachable one is RED.

    The bias here is the one that made `has_tools = ["mcp__*"]` vacuous
    before: --eval opened no servers, so the assertion graded an empty set
    and passed by being about nothing. Fixing that creates the opposite
    risk -- a server that fails to start leaves an agent with fewer tools
    than it ships with, and grading THAT green is the failure this whole
    format exists to prevent. So the tests read the exit code on the
    unreachable path, not just the output.
    """

    WITH_SERVER = ('[agent]\nname = "pkg"\n\n'
                   '[[mcp]]\nname = "tiny"\ncommand = "true"\n')

    def _run(self, tmp_path, monkeypatch, argv, *, tools=("echo",),
             fails: str | None = None):
        import yantra.cli.main as cli_main
        from yantra.mcp import MCPError

        opened: list[str] = []
        closed: list[str] = []

        class FakeManager:
            def __init__(self, registry, **kwargs):
                self.registry = registry

            def connect(self, cfg, **kwargs):
                if fails is not None:
                    raise MCPError(fails)
                names = []
                for tool in tools:
                    name = f"mcp__{cfg.name}__{tool}"
                    self.registry.register(_named_tool(name))
                    names.append(name)
                opened.append(cfg.name)
                return names

            def shutdown(self):
                closed.extend(opened)

        monkeypatch.setattr(cli_main, "MCPManager", FakeManager)
        monkeypatch.setattr(cli_main, "guess_provider",
                            lambda: "anthropic")
        monkeypatch.setattr(cli_main, "load_settings", lambda name: object())
        monkeypatch.setattr(cli_main, "get_provider",
                            lambda *a, **k: ScriptedProvider([]))
        self.opened, self.closed = opened, closed
        return cli_main.main(argv)

    def test_a_declared_servers_tools_are_on_the_roster(self, tmp_path,
                                                        monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\n'
                                'has_tools = ["mcp__tiny__*"]\n',
                      package=self.WITH_SERVER)
        rc = self._run(tmp_path, monkeypatch, ["--agent", str(root), "--eval"])
        assert rc == 0
        assert self.opened == ["tiny"]
        assert "1 tool(s) under test" in capsys.readouterr().out

    def test_an_unreachable_server_is_red_not_a_smaller_agent(self, tmp_path,
                                                              monkeypatch,
                                                              capsys):
        """Everywhere else in this CLI a dead server is a warning. A gate
        is the one place it cannot be."""
        root = _suite(tmp_path, '[[case]]\nid = "x"\n'
                                'has_tools = ["mcp__tiny__*"]\n',
                      package=self.WITH_SERVER)
        rc = self._run(tmp_path, monkeypatch, ["--agent", str(root), "--eval"],
                       fails="connection refused")
        assert rc == 2
        err = capsys.readouterr().err
        assert "smaller than the one that ships" in err
        assert "connection refused" in err

    def test_no_mcp_says_the_agent_under_test_is_smaller(self, tmp_path,
                                                         monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\n'
                                'has_tools = ["mcp__tiny__*"]\n',
                      package=self.WITH_SERVER)
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--no-mcp"])
        assert rc == 1                      # the assertion fails, honestly
        out = capsys.readouterr().out
        assert "NOT under test" in out
        assert self.opened == []

    def test_the_servers_are_closed_when_the_run_ends(self, tmp_path,
                                                      monkeypatch, capsys):
        root = _suite(tmp_path, '[[case]]\nid = "x"\n'
                                'has_tools = ["mcp__tiny__*"]\n',
                      package=self.WITH_SERVER)
        self._run(tmp_path, monkeypatch, ["--agent", str(root), "--eval"])
        assert self.closed == ["tiny"]

    def test_the_packages_own_policy_still_governs_them(self, tmp_path,
                                                        monkeypatch, capsys):
        """A whitelist is COMPLETE, MCP tools included -- connecting a
        server does not smuggle its tools past the package's own list."""
        root = _suite(tmp_path, '[[case]]\nid = "x"\n'
                                'lacks_tools = ["mcp__tiny__*"]\n',
                      package=self.WITH_SERVER
                      + '\n[tools]\nallow = ["read_file"]\n')
        rc = self._run(tmp_path, monkeypatch, ["--agent", str(root), "--eval"])
        assert rc == 0                      # they were refused admission


def _named_tool(name: str) -> Tool:
    """A minimal registry-fillable Tool under an exact name."""
    class Named(Tool):
        read_only = True

        def __init__(self) -> None:
            self.name = name
            self.description = "a tool from a server"
            self.parameters = {"type": "object", "properties": {}}

        def summary(self, args, ctx):
            return name

        def run(self, args, ctx):
            return ""

    return Named()


# ---- writing a case back out ------------------------------------------------


class TestRenderingACase:
    """Bias: a writer that drifts from the reader beside it.

    Every test here is a round trip rather than a string comparison,
    because what matters is not what the block LOOKS like -- it is that
    the parser one module up reads back what was written. The strings are
    chosen to be the ones a naive quoter breaks on: an error message with
    quotes in it, a description over several lines, a Windows path.
    """

    def roundtrip(self, tmp_path, case: EvalCase) -> EvalCase:
        (loaded,) = load_cases(_suite(tmp_path, render_case(case)))
        return loaded

    def test_a_minimal_case_survives(self, tmp_path):
        case = EvalCase(id="c", description="d", user_message="do the thing")
        back = self.roundtrip(tmp_path, case)
        assert (back.id, back.description, back.user_message) == (
            "c", "d", "do the thing")

    def test_a_description_with_quotes_and_newlines_survives(self, tmp_path):
        text = 'crashed: KeyError: "path"\nand then again on the retry'
        back = self.roundtrip(tmp_path, EvalCase(id="c", description=text,
                                                 user_message="x"))
        assert back.description == text

    def test_a_message_with_a_backslash_survives(self, tmp_path):
        text = r"read C:\Users\me\notes.txt and say what is in it"
        back = self.roundtrip(tmp_path, EvalCase(id="c", description="d",
                                                 user_message=text))
        assert back.user_message == text

    def test_a_value_ending_in_a_quote_does_not_run_into_the_delimiter(
            self, tmp_path):
        text = 'the model answered "no"\nand stopped there"'
        back = self.roundtrip(tmp_path, EvalCase(id="c", description=text,
                                                 user_message="x"))
        assert back.description == text

    def test_every_assertion_survives(self, tmp_path):
        case = EvalCase(
            id="c", description="d", user_message="x",
            required_tools=["read_file"], forbidden_tools=["bash"],
            has_tools=["glob"], lacks_tools=["write_*"],
            subagent_has_tools={"fact_checker": ["read_file"]},
            subagent_lacks_tools={"*": ["web_*"]},
            max_tokens=3000, max_iterations=6, min_pass_rate=0.7,
        )
        back = self.roundtrip(tmp_path, case)
        for key in ("required_tools", "forbidden_tools", "has_tools",
                    "lacks_tools", "subagent_has_tools",
                    "subagent_lacks_tools", "max_tokens", "max_iterations",
                    "min_pass_rate"):
            assert getattr(back, key) == getattr(case, key), key

    def test_a_roster_only_case_survives_without_a_message(self, tmp_path):
        case = EvalCase(id="c", description="d", lacks_tools=["bash"])
        back = self.roundtrip(tmp_path, case)
        assert back.user_message == ""
        assert back.needs_a_model is False

    def test_defaults_are_not_written_as_choices(self, tmp_path):
        """A block full of min_pass_rate = 1.0 reads as though somebody
        picked it."""
        block = render_case(EvalCase(id="c", description="d",
                                     user_message="x"))
        for key in ("min_pass_rate", "max_tokens", "required_tools",
                    "lacks_tools", "subagent_has_tools"):
            assert key not in block

    def test_a_case_carrying_python_refuses_to_be_written(self, tmp_path):
        """Dropping the assertion silently would be a suite that checks
        less than its author believes -- the failure this format exists
        against."""
        case = EvalCase(id="c", description="d", user_message="x",
                        check_answer=lambda answer: True)
        with pytest.raises(ConfigError, match="carries Python"):
            render_case(case)

    def test_two_rendered_cases_append_into_one_suite(self, tmp_path):
        """The shape the failure loop actually uses: a block appended to a
        file that already had cases in it."""
        text = (render_case(EvalCase(id="one", description="d",
                                     user_message="a"))
                + "\n"
                + render_case(EvalCase(id="two", description="d",
                                       user_message="b")))
        assert [c.id for c in load_cases(_suite(tmp_path, text))] == ["one",
                                                                      "two"]
