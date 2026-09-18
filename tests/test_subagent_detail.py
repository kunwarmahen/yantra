"""What a manifest decided about a child, besides its tool list.

The bias is the one note 44 installed and this extends: an assertion
about a child that does not exist must be RED, never green. A typo'd
name, a renamed sub-agent, a package that declares no children at all --
each of those makes the claim vacuously true, and a vacuously true claim
is the one shape of green nobody checks.

The second bias is that these are free. A case built only of these keys
must reach no model, so the tests run without a provider at all and
would raise if anything tried to make a request.

The third is about prose. A prompt assertion matches a SUBSTRING, case
insensitively, and is not a pattern: ``*quote*`` must fail against a
prompt containing the word "quote", or authors end up debugging regexes
against instruction files.
"""

from __future__ import annotations

import pytest

from yantra.errors import ConfigError
from yantra.eval_suite import load_cases, render_case
from yantra.evals import EvalCase, subagent_detail_failures
from yantra.subagent import SubagentSpec


def child(name="fact_checker", instructions="Quote the FILE and LINE number.",
          model=None, max_iterations=12, tools=("read_file",)):
    return SubagentSpec(name=name, description="d", instructions=instructions,
                        tools=tuple(tools), model=model,
                        max_iterations=max_iterations)


def specs(*children):
    return {c.name: c for c in children}


def case(**keys):
    return EvalCase(id="x", description="", **keys)


class TestThePrompt:
    def test_a_phrase_the_prompt_carries_passes(self):
        assert subagent_detail_failures(
            case(subagent_prompt_contains={"fact_checker": ["line number"]}),
            specs(child())) == []

    def test_matching_ignores_case(self):
        assert subagent_detail_failures(
            case(subagent_prompt_contains={"fact_checker": ["LINE NUMBER"]}),
            specs(child())) == []

    def test_a_phrase_it_lost_is_named(self):
        """The failure this exists for: somebody rewrote the prompt."""
        (failure,) = subagent_detail_failures(
            case(subagent_prompt_contains={"fact_checker": ["line number"]}),
            specs(child(instructions="Answer the question.")))
        assert "does not mention" in failure and "line number" in failure

    def test_a_phrase_it_must_not_carry(self):
        (failure,) = subagent_detail_failures(
            case(subagent_prompt_lacks={"fact_checker": ["guess"]}),
            specs(child(instructions="Never guess; quote the source.")))
        assert "mentions" in failure and "should not" in failure

    def test_a_phrase_is_a_substring_and_not_a_pattern(self):
        """`*quote*` is four characters that do not appear in the prompt,
        and reading it as a glob would make this assertion pass."""
        (failure,) = subagent_detail_failures(
            case(subagent_prompt_contains={"fact_checker": ["*quote*"]}),
            specs(child(instructions="Quote the source.")))
        assert "does not mention" in failure

    def test_every_child_covers_children_added_later(self):
        failures = subagent_detail_failures(
            case(subagent_prompt_lacks={"*": ["ignore the sources"]}),
            specs(child(), child(name="summarizer",
                                 instructions="Ignore the sources.")))
        assert len(failures) == 1 and "summarizer" in failures[0]


class TestTheModel:
    def test_the_slug_the_manifest_names(self):
        assert subagent_detail_failures(
            case(subagent_model={"fact_checker": "gemma4:12b"}),
            specs(child(model="gemma4:12b"))) == []

    def test_a_different_slug_is_named_both_ways(self):
        (failure,) = subagent_detail_failures(
            case(subagent_model={"fact_checker": "gemma4:12b"}),
            specs(child(model="qwen3.8:27b")))
        assert "qwen3.8:27b" in failure and "gemma4:12b" in failure

    def test_empty_string_asserts_it_runs_on_the_parents_model(self):
        assert subagent_detail_failures(
            case(subagent_model={"fact_checker": ""}), specs(child())) == []

    def test_a_child_that_grew_its_own_model_fails_that_claim(self):
        """The edit worth catching: a child quietly pointed at a dearer
        model than the one the operator chose."""
        (failure,) = subagent_detail_failures(
            case(subagent_model={"fact_checker": ""}),
            specs(child(model="claude-opus-5")))
        assert "declares its own model" in failure

    def test_a_child_with_no_model_fails_a_named_slug(self):
        (failure,) = subagent_detail_failures(
            case(subagent_model={"fact_checker": "gemma4:12b"}),
            specs(child(model=None)))
        assert "the parent's model" in failure


