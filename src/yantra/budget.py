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

The honest limit, stated once: a meter can only count what the provider
reports. ``Usage`` zeros are normal on some streamed calls, and a turn
billed in silence is a turn this ceiling does not see.
"""

from __future__ import annotations

from yantra.errors import ConfigError
from yantra.pricing import ModelPrice, bills_nothing, cost_of, price_for
from yantra.types import Usage


class Budget:
    """A per-turn dollar ceiling, and the running total under it.

    Hosts rarely build one directly: ``AgentSpec`` does it from
    ``[budget] max_usd_per_turn``, and hands the same object to the
    agent's sub-agents.
    """

    def __init__(self, max_usd: float, *, metered: bool = True) -> None:
        if max_usd <= 0:
            raise ConfigError(
                f"a budget of ${max_usd:.2f} is not a ceiling, it is a "
                f"refusal to run; omit the ceiling instead"
            )
        self.max_usd = float(max_usd)
        #: False when the provider bills nothing (a local model). The
        #: ceiling is then inert -- kept rather than dropped so a host can
        #: still say on screen that it will never fire.
        self.metered = metered
        self.spent = 0.0
        #: Set when a charge arrives for a model with no list price. The
        #: turn stops: a meter that has gone blind mid-run cannot honour
        #: the ceiling, and carrying on regardless is the failure this
        #: whole module exists to refuse.
        self.unpriced_model: str | None = None
        self._owner: object | None = None

    # ---- construction ------------------------------------------------------

    @classmethod
    def for_model(cls, max_usd: float, *, provider_name: str,
                  model: str) -> Budget:
        """A ceiling for one provider and model, or a ConfigError saying why not.

        This is where the refusal lives: an operator who asked for a
        ceiling on a model nobody can price learns at startup, not at
        whatever hour the bill arrives.
        """
        if price_for(model) is not None:
            # Deliberately ahead of the free-provider check: a price you
            # put in $YANTRA_PRICES is a thing you asked for, and asking
            # to meter your own local model is how you try a ceiling out
            # before pointing it at an account with a card behind it.
            return cls(max_usd, metered=True)
        if bills_nothing(provider_name):
            return cls(max_usd, metered=False)
        raise ConfigError(
            f"budget: no list price is known for {model!r}, so a "
            f"${max_usd:.2f} ceiling could never stop anything. Add the "
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
            self.unpriced_model = None

    def charge(self, usage: Usage, model: str) -> None:
        """Add one response's cost to the meter.

        A model with no list price is recorded rather than guessed at --
        ``pricing.py``'s rule, and here it is also the trigger for a stop.
        """
        if not self.metered:
            return
        price: ModelPrice | None = price_for(model)
        if price is None:
            self.unpriced_model = model
            return
        self.spent += cost_of(usage, price)

    def exceeded(self) -> bool:
        """Has this turn earned the right to another model call?"""
        if not self.metered:
            return False
        return self.unpriced_model is not None or self.spent >= self.max_usd

    def explain(self) -> str:
        """One line saying what stopped the turn, with the numbers in it.

        'over_budget' on its own answers nothing an operator wants to
        know -- over what, by how much, and did it even see the model it
        was metering.
        """
        if self.unpriced_model is not None:
            return (f"no list price for {self.unpriced_model!r}, so the "
                    f"${self.max_usd:.2f} ceiling stopped seeing the bill")
        return (f"spent ~${self.spent:.4f} of the ${self.max_usd:.2f} "
                f"ceiling for this turn")

    def describe(self) -> str:
        """The startup line: the ceiling, and whether it can ever fire."""
        if not self.metered:
            return (f"${self.max_usd:.2f} per turn -- inert here, a local "
                    f"model bills nothing")
        return f"${self.max_usd:.2f} per turn"
