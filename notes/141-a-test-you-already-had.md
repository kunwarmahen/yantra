# 141 — A test you already had

A package's suite ([note 33](33-evals-as-a-gate.md)) is written by its
author before anybody has used the agent, on cases they made up. The
cases that matter most to a person are the ones they already had: "what
are my accounts on Robinhood", "what Notion pages did I look at",
asked last week and answered right. History
([note 130](130-every-conversation-kept.md)) keeps every one of those
word for word and tool by tool, so the test is already written down:
what was asked, which tools answered, what they said, and what the
agent made of it.

So a conversation can now be kept as a test:

```bash
yantra --keep-test aa5f4ecb --words IRA --words margin    # an id from /chats
/chats test 3 IRA, margin                                  # in the terminal
# the page: History → open one → keep as a test
yantra --eval --kept                                       # run them
```

Each keep writes one `[[case]]` into a suite of the person's own, one
folder per agent (`~/.local/state/yantra/tests/<agent>/cases.toml`,
`YANTRA_TESTS=PATH` to move it), and the recording beside it in
`replay/<id>.json`. `--eval --kept` runs that suite instead of the
package's, and everything `--eval` already does still applies:
`--report`, `--against` last night's, `--repeat`, `--case`.

## Nothing real runs

A test that sent the email again, or pressed Buy on the phone again,
is not a test anybody can run every night. So every tool the agent is
offered during a kept test is a fake that answers from the recording,
writes included. A fake says it is read-only, which it is, whatever the
tool it stands for would have done; that is also how it gets past the
unattended gate, which refuses anything else.

The fakes are added where tools are added (`ReplayRegistry.register`),
not wrapped once at the start, because a package's tools, skills and
MCP tools arrive after the registry is handed over. One real tool
slipping through is a replay that sends an email.

## Same name, best match

A model asked the same question again rarely calls a tool with the
same words twice. A replay that only answered the identical call would
go red every time the model rephrased a search. So a fake answers:

| the call tonight | what it gets |
|---|---|
| same tool, same arguments | what that call returned |
| same tool, other arguments | the recording of that tool sharing the most argument values (the earliest on a tie) |
| a tool never called in the recording | `not recorded in this test: … Answer with what the other tools gave you.` |

The last one is information, not an error to fight. An agent that
wanders into a tool it never needed is told so, and either comes back
or goes red on the answer. A call that failed when it was kept fails
again the same way.

## Tonight's agent, with stand-ins

The agent under test is tonight's: its prompt, its package and its tool
list as they are when the run starts. That is what the test is for. A
changed prompt or a swapped model is the thing being checked.

A tool the recording used that tonight's agent does not have (a Setu
connection the eval run does not start, a phone tool) is offered as a
stand-in made from the recording. This lets a test kept on Telegram
still run from a timer with nothing connected. When the conversation
was kept from a live session (the page, `/chats test`), the stand-in
carries the real tool's description and arguments. Kept from the
command line, it takes any arguments.

The kept turn is the conversation's LAST one, asked with everything
before it put back behind it, the way it was asked the first time.

## Three checks

| key | what it checks | costs |
|---|---|---|
| `required_tools` | the tools the kept turn used are used again | nothing |
| `answer_contains` | the words typed when it was kept are in the answer (any case) | nothing |
| `judge = true` | the model says whether tonight's answer gives the same facts as the kept one | one more call |

The words are the person's: what made the answer right when they saw
it. The judge is told to compare facts, not wording ("different
wording, order or length is fine"), because a local model rephrases
every time. The judge is the model under test unless `--judge-model`
names another. A judge sharing a brain with the candidate correlates
their errors ([note 10](10-evals.md)), which is why it is one check of
three and never the only one. A judge that cannot answer fails the
case. A verdict nobody gave is not a pass.

`answer_contains` is an ordinary suite key, so a package's own
`cases.toml` can use it too. `replay` must name a file inside the suite
folder, is read when the suite loads (a recording that will not open
costs nothing to find out), and its bytes go into the case's
fingerprint ([note 72](72-what-changed-under-a-name.md)). An edited
recording is a different case.

## Live receipt

Two real conversations from History, kept and replayed on
`qwen3.8-64k:latest` (Ollama). No Setu was started, so the Robinhood
and Notion tools were stand-ins. Account digits are masked here.

```
$ yantra --keep-test aa5f4ecb --words IRA --words margin
kept as a test of yantra: kept-aa5f4ecb in ~/.local/state/yantra/tests/yantra
asks: what are the accounts on robinhood
tools it must use: mcp__robinhood-personal__get_accounts
the answer must say: IRA, margin

$ yantra --provider ollama --model qwen3.8-64k:latest --eval --kept --report night1.json
eval yantra (kept tests) · 1 case(s) · ollama · qwen3.8-64k:latest
  FAIL  kept-aa5f4ecb  27.8s · 11311 tok · 2 it · mcp__robinhood-personal__get_accounts
        judge: FAIL The candidate answer lists different account numbers
        (••••9XXXX, …) compared to the reference answer (••••XXXX, …), so the
        specific identifiers do not match.
SUITE RED · 0/1 passed · 11311 tokens
```

The stand-in answered from the recording, both words were in the
answer, and the judge caught a real difference: tonight's answer masked
five digits where the kept one masked four. The second night, with a
two-turn Notion conversation kept beside it:

```
$ yantra … --eval --kept --report night2.json --against night1.json
  FAIL  kept-aa5f4ecb  47.3s · 11729 tok · 2 it · mcp__robinhood-personal__get_accounts
        judge: FAIL The candidate answer lists the crypto account for ••••XXXX
        as ••••3XXX, while the reference answer states it is ••••6XXX
  PASS  kept-da553c46  26.5s · 12554 tok · 5 it · mcp__notion-personal__notion-search
SUITE RED · 1/2 passed · 24283 tokens
against yantra (kept tests) on ollama/qwen3.8-64k:latest (0/1 passed)
  added  kept-da553c46
```

That red is the lesson. Tonight's crypto account number is the one in
the tool's own output. The KEPT answer had it wrong, and the judge was
faithful to a wrong reference. **Keep a conversation whose answer you
checked.** A test is only as right as the answer it was kept from.
When a kept answer turns out wrong, delete its case, or put the right
answer into `answer` in its recording.

## Tests

`tests/test_kept_tests.py` (30). The bias is against a replay that is
not one: every tool there that is not a fake raises if it runs, and the
gate is the unattended one. They check:

* the last turn is kept with the conversation before it, and a turn
  with no answer cannot be kept;
* only the called tools' schemas are kept;
* same name, best match (identical, closest, earliest on a tie, none);
* a keep appends with a fresh id and loads with its recording, and an
  edited recording changes the fingerprint;
* a replay outside the suite folder is refused, and so is a judge with
  nothing to compare;
* `answer_contains` renders back;
* nothing real runs, and an unrecorded tool is told so;
* a recorded tool tonight lacks is stood in;
* missing words fail by name and ignore case;
* the judge sees the kept answer, runs with no tools, and fails closed;
* a recorded error is replayed as an error;
* `--keep-test`, `/chats test` and the page's endpoint each keep one.

## What is not here yet

* **A night run on its own.** Samay running `--eval --kept` every
  night, and a pass/fail card on Sarathi's home page with what changed
  since last night.
* **Dvara's people.** `/keep test` on Telegram, into that person's own
  suite for that agent.
* **Choosing the turn.** The last turn is kept. A conversation whose
  good answer came earlier is kept by carrying it on to that point
  in a new conversation.
* **Pictures.** A screenshot a tool returned is replayed as its text
  only.
