# 154 — A reading the model knows is there

Prahari keeps **indriyas**: a reading kept up to date by one call on its
own clock (*the outside temperature, every 15 minutes*), whose value any
number of watches decide on instead of each making the call (Prahari's
notes/06). The guestroom band is two watches on one number. With an
indriya under them, it's one call per reading, not two.

A reading is a value nobody looks at unless they know it's there.
Ask the agent *"how cold is it outside?"* and a model that has never
heard of `outside-temp` does what it always did: fetches the weather.
That's the third call for one number, and the answer can disagree with
the one the watches just took. Prahari's `list_indriyas` says *"answer
from it instead of calling the tool again"* in its description. But a
description is read only after a model has chosen the tool, and with
tool selection a tool it doesn't think of may not even be offered that
turn ([notes/17](17-tool-selection.md)).

Continues [140 — a watch, offered](140-a-watch-offered.md) and
[147 — the watches on the page](147-the-watches-on-the-page.md).

## Names in the prompt, never values

The `watches` layer (prahari_link.py) now ends with the readings the
person may use, their own and then the ones someone shared with the
house:

```
Readings Prahari already keeps (indriyas). When one answers what the person
asks, read it with `mcp__prahari__read_indriya` instead of calling the tool
it reads with, and say how old it is; call the tool yourself only when the
reading is too old for the question or did not read. A watch on one of these
numbers senses it (`sense`: its name) instead of a check:
- outside-temp: reads the outside temperature every 15 minutes: looks at
  api.open-meteo.com/v1/forecast (with web_fetch); shared with the house
```

**WHAT AND HOW OFTEN, NEVER THE VALUE.** Writing *49.2°F* into the
prompt looks cheaper, since it saves the read. But the prompt is written
when the session starts, and a page left open all afternoon would still
say 49.2 at teatime. A prompt that changed with every reading would
also never be the same twice, so no cache would hold it. So the layer
says what exists, and the model reads the value with its age when the
question needs it. *"Say how old it is"* is part of the instruction,
because 49.2 and 49.2 from three hours ago are different answers.

