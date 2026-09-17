"""Sub-agents a package declares, and the promises the manifest makes.

The bias here is that a declared sub-agent is the one place where an
agent's boundaries can be widened WITHOUT any of the usual signals. A
tool the model is not supposed to have shows up in a roster; a prompt
change shows up in a diff everyone reads. A child's tool list is a list
inside a table inside a manifest, and a child that can suddenly reach the
internet behaves identically until the day it does not.

So most of these tests assert on what the CHILD was actually built with
-- its registry, its system prompt, its model -- rather than on whether a
call succeeded. A test that only checked the tool returned a string would
keep passing the day the author's tool list stopped being enforced.

The other bias is the arithmetic of delegation: three 85%-reliable agents
in series is 61% end to end. One level deep is checked here in both the
orders somebody would write it, because the obvious check (name it as you
read each entry) passes a hierarchy declared bottom-up.
"""

from __future__ import annotations

import asyncio

import pytest

from yantra.agent import Agent
from yantra.async_agent import AsyncAgent
from yantra.errors import ConfigError, ToolError
from yantra.package import MANIFEST, load_package
from yantra.permissions import PermissionRequest, yolo
from yantra.spec import AgentSpec
from yantra.subagent import (
    SPAWN_TOOL_NAME,
    DeclaredSubagent,
    SubagentSpawner,
    SubagentSpec,
)
from yantra.tools.base import Tool, ToolRegistry
from yantra.types import Message, ModelResponse, ToolCall

from conftest import ScriptedProvider, assistant_text


# ---- fixtures ---------------------------------------------------------------


class NoteTool(Tool):
    name = "note"
    description = "write something down"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    read_only = False

    def summary(self, args, ctx):
        return f"note({args.get('text', '')})"

    def run(self, args, ctx):
        return f"noted: {args.get('text', '')}"


class LookTool(Tool):
    name = "look"
    description = "look at something"
    parameters = {"type": "object", "properties": {}}
    read_only = True

    def summary(self, args, ctx):
        return "look()"

    def run(self, args, ctx):
        return "looked"


def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(LookTool())
    reg.register(NoteTool())
    return reg


def a_spec(**overrides) -> SubagentSpec:
    fields = dict(name="checker", description="Check one claim.",
                  instructions="You check claims.", tools=("look",))
    fields.update(overrides)
    return SubagentSpec(**fields)


def pkg(root, manifest: str, name="p"):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MANIFEST).write_text(manifest, encoding="utf-8")
    return folder


DECLARED = '''
[[subagent]]
name = "checker"
description = "Check one claim against the files here."
instructions = "You check claims and quote the evidence."
tools = ["look"]
'''


# ---- what a manifest may say ------------------------------------------------


class TestTheManifest:
    def test_a_declared_subagent_becomes_a_spec(self, tmp_path):
        spec = load_package(pkg(tmp_path, DECLARED))
        (sub,) = spec.subagents
        assert sub.name == "checker"
        assert sub.tools == ("look",)
        assert sub.max_iterations == 20      # the default, not zero
        assert sub.output_format is None

    def test_instructions_may_come_from_a_file(self, tmp_path):
        folder = pkg(tmp_path, '''
[[subagent]]
name = "checker"
description = "Check things."
prompt = "kids/checker.md"
tools = ["look"]
''')
        (folder / "kids").mkdir()
        (folder / "kids" / "checker.md").write_text("Check carefully.\n")
        (sub,) = load_package(folder).subagents
        assert sub.instructions == "Check carefully."

    def test_a_prompt_that_is_not_there_names_the_path(self, tmp_path):
        folder = pkg(tmp_path, '''
[[subagent]]
name = "checker"
description = "Check things."
prompt = "kids/missing.md"
tools = ["look"]
''')
        with pytest.raises(ConfigError, match="missing.md"):
            load_package(folder)

    def test_both_prompt_and_instructions_is_an_error(self, tmp_path):
        """"Exactly one" is checkable; "whichever one you meant" is not."""
        with pytest.raises(ConfigError, match="exactly one"):
            load_package(pkg(tmp_path, '''
[[subagent]]
name = "checker"
description = "Check things."
prompt = "a.md"
instructions = "inline too"
tools = ["look"]
'''))

    def test_neither_prompt_nor_instructions_is_an_error(self, tmp_path):
        with pytest.raises(ConfigError, match="exactly one"):
            load_package(pkg(tmp_path, '''
[[subagent]]
name = "checker"
description = "Check things."
tools = ["look"]
'''))

    def test_a_description_is_required_because_the_model_reads_it(self, tmp_path):
        with pytest.raises(ConfigError, match="description"):
            load_package(pkg(tmp_path, '''
[[subagent]]
name = "checker"
instructions = "Check."
tools = ["look"]
'''))

    def test_an_empty_tools_list_is_refused(self, tmp_path):
        """A child with no tools is a second opinion from the same model on
        a smaller prompt -- never what this table was written for."""
        with pytest.raises(ConfigError, match="non-empty tools"):
            load_package(pkg(tmp_path, '''
[[subagent]]
name = "checker"
description = "Check."
instructions = "Check."
tools = []
'''))

    def test_a_name_the_model_could_not_type_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="usable tool name"):
            load_package(pkg(tmp_path, '''
[[subagent]]
name = "Fact Checker"
description = "Check."
instructions = "Check."
tools = ["look"]
'''))

    def test_two_subagents_may_not_share_a_name(self, tmp_path):
        with pytest.raises(ConfigError, match="both called"):
            load_package(pkg(tmp_path, DECLARED + DECLARED))

    def test_an_unknown_key_names_the_known_ones(self, tmp_path):
        with pytest.raises(ConfigError, match="unknown key"):
            load_package(pkg(tmp_path, '''
[[subagent]]
name = "checker"
description = "Check."
instructions = "Check."
tools = ["look"]
temperature = 0.3
'''))

    def test_an_iteration_cap_outside_the_range_is_refused(self, tmp_path):
        with pytest.raises(ConfigError, match="between 1 and 50"):
            load_package(pkg(tmp_path, DECLARED + "max_iterations = 500\n"))


