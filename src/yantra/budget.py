"""A dollar ceiling the loop actually stops at.

``pricing.py`` turns token counts into dollars. This module turns dollars
into a DECISION: the loop checks the running total between iterations,
and when the ceiling is crossed it stops the turn instead of asking for
one more model call.

The thing being protected against is specific. An agent that reasons in
a circle -- fetch, re-read, fetch the same page again -- does not crash
and does not look broken from the outside. It looks busy, right up until
twenty-five iterations of a long context have been billed at full rate.
``max_iterations`` already caps the COUNT of those round trips, which is
the wrong unit: twenty-five cheap iterations and twenty-five expensive
ones differ by two orders of magnitude on the invoice.

THE CEILING IS A STOP, NOT A CAP. The only way to learn what a model
call cost is to make it, so the meter is read between iterations and the
overshoot is bounded by exactly one call. A ceiling of $0.50 can end a
turn at $0.53. Any design that promised otherwise would be estimating
the bill instead of reading it, and an estimate is what nobody wants
standing between them and their card.

PER TURN, NOT PER SESSION. One turn is one thing the agent was asked to
do, and it is the only unit a package author can estimate: they know
roughly what answering a question should cost, and they cannot know how
many questions you will ask. The meter therefore zeroes at the top of
every turn (``begin_turn``). A ceiling over a whole session needs a store
and an identity to be worth anything, which is a service's problem and
not the harness's.

A METER, NOT A COUNTER. One ``Budget`` is shared by everything spending
under one turn -- notably sub-agents, which get the PARENT's meter rather
than a fresh ceiling each (``subagent.py``). A ceiling that a model can
reset by delegating is the same lie as a ceiling that does nothing, just
better hidden. Sharing is also why ``begin_turn`` only listens to the
agent the ceiling was set on: a child starting its turn must not be able
to zero the money its parent already spent.

WHAT CANNOT BE PRICED CANNOT BE CAPPED, and the two ways that happens get
opposite answers:

* A local model bills nothing. ``price_for("qwen3.8:latest")`` returns
  None, and that is not ignorance -- an Ollama turn costs $0.00 no matter
  how long it runs. The ceiling is inert and says so, and the turn runs.
* A metered model with no known price is a hole. ``Budget.for_model``
  REFUSES to build one, before a token is spent, because the alternative
  is an operator who set a ceiling, saw no complaint, and is not
  protected. ``$YANTRA_PRICES`` (notes/21) is the fix, and the error
  names it.

A PRICE YOU WROTE DOWN YOURSELF ALWAYS WINS, local server included.
``pricing.py`` lets overrides beat the built-in table at every stage, and
the same rule holds one level up: a ``$YANTRA_PRICES`` entry for your own
Ollama tag makes the ceiling real against it. Nobody is billing you, so
what you are metering is your own made-up number -- which is exactly what
you want when the thing being rehearsed is whether the ceiling fires at
all, and you would rather find out on hardware you already own.

A WARNING BEFORE THE STOP, ONCE. Being stopped is a poor way to learn
that a ceiling was close, so the meter also hands out one heads-up per
turn (``take_warning``). It is ADVICE and nothing else: no tool is
blocked, no call is skipped, and a turn that ignores it is not treated
differently for having been warned.

THE CEILING IS READ AND THE WARNING IS ESTIMATED, and the difference is
the whole reason both exist. A stop costs someone the rest of their turn,
so it is only ever made on money actually billed. A warning costs
nothing, so it is free to guess -- and it has to, because every rule
built on money ALREADY SPENT turns out to be nearly useless here. A
tool-using turn does not get more expensive gradually: one ``read_file``
pair puts eleven thousand tokens of context into the next request, and a
real turn measured here went $0.0088, $0.0095, $0.0108, $0.0435. Nothing
about the first three calls predicts the fourth, so "you are at 80% of
the ceiling" and "another call like the last one would cross" both stay
silent right through the only iteration that mattered.

So the forecast is of THE CALL ABOUT TO BE MADE, not the one just
finished, and it is taken at the top of an iteration -- the moment the
request is assembled and its size is known. The loop sizes it
(``Agent._forecast_tokens``, which anchors on the tokens the last
response reported and estimates only what has been appended since) and
this module prices it.

THE REPLY IS FORECAST FROM THE TURN'S OWN REPLIES (notes/69). Nobody can
know a reply's length before it arrives, and for a long time that was
the reason not to guess. But a turn is not a stranger to itself: a model
that thinks for three thousand tokens before every tool call does it on
the next call too. Measured on a thinking-heavy local model, eight of
twenty turns jumped from comfortably under the ceiling to over it in one
call, never warned, because the forecast priced the request and not the
reply. So the forecast adds the LARGEST reply this turn has had so far --
the largest, not the average, because an under-forecast is the direction
that loses warnings, and a warning a call early costs a sentence. Before
the turn has a reply of its own, the PREVIOUS turn's largest stands in
(notes/73): the same agent in the same session, a turn ago. Only the one
turn before, never the session's all-time largest, so a single huge
answer early on does not make every later turn warn early. A fresh
agent's first call has nothing to go on and is forecast on its input.

A FRESH AGENT REMEMBERS (notes/76). That previous-turn figure is also
kept on disk, per package and model (``.yantra/replies.json`` beside the
session store), so an eval case, a one-shot run or a new process starts
with the last turn anybody ran on that model instead of nothing.

That still undercounts a reply longer than any before it, which is why
``WARN_AT`` stays on as a floor: a turn whose cost is mostly output would
otherwise creep to the ceiling with the forecast saying "fine" each
time.

``take_warning`` takes an owner for the same reason ``begin_turn`` does,
and the reason is sharper here. A sub-agent shares the meter, so it can
be the one to cross the line -- and its events go to the parent's TOOL
RESULT, not to anybody's screen. A warning raised there is a warning
delivered to nothing, and worse, a one-shot latch would then swallow the
copy the human was going to get. So only the agent whose turn this is
gets advised; the child's spending still counts, and the parent's very
next iteration is where it surfaces, in the stream somebody is reading.

The fraction is a constant and not a key, which is the one place this
module departs from letting the author decide. A ceiling is a number
somebody's money depends on, so it is configurable at three levels; the
point at which a line of text appears costs nothing to get wrong, and a
third way to spell the same intent is surface nobody asked for.

There is one stop that can never be warned about: a charge for a model
with no list price blinds the meter and trips ``exceeded`` in the same
breath, so the first evidence of trouble IS the stop. Nothing can be said
earlier, because until that charge arrived there was nothing to say.

THE CAP IS OPT-IN, AND IT IS THE ONE PLACE THE REPLY IS LIMITED
(notes/76). ``cap_reply`` sets each call's ``max_tokens`` to what the
money left can buy, so no reply can carry a turn past its ceiling. The
price is the rule every other part of this module keeps -- a final
answer is never discarded -- because a capped answer can arrive cut
off. That is a trade an operator may prefer (a truncated answer over
an overspend), so it is theirs to switch on, like the budget notice,
and never a package key.

The honest limit, stated once: a meter can only count what the provider
reports. ``Usage`` zeros are normal on some streamed calls, and a turn
billed in silence is a turn this ceiling does not see.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from yantra.errors import ConfigError
from yantra.pricing import ModelPrice, cost_of, is_free, price_for
from yantra.types import Usage

#: Fraction of the ceiling that earns a heads-up on its own. The floor
#: under the forecast, which sees context but not the reply it will get.
#: Deliberately not configurable; see the module docstring.
WARN_AT = 0.8


class Budget:
    """A per-turn dollar ceiling, and the running total under it.

    Hosts rarely build one directly: ``AgentSpec`` does it from
    ``[budget] max_usd_per_turn``, and hands the same object to the
    agent's sub-agents.
    """

    def __init__(self, max_usd: float, *, metered: bool = True,
                 notify_agent: bool = False) -> None:
        if max_usd <= 0:
            raise ConfigError(
                f"a budget of {_usd(max_usd)} is not a ceiling, it is a "
                f"refusal to run; omit the ceiling instead"
            )
        self.max_usd = float(max_usd)
        #: False when the provider bills nothing (a local model). The
        #: ceiling is then inert -- kept rather than dropped so a host can
        #: still say on screen that it will never fire.
        self.metered = metered
        self.spent = 0.0
        #: The part of ``spent`` charged by anything OTHER than the agent
        #: that owns the turn -- its sub-agents (notes/64). Already inside
        #: ``spent``; kept apart only so a host can show where the money
        #: went, which one shared number cannot.
        self.delegated = 0.0
        #: Set when a charge arrives for a model with no list price. The
        #: turn stops: a meter that has gone blind mid-run cannot honour
        #: the ceiling, and carrying on regardless is the failure this
        #: whole module exists to refuse.
        self.unpriced_model: str | None = None
        self._owner: object | None = None
        #: The heads-up is a ONE-SHOT per turn, and the latch belongs to
        #: the meter so a parent and its sub-agents share it.
        self._warned = False
        #: The largest reply (output tokens) the OWNER has had this turn:
        #: the forecast of the next one (notes/69). A sub-agent's replies
        #: are left out -- they are a different agent's habit, and the
        #: call being forecast is the owner's.
        self.largest_reply = 0
        #: The previous turn's ``largest_reply``: the forecast for a turn
        #: that has not had a reply of its own yet (notes/73).
        self.last_turn_reply = 0
        #: Where the last turn's largest reply is kept between processes,
        #: and under which key (notes/76). None: remembered in memory only.
        self._memory: tuple[Path, str] | None = None
        #: Opt-in: limit each reply to what the money left can buy
        #: (notes/76). Off by default; see the module docstring.
        self.cap_reply = False
        #: Whether the MODEL is told, as well as the operator. Off by
        #: default and deliberately not a package key: see ``notice``.
        self.notify_agent = notify_agent

    # ---- construction ------------------------------------------------------

    @classmethod
    def for_model(cls, max_usd: float, *, provider_name: str,
                  model: str, notify_agent: bool = False) -> Budget:
        """A ceiling for one provider and model, or a ConfigError saying why not.

        This is where the refusal lives: an operator who asked for a
        ceiling on a model nobody can price learns at startup, not at
        whatever hour the bill arrives.
        """
        if is_free(provider_name, model):
            return cls(max_usd, metered=False, notify_agent=notify_agent)
        if price_for(model) is not None:
            # A local model reaches here only when YOU priced it in
            # $YANTRA_PRICES (pricing.is_free) -- which is how you try a
            # ceiling out before pointing it at an account with a card
            # behind it, and how the notice was measured (notes/64).
            return cls(max_usd, metered=True, notify_agent=notify_agent)
        raise ConfigError(
            f"budget: no list price is known for {model!r}, so a "
            f"{_usd(max_usd)} ceiling could never stop anything. Add the "
            f"model to a $YANTRA_PRICES file, or drop the ceiling"
        )

    # ---- the meter ---------------------------------------------------------

    def begin_turn(self, owner: object) -> None:
        """Zero the meter -- but only for the agent this ceiling belongs to.

        The first agent to start a turn claims the meter. Everything else
        holding it is spending under that agent's turn (a sub-agent, and
        in time whatever else delegates), so its turns charge the same
        ceiling and cannot clear it.
        """
        if self._owner is None:
            self._owner = owner
        if self._owner is owner:
            self.spent = 0.0
            self.delegated = 0.0
            self.unpriced_model = None
            self._warned = False
            self.last_turn_reply = self.largest_reply or self.last_turn_reply
            self.largest_reply = 0

    def charge(self, usage: Usage, model: str, *,
               spender: object | None = None) -> None:
        """Add one response's cost to the meter.

        A model with no list price is recorded rather than guessed at --
        ``pricing.py``'s rule, and here it is also the trigger for a stop.
        ``spender`` is the agent that made the call; when it is not the
        turn's owner the cost is also counted as ``delegated``.
        """
        if not self.metered:
            return
        price: ModelPrice | None = price_for(model)
        if price is None:
            self.unpriced_model = model
            return
        cost = cost_of(usage, price)
        self.spent += cost
        if spender is not None and self._owner is not None \
                and spender is not self._owner:
            self.delegated += cost
        elif usage.output_tokens > self.largest_reply:
            self.largest_reply = usage.output_tokens
            self._remember()

    def remember(self, path: Path, key: str) -> None:
        """Keep the last turn's largest reply at ``path`` under ``key``,
        and start from what is there (notes/76). A file that cannot be
        read is nothing remembered; one that cannot be written is a
        forecast that stays in memory -- advice is not worth a crash."""
        self._memory = (Path(path), key)
        if not self.last_turn_reply:
            self.last_turn_reply = _recall(Path(path), key)

    def _remember(self) -> None:
        if self._memory is None or not self.metered:
            return
        path, key = self._memory
        try:
            known = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(known, dict):
                known = {}
        except (OSError, ValueError):
            known = {}
        known[key] = self.largest_reply
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{path.name}.{os.getpid()}")
            temp.write_text(json.dumps(known, indent=1, sort_keys=True),
                            encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            pass

    def reply_cap(self, *, next_input_tokens: int, model: str,
                  asked: int) -> int:
        """``max_tokens`` for the call about to go out (notes/76).

        ``asked`` unless the operator switched ``cap_reply`` on and the
        model is priced: then the reply is limited to what is left of the
        ceiling once this call's context is paid for. At least one token
        -- a request for zero is refused by some providers, and a turn
        with nothing left is stopped by ``exceeded`` before it gets here.
        """
        if not self.cap_reply or not self.metered:
            return asked
        price = price_for(model)
        if price is None or not price.output_per_mtok:
            return asked
        left = (self.max_usd - self.spent
                - cost_of(Usage(input_tokens=next_input_tokens), price))
        affordable = int(left * 1_000_000 / price.output_per_mtok)
        return max(1, min(asked, affordable))

    def capped(self, limit: int) -> str:
        """Why a turn ended on a reply cut short by ``reply_cap``."""
        return (f"the reply was cut off at {limit:,} tokens, what was left "
                f"of the {_usd(self.max_usd)} ceiling for this turn "
                f"(--budget-cap-reply)")

    def exceeded(self) -> bool:
        """Has this turn earned the right to another model call?"""
        if not self.metered:
            return False
        return self.unpriced_model is not None or self.spent >= self.max_usd

    def take_warning(self, owner: object, *, next_input_tokens: int = 0,
                     model: str = "") -> str | None:
        """The turn's one heads-up, asked at the top of every iteration.

        Returns the sentence once and None forever after, which is the
        whole contract: a loop may call this every iteration without
        producing a wall of identical advice, and a caller that got a
        string is the only one that will.

        Two things earn it. The call about to go out is priced -- its
        context size, plus a reply as long as the longest this turn has
        had (notes/69) -- and if that would not fit under what is left,
        this is the last moment anyone can be told. Failing that, the
        turn has simply spent ``WARN_AT`` of the ceiling -- the floor
        under a forecast that cannot see a reply longer than any before.

        Says nothing to anyone but the agent whose turn this is -- a
        sub-agent's events end up inside a tool result, so warning there
        would spend the one-shot on a reader who does not exist. Says
        nothing when the ceiling is inert (a local model bills nothing,
        so there is nothing to approach), and nothing once the turn has
        crossed -- by then the stop is the message, and advice about a
        line already passed is not advice.
        """
        if self._owner is not owner:
            return None
        if not self.metered or self._warned or self.exceeded():
            return None
        reply = self.largest_reply or self.last_turn_reply
        from_last_turn = not self.largest_reply and bool(reply)
        price = (price_for(model) if model and (next_input_tokens or reply)
                 else None)
        forecast = (0.0 if price is None
                    else cost_of(Usage(input_tokens=next_input_tokens,
                                       output_tokens=reply), price))
        if (self.spent + forecast < self.max_usd
                and self.spent < self.max_usd * WARN_AT):
            return None
        self._warned = True
        left = self.max_usd - self.spent
        if forecast and self.spent + forecast >= self.max_usd:
            if reply:
                when = "last turn" if from_last_turn else "this turn"
                return (f"the next call carries ~{next_input_tokens:,} tokens "
                        f"of context, and replies {when} have run to "
                        f"~{reply:,} tokens -- about ${forecast:.4f} for both, "
                        f"and ~${left:.4f} is left of the "
                        f"{_usd(self.max_usd)} ceiling for this turn")
            return (f"the next call carries ~{next_input_tokens:,} tokens of "
                    f"context, about ${forecast:.4f} before the reply -- and "
                    f"~${left:.4f} is left of the {_usd(self.max_usd)} ceiling "
                    f"for this turn")
        return (f"spent ~${self.spent:.4f} of the {_usd(self.max_usd)} "
                f"ceiling for this turn -- ~${left:.4f} left")

    #: What the MODEL is told when ``notify_agent`` is on. No figures in
    #: it, on purpose -- see ``notice``.
    NOTICE = (
        "[budget notice] This turn is nearly out of its spending limit and "
        "will be stopped shortly, probably after the next model call. "
        "Finish with what you already have: give your best answer now, and "
        "say plainly what you did not get to. Do not start new work, do not "
        "open anything further, and do not shorten the answer itself to save "
        "room -- the limit is on the work, not on the reply."
    )

    def notice(self) -> str | None:
        """The sentence for the AGENT, or None when it is not to be told.

        THE AGENT IS TOLD THE DEADLINE, NOT THE METER, and the missing
        dollar figures are the entire design. A model handed "you have
        $0.08 left" is a model that has been given a number to optimise,
        and the two ways it optimises are both bad: it starts trimming the
        answer to save tokens nobody asked it to save, or it reasons about
        how much more it can afford and spends exactly that. Neither is
        work. A deadline is different -- "stop soon and say what you
        missed" is a constraint about the SHAPE of the remaining turn, and
        a model can act on it without having a quantity to game.

        Off unless the operator asked (``notify_agent``). It is their
        call and not the package author's: the author estimated the
        ceiling, but whoever is running the thing is the one who cares
        whether the answer arrives rushed. And a warned turn still goes
        ahead and crosses -- this changes what the model knows, never
        what the loop does.
        """
        return self.NOTICE if self.notify_agent else None

    def explain(self) -> str:
        """One line saying what stopped the turn, with the numbers in it.

        'over_budget' on its own answers nothing an operator wants to
        know -- over what, by how much, and did it even see the model it
        was metering.
        """
        if self.unpriced_model is not None:
            return (f"no list price for {self.unpriced_model!r}, so the "
                    f"{_usd(self.max_usd)} ceiling stopped seeing the bill")
        return (f"spent ~${self.spent:.4f} of the {_usd(self.max_usd)} "
                f"ceiling for this turn")

    def describe(self) -> str:
        """The startup line: the ceiling, and whether it can ever fire."""
        if not self.metered:
            return (f"{_usd(self.max_usd)} per turn -- inert here, a local "
                    f"model bills nothing")
        told = " (the agent is told too)" if self.notify_agent else ""
        capped = ("; replies capped at what is left" if self.cap_reply
                  else "")
        return (f"{_usd(self.max_usd)} per turn -- a heads-up once one "
                f"more call would not fit{told}{capped}")


def _usd(amount: float) -> str:
    """A ceiling as its owner wrote it: "$0.25", "$2.00", and "$0.012"
    rather than "$0.01" -- two decimals would misstate any ceiling set
    between whole cents, and the warning then contradicts itself
    ("$0.0120 left of the $0.01 ceiling")."""
    cents = amount * 100
    if abs(cents - round(cents)) < 1e-9:
        return f"${amount:.2f}"
    return f"${amount:g}"


def _recall(path: Path, key: str) -> int:
    """The remembered reply for ``key``, or 0 for nothing (notes/76)."""
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    value = known.get(key) if isinstance(known, dict) else None
    return value if isinstance(value, int) and value > 0 else 0