**ASKED AS THE PERSON.** The list comes from `prahari indriya list
--as PERSON`, and Prahari decides what that person may see: their own,
then the house's. A private reading (unread mail from the bank) never
reaches someone else's prompt through Dvara. A house reading kept by
someone else is said as theirs, *(asha's, shared with the house)*. At
the computer, readings made from the owner's own chat count as the
owner's too.

**TWELVE, THEN A POINTER.** Past twelve, the layer says *"and 3 more
(`list_indriyas`)"*. A person with thirty readings shouldn't pay for
thirty lines on every turn.

**AND AGAIN AFTER ONE IS MADE.** When the agent makes, mends or
removes an indriya in a turn, the layer is written again before that
turn's next step, so the watch it offers next can `sense` the reading it
just made.

A step 8 joins the seven that say how to write a watch: one number
wanted by two watches, or asked for often, is one reading, made after a
preview and the person's yes.

## The card says what a yes covers, again

`create_indriya` is a call on a timer with the person's accounts, the
same yes as a watch. It had no card of its own, so the person saw raw
JSON. It now gets one in the shape of a watch's
([note 140](140-a-watch-offered.md#the-card-the-call-said-as-it-will-run)):

```
New reading kept up to date: outside-temp

What it reads, with no model
  every 15m: web_fetch {"url": "https://api.open-meteo.com/…"}
  web_fetch IS NOT READ-ONLY: saying yes lets Prahari run exactly this call
  every 15m, with nobody asked again
What it keeps
  the outside temperature (the value at current.temperature_2m)
Who may read it
  only you

It tells nobody anything: watches that sense it decide.
```

`share: house` turns the last block into capitals: *ANYONE IN THE HOUSE:
their watches and agents read its value (never call anything as you)*.
A mend shows the reading as it is now and says when the watches that
sense it will start over. A removal says it's for good.

The watch card had a gap too. A `create_watch` with `sense` has no
check, so its card said *"every 1h: ? {}"* and *"NO TOOL BY THIS NAME"*,
a warning in capitals about a call that would never be made. It now says
what it decides on: *the reading 'outside-temp' that Prahari already
keeps; it makes no call of its own*. Its preview passes `sense` along,
so the card's *now:* line is the reading's.

**A reading from a template has a card too.** Prahari can make a reading
from a template somebody already wrote, its blanks filled (Prahari's
notes/11): `install_indriya_template`, after `indriya_templates` lists
them. The layer's step 8 says to prefer one of those to writing a URL.
Its card is the template's own line, *what it calls*, as
`indriya_templates` says it; the blanks as the agent filled them
(*Filled in with latitude = 35.79*); and, for one from a marketplace,
*made only if a reviewer you trust signed it*. So the yes covers the
call that will actually run, not a template's name.

## On the page

The Watches panel lists the readings under the watches: the value in
bold with its age, its sentence, when it reads next, which watches sense
it, and its last bad reading if the latest one failed. The person's own
readings get **read now** (not waited for, like **look now**) and
**pause**. A house reading someone else keeps has no buttons, because
changing it is theirs. A watch that senses one says *senses
outside-temp*, and has no **look now** and no **every** box, because it
looks at nothing itself. Its **edit** form keeps pick and when, which
are the watch's own, and says the call and how often belong to the
reading.

```
GET  /api/indriyas                    each reading this person may read; mine: true for theirs
POST /api/indriyas/NAME/look|pause|resume
```

## Live receipt

A scratch Prahari state, one real reading, a watch that senses it, and
a local model with no other help:

```
$ prahari indriya add outside-temp --check web_fetch --args '{"url": "…open-meteo…"}' \
    --pick json:current.temperature_2m --thing "the outside temperature" --share house
$ prahari add "Heat the guestroom" --sense outside-temp --when 'below 60'
$ prahari indriya look outside-temp
Sun 8 Nov 23:04  read       49.2
33e301bf  Sun 8 Nov 23:04  fired      49.2  then: told

$ yantra --provider ollama --model qwen3.8:latest --memory off \
    --prompt "How cold is it outside right now?"
prahari: 15 tool(s), 1 active watch(es); …
tool selection: top 12 of 38+discovery each turn
· thinking
The person is asking how cold it is outside right now. Let me check the
indriyas list — there's a shared reading called "outside-temp" which reads
the outside temperature every 15 minutes. I'll just read that instead of
calling the tool again.
→ mcp__prahari__read_indriya()   {"name": "outside-temp"}
  outside-temp: 49.2 (just now) -- reads the outside temperature every 15
  minutes: …
It's about **49°F outside** (49.2°, read just now) — a crisp, cool November
morning.
── end_turn · 8410 in / 146 out · 2 iteration(s)
```

One read, no weather call, and the age said. The model's reason names
the layer's own words. Tool selection offered `read_indriya` among its
twelve, since the question and the prompt line share *outside* and
*temperature*.

The answer also shows what the reading does not carry. It was 23:04,
not morning: the model guessed the time of day, since nothing in the
reading says it. And *"probably Fahrenheit"* was a guess from where the
URL points. A reading's `thing` that says its unit (*the outside
temperature in °F*) takes the second guess away.

## Not here

- ~~**A read that asks for fresh.**~~ `read_indriya` takes `max_age`,
  and the layer says to give it when the kept value is too old for the
  question. Prahari reads again first, never within five minutes of the
  last reading (Prahari's notes/07). A local model asked *"it has to be
  current"* gave `max_age: "5m"` on its own.
- **Readings for a run nobody watches.** An unattended run gets no
  Prahari at all, as before ([note 140](140-a-watch-offered.md)).
- **Trusting a reading.** An indriya's value came from outside (a
  weather site, a page), so reading one marks the turn like any other
  outside read ([note 148](148-a-source-you-trust.md)). An owner who
  trusts a reading names `mcp__prahari__read_indriya` in
  `trusted_reads`, by its exact name.
