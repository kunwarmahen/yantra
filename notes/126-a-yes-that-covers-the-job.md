# 126 — A yes that covers the job

[Note 115](115-what-a-yes-covers.md) made the approval card for a new
schedule readable, and [note 118](118-the-internet-is-not-a-read.md)
taught the model that a site check needs `web_fetch` listed. Neither
covered the plainest kind of schedule: one whose whole job is to change
something.

## The light that stayed on

A person typed *turn off my stairs light in 5 minutes*. The agent
offered a schedule, the person said yes on the card, and five minutes
later Samay ran it. The run came back:

```
Couldn't do it. The scheduled run is unattended, so it only
auto-approves read-only tools — my switch/turn_off call on
switch.lights_2 was refused ("Nobody is available to ask").
```

Samay filed it as `ok`. The light was still on. The person was sitting
in front of Telegram and the page the whole time, ready to tap yes.

Three things went wrong, and each one alone would have saved it:

1. **The model allowed nothing.** The schedule was saved with
   `allow_tools: []`. The prompt layer told the model how to list
   website tools, but it never said that when the job *is* a change, the
   tool that makes the change has to be listed.
2. **The card didn't warn anyone.** It said *only tools that read*, and
   at the bottom, in the same words it uses for every schedule, that
   anything else is refused. Both were true. Neither said *this
   schedule cannot do what you asked*.
3. **Nobody could be asked.** A schedule made at this computer runs on
   Samay's direct road: Yantra started as a program with nobody at it.
   "Unattended" meant "refuse what was not allowed ahead of time", even
   when the person was a tap away.

And a fourth, in Samay: a run that failed for want of a tool was filed as
done. That fix is Samay's (its note 07).

## The model is told to list the tool

The `schedules` layer gained one sentence:

> IF THE JOB ITSELF CHANGES SOMETHING -- turns a light off, sends the
> message, writes the file -- list the tool that does it, or it is
> refused at its time and the job fails.

Before it, `qwen3.8:latest` (asked *in 30 minutes, write OFF into
stairs.txt*) saved the schedule with nothing allowed. After it, the same
model saved `allow_tools: ['write_file']`.

## A grant can be fixed to one thing

Listing the tool has a cost. Home Assistant's `call_service` is one tool
for every device in the house: lights, fans, the thermostat. A yes to
"turn off the stairs light" listed as `call_service` is also a yes to
anything else that tool can reach.

So an entry in `allow_tools` can now fix arguments, in brackets after the
tool's name:

```
mcp__homeassistant-home__call_service(domain=switch, service=turn_off, entity_id=switch.lights_2)
```

Only a call with exactly those values is covered. The same tool on the
front door, or on `switch.lights_2,lock.front_door`, falls through to
whatever would have happened with no grant at all: asked, or refused.
Arguments the grant doesn't name are left free, so `data` on a light's
`turn_on` may still carry a brightness.

**THE NAME MAY BE A GLOB, A VALUE MAY NOT.** Dvara's standing rules
learned this first (its note 03). `entity_id=switch.*` looks narrow and
covers every switch in the house. So values match exactly, as written; a
value with a comma in it goes in double quotes.

**A GRANT TYPED WRONG IS AN ERROR, NOT THE WHOLE TOOL.** A bracket left
open could easily be read as "the name, then some noise", which would
quietly mean *any arguments*. That is the mistake this form exists to
prevent, so `parse_grant` refuses it: `yantra --allow-tools` exits with
an error, Samay won't save the schedule, Dvara's `/message` answers 400,
and the card shows the entry in capitals.

The form is `Grant` in `permissions.py`, and `allow_named` matches with
it. Dvara's answers-ahead-of-time use the same function, so a grant means
the same thing on both roads.

## The card says when the job can't work

When the prompt reads like a change and nothing listed can make one, the
card says so in capitals, before the yes. This is the card for the
schedule above, as the new code draws it:

```
May use without asking
  only tools that read
  IT SAYS "turn off", BUT NOTHING ALLOWED HERE CAN CHANGE ANYTHING: at its
  time that step will be asked through Dvara (Telegram, Sarathi's page),
  and refused if nobody answers.
```

And with the grant fixed to the switch:

```
May use without asking
  mcp__homeassistant-home__call_service -- only with domain=switch,
  service=turn_off, entity_id=switch.lights_2
```

A tool that changes things listed with no arguments fixed gets a line of
its own: `call_service WITH ANY ARGUMENTS -- whatever it can reach, not
one thing`.

**IT IS A GUESS, AND A WARNING IS ALL IT DOES.** "Reads like a change"
is a short list of verbs (*turn off*, *send*, *delete*, *book* …) plus
any tool of this agent that changes things whose name appears in the
prompt. A false warning costs a line on a card. A missed one cost a
light left on.

## A run nobody watches can still ask

`unattended.Reach` is a gate for a run nobody is watching that still
knows where its person is. Whoever starts Yantra sets four variables:
`YANTRA_ASK_URL` (a Dvara), `YANTRA_ASK_TOKEN`, `YANTRA_ASK_ACTOR` (the
person) and `YANTRA_ASK_WAIT` (seconds, for the whole run). A write that
nothing allowed then becomes `POST /ask` on that Dvara. The person sees
the same two buttons on Telegram and the same card on Sarathi's page as
for any other question, and the answer comes back as the decision.

Samay sets these on its direct road when it knows a Dvara (Samay's note
07), so a schedule made here asks its person instead of refusing. The
card knows this too. `Samay.asks_go` reads `local_to` from Samay's
status, and the foot of the card changes from *refused while nobody is
there* to *asked through Dvara …; it waits 30 minutes in all*.

**THE WAIT BELONGS TO THE RUN, NOT TO EACH QUESTION.** Five held calls
don't wait five times thirty minutes. Once the run's wait is spent,
what's left is refused without asking, the way Dvara's own scheduled
turns lapse. Silence is a refusal and never a yes. So is a Dvara that
can't be reached. Every refusal is still written to the run's `refused`
list, so Samay sees what the run reached for.

**IT CARRIES THE QUESTION; IT GRANTS NOTHING.** Dvara decides whether
the person may be asked: the owner's deny rules still refuse, and a
person on `read_only` is never put on the spot.

## The receipt

Scratch Samay and scratch Dvara on this machine, the direct road,
`qwen3.8:latest`. The job was *write OFF into stairs.txt*, standing in
for the switch.

- **Nothing allowed, asked, yes.** The question appeared on Dvara as
  `write_file: NEW FILE stairs.txt (1 lines)` from the direct-road
  Yantra. It was answered yes the way the Telegram button answers. The
  run finished `ok`: *The stairs light is off.* and the file held `OFF`.
- **Nothing allowed, asked, no.** Samay filed the run as `refused` (not
  `ok`), and the notice through Dvara ended *Not allowed without asking:
  write_file. To let it next time, say so in a chat with me, or run:
  samay allow bf1d68c0 'write_file'*.
- **Fixed grant.** After `samay allow bf1d68c0
  'write_file(path=stairs2.txt)'` the next run wrote the file, and
  Dvara's question list stayed empty.
- **The model and the new sentence.** Asked at the terminal to do it in
  30 minutes, qwen saved `allow_tools: ['write_file']`. It listed the
  tool, but didn't fix its argument; on the ask road a grant that misses
  is asked about, not refused.

## Not built

- **Inferring the grant from the prompt.** Yantra could parse *switch
  off switch.lights_2* and write the brackets itself. It would be right
  for this case and wrong in quiet ways for the next one, and the card
  would show a yes the person never asked for. The model proposes and
  the card shows the proposal exactly.
- **Asking from the terminal.** `Reach` speaks only to a Dvara. A run at
  this computer with nobody at it has no other place to put a question.
- **`ask_user` through Dvara.** A scheduled run's free-form question
  still fails the turn (note 114). Two buttons fit a yes or no; a
  question that needs typed words has nowhere to wait.