class TestOneLevelDeep:
    """Nested delegation compounds failure rates, and a manifest is
    exactly where somebody would try to build a hierarchy."""

    def test_a_subagent_may_not_be_given_the_freeform_spawn_tool(self, tmp_path):
        with pytest.raises(ConfigError, match="may not delegate"):
            load_package(pkg(tmp_path, f'''
[[subagent]]
name = "checker"
description = "Check."
instructions = "Check."
tools = ["look", "{SPAWN_TOOL_NAME}"]
'''))

    def test_a_subagent_may_not_be_given_another_subagent(self, tmp_path):
        with pytest.raises(ConfigError, match="one level deep"):
            load_package(pkg(tmp_path, '''
[[subagent]]
name = "reader"
description = "Read."
instructions = "Read."
tools = ["look"]

[[subagent]]
name = "checker"
description = "Check."
instructions = "Check."
tools = ["look", "reader"]
'''))

    def test_the_check_survives_being_written_in_the_other_order(self, tmp_path):
        """The bug a per-entry check has: naming a child declared BELOW you
        passes, because it has not been read yet."""
        with pytest.raises(ConfigError, match="one level deep"):
            load_package(pkg(tmp_path, '''
[[subagent]]
name = "checker"
description = "Check."
instructions = "Check."
tools = ["look", "reader"]

[[subagent]]
name = "reader"
description = "Read."
instructions = "Read."
tools = ["look"]
'''))


class TestAgainstThePackagesOwnPolicy:
    def test_a_child_may_not_want_a_tool_its_package_excludes(self, tmp_path):
        """Both lists are in one file, so this is exact rather than a
        guess -- and the alternative is a ToolError in front of a user
        after the spawn budget has already been charged."""
        with pytest.raises(ConfigError, match="policy excludes"):
            load_package(pkg(tmp_path, '''
[tools]
allow = ["look"]

[[subagent]]
name = "checker"
description = "Check."
instructions = "Check."
tools = ["look", "note"]
'''))

    def test_a_denied_tool_counts_too(self, tmp_path):
        with pytest.raises(ConfigError, match="policy excludes"):
            load_package(pkg(tmp_path, '''
[tools]
deny = ["note"]

[[subagent]]
name = "checker"
description = "Check."
instructions = "Check."
tools = ["note"]
'''))

    def test_a_wide_open_package_checks_nothing_at_load_time(self, tmp_path):
        """No allow list means tools arrive later (MCP, package tools), so
        a name is only checkable when the child is actually spawned."""
        spec = load_package(pkg(tmp_path, '''
[[subagent]]
name = "checker"
description = "Check."
instructions = "Check."
tools = ["mcp__wiki__search"]
'''))
        assert spec.subagents[0].tools == ("mcp__wiki__search",)


# ---- what the build does with one ------------------------------------------


