"""The tools a page offers, read off the classes (tools/catalog.py).

The bias is A BOX THAT SAYS SOMETHING OTHER THAN WHAT THE TICK DOES. A
row's ``asks`` must be the gate's own bit (the class's ``read_only``),
every built-in the default registry carries must have a row, and a tool
an allow list turned away must still be describable by the host that
wants to offer it back.
"""

from __future__ import annotations

from yantra.tools import default_registry
from yantra.tools.base import Tool, ToolRegistry
from yantra.tools.catalog import PLAIN, builtin_catalog, said


def test_every_tool_the_default_registry_carries_has_a_row():
    rows = {r["name"] for r in builtin_catalog()}
    assert {t.name for t in default_registry()} <= rows


def test_asks_is_the_opposite_of_read_only_for_every_row():
    from yantra.tools import catalog

    classes = {cls.name: cls for _, group in catalog.GROUPS for cls in group}
    for row in builtin_catalog():
        assert row["asks"] is (not classes[row["name"]].read_only)


def test_a_read_runs_unasked_and_a_write_asks():
    rows = {r["name"]: r for r in builtin_catalog()}
    assert rows["read_file"]["asks"] is False
    assert rows["write_file"]["asks"] is True
    assert rows["web_fetch"]["asks"] is True


def test_every_row_is_worded_for_a_person_not_the_model():
    for row in builtin_catalog():
        assert row["said"] == PLAIN[row["name"]]


def test_a_tool_without_a_plain_line_gets_its_first_sentence():
    class Weather(Tool):
        name = "weather"
        description = "Today's weather where you are. Call it with a city."
        parameters = {"type": "object", "properties": {}}

        def run(self, args, ctx):  # pragma: no cover
            return ""

    assert said(Weather) == "Today's weather where you are"


def test_a_tool_the_allow_list_turned_away_can_still_be_described():
    class Weather(Tool):
        name = "mcp__wx__weather"
        description = "Weather."
        parameters = {"type": "object", "properties": {}}
        read_only = True

        def run(self, args, ctx):  # pragma: no cover
            return ""

        def summary(self, args):  # pragma: no cover
            return "weather"

    registry = ToolRegistry()
    registry.admit_only(["read_file"])
    registry.register(Weather())
    assert "mcp__wx__weather" not in registry
    assert [t.name for t in registry.turned_away()] == ["mcp__wx__weather"]
    assert registry.turned_away()[0].read_only is True
