# 155 — Routines, run by name

Vidhi keeps routines a person drew once: steps that run side by side, ask
the model, ask the person yes or no, and tell them (Vidhi's notes/01). A
person who set up a morning brief wants to say *"do my morning thing"*,
and a model that has never heard of `morning-brief` will do what it always
did: fetch the weather, search the mail, write something, its own way,
again.

Continues [140 — a watch, offered](140-a-watch-offered.md) and
[154 — a reading the model knows](154-a-reading-the-model-knows.md).

## Run or install, never write

`vidhi_link.py` finds Vidhi as `prahari_link.py` finds Prahari (`vidhi
status --json`, format `vidhi.status.v1`; `YANTRA_VIDHI`, `--vidhi`,
`--no-vidhi`) and starts `vidhi mcp --for PERSON`: seven tools, three
that read (`list_routines`, `list_templates`, `routine_runs`) and four
that write (`run_routine`, `install_template`, `set_routine_inputs`,
`stop_run`).

**NONE WRITES A STEP.** Drawing a routine is the person's, on Vidhi's
page. Writing a graph is exactly where a small local model goes wrong;
picking `morning-brief` and filling `latitude = 51.5` is where it goes
right. So the tools are picking and filling.

**NONE ANSWERS A ROUTINE'S QUESTION.** A routine's *ask me* step asks the
person, with buttons in their chat. If the agent could answer it, the
question would be asking the agent.

## The card says what a yes covers

A run is a yes to every call the routine makes, unattended. The card
comes from Vidhi's own words for the routine (`vidhi show --json`):

```
Run the routine morning-brief now: Morning brief

What it does, by itself, once started
  - Today's weather: calls web_fetch {"url": "https://api.open-meteo.com/v1/forecast?…"}
  - Unread mail: calls mcp__gmail__search_threads {"account": "personal", …}
  - Headlines: calls web_fetch {"url": "https://feeds.npr.org/1001/rss.xml"}
  - Write the brief: asks the model, with no tools
  - Send it to me: tells you, on your channels
  - uses your accounts: gmail

Cost: 1 model ask per run

With
  latitude = 35.78
  longitude = -78.64
  mail_account = personal
  unit = fahrenheit
```

An input given for this run only is marked so; a paused routine says
Vidhi will refuse it. An install card says where the template came from
(*comes with Vidhi*, *signed by rita*), every blank and whether it was
given or left to its default, and that installing doesn't run it.

A Prahari watch that starts a routine (`routine` on `create_watch`) says
so on its card, *Then it starts your routine price-then-plan (Vidhi)*,
in place of the then-turn.

## Names in the prompt

A `routines` layer, after `watches`, says what Vidhi is for, to start a
routine by name or by what it does and say it started rather than wait,
to offer a template for something one already does, and never to write
steps; then the person's routines, one line each, *name: title -- cost*.
What a run found never goes in: it's old by the time the prompt is
read, and it would change the prompt on every run. Twelve, then a
pointer. A Vidhi that isn't running is said, so the model tells the
person the run will wait.

## Live receipt

A scratch Vidhi with the morning brief installed, `qwen3.8:latest`, and
nothing else said:

```
$ yantra --provider ollama --model qwen3.8:latest --memory off --no-prahari --no-samay \
    --vidhi …/vidhi --prompt "do my morning thing"
vidhi: 7 tool(s), 1 routine(s); not running (vidhi serve) -- …/vidhi
→ list_available_tools()
→ mcp__vidhi__list_routines()
→ mcp__vidhi__run_routine()     {"name": "morning-brief"}
Morning brief is kicked off (run 7dc947c9). One thing though: Vidhi isn't running
on this machine right now, so the run is queued — it'll go when `vidhi serve`
starts. The brief (weather, your unread mail, headlines) will come to you on
your channels once it fires.
── end_turn · 29093 in / 557 out · 4 iteration(s)
```

The first run of the same question was asked without anyone at the
terminal, and its card (above) was asked even with `--allow-tools
mcp__vidhi__run_routine`: the turn had read `list_routines`' answer,
text it didn't write, and a yes after that is asked again
([note 148](148-a-source-you-trust.md)). The model, refused by the
closed terminal, then fetched the weather itself. With someone to say
yes, it took four steps and started the routine.

## Not here

- **A Routines panel on this page.** Vidhi's own page draws, runs and
  answers; Sarathi's home page has its card.
- **Routines for an unattended run.** None, as for Prahari.
