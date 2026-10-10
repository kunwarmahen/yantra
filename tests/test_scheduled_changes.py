"""A schedule that changes something: allowed narrowly, warned about, asked.

"Turn off the stairs light in five minutes" became a schedule with
nothing allowed, its card said "only tools that read", and at its time
the switch was refused -- while its person sat in front of Telegram.
The bias these tests are designed against is A YES THAT DOES NOT COVER
THE JOB, in its three shapes:

* **Too little.** A job that changes something accepted with nothing
  that can change anything: the card must say so before the yes.
* **Too much.** "call_service" is every device in the house. A grant
  can fix the arguments -- one switch -- and a call with other values
  must fall through, not ride on the stairs light's yes.
* **Nobody to ask.** A run nobody watches can still reach its person
  through Dvara; a question there is a yes or a no, and silence or an
  unreachable Dvara is a refusal, never a yes.

Also designed against a grant typed wrong being read as the whole tool:
a bracket left open is an error, not ``call_service`` with any arguments.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from yantra import samay_link, unattended
from yantra.permissions import (PermissionRequest, allow_named, denial_code,
                                denial_text, parse_grant)
from yantra.tools.base import Tool, ToolRegistry

HA = "mcp__homeassistant-home__call_service"
STAIRS = f"{HA}(domain=switch, service=turn_off, entity_id=switch.lights_2)"


def request(tool=HA, read_only=False, always_ask=False, **arguments):
    return PermissionRequest(tool_name=tool, arguments=arguments, summary=f"{tool}?",
                             read_only=read_only, always_ask=always_ask)


class Named(Tool):
    description = "x"
    parameters: dict = {"type": "object"}

    def __init__(self, name, read_only=False):
        self.name, self.read_only, self.always_ask = name, read_only, False

    def summary(self, args, ctx):
        return self.name

    def run(self, args, ctx):
        return "ran"


def home() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Named(HA))
    registry.register(Named("mcp__homeassistant-home__get_state", read_only=True))
    return registry


class TestAGrantCanFixWhatATouchesTo:
    def test_the_stairs_light_is_allowed_and_the_front_door_is_not(self):
        gate = allow_named([STAIRS], inner=lambda r: False)
        assert gate(request(domain="switch", service="turn_off",
                            entity_id="switch.lights_2"))
        assert not gate(request(domain="lock", service="unlock",
                                entity_id="lock.front_door"))
        assert not gate(request(domain="switch", service="turn_off",
                                entity_id="switch.lights_2,lock.front_door"))

    def test_an_argument_the_grant_names_must_be_there(self):
        gate = allow_named([STAIRS], inner=lambda r: False)
        assert not gate(request(domain="switch", service="turn_off"))

    def test_arguments_it_does_not_name_are_free(self):
        grant = parse_grant(f"{HA}(domain=light, service=turn_on, entity_id=light.desk)")
        assert grant.allows(HA, {"domain": "light", "service": "turn_on",
                                 "entity_id": "light.desk", "data": {"brightness_pct": 40}})

    def test_a_value_is_exact_never_a_glob(self):
        grant = parse_grant(f"{HA}(entity_id=switch.*)")
        assert not grant.allows(HA, {"entity_id": "switch.lights_2"})
        assert grant.allows(HA, {"entity_id": "switch.*"})

    def test_a_quoted_value_may_hold_a_comma(self):
        grant = parse_grant(f'{HA}(entity_id="switch.a,switch.b")')
        assert grant.allows(HA, {"entity_id": "switch.a,switch.b"})
        assert str(grant) == f'{HA}(entity_id="switch.a,switch.b")'

    def test_numbers_and_flags_match_as_they_are_written(self):
        grant = parse_grant("thermostat(temperature=21, eco=true)")
        assert grant.allows("thermostat", {"temperature": 21, "eco": True})
        assert not grant.allows("thermostat", {"temperature": 25, "eco": True})

    def test_the_bare_name_still_means_the_whole_tool_and_globs_still_work(self):
        gate = allow_named(["browser_*", HA], inner=lambda r: False)
        assert gate(request("browser_click"))
        assert gate(request(domain="lock", service="unlock"))

    def test_an_always_ask_tool_is_not_covered_even_by_a_narrow_grant(self):
        gate = allow_named([STAIRS], inner=lambda r: False)
        assert not gate(request(always_ask=True, domain="switch", service="turn_off",
                                entity_id="switch.lights_2"))

    @pytest.mark.parametrize("bad", [f"{HA}(domain=switch", f"{HA}()",
                                     f"{HA}(domain)", "two words",
                                     f"{HA}(a=1, a=2)", f'{HA}(a="open)'])
    def test_a_grant_typed_wrong_is_an_error_not_the_whole_tool(self, bad):
        with pytest.raises(ValueError):
            allow_named([bad])


class TestTheCardSaysWhenTheJobCannotWork:
    def test_a_change_with_nothing_allowed_to_make_it_is_said_in_capitals(self):
        said = samay_link.explain_create(
            {"prompt": "Turn off the stairs light: call Home Assistant service "
                       "switch/turn_off on switch.lights_2", "when": {"once": "16:03"}},
            registry=home())
        assert "only tools that read" in said
        assert 'IT SAYS "turn off", BUT NOTHING ALLOWED HERE CAN CHANGE ANYTHING' in said
        assert "refused, and the job will not get done" in said

    def test_where_it_can_ask_the_card_says_it_will_ask_instead(self):
        said = samay_link.explain_create(
            {"prompt": "turn off the stairs light", "when": {"once": "16:03"}},
            registry=home(), asked="through Dvara (Telegram, Sarathi's page)")
        assert "asked through Dvara (Telegram, Sarathi's page), and refused if " \
               "nobody answers" in said
        assert "asked through Dvara (Telegram, Sarathi's page); it waits 30 " \
               "minutes in all" in said

    def test_a_tool_named_in_the_prompt_counts_as_a_change(self):
        assert samay_link.would_change("use call_service on the fan", home()) == \
            "call_service"

    def test_a_check_that_only_reads_gets_no_warning(self):
        said = samay_link.explain_create(
            {"prompt": "check my mail and tell me what needs me", "when": "every 2h"},
            registry=home())
        assert "BUT NOTHING ALLOWED" not in said

    def test_a_narrow_grant_is_said_with_what_it_is_fixed_to(self):
        said = samay_link.explain_create(
            {"prompt": "turn off the stairs light", "when": {"once": "16:03"},
             "allow_tools": [STAIRS]}, registry=home())
        assert (f"{HA} -- only with domain=switch, service=turn_off, "
                "entity_id=switch.lights_2") in said
        assert "BUT NOTHING ALLOWED" not in said and "ANY ARGUMENTS" not in said

    def test_a_bare_tool_that_changes_things_is_said_to_reach_anything(self):
        said = samay_link.explain_create(
            {"prompt": "turn off the stairs light", "when": {"once": "16:03"},
             "allow_tools": [HA]}, registry=home())
        assert "call_service WITH ANY ARGUMENTS" in said

    def test_a_grant_typed_wrong_is_loud_on_the_card(self):
        said = samay_link.explain_create(
            {"prompt": "x", "when": "every 2h", "allow_tools": [f"{HA}(domain"]},
            registry=home())
        assert "NOT A TOOL NAME OR tool(key=value)" in said

    def test_allowing_a_tool_later_has_a_card_of_its_own(self):
        said = samay_link.explain_allow(
            {"id": "a4e35d0d", "allow_tools": [STAIRS]}, home(),
            "a4e35d0d [paused] once, on Sat 10 Oct at 16:03")
        assert "Let schedule a4e35d0d use without asking" in said
        assert "a4e35d0d [paused] once" in said
        assert "only with domain=switch" in said and "CHANGES THINGS" in said

    def test_asks_go_follows_the_person_and_the_running_clock(self):
        assert samay_link.Samay(mode="on", data={"local_to": ""}).asks_go() == ""
        assert "Dvara" in samay_link.Samay(mode="on", data={"local_to": "owner"}).asks_go()
        assert samay_link.Samay(mode="on", person="raj").asks_go() == "in your chat"

    def test_the_model_is_told_to_list_the_tool_that_does_the_job(self):
        text = samay_link.prompt_text({"serving": True})
        assert "IF THE JOB ITSELF CHANGES SOMETHING" in text
        assert "entity_id=switch.lights_2)` allows that one switch" in text


class FakeDvara(BaseHTTPRequestHandler):
    answers: list = []
    seen: list = []

    def do_POST(self):  # noqa: N802 -- http.server's name
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeDvara.seen.append((self.path, self.headers.get("Authorization"), body))
        status, reply = FakeDvara.answers.pop(0)
        data = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def dvara():
    server = HTTPServer(("127.0.0.1", 0), FakeDvara)
    FakeDvara.answers, FakeDvara.seen = [], []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    unattended.clear()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        unattended.clear()


class TestARunNobodyWatchesCanStillAsk:
    def test_a_yes_in_the_chat_lets_the_call_run(self, dvara):
        FakeDvara.answers = [(200, {"approved": True, "via": "telegram"})]
        reach = unattended.Reach(url=dvara, token="t", actor="owner", seconds=600)
        assert reach.gate(request(domain="switch", service="turn_off",
                                  entity_id="switch.lights_2"))
        path, auth, body = FakeDvara.seen[0]
        assert path == "/ask" and auth == "Bearer t"
        assert body["actor"] == "owner" and body["tool"] == HA
        assert body["arguments"]["entity_id"] == "switch.lights_2"
        assert 0 < body["timeout"] <= 600
        assert unattended.refused() == []

    def test_a_no_is_refused_in_dvaras_words_and_written_down(self, dvara):
        FakeDvara.answers = [(200, {"approved": False, "code": "user",
                                    "reason": "owner said no."})]
        reach = unattended.Reach(url=dvara, token="t", actor="owner", seconds=600)
        asked = request()
        assert not reach.gate(asked)
        assert denial_text(asked) == "owner said no." and denial_code(asked) == "user"
        assert unattended.refused() == [HA]

    def test_reads_are_never_asked(self, dvara):
        reach = unattended.Reach(url=dvara, token="t", actor="owner", seconds=600)
        assert reach.gate(request("get_state", read_only=True))
        assert FakeDvara.seen == []

    def test_an_unreachable_dvara_is_a_refusal_not_a_yes(self):
        reach = unattended.Reach(url="http://127.0.0.1:9", token="t", actor="owner",
                                 seconds=5)
        asked = request()
        assert not reach.gate(asked)
        assert "could not be reached" in denial_text(asked)
        assert denial_code(asked) == "unattended"

    def test_dvara_saying_no_to_the_caller_is_a_refusal(self, dvara):
        FakeDvara.answers = [(404, {"detail": "no actor 'ghost'"})]
        reach = unattended.Reach(url=dvara, token="t", actor="ghost", seconds=60)
        asked = request()
        assert not reach.gate(asked)
        assert "no actor 'ghost'" in denial_text(asked)

    def test_once_the_runs_wait_is_spent_nothing_more_is_asked(self, dvara):
        reach = unattended.Reach(url=dvara, token="t", actor="owner", seconds=60,
                                 spent=60)
        asked = request()
        assert not reach.gate(asked)
        assert denial_code(asked) == "out_of_time" and FakeDvara.seen == []

    def test_it_is_set_up_from_the_environment_or_not_at_all(self, monkeypatch):
        monkeypatch.delenv(unattended.ASK_URL, raising=False)
        assert unattended.Reach.from_env() is None
        monkeypatch.setenv(unattended.ASK_URL, "http://dvara:8765/")
        monkeypatch.setenv(unattended.ASK_ACTOR, "owner")
        monkeypatch.setenv(unattended.ASK_WAIT, "900")
        reach = unattended.Reach.from_env()
        assert reach.url == "http://dvara:8765" and reach.seconds == 900

    def test_a_granted_call_is_never_asked(self, dvara):
        reach = unattended.Reach(url=dvara, token="t", actor="owner", seconds=60)
        gate = allow_named([STAIRS], inner=reach.gate)
        assert gate(request(domain="switch", service="turn_off",
                            entity_id="switch.lights_2"))
        assert FakeDvara.seen == []