def built(script=None, **spec_kwargs) -> Agent:
    provider = ScriptedProvider(script or [assistant_text("done")])
    spec = AgentSpec(subagents=(a_spec(**spec_kwargs.pop("sub", {})),),
                     **spec_kwargs)
    return spec.build(provider=provider, provider_name="ollama",
                      model="m", registry=registry(), permissions=yolo)


class TestTheBuild:
    def test_a_declared_subagent_is_a_tool_the_model_can_see(self):
        agent = built()
        assert "checker" in agent.registry.names()
        tool = agent.registry.get("checker")
        assert list(tool.parameters["properties"]) == ["task"]
        assert "Check one claim." in tool.description

    def test_the_model_is_actually_told_this_tool_exists(self):
        """Asserted through the registry's own specs(), not by reading the
        attributes: a tool that holds its declaration under the name
        ``spec`` shadows Tool.spec() and vanishes from every request, with
        a TypeError from deep inside the loop as the only symptom."""
        names = [s.name for s in built().registry.specs()]
        assert "checker" in names

    def test_the_description_says_what_the_child_may_touch(self):
        """The model decides whether to delegate from this string alone."""
        assert "look" in built().registry.get("checker").description

    def test_the_package_policy_governs_it_like_any_other_tool(self):
        """Refused, not an error -- note 32's rule for package tools, and
        a declared sub-agent is a tool the package brought with it."""
        provider = ScriptedProvider([assistant_text("done")])
        spec = AgentSpec(subagents=(a_spec(),), tool_allow=("look",))
        agent = spec.build(provider=provider, provider_name="ollama",
                           model="m", registry=registry(), permissions=yolo)
        assert "checker" not in agent.registry.names()
        assert "checker" in agent.registry.refused_names()

    def test_one_spawner_is_shared_by_every_declared_child(self):
        provider = ScriptedProvider([assistant_text("done")])
        spec = AgentSpec(subagents=(a_spec(name="one"), a_spec(name="two")))
        agent = spec.build(provider=provider, provider_name="ollama",
                           model="m", registry=registry(), permissions=yolo)
        assert (agent.registry.get("one").spawner
                is agent.registry.get("two").spawner)
        # published, so a later --subagents joins this budget rather than
        # opening a second one beside it
        assert agent.subagents is agent.registry.get("one").spawner

    def test_a_subagent_that_would_shadow_a_tool_names_the_package(self):
        provider = ScriptedProvider([assistant_text("done")])
        spec = AgentSpec(name="mine", subagents=(a_spec(name="look"),))
        with pytest.raises(ConfigError, match="shadow"):
            spec.build(provider=provider, provider_name="ollama", model="m",
                       registry=registry(), permissions=yolo)

    def test_a_child_model_nobody_can_price_is_refused_at_build(self):
        """A child spends the PARENT's meter, so a ceiling that cannot see
        the child's model is a ceiling that stops the turn the first time
        anybody delegates."""
        provider = ScriptedProvider([assistant_text("done")])
        spec = AgentSpec(max_usd_per_turn=0.5,
                         subagents=(a_spec(model="some-gateway-slug"),))
        with pytest.raises(ConfigError, match="checker"):
            spec.build(provider=provider, provider_name="anthropic",
                       model="claude-sonnet-4-5", registry=registry(),
                       permissions=yolo)


# ---- what the child is actually built with ---------------------------------


def delegating_script(task="is it true?"):
    return [
        ModelResponse(
            message=Message("assistant",
                            [ToolCall("d1", "checker", {"task": task})]),
            stop_reason="tool_use"),
        assistant_text("parent relays"),
    ]


def child_script():
    return [
        ModelResponse(message=Message("assistant",
                                      [ToolCall("k1", "look", {})]),
                      stop_reason="tool_use"),
        assistant_text("SUPPORTED: line 3"),
    ]


