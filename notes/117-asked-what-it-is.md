# 117 — Asked what it is

Yantra has siblings that each answer one question about themselves.
`setu status --json` lists your accounts; `samay status --json` says
whether the clock is running; `dvara status --json` says whether the door
is open. Sarathi, which puts them together, asks each one and repeats
what it says.

Yantra couldn't be asked. Sarathi's line for it said where the program
was and nothing else. That leaves out the two things a person setting up
a helper actually wants to know: *which release is this?* and *if I ask
it something now, will a model answer?* The second one matters most on a
local model. Ollama not running, or a model tag that was never pulled, is
the first thing that goes wrong for about half the people reading this.
Until now the only way to find out was to ask a question and watch it
fail.

## One word that is not a prompt

Everything Yantra takes is a flag, and a bare word is a question for the
model: `yantra "what is in README.md?"`. So before this,
`yantra status --json` sent the word *status* to a model and printed its
answer as JSON. A program reading that would find no `format` and refuse
it, but only after paying for a turn.

**`status` FIRST, OR NOT AT ALL.** When the first word on the command line
is `status`, Yantra answers it before the flag parser runs, and never
builds an agent. Only `--json` may follow it. Anything else is an error
that names the way out, because the person who typed
`yantra status of my order` meant a question:

```
$ yantra status --verbose
error: yantra status takes only --json (to ask a model about 'status', use --prompt)
```

The tradeoff: the one-word prompt *status* now needs `--prompt status`.
The other choice was a flag, `yantra --status`, which nobody who has used
Setu, Samay or Dvara would guess.

## What it says

```
$ yantra status
yantra 0.1.0 · /home/you/yantra/.venv/bin/python3
would ask ollama for qwen3.8:latest (chosen by YANTRA_PROVIDER)
local server answering at http://localhost:11434/v1; qwen3.8:latest is pulled
extras: web, browse
tool packs: none
setu: 9 account(s), found by /home/you/.local/bin/setu
samay: clock running (/home/you/samay/.venv/bin/samay)
```

`--json` prints the same thing as `yantra.status.v1`: `version`,
`python`, `provider`, `chosen_by`, `model`, `base_url`, `local`
(`answering`, `pulled`), `extras`, `packs`, `setu` (`found`, `road`,
`connections`), `samay` (`found`, `program`, `serving`) and `problems`. As with the
others, the `format` field is the contract, and a reader refuses a
version it doesn't know.

**WHAT YANTRA WOULD DO, NOT WHAT IS CONFIGURED SOMEWHERE.** The provider
comes from the same ladder a turn uses (`guess_provider`: `YANTRA_PROVIDER`,
then a key, then an `OLLAMA_*` line), read in the same folder with the
same `.env`. `chosen_by` names the rung that decided it. A machine with
a forgotten `ANTHROPIC_API_KEY` in its shell explains itself on one line
instead of in a bill.

**NO SECRET IN IT.** `chosen_by` names a variable, never its value.

**ONE QUESTION OVER THE NETWORK, AND ONLY TO THIS MACHINE.** On the local
road, Ollama is asked for its list of models (`/api/tags`, two seconds at
most). That turns the two common failures into a sentence with the fix
in it:

```
problem: the local server is not answering at http://localhost:11434/v1 (start it: ollama serve)
problem: gemma4:12b is not pulled (ollama pull gemma4:12b)
```

A cloud provider is never asked anything. Whether a key works is for the
provider to say, and finding out costs a call.

**SETU AND SAMAY, AS A SESSION WOULD FIND THEM.** The same finders a
session runs at startup, with the same settings (`YANTRA_SETU`,
`YANTRA_SAMAY`, then what is installed), so "found" means a session
started here would get their tools. Each one is asked its own
`status --json`, and neither is started. Setu's answer is a count of
accounts and never their names: those are Setu's to list. A finder that
was pointed somewhere and found nothing is a problem line
(`samay: no samay program at /opt/samay`).

**A PROBLEM IS A LINE, NOT AN EXIT.** No provider at all is reported in
`problems` and the command still exits 0 with the rest of the answer. A
reader that gets a non-zero exit only gets one line of stderr, and the
person asking `status` is the one who most needs the whole answer.

## What Sarathi does with it

Sarathi asks it with what `sarathi up` would give Yantra: the model
settings from `sarathi.toml`, and the Setu and Samay it would name with
`--setu` and `--samay` (as `YANTRA_SETU` and `YANTRA_SAMAY`, or `off`
for a clock turned off). Asked any other way, Yantra would describe a
model it will never be started with, or miss a Samay that isn't on its
PATH. Sarathi's line repeats the model and adds only what Yantra *doesn't*
find (`finds no samay`), since Setu's and Samay's own lines already say
what they are. Live, on the machine this was
written on:

```
$ sarathi status
yantra      ~/Documents/ai/agent/yantra/.venv/bin/yantra  (beside)
            the machine: the agent itself
            yantra 0.1.0; would ask ollama for qwen3.8-64k:latest
```

and in the folder Yantra is developed in, where `.env` holds a key for
an OpenAI-compatible gateway:

```
$ yantra status --json
{
  "format": "yantra.status.v1",
  "version": "0.1.0",
  "provider": "responses",
  "chosen_by": "RESPONSES_API_KEY",
  "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
  "local": null,
  ...
  "problems": []
}
```

## What was deliberately not built

* **Checking a cloud key.** It would cost a call and only proves the key
  worked a moment ago.
* **The package.** `status` describes Yantra with no flags. An agent
  package changes the model and tools, and `yantra --agent DIR --eval` is
  what tells you whether that one works.
* **A release number that means something.** Every checkout says `0.1.0`
  until there are releases to number. The field is there for when there
  are.
