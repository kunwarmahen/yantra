"""The answer that is neither yes nor no.

A gate could always refuse with a sentence -- ``refuse(request, "...")``
has existed since notes/37 -- and neither frontend could produce one. A
person at a terminal had exactly two useful answers and a third that
asked them to hand-write a JSON arguments dict, which is fine for fixing
a path and hopeless for "use the staging database instead".

The bias in these tests is that a person's words must reach the MODEL,
verbatim and attributed, because that is the entire value: the model has
to be able to tell an instruction from a human apart from the harness's
own voice, and act on it. So the assertions read the reason the model
would see, not the return value of the gate.

The second bias is that an empty sentence must not become a refusal that
says nothing -- "" would leave the model worse informed than the default
denial does.
"""

from __future__ import annotations

import io

import unittest.mock as mock

from rich.console import Console
from rich.prompt import Prompt

from yantra.cli.repl import confirm_gate
from yantra.permissions import REFUSED_USER, PermissionRequest, denial_code


def request(tool_name="bash", read_only=False) -> PermissionRequest:
    return PermissionRequest(tool_name=tool_name, arguments={"command": "rm -rf /"},
                             summary=f"{tool_name}(rm -rf /)",
                             read_only=read_only)


class Answers:
    """Drives one approval prompt: the y/n/e/s choice, then whatever the
    person types at the follow-up.

    ``Prompt.ask`` is patched (it reads the real stdin) while
    ``console.input`` is replaced, because the two questions come through
    two different doors and a test that only patched one would be testing
    the door.
    """

    def __init__(self, *lines):
        self.fed = list(lines)
        self.console = Console(file=io.StringIO(), width=100)
        self.console.input = self._next          # type: ignore[method-assign]
        self.prompts: list[str] = []

    def _next(self, *args, **kwargs):
        return self.fed.pop(0)

    def __enter__(self):
        self._patch = mock.patch.object(
            Prompt, "ask",
            side_effect=lambda *a, **k: (self.prompts.append(str(k.get(
                "choices", ""))) or self.fed.pop(0)))
        self._patch.start()
        return confirm_gate(self.console)

    def __exit__(self, *exc):
        self._patch.stop()
        return False


class TestTheTerminal:
    def test_a_spoken_refusal_reaches_the_model_verbatim(self):
        req = request()
        with Answers("s", "not on prod -- use the staging db") as gate:
            assert gate(req) is False
        assert "not on prod -- use the staging db" in req.reason

    def test_the_sentence_is_attributed_to_a_person(self):
        """The model must be able to tell an instruction from a human
        apart from the harness's own voice."""
        req = request()
        with Answers("s", "use staging") as gate:
            gate(req)
        assert req.reason.startswith("bash was denied. The person said:")

    def test_it_is_still_a_refusal_with_a_human_behind_it(self):
        req = request()
        with Answers("s", "use staging") as gate:
            assert gate(req) is False
        assert denial_code(req) == REFUSED_USER

    def test_an_empty_sentence_is_a_plain_no(self):
        """Not a refusal that says nothing: "" would leave the model
        worse off than the default denial."""
        req = request()
        with Answers("s", "   ") as gate:
            assert gate(req) is False
        assert req.reason == ("bash was denied: you said no at the approval "
                              "prompt.")

    def test_a_bare_no_is_unchanged(self):
        req = request()
        with Answers("n") as gate:
            assert gate(req) is False
        assert "said no at the approval prompt" in req.reason

    def test_yes_is_unchanged(self):
        with Answers("y") as gate:
            assert gate(request()) is True

    def test_a_read_only_tool_still_never_asks(self):
        with Answers() as gate:             # no answers available at all
            assert gate(request("read_file", read_only=True)) is True

    def test_the_prompt_offers_the_new_answer(self):
        answers = Answers("n")
        with answers as gate:
            gate(request())
        assert "'s'" in answers.prompts[0]


class TestTheBrowser:
    def _gate(self, answer):
        from yantra.web.server import WebSession

        session = WebSession()
        session._ask_human = lambda payload: answer  # type: ignore[method-assign]
        return session.permission_gate()

    def test_a_reason_typed_in_the_modal_reaches_the_model(self):
        gate = self._gate({"decision": "deny", "reason": "use staging"})
        req = request()
        assert gate(req) is False
        assert req.reason == "bash was denied. The person said: use staging"

    def test_an_empty_box_is_a_plain_no(self):
        gate = self._gate({"decision": "deny", "reason": "  "})
        req = request()
        gate(req)
        assert "said no at the approval prompt" in req.reason

    def test_a_deny_with_no_reason_key_still_works(self):
        """An older page, or one that never focused the box."""
        gate = self._gate({"decision": "deny"})
        req = request()
        assert gate(req) is False
        assert "said no at the approval prompt" in req.reason

    def test_a_reason_that_is_not_a_string_is_ignored(self):
        gate = self._gate({"decision": "deny", "reason": {"nice": "try"}})
        req = request()
        gate(req)
        assert "said no at the approval prompt" in req.reason

    def test_approve_is_unchanged(self):
        assert self._gate({"decision": "approve"})(request()) is True


class TestThePageOffersIt:
    def test_the_modal_carries_a_reason_box(self):
        from pathlib import Path

        import yantra.web as web
        app_js = (Path(web.__file__).parent / "static" / "app.js").read_text()
        assert 'id="deny-reason"' in app_js
        assert '"deny", reason:' in app_js.replace("decision: ", "")

    def test_enter_in_the_reason_box_denies_rather_than_approves(self):
        """A person who has just typed "no, use staging" and pressed
        Enter did not mean yes."""
        from pathlib import Path

        import yantra.web as web
        app_js = (Path(web.__file__).parent / "static" / "app.js").read_text()
        handler = app_js.split("const onKey")[1].split("};")[0]
        assert "deny-reason" in handler
