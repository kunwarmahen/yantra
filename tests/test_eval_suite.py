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
"""

from __future__ import annotations

import pytest

from conftest import ScriptedProvider, assistant_text, assistant_tool_call
from yantra.errors import ConfigError
from yantra.eval_suite import CASES, SUITE_DIR, find_suite, load_cases
from yantra.evals import EvalCase, EvalRunner, recording_registry
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


class TestTheCliGate:
    """--eval, and the exit code being the whole product."""

    def _run(self, tmp_path, monkeypatch, argv, script=None):
        import yantra.cli.main as cli_main
        provider = ScriptedProvider(script or [assistant_text("done")])
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

    def test_eval_is_its_own_mode(self, tmp_path, monkeypatch, capsys):
        root = _suite(tmp_path, ONE_CASE)
        rc = self._run(tmp_path, monkeypatch,
                       ["--agent", str(root), "--eval", "--provider",
                        "anthropic", "hello"])
        assert rc == 2
        assert "--eval runs the package's suite" in capsys.readouterr().err
