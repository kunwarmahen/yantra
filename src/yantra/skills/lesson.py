"""A lesson: a job taught one step at a time, kept as one skill.

``/learn`` reads the LAST turn (learn.py): the person asked, the agent
did it in eight calls, and those eight calls are the recipe. A job a
person walks the agent through is not one turn. "Open WhatsApp" is a
message, "tap Mom" is the next, "type running late" the one after; read
the old way, the skill learned from that walk is its last step.

So a lesson is a span of turns read as ONE task. ``/learn start <what
it's for>`` opens it, every turn after it is added as it ends -- the
person's words, then the calls that followed them -- and ``/learn done``
hands the whole of it to the same one-call write-up, with the person's
words shown above the steps they asked for
([notes/129](../../notes/129-taught-step-by-step.md)).

THE AGENT IS TOLD IT IS BEING TAUGHT, in the conversation itself: the
lesson's first message (``opening``) asks it to do only the step it is
told, say what is on the screen, and ask for the next. Left unsaid, a
capable model runs ahead and finishes the job its own way -- which is
exactly the job the person did not show it.

NOTHING IS OFFERED HALFWAY. While a lesson is open, the look after each
turn is skipped (hosts check ``open``): one step of a walk, offered as a
skill of its own, is worse than nothing.

THE STEPS ARE KEPT HERE, NOT IN THE HISTORY. A lesson on a phone is a
dozen screens of text, and a small context window compacts part of it
away before the person says done. Each turn is copied into the lesson as
it ends, clipped the way the write-up would clip it anyway, so what the
history forgets the lesson still has -- and a host that rebuilds its
agent per message (Dvara) keeps the lesson on disk (``to_dict``).

SHOWN, NOT TOLD. ``watching`` marks a lesson where the person does each
step with their own hand: the opening asks the agent to read what
changed on the phone when they say next (Sparsh's ``watched``), and the
calls that read it are the lesson's steps like any other.

ONE SKILL FOR BOTH PHONES. A lesson is taught on one phone, and what it
saw there is that phone's: an Android package, an iPhone bundle id, the
folder its home screen keeps the app in. The lesson notes which phone it
was taught on (``phone``, from the agent's phone layer: sparsh_link.py)
and the write-up is told to open the app by its everyday name with
``open_app`` -- which finds it on either phone -- and to say which phone
the steps were seen on, so the other one knows which may differ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from yantra.skills.learn import MAX_RESULT_CHARS, Step, Turn, _clip

#: The word that opens, closes and drops one, after ``/learn``.
VERBS = ("start", "watch", "done", "stop", "clear")

#: Other words for one of them: ``clear`` is what people reach for when
#: they mean "drop this", from ``/clear``.
ALIASES = {"clear": "stop"}

#: A lesson longer than this many turns is cut from the front: a walk
#: that long is not one job, and the write-up's call has to fit.
MAX_TURNS = 40

OPENING = """\
I'm going to teach you how to {task}, one step at a time, so you can do \
it by yourself next time. Do ONLY the step I tell you, nothing more. After \
each step, tell me in a sentence or two what is on the screen now and ask \
me what's next. Don't finish the job your own way, and don't guess steps \
I haven't told you. Say you're ready."""

OPENING_WATCH = """\
I'm going to show you how to {task} on my phone, doing each step myself, \
so you can do it by yourself next time. Don't touch the phone. First call \
watch_phone so you see where I start. Each time I say "next", call \
watched to see what I did, and tell me in one line what you think I did \
("you opened WhatsApp", "you tapped Mom"), so I can correct you. Say you \
are ready."""


@dataclass(slots=True)
class Lesson:
    """What one walk-through taught: what it is for, and every turn of it."""

    task: str
    watching: bool = False
    #: The phone it was taught on ("iPhone", "Android phone"), or "".
    phone: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    #: One entry per turn: the person's words and the calls after them.
    turns: list[Turn] = field(default_factory=list)

    def opening(self) -> str:
        """The message the lesson starts with, sent to the agent."""
        return (OPENING_WATCH if self.watching else OPENING).format(task=self.task)

    def add(self, turn: Turn | None, agent: Any = None) -> None:
        """Keep one finished turn -- each result clipped as the write-up
        would clip it, so a long lesson stays a small file. ``agent``
        says which phone it is working, if any (the latest one wins: a
        phone swapped mid-lesson is the one the steps were seen on)."""
        if turn is None:
            return
        if agent is not None and turn.steps:
            from yantra.sparsh_link import phone_kind

            self.phone = phone_kind(agent) or self.phone
        steps = [Step(s.call_id, s.name, dict(s.arguments),
                      _clip(s.result, MAX_RESULT_CHARS), s.ok) for s in turn.steps]
        self.turns.append(Turn(task=turn.task, steps=steps, answer=_clip(turn.answer, 600)))
        del self.turns[:-MAX_TURNS]

    @property
    def steps(self) -> int:
        return sum(len(t.steps) for t in self.turns)

    def told(self) -> int:
        """How many times the person said what to do next (the opening
        is not one of them)."""
        return max(0, len(self.turns) - 1)

    @property
    def empty(self) -> bool:
        """Nothing taught yet: only the opening. A new ``/learn start``
        replaces such a lesson rather than reporting on it -- on Telegram a
        person started a second, was told how far the first had got (0
        steps), and took it for the new one."""
        return self.told() == 0 and self.steps == 0

    def as_turn(self) -> Turn:
        """The whole lesson as the one turn the write-up reads: its task,
        every step in order, and the person's words where they came."""
        steps: list[Step] = []
        said: list[tuple[int, str]] = []
        for n, turn in enumerate(self.turns):
            if n and turn.task:        # the opening is the lesson's task, not a step
                said.append((len(steps), turn.task))
            steps.extend(turn.steps)
        answer = self.turns[-1].answer if self.turns else ""
        return Turn(task=self.task, steps=steps, answer=answer, said=said,
                    phone=self.phone)

    def status(self) -> str:
        """One line for the person: what is being taught, how far it got."""
        how = "showing" if self.watching else "teaching"
        return (f"You're {how} me how to {self.task}: {self.told()} step(s) so far "
                f"({self.steps} tool call(s)). Send /learn done to keep it as a "
                f"skill, or /learn stop to drop it.")

    def describe(self) -> dict[str, Any]:
        """For a page: what is being taught, how, and how far it got."""
        return {"task": self.task, "watching": self.watching, "told": self.told(),
                "steps": self.steps, "phone": self.phone, "status": self.status()}

    def to_dict(self) -> dict[str, Any]:
        return {"task": self.task, "watching": self.watching, "phone": self.phone,
                "started_at": self.started_at,
                "turns": [{"task": t.task, "answer": t.answer,
                           "steps": [{"id": s.call_id, "name": s.name,
                                      "arguments": s.arguments, "result": s.result,
                                      "ok": s.ok} for s in t.steps]}
                          for t in self.turns]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Lesson:
        turns = [Turn(task=str(t.get("task", "")), answer=str(t.get("answer", "")),
                      steps=[Step(str(s.get("id", "")), str(s.get("name", "")),
                                  dict(s.get("arguments") or {}), str(s.get("result", "")),
                                  bool(s.get("ok"))) for s in t.get("steps") or []])
                 for t in data.get("turns") or []]
        return cls(task=str(data.get("task", "")), watching=bool(data.get("watching")),
                   phone=str(data.get("phone", "")),
                   started_at=str(data.get("started_at", "")), turns=turns)


def parse(arg: str) -> tuple[str, str]:
    """``/learn``'s argument as (verb, rest): ``("", "")`` for plain
    ``/learn``, ``("start", "send a WhatsApp")`` and so on; ``clear`` comes
    back as ``stop`` (``ALIASES``). An unknown word is returned as the
    verb, for the host to answer."""
    verb, _, rest = arg.strip().partition(" ")
    verb = verb.lower()
    return ALIASES.get(verb, verb), rest.strip()


#: What ``/learn done`` says to a lesson nothing was taught in. Kept open:
#: they may have more to say. The common case, met on a real iPhone: the
#: person did each step on the phone by hand during a ``start`` lesson,
#: which learns only the steps it is told.
NOTHING_YET = ("You haven't shown me any steps yet, so there's nothing to keep. With "
               "/learn start, tell me each step in a message and I'll do it. If you'd "
               "rather do the steps yourself on the phone, send /learn watch and what "
               "the job is for, then next after each step.")

#: What a host adds when /new or /clear ends an open lesson: the
#: conversation that told the agent it was being taught is gone.
DROPPED = "The lesson you'd started was dropped; nothing was kept."

#: What a host says to ``/learn start`` without a task, and to a word it
#: doesn't know.
USAGE = ("Two ways to teach me a job. TELL: /learn start and what it's for "
         "(\"/learn start send a WhatsApp message\"), then type each step and "
         "I'll do it -- you don't touch the phone. SHOW: /learn watch and what "
         "it's for (\"/learn watch check my Coinbase portfolio value\"), then do "
         "each step yourself on the phone and send next after each one -- I "
         "don't touch it. Either way, /learn done keeps it as a skill and /learn "
         "stop (or /learn clear) drops it. Plain /learn keeps what the last answer did.")