class TestTheChild:
    @staticmethod
    def _run(sub: SubagentSpec, task="is it true?"):
        """One delegation, with the child's construction captured."""
        script = [delegating_script(task)[0], *child_script(),
                  assistant_text("parent relays")]
        provider = ScriptedProvider(script)
        parent = Agent(provider, model="parent-model", tools=registry(),
                       permissions=yolo)
        spawner = SubagentSpawner(parent)
        built_children = []
        original = spawner._build

        def watch(**kwargs):
            child = original(**kwargs)
            built_children.append(child)
            return child

        spawner._build = watch
        parent.registry.register(DeclaredSubagent(sub, spawner))
        parent.run("go")
        return built_children[0], provider

    def test_the_childs_tool_list_is_the_authors(self):
        """Scope restriction is a fact about the child's REGISTRY, not a
        sentence in its prompt -- the child physically has no note tool."""
        child, _ = self._run(a_spec())
        assert child.registry.names() == ["look"]

    def test_the_authors_instructions_come_first(self):
        child, _ = self._run(a_spec(instructions="You check claims."))
        assert child.system.startswith("You check claims.")

    def test_the_task_is_the_only_thing_that_crosses(self):
        """Fresh context is the feature: nothing from the parent's
        conversation reaches the child except this one string."""
        child, _ = self._run(a_spec(), task="does prompt.md say X?")
        assert "does prompt.md say X?" in child.system
        assert "go" not in [m.text() for m in child.history]

    def test_an_output_format_is_optional_and_absent_when_unset(self):
        child, _ = self._run(a_spec())
        assert "OUTPUT FORMAT" not in child.system
        child, _ = self._run(a_spec(output_format="one line"))
        assert "OUTPUT FORMAT" in child.system and "one line" in child.system

    def test_the_child_may_run_a_different_model(self):
        child, provider = self._run(a_spec(model="cheap-one"))
        assert child.model == "cheap-one"
        assert [r["model"] for r in provider.requests] == [
            "parent-model", "cheap-one", "cheap-one", "parent-model"]

    def test_the_childs_cap_is_the_authors_not_the_defaults(self):
        child, _ = self._run(a_spec(max_iterations=3))
        assert child.max_iterations == 3

    def test_the_child_cannot_escalate_past_its_parents_gate(self):
        child, _ = self._run(a_spec())
        assert child.permissions is yolo

    def test_only_the_childs_conclusion_reaches_the_parent(self):
        """The parent's context inflates by what the child CONCLUDED,
        never by the transcript it took to conclude it."""
        script = [delegating_script()[0], *child_script(),
                  assistant_text("parent relays")]
        parent = Agent(ScriptedProvider(script), model="m", tools=registry(),
                       permissions=yolo)
        spawner = SubagentSpawner(parent)
        parent.registry.register(DeclaredSubagent(a_spec(), spawner))
        parent.run("go")
        results = [b for m in parent.history for b in m.content
                   if type(b).__name__ == "ToolResult"]
        assert "SUPPORTED: line 3" in results[0].content
        assert "looked" not in results[0].content   # the child's own work


class TestHowSafeItIs:
    """The one delegation whose blast radius is knowable in advance."""

    def test_a_child_that_can_only_read_needs_no_approval(self):
        """A prompt with nothing to decide teaches people to approve
        without reading, which is the opposite of what it is for."""
        agent = built()
        assert agent.registry.get("checker").read_only is True

    def test_a_child_that_can_change_things_still_asks(self):
        provider = ScriptedProvider([assistant_text("done")])
        spec = AgentSpec(subagents=(a_spec(name="writer", tools=("note",)),))
        agent = spec.build(provider=provider, provider_name="ollama",
                           model="m", registry=registry(), permissions=yolo)
        assert agent.registry.get("writer").read_only is False

    def test_one_unsafe_tool_in_the_list_is_enough(self):
        provider = ScriptedProvider([assistant_text("done")])
        spec = AgentSpec(subagents=(a_spec(tools=("look", "note")),))
        agent = spec.build(provider=provider, provider_name="ollama",
                           model="m", registry=registry(), permissions=yolo)
        assert agent.registry.get("checker").read_only is False

    def test_a_tool_the_registry_has_not_met_counts_as_unsafe(self):
        """MCP tools register after the build; pessimism is the same rule
        their own readOnlyHint gets."""
        parent = Agent(ScriptedProvider([]), model="m", tools=registry(),
                       permissions=yolo)
        tool = DeclaredSubagent(a_spec(tools=("mcp__wiki__search",)),
                                SubagentSpawner(parent))
        assert tool.read_only is False

    def test_the_verdict_is_taken_at_call_time_not_at_build(self):
        """A list naming a tool that arrives later must not be judged
        against a registry that had not met it yet."""
        reg = ToolRegistry()
        reg.register(LookTool())
        parent = Agent(ScriptedProvider([]), model="m", tools=reg,
                       permissions=yolo)
        tool = DeclaredSubagent(a_spec(tools=("look", "late")),
                                SubagentSpawner(parent))
        assert tool.read_only is False

        class LateTool(LookTool):
            name = "late"

        reg.register(LateTool())
        assert tool.read_only is True


