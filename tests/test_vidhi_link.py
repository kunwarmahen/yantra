"""Vidhi, found and named: the card a person says yes to, and the prompt.

BIAS: against a yes the person couldn't see the meaning of -- a run card
that leaves out a call the routine makes, an install card that hides
which blanks were guessed, a model told it may write a routine's steps,
another person's routines reached through ``--for``.
"""

from __future__ import annotations

import json
import sys

import pytest

from yantra import vidhi_link as vl

CARD = {"name": "morning-brief", "title": "Morning brief", "state": "active",
        "cost": "1 model ask per run",
        "touches": ["Today's weather: calls web_fetch {\"url\": \"https://w.example\"}",
                    "Write the brief: asks the model, with no tools",
                    "Send it to me: tells you, on your channels"],
        "inputs": {"latitude": 35.78, "unit": "fahrenheit"}}


def test_the_mode_comes_from_the_flag_before_the_environment():
    assert vl.resolve_mode(None, "") == ("auto", None)
    assert vl.resolve_mode("off", "on") == ("off", None)
    assert vl.resolve_mode(None, "/opt/vidhi") == ("on", "/opt/vidhi")


def test_the_server_is_for_one_named_person():
    data = {"mcp": {"command": "vidhi", "args": ["--state", "/s", "mcp"]}}
    cfg = vl.server_config(data, "asha", "helper", "dvara")
    assert cfg.args == ["--state", "/s", "mcp", "--for", "asha", "--agent", "helper",
                        "--runner", "dvara"]
    with pytest.raises(vl.VidhiLinkError):
        vl.server_config(data, "asha --for mahen")


def test_the_run_card_says_every_thing_the_routine_touches_and_its_cost():
    card = vl.explain_run({"name": "morning-brief", "inputs": {"unit": "celsius"}}, CARD)
    assert "Run the routine morning-brief now: Morning brief" in card
    for line in CARD["touches"]:
        assert line in card
    assert "Cost: 1 model ask per run" in card
    assert "unit = celsius (this run only)" in card and "latitude = 35.78" in card


def test_a_paused_routine_is_said_on_its_card():
    card = vl.explain_run({"name": "x"}, {**CARD, "state": "paused"})
    assert "it is paused" in card


def test_the_install_card_says_which_blanks_were_left_to_their_default():
    template = {"name": "cold-night", "title": "Cold night warning", "source": "vidhi",
                "cost": "no model", "touches": ["Send it: tells you"],
                "inputs": [{"name": "line", "label": "Cold means below", "default": 35},
                           {"name": "reading", "label": "The reading", "default": "outside-temp"}]}
    card = vl.explain_install({"template": "cold-night", "inputs": {"line": 30}}, template)
    assert "Cold means below: 30\n" in card + "\n"
    assert "The reading: outside-temp (its default)" in card
    assert "Installing doesn't run it" in card


def test_an_install_from_a_marketplace_says_who_signed_it():
    template = {"name": "x", "title": "X", "source": "market", "signed_by": "rita",
                "cost": "no model", "touches": [], "inputs": []}
    assert "signed by rita" in vl.explain_install({"template": "x"}, template)


def test_the_prompt_names_routines_and_says_the_model_never_writes_steps():
    text = vl.prompt_text([CARD, {**CARD, "name": "cold-night", "title": "Cold",
                                  "state": "paused"}])
    assert "You never write a routine's steps" in text
    assert "- morning-brief: Morning brief -- 1 model ask per run" in text
    assert "- cold-night: Cold (paused)" in text
    assert "35.78" not in text, "inputs and results stay out of the prompt"


def test_a_vidhi_not_running_is_said():
    assert "not running on this computer" in vl.prompt_text([], serving=False)


def test_many_routines_end_in_a_pointer():
    cards = [{**CARD, "name": f"r{n}"} for n in range(vl.NAMED + 3)]
    assert "and 3 more (`list_routines`)" in vl.prompt_text(cards)


def test_a_program_that_is_not_vidhi_is_refused(tmp_path):
    fake = tmp_path / "vidhi"
    fake.write_text(f"#!{sys.executable}\nimport json; print(json.dumps({{'format': 'x'}}))\n")
    fake.chmod(0o755)
    with pytest.raises(vl.VidhiLinkError, match="format this Yantra does not know"):
        vl.load("on", str(fake))


def test_vidhi_found_reports_itself(tmp_path):
    fake = tmp_path / "vidhi"
    report = {"format": vl.FORMAT, "serving": True, "routines": {"active": 2},
              "state": "/s", "mcp": {"command": "vidhi", "args": []}}
    fake.write_text(f"#!{sys.executable}\nprint({json.dumps(json.dumps(report))})\n")
    fake.chmod(0o755)
    data, program = vl.load("auto", str(fake))
    assert program == str(fake) and vl.announce(data, 7, program).startswith(
        "vidhi: 7 tool(s), 2 routine(s); running")


def test_a_watch_card_says_it_starts_a_routine():
    from yantra import prahari_link
    from yantra.tools.base import ToolRegistry as Registry

    card = prahari_link.explain_create(
        {"name": "Cheap", "check": {"tool": "web_fetch", "args": {"url": "https://p"}},
         "pick": {"number": ""}, "when": {"below": 10}, "every": "1h",
         "routine": "price-then-plan"}, registry=Registry())
    assert "Then it starts your routine price-then-plan (Vidhi)" in card
