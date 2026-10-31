"""Yes to all for this task, at the terminal and on Yantra's page.

After a turn reads text the person didn't write, every change asks
(outside.py). A task that answers three mails after reading one inbox
asked three times about the same reading; Dvara's channels had a "yes,
and the rest of this task" for it, and Yantra's own page and terminal
didn't.

The bias these tests encode is A YES THAT COVERS MORE THAN WAS SEEN. It
covers only the cards that ask BECAUSE of reading (a spend or a phone's
confirm asks every time), only in the turn it was given in, and only
while nothing new has been read: a page opened after the yes is one the
person never saw, so the next card asks again.
"""

from __future__ import annotations

import io
import unittest.mock as mock

from rich.console import Console
from rich.prompt import Prompt

from yantra.cli.repl import confirm_gate
from yantra.permissions import PermissionRequest, TurnYes


def after_reading(read=("a mail from bank@example.com",), turn="t1", tool="send_mail",
                  only=True) -> PermissionRequest:
    return PermissionRequest(tool_name=tool, arguments={"to": "x"}, summary=f"{tool}(x)", read_only=False,
                             turn_id=turn, after_reading=tuple(read),
                             only_after_reading=only, always_ask=True)


class Prompts:
    """The terminal's prompt, answered from a list; what it offered is kept."""

    def __init__(self, *answers):
        self.fed = list(answers)
        self.offered: list[list[str]] = []
        self.console = Console(file=io.StringIO(), width=100)

    def _ask(self, *args, **kwargs):
        self.offered.append(list(kwargs.get("choices") or []))
        return self.fed.pop(0)

    def __enter__(self):
        self._patch = mock.patch.object(Prompt, "ask", side_effect=self._ask)
        self._patch.start()
        return confirm_gate(self.console)

    def __exit__(self, *exc):
        self._patch.stop()
        return False


class TestWhatAYesCovers:
    def test_the_rest_of_the_turn_while_nothing_new_is_read(self):
        yes = TurnYes()
        yes.given(after_reading())
        assert yes.covers(after_reading(tool="reply_mail"))
        assert not yes.covers(after_reading(read=("a mail from bank@example.com",
                                                  "a page from evil.example")))

    def test_never_the_next_turn(self):
        yes = TurnYes()
        yes.given(after_reading())
        assert not yes.covers(after_reading(turn="t2"))

    def test_never_a_card_that_asks_for_itself(self):
        yes = TurnYes()
        yes.given(after_reading())
        spend = after_reading(tool="buy", only=False)
        assert not TurnYes.offered(spend) and not yes.covers(spend)


class TestTheTerminal:
    def test_t_is_offered_only_after_reading_and_covers_the_next_card(self):
        with Prompts("t") as gate:
            assert gate(after_reading()) is True
            later = after_reading(tool="reply_mail")
            assert gate(later) is True                      # not asked
            assert later.answered_by == "your yes for this task, in the terminal"

    def test_something_read_after_the_yes_asks_again(self):
        p = Prompts("t", "n")
        with p as gate:
            assert gate(after_reading()) is True
            more = after_reading(read=("a mail from bank@example.com",
                                       "a page from evil.example"))
            assert gate(more) is False
        assert len(p.offered) == 2 and "t" in p.offered[1]

    def test_a_spend_is_not_offered_t(self):
        p = Prompts("y")
        with p as gate:
            assert gate(after_reading(tool="buy", only=False)) is True
        assert p.offered == [["y", "n", "e", "s"]]


class TestThePage:
    def _gate(self, *answers):
        from yantra.web.server import WebSession

        session = WebSession()
        sent: list[dict] = []
        fed = list(answers)

        def ask(envelope, deadline=None):
            sent.append(envelope)
            return fed.pop(0)
        session._ask_human = ask                           # type: ignore[method-assign]
        return session.permission_gate(), sent

    def test_the_card_offers_it_only_after_reading(self):
        gate, sent = self._gate({"decision": "approve"}, {"decision": "approve"})
        gate(after_reading())
        gate(after_reading(tool="buy", only=False))
        assert [e["for_task"] for e in sent] == [True, False]

    def test_yes_to_all_for_this_task_covers_the_turns_next_card(self):
        gate, sent = self._gate({"decision": "approve_task"})
        assert gate(after_reading()) is True
        later = after_reading(tool="reply_mail")
        assert gate(later) is True and len(sent) == 1
        assert later.answered_by == "your yes for this task, on the page"

    def test_yes_to_all_on_a_spend_is_a_plain_yes(self):
        gate, sent = self._gate({"decision": "approve_task"}, {"decision": "deny"})
        assert gate(after_reading(tool="buy", only=False)) is True
        assert gate(after_reading()) is False and len(sent) == 2

    def test_the_page_has_the_button(self):
        from pathlib import Path

        import yantra.web as web
        app_js = (Path(web.__file__).parent / "static" / "app.js").read_text()
        assert 'data-act="approve_task"' in app_js and "env.for_task" in app_js