class TestTheToolItself:
    def test_a_missing_task_says_what_to_write(self):
        parent = Agent(ScriptedProvider([]), model="m", tools=registry(),
                       permissions=yolo)
        tool = DeclaredSubagent(a_spec(), SubagentSpawner(parent))
        with pytest.raises(ToolError, match="self-contained"):
            tool.run({}, parent.ctx)

    def test_a_tool_the_agent_does_not_have_is_a_readable_refusal(self):
        """The author's list, checked at the only moment it CAN be: MCP and
        package tools register after the manifest is read."""
        parent = Agent(ScriptedProvider([]), model="m", tools=registry(),
                       permissions=yolo)
        tool = DeclaredSubagent(a_spec(tools=("teleport",)),
                                SubagentSpawner(parent))
        with pytest.raises(ToolError, match="teleport"):
            tool.run({"task": "go"}, parent.ctx)

    def test_the_spawn_budget_is_shared_with_the_freeform_tool(self):
        parent = Agent(ScriptedProvider([]), model="m", tools=registry(),
                       permissions=yolo)
        spawner = SubagentSpawner(parent, max_per_session=1)
        spawner.spawned = 1
        tool = DeclaredSubagent(a_spec(), spawner)
        with pytest.raises(ToolError, match="budget exhausted"):
            tool.run({"task": "go"}, parent.ctx)

    def test_the_preview_says_where_the_work_is_going(self):
        parent = Agent(ScriptedProvider([]), model="m", tools=registry(),
                       permissions=yolo)
        tool = DeclaredSubagent(a_spec(model="cheap-one"),
                                SubagentSpawner(parent))
        preview = tool.summary({"task": "check X"}, parent.ctx)
        assert "checker" in preview and "cheap-one" in preview


# ---- the async half --------------------------------------------------------


class TestUnderAnAsyncParent:
    """A service is the reason any of this exists, and a service is async.
    The child has to be too, or the parent's gate is one the child cannot
    use."""

    def test_an_awaited_delegation_builds_an_async_child(self):
        async def _scenario():
            script = [delegating_script()[0], *child_script(),
                      assistant_text("parent relays")]
            parent = AsyncAgent(ScriptedProvider(script), model="m",
                                tools=registry(), permissions=yolo)
            spawner = SubagentSpawner(parent)
            seen = []
            original = spawner._build

            def watch(**kwargs):
                child = original(**kwargs)
                seen.append(child)
                return child

            spawner._build = watch
            parent.registry.register(DeclaredSubagent(a_spec(), spawner))
            await parent.run("go")
            assert isinstance(seen[0], AsyncAgent)

        asyncio.run(_scenario())

    def test_a_child_can_use_a_gate_that_suspends(self):
        """The failure this fixes: a synchronous child handed an awaitable
        gate refuses every dangerous call it makes, because a coroutine is
        truthy and `decide` refuses to guess (notes/37)."""
        async def _scenario():
            asked: list[str] = []

            async def gate(request: PermissionRequest) -> bool:
                await asyncio.sleep(0)
                asked.append(request.tool_name)
                return True

            script = [
                ModelResponse(message=Message(
                    "assistant", [ToolCall("d1", "writer", {"task": "note it"})]),
                    stop_reason="tool_use"),
                ModelResponse(message=Message(
                    "assistant", [ToolCall("k1", "note", {"text": "hi"})]),
                    stop_reason="tool_use"),
                assistant_text("wrote it"),
                assistant_text("parent relays"),
            ]
            parent = AsyncAgent(ScriptedProvider(script), model="m",
                                tools=registry(), permissions=gate)
            spawner = SubagentSpawner(parent)
            parent.registry.register(DeclaredSubagent(
                a_spec(name="writer", tools=("note",)), spawner))
            reply = await parent.run("go")
            assert asked == ["writer", "note"]   # the CHILD's call got through
            assert "parent relays" in reply.message.text()

        asyncio.run(_scenario())

    def test_a_synchronous_spawn_still_gets_a_synchronous_child(self):
        """Keyed to the CALL PATH, not the parent: a coroutine returned to
        a synchronous caller is never awaited, and the child never runs."""
        script = [delegating_script()[0], *child_script(),
                  assistant_text("parent relays")]
        parent = AsyncAgent(ScriptedProvider(script), model="m",
                            tools=registry(), permissions=yolo)
        spawner = SubagentSpawner(parent)
        result = spawner.spawn_declared(a_spec(), {"task": "check"})
        assert result.summary == "SUPPORTED: line 3"