class TestTheIterationCap:
    def test_a_cap_at_or_under_the_ceiling_passes(self):
        assert subagent_detail_failures(
            case(subagent_iterations_at_most={"fact_checker": 12}),
            specs(child(max_iterations=12))) == []
        assert subagent_detail_failures(
            case(subagent_iterations_at_most={"fact_checker": 12}),
            specs(child(max_iterations=4))) == []

    def test_a_cap_above_it_is_named_with_both_numbers(self):
        (failure,) = subagent_detail_failures(
            case(subagent_iterations_at_most={"fact_checker": 12}),
            specs(child(max_iterations=200)))
        assert "200" in failure and "12" in failure

    def test_it_is_a_ceiling_and_not_an_equality(self):
        """A case that reddened when somebody LOWERED a cap is a case
        nobody keeps."""
        assert subagent_detail_failures(
            case(subagent_iterations_at_most={"*": 20}),
            specs(child(max_iterations=1))) == []


class TestAChildThatIsNotThere:
    def test_a_typod_name_is_red_in_every_key(self):
        for keys in ({"subagent_prompt_contains": {"fact_checkr": ["x"]}},
                     {"subagent_prompt_lacks": {"fact_checkr": ["x"]}},
                     {"subagent_model": {"fact_checkr": "m"}},
                     {"subagent_iterations_at_most": {"fact_checkr": 5}}):
            (failure,) = subagent_detail_failures(case(**keys),
                                                  specs(child()))
            assert "no sub-agent called 'fact_checkr'" in failure
            assert "fact_checker" in failure          # what IS declared

    def test_every_child_against_a_package_with_none_is_red(self):
        (failure,) = subagent_detail_failures(
            case(subagent_model={"*": "m"}), {})
        assert "no sub-agents are declared" in failure


class TestTheFileFormat:
    def _suite(self, tmp_path, text):
        (tmp_path / "cases.toml").write_text(text)
        return tmp_path / "cases.toml"

    def test_the_four_keys_round_trip(self, tmp_path):
        path = self._suite(tmp_path, '''
[[case]]
id = "the-child-is-what-the-manifest-says"
subagent_prompt_contains = { fact_checker = ["line number"] }
subagent_prompt_lacks = { "*" = ["ignore the sources"] }
subagent_model = { fact_checker = "" }
subagent_iterations_at_most = { fact_checker = 12 }
''')
        (loaded,) = load_cases(path)
        assert loaded.subagent_prompt_contains == {"fact_checker":
                                                   ["line number"]}
        assert loaded.subagent_prompt_lacks == {"*": ["ignore the sources"]}
        assert loaded.subagent_model == {"fact_checker": ""}
        assert loaded.subagent_iterations_at_most == {"fact_checker": 12}
        # The writer beside the reader (notes/33): what render_case emits
        # has to load back in, or a host turning a failed run into a
        # regression case is a second implementation of this format.
        back = tmp_path / "again"
        back.mkdir()
        (written,) = load_cases(self._suite(back, render_case(loaded)))
        assert written.subagent_prompt_contains == \
            loaded.subagent_prompt_contains
        assert written.subagent_prompt_lacks == loaded.subagent_prompt_lacks
        assert written.subagent_model == loaded.subagent_model
        assert written.subagent_iterations_at_most == \
            loaded.subagent_iterations_at_most

    def test_a_case_of_only_these_keys_needs_no_task(self, tmp_path):
        path = self._suite(tmp_path, '[[case]]\nid = "x"\n'
                                     'subagent_model = { c = "m" }\n')
        (loaded,) = load_cases(path)
        assert loaded.needs_a_model is False

    def test_a_pattern_key_is_refused(self, tmp_path):
        path = self._suite(tmp_path, '[[case]]\nid = "x"\n'
                                     'subagent_model = { "fact_*" = "m" }\n')
        with pytest.raises(ConfigError, match="NAME, not a pattern"):
            load_cases(path)

    def test_a_cap_must_be_a_whole_number_of_iterations(self, tmp_path):
        path = self._suite(tmp_path, '[[case]]\nid = "x"\n'
                                     'subagent_iterations_at_most '
                                     '= { c = 0 }\n')
        with pytest.raises(ConfigError, match="at least 1"):
            load_cases(path)

    def test_a_model_must_be_a_slug_not_a_list(self, tmp_path):
        path = self._suite(tmp_path, '[[case]]\nid = "x"\n'
                                     'subagent_model = { c = ["m"] }\n')
        with pytest.raises(ConfigError, match="model slug"):
            load_cases(path)

    def test_an_unknown_subagent_key_is_still_refused_by_name(self, tmp_path):
        path = self._suite(tmp_path, '[[case]]\nid = "x"\n'
                                     'subagent_prompt = { c = ["m"] }\n')
        with pytest.raises(ConfigError, match="subagent_prompt"):
            load_cases(path)
