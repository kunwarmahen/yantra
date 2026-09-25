# Tutorial — the whole thing, end to end

> Written for someone who wants to know what these two projects can do
> today, in what order the pieces make sense, and what to type to see
> each one working. No code-reading required. Every step names the note
> that argues the design, so you can drop down a level wherever you get
> curious.

There are two things here, and they are one story told twice.

**Yantra** is the machinery behind tools like Claude Code, written from
scratch: a loop around a chat model, tools the model can call, a
permission gate in front of them, and — on top of that harness — a
framework where an agent is a *directory you can hand to someone*.

**dvara** is what happens when you stop watching that directory. One
process, many people, many agents, many conversations, with the owner's
money and permissions wrapped around all of it. *dvāra* (द्वार) is
Sanskrit for a door.

```
   ┌───────────────────────────────────────────────────────────┐
   │  YANTRA — one agent, one person, one keyboard             │
   │                                                           │
   │   the loop · tools · permission gate · skills             │
   │   sub-agents · MCP · sessions · budgets                   │
   │                        │                                  │
   │                        ▼                                  │
   │   an agent is a DIRECTORY:  agent.toml · prompt.md        │
   │                             tools/ · skills/ · evals/     │
   └────────────────────────┬──────────────────────────────────┘
                            │  those directories, once nobody
                            ▼  is watching them
   ┌───────────────────────────────────────────────────────────┐
   │  DVARA — one process, many people                         │
   │                                                           │
   │   actors · roster · threads · daily allowances            │
   │   escalation to a person · run history · HTTP             │
   └───────────────────────────────────────────────────────────┘
```

The dependency runs one way. dvara imports Yantra; Yantra has never
heard of dvara. When the service needs something the framework does not
have, that becomes a framework feature argued on the framework's terms —
which is how the awaitable permission gate in Act VI got built.

---

## How to read this

Pick one:

* **Twenty minutes, no typing.** Read Act I, then Act III, then Act VI.
  That is the arc: a turn, an agent you can hand over, a service.
* **One hour, hands on.** Follow [Act VII](#act-vii--the-one-hour-path)
  from the top. It is a checklist of things to type, in an order where
  each one explains the next.
* **Looking something up.** [Act VIII](#act-viii--where-to-read-next) maps
  every topic to the note that argues it, in both repos.

Two roads run through the whole document, and neither is the poor
relation:

| | |
|---|---|
| **local** | an Ollama box on hardware you own. No key, no bill, nothing leaves the machine. Every command here works on it. |
| **cloud** | an API key in `.env`. Faster, more capable, metered — which is why half the features below are about money. |

Where a receipt is shown, it says which road produced it.

---

# Act I — one turn at a keyboard

## 1 · Setup

Working *on* Yantra:

```bash
cd yantra
uv sync                          # creates .venv from pyproject.toml
cp .env.example .env             # then fill in a key, or point at Ollama
```

Building *with* it from another project — it installs from git, because
the name on PyPI belongs to something unrelated:

```bash
uv add git+https://github.com/kunwarmahen/yantra
```

`.env` is loaded automatically by a ~15-line loader in `config.py` (no
`python-dotenv` dependency). Real environment variables still win.

The local road needs nothing in `.env` at all:

```bash
ollama pull qwen3.8            # or any tag you like
uv run yantra --provider local --model qwen3.8
```

`local` and `ollama` are the same road — the first is the word most
people reach for, and meeting a brand name for the first time inside an
error message is a poor introduction. Whichever you type, the harness
stores `ollama`: a provider name ends up as a key in the price table and
a column in an eval report, and two spellings there would be two models
([notes/54](notes/54-the-word-for-a-road.md)).

The cloud road needs one line:

```
ANTHROPIC_API_KEY=sk-...
```

and then bare `uv run yantra` — no flag — runs against it.

Bare `uv run yantra` works on the local road too, but a key ladder cannot
see a road whose whole point is having no key, so you say so in `.env`:

```
YANTRA_PROVIDER=local          # ends the question outright ("ollama" works too)
OLLAMA_MODEL=qwen3.8:latest    # or just this: a tag typed by hand is a declaration
```

The full order it resolves in: `YANTRA_PROVIDER`, then whichever API key
is present (Anthropic, OpenAI, Responses), then any `OLLAMA_*` line. A
`--provider` flag beats all of it, and nothing is probed over the network
— a local server that happens to be listening is not a declaration
([notes/45](notes/45-the-road-with-no-key.md)).

One trap worth knowing before it bites you: for OpenAI-dialect providers
— which includes Ollama — `{PREFIX}_BASE_URL` **includes** the `/v1`
segment, because the adapter appends `/chat/completions` to it. For
Anthropic it **excludes** `/v1`. An uncommented line with the wrong shape
beats a correct default, and every turn comes back `404 page not found`.

## 2 · Your first session

```bash
uv run yantra                                   # a REPL
uv run yantra "summarize README.md"             # one-shot, then exit
uv run yantra --image photo.png "what's this?"  # vision one-shot
```

Type something that needs a file and watch it reach for one. The REPL's
commands are `/help /model /provider /tools /history /usage /save /load
/compact /clear /image /build /quit`, plus `/mcp`, `/skills`, `/env` and
`/yolo` as those features arrive below. Ctrl-C cancels the current turn,
not the session.

## 3 · What actually happened

The whole agent is a circle with four steps, and everything else in both
repos exists to serve it:

```
   1. ASK      send the entire transcript to the model
   2. READ     stream the reply. Just an answer? → done.
               Or a request: "run `ls` for me"
   3. ACT      permission gate → actually run it
   4. RECORD   paste the result into the transcript
               └──────────── back to 1 ─────────────
```

The model is a text-in, text-out function. It has no memory of
yesterday, cannot see your files, and cannot run anything. It can only
*ask*. The harness is the part that decides, does it, and writes the
answer down. ([notes/05](notes/05-agent-loop.md))

Two rules from that loop are worth carrying into everything below:

* **Errors are data.** A tool that crashes, is denied, or does not exist
  becomes an error *result* the model reads and works around. The loop
  cannot be crashed by a tool. Provider errors — auth, rate limits,
  context overflow — are the opposite: exceptions, terminal for the turn.
* **The history invariant.** Every tool call gets a matching result
  before the next request goes out. On every exit path, including the
  iteration cap and a mid-turn Ctrl-C. Break it and the next request is
  rejected by the provider for a reason that reads like nonsense.

## 4 · The seatbelt

Before any tool that could change something runs, you are asked:

```
agent wants to run write_file:
  NEW FILE notes.txt (3 lines)
run it? [y/n/e/s] (n):
```

The `e` and the `s` are the interesting ones. A gate may **rewrite the
arguments** before approving, and the loop notices the swap and runs the
edited form — so approval is a review step rather than a rubber stamp
([notes/20](notes/20-approve-with-edits.md)).

And `s` refuses it **in your own words**:

```
run it? [y/n/e/s] (n): s
no, because (or why not): not in the repo root -- try /tmp/scratch instead
```

The model reads that sentence, attributed, in place of "Permission
denied by user." — and builds the call you asked for instead. Editing the
arguments dict by hand is fine for a wrong path and hopeless for "use the
staging database", which is a sentence you produce in two seconds.
([notes/56](notes/56-not-like-that-like-this.md))

The preview is built by the tool itself, with the same context execution
will get — for an edit it is a real unified diff. What you approve is
what runs.

Modes:

```bash
uv run yantra --yolo          # never ask (careful)
```

and `/yolo` flips it back mid-session; in the browser UI it is a chip in
the header. Read-only tools auto-approve in every mode — a service that
woke you up to confirm a `read_file` is a service you turn off.

## 5 · It knows where and when it is

Sessions start aware instead of clueless. The system prompt carries your
time and timezone, host and working directory — and by default your city,
from one keyless lookup to ipinfo.io at startup. Ask *"what's the
temperature outside?"* and it answers for where you actually are, using
its own tools, instead of burning a turn asking which city you are in.

```bash
uv run yantra --env-context local    # machine facts only, no lookup
uv run yantra --env-context off      # nothing injected
```

`/env` flips it live. The honest tradeoff on `full`: your city rides
inside every request to your provider. On the local road it never leaves
the building anyway. ([notes/29](notes/29-environment-awareness.md))

---

# Act II — teaching it things

## 6 · Skills — your procedures, written down once

Some knowledge is yours, not the model's: how *this* repo reviews a PR,
the three files everyone forgets, the release steps in the order that
actually works. Write it in a folder and the agent picks it up **only
when the work calls for it**:

```
skills/new-tool/
├── SKILL.md        instructions, with a header saying what it is for
└── template.py     referenced by the instructions, read only if needed
```

```markdown
---
name: new-tool
description: Add a new built-in tool to this harness -- schema, summary,
  run, registration, permission gating, tests and docs. Use when asked to
  add, write, or wire up a tool that the model can call.
---

# Adding a tool to Yantra
1. Read the neighbours first. `src/yantra/tools/glob.py` is the...
```

Nothing to wire up. Drop the folder in and start a session.

The cost model is the whole design. Only the name and description ride in
the prompt — about 25 tokens each. The instructions arrive as a tool
result when the model calls `load_skill`. Bundled files arrive only if the
instructions send it there. Ten skills cost a rounding error per turn and
the right one shows up in full, on demand.

Where they are found:

| where | for |
|---|---|
| `skills/` | the set your repo commits |
| `.yantra/skills/` | private overrides (gitignored) |
| `~/.yantra/skills/` | yours, on every project |
| `--skills-dir` / `$YANTRA_SKILLS_PATH` | explicit, wins over all of them |

In the REPL: `/skills` lists them with what broke and what loaded,
`/skills NAME` prints one without spending a turn, `/skills off deploy-*`
pulls one mid-session, `/skills reload` re-scans after an edit, and
`/new-tool add a count_lines tool` runs one directly.

A skill may also declare `mode: subagent`, and then its `allowed-tools`
stop being advice: the instructions run in a **fresh child agent** built
with exactly those tools and nothing else, and only its conclusion comes
back. Use it where isolation is the point — a survey that must not be
able to write. ([notes/30](notes/30-skills.md))

## 7 · Sub-agents — a fresh context window on demand

A long job fills the context window with material that is of no further
interest once the answer is known. A sub-agent is a child conversation
with its own window; the parent gets back only the conclusion.

```bash
uv run yantra --subagents "research X and report back"
```

It is an operator flag, not a default, because with `spawn_subagent` the
*model* chooses the child's tools, prompt and iteration cap at call time.
The constraints are in code rather than in prompts: one level deep,
a per-session spawn budget, mandatory justification, compact results
(conclusions and cost metadata, never transcripts), and permissions
inherited so a child cannot escalate by being a child.
([notes/08](notes/08-sub-agents.md))

Act III has the other half of this: a package can *declare* a child, and
then the author owns its tool list instead of the model.

## 8 · MCP — tools somebody else wrote

The Model Context Protocol, hand-rolled here as JSON-RPC over two
transports, picked by the shape of your config. Tools register as
`mcp__<server>__<tool>`.

```json
{"servers": {"tiny":   {"command": "python",
                        "args": ["examples/tiny_mcp_server.py"]},
             "remote": {"url": "http://127.0.0.1:8000/mcp"},
             "paid":   {"url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer ${MY_TOKEN}"}}}}
```

```bash
uv run yantra --mcp-config mcp.json
```

`${MY_TOKEN}` resolves at connect time — unset gives you a named error,
never a blank `Bearer` — and the remembered config stores the
placeholder rather than the secret.

Servers that only issue tokens through a login get the spec's OAuth 2.1
flow, also by hand: discovery, dynamic registration, PKCE in your
browser, refresh.

```bash
uv run yantra --mcp-login vendor --mcp-config vendor.json
```

Tokens land in `~/.local/state/yantra/mcp-tokens.json` at mode 0600 —
never in the working directory.

Servers are runtime furniture, not startup wiring: `/mcp` lists them,
`/mcp add NAME URL` connects one mid-session and offers to remember it,
`/mcp off|on|remove NAME` does the rest. The browser UI's panel does the
same with health dots and a paste-JSON box.
([notes/09](notes/09-mcp.md))

## 9 · The tools that make it finish rather than answer

Sixteen built-ins cover the whole autonomy loop, and four more appear if
you install the browser extra.

| family | tools |
|---|---|
| SEE | `read_file` `list_dir` `glob` `grep` `read_image` |
| CHANGE | `write_file` `edit_file` `bash` |
| RUN LONG | `bash_start` `bash_poll` `bash_kill` |
| LOOK OUT | `web_fetch` |
| REMEMBER | `write_note` `recall_notes` (durable) · `todo_write` `todo_read` (live plan) |
| OPERATE | `browser_open` `browser_click` `browser_fill` `browser_close` — only with `[browse]` |

A few deserve a sentence each:

* **glob** finds files by name (`**` recursion, newest-first) without a
  permission-gated bash call. Read-only, so it never gates.
* **todo lists** are live plan state with replace-whole-list semantics,
  distinct from `write_note`'s durable facts. Cheap grounding that keeps
  local models on script through long missions.
* **web_fetch** pulls one http(s) address as readable text. It is
  deliberately **not** read-only: it reaches the network from outside
  every sandbox wall, so it gates like `bash` and a human approves the
  address.
* **bash_start/poll/kill** run things that outlive one tool call — dev
  servers, watchers, long builds — teeing output to `.yantra/jobs/<id>.log`.
  They are plain subprocesses by design (they outlive any sandbox), so
  they always gate.
* **browser_\*** drive a real browser. JavaScript runs, so
  JS-rendered apps work where `web_fetch` sees an empty shell. Every
  action returns prose plus numbered element refs (`[e1]`, `[e2]`)
  harvested from the live DOM. Logins persist through a profile directory
  — cookies stay on disk, never in model context.

```bash
uv sync --extra browse && uv run playwright install chromium
export YANTRA_BROWSER_PROFILE=~/yantra-browser-profile
uv run yantra --browse-login https://example.com     # sign in once, by hand
```

Sign in fully, then **close the window** — closing it is what writes the
session to disk, and interrupting the command with Ctrl-C is not the
same thing. Yantra then counts what actually landed:

```
profile saved -- 46 cookies; future browser_* sessions start from these logins
```

If it says `nothing was saved` instead, nothing was: sign in again
rather than moving on.

Out of the box that is Playwright's own bundled Chromium, which some
sites refuse — its user-agent says `HeadlessChrome` out loud, and
sign-in pages that check for automation turn it away however visible
the window is. Point Yantra at a browser you already have and both
problems go away: the agent drives that browser, and `--browse-login`
runs it as a plain subprocess with no automation attached, which is
what those sign-in pages are testing for. Use the same value for both —
a profile belongs to the browser that wrote it.

```bash
export YANTRA_BROWSER_EXECUTABLE=chrome        # a channel Playwright knows
# ...or a path, when the channel name does not find it:
#   /usr/bin/google-chrome     Chrome from the .deb/.rpm
#   /snap/bin/brave            Brave from the snap
#   /usr/bin/chromium          Chromium from your distro
# A bare command works too — it is looked up on $PATH.
export YANTRA_BROWSER_HEADED=1                 # a real window, on an Xvfb
uv run yantra --browse-login https://mail.google.com
```

`YANTRA_BROWSER_HEADED=1` is worth setting on a stubborn site: headless
is a different Chromium build with its own fingerprints, and when no
display is attached Yantra starts an Xvfb — a real X server with no
monitor — so the window exists without anyone seeing it. If your
browser is a **snap**, keep the profile path out of hidden (dot)
directories: snap confinement cannot write into them and saves nothing
rather than complaining. None of this is stealth; a captcha still
refuses you ([notes/58](notes/58-the-browser-you-already-have.md)).

One thing that will bite you if nobody warns you: `export` in your
shell **beats** `.env`. If you once pasted an `export
YANTRA_BROWSER_PROFILE=...` line, it outranks the file you are editing
and nothing you change there takes effect. Yantra says so when the two
disagree, but a new terminal is the quicker cure.

### Did the login actually stick?

Ask the agent to go look. No special command — the browser tools are
just tools, so a prompt is the test:

```bash
uv run yantra --prompt "open https://mail.google.com, re-read the page, \
  then tell me whether it shows an inbox or a sign-in screen"
```

Signed in, the agent reads back your inbox; signed out, it reads back
a login form, and nothing about the answer is ambiguous.

The **re-read** in that prompt is not padding. `browser_open` returns
the page a beat after it loads, which is right for ordinary pages and
too early for a heavy JavaScript app like Gmail — the first snapshot
can come back before the app has drawn anything. Calling `browser_open`
again with **no url** re-reads the page already open, without
navigating, and that second look has the content. Worth putting in any
prompt that drives a big web app; the model can work it out alone, but
telling it saves an iteration.

Past about twenty tools, model selection accuracy hits a cliff, so only
the top-K best-matching tools are **sent** each turn (BM25 over name and
description). The autonomy floor always loads regardless, and calling any
real tool by its exact name loads it on the spot — so a selection miss
costs nothing. ([notes/17](notes/17-tool-selection.md))

```bash
uv run yantra --mcp-config big.json --tool-select 12
YANTRA_DISABLED_TOOLS=browser_*,mcp__slack__*      # permanent kill-switch
```

`/tools off bash` pulls one for this session only; `/tools on bash` puts
it back. Both take effect immediately, even mid-turn.

---

# Act III — the agent becomes an object

This is the hinge of the whole project. Everything in Act I and II is a
*session*: something you configure with flags and lose when you close the
laptop. Everything from here on is an *agent*: a thing with a name, a
version, and a diff somebody can review.

## 10 · An agent is a directory

```
researcher/
├── agent.toml      identity · model · tools · skills · servers · policy
├── prompt.md       the system prompt
├── tools/          Tool subclasses this agent brings with it
├── skills/         procedures this agent knows
├── subagents/      prompts for the children it delegates to
└── evals/          the cases that say it still works
```

`./researcher` above is *your* directory, wherever you make it. A worked
one already ships in this repo, so the lines below run from the repo root
as they stand:

```bash
uv run yantra --agent examples/agents/researcher "what changed in notes/30 recently?"
uv run yantra --agent examples/agents/researcher --provider ollama   # your own hardware
cd examples/agents/researcher && uv run yantra                       # ./agent.toml is found
```

`--agent` takes the directory or the `agent.toml` inside it; both work.
That shipped example — [examples/agents/researcher](examples/agents/researcher) —
is a read-only research agent with its own skill, its own tool, its own
declared child and its own eval suite. Read it before writing your own;
it is commented the way a tutorial is.

**The smallest package that works is two lines:**

```toml
[agent]
name = "tiny"
```

**Every field is optional, and that is the portability promise.**

```
command line   >   agent.toml   >   environment   >   built-in default
```

So a package you wrote against a frontier cloud model runs on somebody
else's Ollama box with `--provider ollama` and *their* model — no fork, no
diff to maintain, no edit to your file. This is the single most important
sentence in the format, and it is why `agent.toml` names no provider in
any example here.

A fuller manifest:

```toml
[agent]
name        = "researcher"
description = "Reads sources and answers with citations."
version     = "0.1.0"

[model]
max_iterations = 20             # provider/model left open on purpose

[tools]
allow = ["read_file", "glob", "grep", "web_fetch", "load_skill"]
deny  = ["browser_*"]           # fnmatch, like $YANTRA_DISABLED_TOOLS
dirs  = ["tools"]               # your own tools; ./tools is found anyway

[budget]
max_usd_per_turn = 0.50         # a STOP, not a cap

[permissions]
mode = "ask"                    # ask | yolo

[env]
context = "local"               # off | local | full
```

Three rules do most of the work here:

**`allow`/`deny` are a standing admission policy, not a one-time sweep.**
They govern tools registered *later* too — `ask_user`, `load_skill`, an
MCP server's tools, the package's own tools. An agent that says it does
not get `bash` never gets bash. A non-empty `allow` is a complete
whitelist, MCP tools included; say `mcp__*` if you want them.

**Unknown keys are errors.** A misspelled `deny` that quietly left `bash`
armed would be the worst bug this format could have:

```
$ uv run yantra --agent ./broken
error: ./broken/agent.toml: unknown key(s) in [tools]: alow
       (known: allow, deny, dirs, per_turn)
```

**Reading a manifest never imports anything.** `tools/` is *named* at
parse time and loaded at build time. That is what makes it safe to list a
directory of packages you have not audited — which is exactly what dvara
does in Act VI.

## 11 · A package brings its own tools

Drop a `Tool` subclass into `tools/` and it loads with the agent:

```python
# researcher/tools/outline.py
from yantra.tools.base import Tool, require_str
from yantra.tools.fs import resolve_in_sandbox        # same fence read_file uses

class Outline(Tool):
    name = "outline"
    description = "List the Markdown headings of a file with line numbers..."
    parameters = {"type": "object", ...}              # hand-written, on purpose
    read_only = True                                  # auto-approves; your word

    def summary(self, args, ctx): return f"outline: {args.get('path')}"
    def run(self, args, ctx): ...
```

```
$ uv run yantra --agent ./researcher "outline notes/30-skills.md at depth 1"
agent: researcher 0.1.0 -- ./researcher
package tools: outline
```

Three things to know before running somebody else's package:

1. **Loading `tools/` runs their Python as you**, at import time, before
   any permission gate exists. Read a package's `tools/` the way you
   would read a dependency you are about to install. This is also why
   package paths come from your command line, never from a message.
2. **`read_only = True` is the author's word** and nothing checks it.
3. **A package tool that shadows a built-in raises**, loudly, instead of
   quietly becoming `bash`.

There is deliberately **no `@tool` decorator**. The hand-written schema is
what makes the model call your tool correctly; generating one from type
hints throws that away. ([notes/32](notes/32-package-tools.md))

A tool worth writing is usually worth writing once, though, and copying
`tools/tides.py` between six repositories is how six copies drift. So a
tool can also arrive by **pip**. The publishing side is one table in the
pack's own `pyproject.toml`:

```toml
[project.entry-points."yantra.tools"]
tides = "tidepack.tools"          # a module: every Tool in it. Or ":Tides"
```

and the using side is one line, in the manifest or on the command line:

```bash
uv run yantra --provider ollama --tool-pack tide-pack "tides at Whitby today?"
```
```toml
[tools]
packs = ["tide-pack"]
```

**Named, never ambient.** Loading every installed distribution that
publishes the group would make your agent's tool list a fact about your
virtualenv — different tools on your colleague's machine, and nothing in
`agent.toml` to say why. A name nothing publishes is an error that lists
what *is* installed, and startup prints `tool packs: tide-pack`, because
pip-installed code is still somebody else's code running as you.
To see which names you could use, `uv run yantra --packs` lists every
installed pack and where its tools live, without importing any of them.
([notes/53](notes/53-a-tool-that-arrives-by-pip.md))

Two more things a manifest can say about a pack:

```toml
[tools]
packs = ["tide-pack==0.2.1", "sea-pack"]   # this exact release, or refuse to start

[tools.prefix]
"sea-pack" = "sea"                          # its search becomes sea_search
```

The `==` line does not install anything. Your lockfile still decides
what is installed; the line just refuses to run the agent against a
release it was not written for. The prefix is for when two packs both
ship a tool with the same name. After it, `sea_search` is the only name:
use it in `tools.allow` and in your eval cases
([notes/87](notes/87-which-release-and-what-its-called.md)).

## 12 · A package that delegates

```toml
[[subagent]]
name        = "fact_checker"
description = "Check one claim against the files here."   # the model reads this
prompt      = "subagents/fact_checker.md"                 # or instructions = "..."
tools       = ["read_file", "glob", "grep"]               # NARROWER than the parent
max_iterations = 12
```

The model then calls `fact_checker(task="...")` — one string, and nothing
else. It does not choose the child's tools, its prompt or its iteration
cap. That is the whole difference from `spawn_subagent`, where the model
chooses all three at call time and needs an operator flag to be allowed
to.

What it buys: a fresh context window for work whose *material* is
uninteresting once the answer is known, and an answerable question —
"does this need a permission prompt?" A child that can only read does not
ask. A declared child is also a tool like any other, so it goes through
the package's own admission policy: leave `fact_checker` out of
`tools.allow` and startup prints `package excludes: fact_checker` rather
than shipping it quietly. ([notes/40](notes/40-a-package-that-delegates.md))

## 13 · The package's own acceptance gate

Running somebody's package means running their prompt, their tool list and
their Python. "It works, I promise" is not evidence. So a package ships
the cases that say so, and one command turns them into an exit code:

```
$ uv run yantra --agent examples/agents/researcher --eval --provider ollama
eval researcher 0.1.0 · 6 case(s) · 2 roster-only · ollama · qwen3.8:latest
cwd: .../examples/agents/researcher
gate: read-only tools only; writes and commands are refused (--yolo opens it)
budget: $0.50 per turn -- inert here, a local model bills nothing

  PASS  outlines-before-reading  10.3s · 9228 tok · 3 it · outline, list_dir, read_file
  PASS  cites-what-it-read  14.8s · 10269 tok · 3 it · list_dir, read_file
  PASS  cannot-write-even-when-asked  27.9s · 9154 tok · 3 it · glob, read_file
  PASS  has-no-way-to-write  roster only · no model call · 0 tok
  PASS  delegation-works-end-to-end  20.5s · 5596 tok · 2 it · fact_checker, glob, grep, read_file
  PASS  the-checker-is-actually-on-the-roster  roster only · no model call · 0 tok

SUITE GREEN · 6/6 passed · 34247 tokens · 2 case(s) cost nothing
```

A case is TOML. `check` is the one hatch to Python, resolved against the
package's own `evals/graders.py`:

```toml
[[case]]
id = "outlines-before-reading"
user_message = "Which section of sources/rate-limiting.md covers throttled clients, and on what line?"
required_tools = ["outline"]          # what ACTUALLY executed, not what was offered
forbidden_tools = ["bash"]
check = "graders:names_a_line_number" # (str) -> bool, optional
max_tokens = 30_000
max_iterations = 8
min_pass_rate = 0.7                   # "holds seven runs in ten"
```

### Two kinds of assertion, and only one costs money

The keys above grade the **trajectory** — what ran — so they need a run.
`has_tools` and `lacks_tools` grade the **roster** — the tools the agent
is offered at all, which is knowable the moment it is built:

```toml
[[case]]
id = "has-no-way-to-write"
lacks_tools = ["write_file", "edit_file", "bash", "browser_*"]
has_tools   = ["read_file", "glob", "outline"]
```

Zero tokens, no model call, no `user_message` needed. This is the claim
`forbidden_tools` *cannot* make — a tool that was available and went
unused looks exactly like one that was absent. It takes fnmatch patterns
because a roster is a set, and patterns are refused in the trajectory keys
where they would match nothing and quietly pass.

**Zero tokens now means zero setup.** A run whose selected cases are all
roster ones resolves no provider at all:

```
$ uv run yantra --agent ./researcher --eval --case "has-no-way*"
eval researcher 0.1.0 · 1 case(s) · 1 roster-only · none · (no model needed)
no provider resolved: every selected case grades the roster, so this run
makes no request and needs no key
filtered: has-no-way* -- 1 of 6 case(s); this is not the package's gate

  PASS  has-no-way-to-write  roster only · no model call · 0 tok

SUBSET GREEN · 1/1 passed · 5 case(s) not run · 0 tokens
```

No key, no `.env`, no local server — which is what makes this affordable
on every push. Note that a filtered run says **SUBSET**, never SUITE: a
green line under a filter is a claim about the cases that ran, and that
line is what ends up in a pull request.

### The roster is a ceiling; a sub-agent has a floor

The researcher declares a `fact_checker` (Act IV), and its tool list is
deliberately narrower than the package's: no `web_fetch`, because
checking a claim against the files in front of you is a different job
from going and finding new ones.

`lacks_tools` cannot pin that. A child is built out of the parent's
registry and cannot exceed it — so the parent's list is a **ceiling**
over the whole package, `web_fetch` is inside it, and moving the child up
to meet it changes nothing the assertion can see. The **floor** each
child was given needs its own key:

```toml
[[case]]
id = "the-checker-is-actually-on-the-roster"
has_tools = ["fact_checker"]
subagent_has_tools   = { fact_checker = ["read_file", "outline"] }
subagent_lacks_tools = { "*" = ["web_*", "write_file", "edit_file", "bash"] }
```

Also free. The key is a sub-agent's name, or `"*"` for every child the
package declares — which is the form worth reaching for, because it
covers the sub-agent somebody adds later without anybody updating the
case. Naming a child that is not declared is a *failure*, not a pass:
otherwise renaming a sub-agent would leave a green case that quietly
checked nothing.

Add `web_fetch` to the child's list in `agent.toml` and the gate says so
in under a second, with no model involved:

```
  FAIL  the-checker-is-actually-on-the-roster  roster failed · no model call · 0 tok
        on fact_checker's roster and should not be: web_* matches web_fetch
```

That `[[subagent]]` table decides four things, though, and the tool list
is only the one that changes what the package can *reach*. The other
three — the prompt file it was handed, the model it runs on, how long it
may go on — are three more keys in the same case:

```toml
subagent_prompt_contains    = { fact_checker = ["Quote the evidence", "file and line"] }
subagent_model              = { fact_checker = "" }
subagent_iterations_at_most = { fact_checker = 12 }
```

```
  FAIL  the-checker-is-actually-on-the-roster  roster failed · no model call · 0 tok
        fact_checker's prompt does not mention 'Quote the evidence'
        fact_checker declares its own model (gemma4:26b); the case says it should run on the parent's
        fact_checker may run 50 iterations, and the case allows at most 12
```

Prose is a case-insensitive **substring** and not a pattern — a prompt is
written for a model to read, not to be matched. `""` under
`subagent_model` says the child names no model of its own, so it costs
whatever the parent costs. And the cap is a **ceiling**: passing at 4 and
failing at 50, because the dangerous edit to a cap is upward.
([notes/50](notes/50-the-rest-of-what-a-child-is.md))

Full reasoning in [notes/44](notes/44-a-ceiling-and-a-floor.md).

### One run is one sample

A trajectory is a die roll. `--repeat N` runs every case N times and
judges it on the rate:

```
$ uv run yantra --agent ./researcher --eval --repeat 4 --provider ollama
  FAIL  outlines-before-reading  ✗✗✓✓ 2/4 runs (needs 4) · 54.2s · 22862 tok
        required tool not used: outline (2 of 4 runs)
```

A case stops as soon as it can no longer reach its rate: a case that
must pass every run stops at its first failure, and its line says
`1/2 runs of 6 · stopped, out of reach`. It never stops early on the
winning side, since those later runs are what tell a real rate from a
lucky streak. Add `--all-runs` when you want every run anyway, for
example to tell a case that fails sometimes from one that always fails
([notes/84](notes/84-a-verdict-already-reached.md)).

`min_pass_rate` in the file is the author's claim; how many runs to buy is
the operator's money, so `repeat` is deliberately not a key. `--case
PATTERN` is how you aim the repeats at the one flaky case instead of
paying for it on every deterministic one:

```bash
uv run yantra --agent ./researcher --eval --case "flaky-*" --repeat 10
```

And the count says what it is evidence of, because a fraction gets read
as a rate. Three green runs are consistent with a case that holds 44% of
the time, and one green run with a case that holds 21%:

```
  PASS  delegation-works-end-to-end  ✓✓✓ 3/3 runs (needs 3) · 0.44-1.00 at 95% · 95.0s

SUBSET GREEN · 1/1 passed · 3 runs · 29930 tokens
1 case(s) passed on evidence that does not reach the rate they claim
  delegation-works-end-to-end  3/3 · true rate could be as low as 0.44 · claims 0.7
  · --repeat 9 would settle it, all green
```

Nothing there changes the verdict — the case passed, exit 0 — and `9` is
arithmetic rather than advice: a claim of 0.7 needs nine perfect runs
before the evidence reaches it, 0.85 needs 22, and 0.95 needs 73. That is
the best reason to write the claim you actually mean.
([notes/47](notes/47-what-seven-of-ten-is-evidence-of.md))

### Writing a run down

```
$ uv run yantra --agent . --eval --model gemma4:12b --against runs/qwen.json
SUITE GREEN · 6/6 passed · 24862 tokens · 2 case(s) cost nothing

against researcher 0.1.0 on ollama/qwen3.8:latest, 2026-09-17T02:33:29Z (6/6 passed)
different model: ollama/qwen3.8:latest → ollama/gemma4:12b
  no case changed verdict or pass count
tokens: 34991 → 24862 (-10129)
```

`--report FILE` writes the run as JSON — red runs included, since that is
the one you compare against tomorrow — and each case carries what it cost
in dollars, priced the day it ran so that a vendor's new price page
cannot rewrite it. A local run records a real `0.0` and prints no figure;
an unpriced hosted model records nothing, because zero and unknown are
different numbers ([notes/48](notes/48-what-the-run-cost.md)).
When a run is priced, each case line ends in what that case cost, and
the line under the verdict names the most expensive one and its share of
the bill:

```
  PASS  cannot-write-even-when-asked  21.9s · 11810 tok · 4 it · glob, list_dir, outline, read_file · $0.0044
SUITE GREEN · 6/6 passed · 33937 tokens · $0.0127 · 2 case(s) cost nothing
dearest case: cannot-write-even-when-asked · $0.0044 · 35% of what the priced cases cost
```

That was a local model given a price in `$YANTRA_PRICES`. Without one,
a local run shows no dollars at all
([notes/82](notes/82-what-each-line-cost.md)).
`--against FILE` says what moved,
and changes **no verdict and no exit code**: a run that got worse and is
still green is still green. Cases are compared as counts (`7/10 → 6/10`,
never percentages), a case present in only one run shows as `added`/`gone`
rather than being intersected away, and comparing two different models is
the point rather than an error.
([notes/42](notes/42-two-runs-of-the-same-suite.md))

Name `--against` more than once and the runs line up as a table instead
— one column per report, this run last — which is the shape of "which of
these three models should this package run on?"
([notes/49](notes/49-three-runs-side-by-side.md)). Each column's totals
(passed, tokens, and dollars if any run cost money) sit under it as
footer rows. `--sort disagree` moves the cases the runs split on to the
top, `--sort red` the ones with the most failures, and `--sort id` sorts
by name. No sort ever hides a row. A table too wide for your terminal is
split into blocks with the case names repeated, instead of being wrapped
into nonsense ([notes/83](notes/83-a-table-that-fits.md)).

You do not need a new run to look at old ones. `--reports` reads report
files and nothing else: no package, no model, no key.

```bash
uv run yantra --reports runs/qwen.json runs/gemma.json          # what moved
uv run yantra --reports runs/qwen-*.json --pool                  # add them up
```

`--pool` adds each case's passes and attempts across every file you
name, and says whether the total **holds** the case's claim, falls
**below** it, or is still **unsettled** (the range of likely pass rates
covers the claim, so more runs would help). Runs on a different model or
package version are kept in separate pools, because they are not
measuring the same thing. Each report also records the prices it was
charged at, so when you compare two paid runs you can tell whether the
agent got cheaper or the vendor did
([notes/62](notes/62-the-reports-you-already-have.md)).

On a paid model the pool also shows what each case cost **per run**,
in the oldest report against the newest, and names the case whose cost
grew fastest. `--pool-json runs/pool.json` does the same pooling and
saves the numbers to a file, so a chart or a weekly job can read them
([notes/66](notes/66-what-each-case-cost.md)).

If you edit your agent and forget to bump its version, the pool notices
anyway. Every report records a fingerprint of the package's files, and
two different packages under one version are pooled separately, with a
line telling you to bump the version
([notes/68](notes/68-the-version-nobody-bumped.md)). The same goes for a
case you edit (its runs before and after are pooled separately) and for
a local model you re-pull with `ollama pull`, which can put new weights
behind the same tag ([notes/72](notes/72-what-changed-under-a-name.md)).
On a cloud model, it tracks the dated snapshot the provider says
answered, so an alias that moves to a new release is noticed too
([notes/75](notes/75-what-answered.md)).

Beside the dollars it shows **tokens per run**, oldest against newest.
Prices change and token counts do not, so if a case's cost went up but
its tokens stayed the same, the vendor raised the price; if the tokens
went up, the agent is doing more work. On a local model there are no
dollars at all, so this is the line that tells you a case got heavier
([notes/67](notes/67-the-agent-or-the-vendor.md)).

And then the file answers the question you actually asked first — *what
broke, and is it fixed?* `--failed FILE` runs the cases that report
recorded as red, and with no `FILE` it uses the one `--against` names, so
the fix-and-check loop is one line:

```bash
uv run yantra --agent . --eval --failed --against runs/red.json
```

On the example package that is 8,423 tokens instead of 47,236 to re-check
a one-line edit. It is a subset like any other — `SUBSET GREEN` means the
broken things are not broken now, not that the package is green. A report
with nothing red in it exits 0 without resolving a provider; a red id the
suite no longer has is named rather than dropped.
([notes/46](notes/46-the-cases-that-were-red.md))

### Three rules that hold the gate up

* Cases are graded against **the package itself** — its prompt, skills,
  package tools and admission policy, built the way a real session builds
  them. The verdict is about the agent you would actually run.
* A case **may not replace the system prompt**. `system` is refused as a
  key, because that would grade some other agent.
* A suite **never asks and cannot open its own gate**. Read-only tools
  auto-approve, everything else is refused until *you* pass `--yolo`, and
  the package's own `permissions.mode` is ignored so an author cannot ship
  their way past it.

Graders resolve while the file is read, never mid-run — a typo that costs
six cases' worth of tokens to discover is a bug in the gate, not in your
package. The package's declared MCP servers are started too, so
`has_tools = ["mcp__docs__*"]` grades something real; an unreachable
declared server exits 2 rather than grading a smaller agent than the one
that ships, and `--no-mcp` skips them for offline CI and says loudly that
it did. ([notes/33](notes/33-evals-as-a-gate.md),
[notes/35](notes/35-roster-and-pass-rates.md),
[notes/41](notes/41-a-gate-you-can-point.md))

### Every real failure leaves a fossil

The case you most want is the one a real turn just failed at. Record the
session, then ask for the case it should have left:

```bash
uv run yantra --provider local --trace runs/today.jsonl "outline notes/30 at depth 1"
uv run yantra --fossil 7aa97a4c --trace runs/today.jsonl >> evals/cases.toml
```

```toml
[[case]]
id = "trace-7aa97a4c"
description = "recorded 2026-09-18T18:36:42Z on ollama/qwen3.8:27b; ended end_turn"
user_message = "outline notes/30-skills.md at depth 1"
required_tools = ["read_file"]
max_tokens = 13140
```

A recording keeps the **shape** of a turn — the task, which tools ran,
what it cost — and never the contents: not tool arguments, not results,
not the answer. A trajectory holds whatever the agent *read*, and a file
of those is a thing you would have to think about before sharing.
`--trace-full` adds them when you need them, and every line says which
level wrote it. The shape is also all a case wants: assert on the
contents of a file and your case goes red the day somebody edits it.
([notes/57](notes/57-a-turn-written-down.md))

The task is kept at every level, though, and people paste things into
prompts. And a full recording keeps whatever the agent read, which might
be a config file with a key in it. To keep those out of the file:

```bash
uv run yantra --trace runs/today.jsonl --trace-full --trace-redact email --trace-redact token
```

Every email address and every recognisable API key or token becomes
`[redacted]` before anything is written, so the real value never
reaches the disk. You can also pass your own pattern, for example
`--trace-redact 'ACME-\d+'` for ticket numbers. Tool names, ids and
counts are never touched, so `--turns` and `--fossil` still work on the
file ([notes/79](notes/79-scrubbed-before-it-is-written.md)).

A pattern cannot find a person's name: "Ana Lima" looks like any other
two words. If you know whose names might turn up (your customers, your
staff), put them in a file, one per line, and pass it along:

```bash
uv run yantra --trace runs/today.jsonl --trace-full --trace-redact-words customers.txt
```

Each name is replaced wherever it appears as a whole word, in any
capitalisation. A name that is not on the list gets through, so the list
is only as good as you keep it. The banner and the page show how many
names are on it, never the names themselves
([notes/86](notes/86-names-on-a-list.md)).

For the names nobody put on a list, a model on your own machine can read
each turn before it is saved:

```bash
uv run yantra --trace runs/today.jsonl --trace-full --trace-redact-reader qwen3.8:latest
```

Every name it finds is hidden too, and each word of a name, so "Dmitri"
goes along with "Dmitri Nkosi". It only ever adds to your list. If the
model fails, that turn is saved with its contents left out rather than
saved unscrubbed. It must be a local Ollama model: the point is that the
text never leaves your machine. Each saved turn costs one extra model
call, a few seconds on `qwen3.8:latest`. Before trusting it with your
data, measure it on your data: label a few dozen of your own records and
run `examples/name_recall_trial.py --cases yours.jsonl`
([notes/89](notes/89-names-nobody-listed.md)).

`--trace` works with `--web` too, so turns you run in the browser are
recorded the same way. When the agent hands work to a sub-agent, the
sub-agent's steps are saved inside the same line: which tools it used,
and whether each one worked. If something failed inside the sub-agent,
the turn counts as failed even if the main agent carried on and
answered anyway ([notes/63](notes/63-the-whole-turn-written-down.md)).
If the permission gate stopped one of the sub-agent's calls, the line
says so and gives the reason, so you can tell "it was not allowed" from
"it tried and failed".

`--trace` works with `--eval` as well. Every run of every case is
recorded, and when a case fails, the line under it shows the id of the
turn that failed:

```
  FAIL  outlines-a-one-word-lookup  ✗✗ 0/2 runs · …
        required tool not used: outline (2 of 2 runs)
        turns: 08ad4e35 cac47280
```

Pass one of those ids to `--fossil` to see exactly what the agent did
instead. The report file keeps the same ids, so you can still find the
turn weeks later ([notes/65](notes/65-the-turn-behind-the-red-line.md)).

To find an id without opening the file, list the recording:

```bash
uv run yantra --turns --trace runs/today.jsonl          # every turn
uv run yantra --turns failed --trace runs/today.jsonl   # only flagged ones
```

A turn is flagged when it ended badly, when a tool or sub-agent failed,
or, for turns a suite recorded, when the case's own grader said no. Each
flag says why. It is a list to choose from, not a verdict: a turn with
no flag can still have given a wrong answer
([notes/70](notes/70-a-list-to-choose-from.md)).

When you read a turn and decide for yourself, record it so the next
person who lists the file sees it:

```bash
uv run yantra --mark 3f9c21ab bad --why "cited a file it never opened" --trace runs/today.jsonl
```

A turn you mark bad is flagged with your reason. A turn you mark good is
not flagged, even if one of its tools failed along the way. And
`--fossil` puts your reason into the case it prints
([notes/74](notes/74-a-verdict-you-write-down.md)).

Changed your mind, or marked the wrong turn? Take it back:

```bash
uv run yantra --mark 3f9c21ab clear --trace runs/today.jsonl
```

If a suite recorded that turn, its grader's verdict comes back. And if
you are working in the browser (`--web --trace FILE`), you don't need
the terminal at all: every recorded turn ends with **good** and **bad**
buttons, and **take it back** once you've used one
([notes/77](notes/77-a-mark-taken-back.md)). Reloaded the page, or
want to judge yesterday's turns? Click the **rec** chip in the header.
It lists every turn in the file, newest first, with the same buttons
([notes/81](notes/81-the-turns-the-page-never-saw.md)). If you recorded
with `--trace-full`, each turn shows what the agent answered, so you can
see what you are marking ([notes/90](notes/90-what-it-said.md)).

A trace file only grows, and `--repeat 10` makes it grow quickly. When
you want to trim it, say how many days to keep:

```bash
uv run yantra --trace runs/today.jsonl --trace-prune 30
```

That removes turns older than 30 days and nothing else. Recording never
deletes anything on its own. Remember that a report may still name a
turn you have pruned; `--fossil` will then tell you it is gone
([notes/67](notes/67-the-agent-or-the-vendor.md)).

### Embedding a package instead of running it

```python
from yantra import load_package, yolo

spec = load_package("./researcher")
agent = spec.build(permissions=yolo)          # or spec.build_async(...)
reply = agent.run("what changed in notes/30?")
print(reply.message.text())
```

`AgentSpec` owns the assembly order — admission policy before any tool is
registered, prompt layers before `env_context` appends to them, skills
before the tool catalog, declared children wired after the agent exists.

---

# Act IV — money, and the manners around it

## 14 · A ceiling in the unit of the bill

`max_iterations` caps how many round trips a turn may take. That is a
*patience* limit, not a spending limit: twenty-five cheap iterations and
twenty-five expensive ones are the same number and differ by two orders of
magnitude on the invoice. So a turn may also carry a ceiling in dollars:

```toml
[budget]
max_usd_per_turn = 0.50
```

```bash
uv run yantra --agent ./researcher --max-usd 0.50 "summarise the notes directory"
```

The meter is read **between iterations**, so it is a stop and not a cap.
The only way to learn what a model call cost is to make it, and the
overshoot is bounded by exactly one call:

```
── turn ended: over_budget -- spent ~$0.0785 of the $0.02 ceiling for this turn
   (after 3 iteration(s))
```

The rules worth knowing before you rely on it:

* **Per turn, not per session.** One turn is one thing the agent was asked
  to do, and it is the only unit a package author can honestly estimate.
  (dvara adds the per-*day* half in Act VI, without building a second
  meter.)
* **A final answer is never discarded.** Crossing the line on the reply
  itself gets you the reply; the money is spent either way. Only further
  *tool calls* are refused.
* **Sub-agents share the meter.** A child charges its parent's ceiling
  rather than getting a fresh one, or delegating would be the cheap way
  around it.
* **Local models bill nothing**, so the ceiling is inert and says so on
  screen. Give your own Ollama tag a price in `$YANTRA_PRICES` and it
  becomes real — which is how you rehearse a ceiling without pointing it
  at an account with a card behind it. Once you price it, the cost line
  and eval reports use that price too, so every number agrees
  ([notes/64](notes/64-a-price-for-the-free-road.md)).
* **A metered model nobody can price refuses to carry a ceiling**, before
  a token is spent, rather than quietly metering `$0.00`.
* **`--max-usd` overrides the package, up or down.** Unlike `tools.deny`,
  which the command line cannot lift. A restriction you cannot lift is a
  security control; a number you can lift is a guard rail.

Costs come from a built-in list-price table with per-model session buckets,
so a mid-session model switch prices correctly. An unknown slug shows **no
figure** — never `$0`. Override or extend it:

```json
{"my-model": {"input": 3.0, "output": 15.0},
 "vendor/slug*": {"input": 1.0, "output": 2.0, "cached_read": 0.1}}
```

([notes/21](notes/21-cost-accounting.md), [notes/34](notes/34-budgets.md))

## 15 · The warning before the stop

You get one heads-up, priced from the request the agent is *about to
send* rather than from what it has already spent:

```
· budget: the next call carries ~11,189 tokens of context, about $0.0336 before
the reply -- and ~$0.0003 is left of the $0.02 ceiling for this turn
```

The percentage version of this does not work, and
[notes/36](notes/36-a-warning-before-the-stop.md) measures why: a tool
result lands in the context and the next call costs four times the last
one, so a turn goes from 58% of its ceiling to 145% in a single step
without ever being seen inside the warning band.

The forecast is an estimate on purpose. The **stop** is only ever made on
money actually billed; a warning that is wrong costs a line of text.

After the first call, the estimate also includes the reply. Nobody knows
how long the next reply will be, but the turn has already seen some, so
it assumes the next one could be as long as the longest so far. Models
that "think" at length before every call are the ones this helps
([notes/69](notes/69-a-reply-the-turn-has-seen-before.md)). On the first
call of a turn, before there is a reply to go on, it uses the longest
reply from your previous turn ([notes/73](notes/73-the-turn-before.md)),
which is remembered between runs too.

If you would rather have an answer cut short than go over the ceiling at
all, add `--budget-cap-reply`: each reply is limited to what is left of
the money, and a reply that hits that limit ends the turn with what it
wrote so far. It is off by default, because nobody likes a half answer
by surprise ([notes/76](notes/76-remembered-and-capped.md)).

Two readers of the same meter want opposite things
([notes/43](notes/43-a-bar-and-a-deadline.md)):

* **The person** wants the numbers. The browser header draws a budget bar
  beside the context-pressure one — same widget, same thresholds — showing
  `$0.07 left` rather than what has been spent, and moving mid-turn rather
  than at the end. An inert ceiling shows empty and `free`, because a full
  bar that can never move looks like protection.
* **The model** must not have them. `--budget-notice` tells the agent, once,
  at the same moment — but it is told the **deadline**, never the figures.
  A model handed a number to optimise starts trimming its answer or
  budgeting its own calls, and neither is the work. It is off by default
  and it is the operator's call rather than the author's, because it
  changes how the model behaves. The notice is *sent*, never written to
  history: it is true of one turn, and history gets replayed.

## 16 · A gate that can wait, and one that can say why

Everything above assumed the person approving is at this keyboard. Three
features take that assumption apart, and together they are what makes
dvara possible.

**A gate may be async.** It suspends only its own conversation; the other
three keep running.

```python
async def ask_the_owner(request):                 # a gate that WAITS
    answer = await send_to_chat(request.summary)  # minutes, if they are out
    if not answer.approved:
        request.reason = "your owner refused this: bash is off in chat."
    return answer.approved

agent = AsyncAgent(provider, model="...", tools=default_registry(),
                   permissions=ask_the_owner)
```

The synchronous `Agent` **refuses** an async gate outright rather than
treating the coroutine it gets back as a truthy yes.

**A gate may write a reason**, and the model reads that instead of
`Permission denied by user.` — a sentence that is only true when there was
a user. A model told nothing is attached goes looking for a read-only
route; a model told a person said no proposes a different approach instead
of apologising to an empty room.
([notes/37](notes/37-a-gate-that-can-wait.md))

**A clock does not get to decide what your silence meant.** So
`on_timeout` has no default:

```python
from yantra import with_deadline

gate = with_deadline(ask_the_owner, 30, on_timeout="deny")   # a deploy
gate = with_deadline(ask_the_owner, 30, on_timeout="allow")  # a nightly batch
gate = with_deadline(ask_the_owner, 30)                      # TypeError
```

On expiry the pending question is **cancelled**, so a chat window can
withdraw it instead of offering Approve for a call that can no longer
happen. A turn cancelled from outside still raises rather than becoming a
denial: nobody said no. And a deadline binds only a gate that *suspends* —
a plain function has already answered by the time a clock could start.

The browser UI has a flag for this. If you start a turn and then walk
away, you may not want the agent waiting forever for you to click
Approve:

```bash
uv run yantra --web --wait-budget 60 --on-timeout deny
```

Each turn may spend sixty seconds **in total** waiting on approval
prompts, and the prompt shows a countdown. When the time is up, the
prompt disappears and the agent is told nobody answered. It is not told
you said no. Reads never need approval, so they carry on. `--on-timeout`
is required: only you know whether silence should mean no or go ahead.
The terminal has no such flag, because there the agent simply waits
until you type ([notes/78](notes/78-a-clock-on-the-page.md)).

There is a third choice, for when you would rather come back to it:

```bash
uv run yantra --web --wait-budget 60 --on-timeout hold
```

When the time is up, the turn **stops** instead of carrying on without
you. The questions stay in the conversation, with how long ago the turn
stopped, and anything you already approved has run. Come back, approve
or deny each one, click **carry on**, and the agent picks up where it
left off. Or just type a new message, and the waiting questions are set
aside. Saving the session keeps a held turn, so you can even answer it after
restarting the server ([notes/88](notes/88-not-yet.md)).

The agent is told how much time is left, too. After a prompt uses some
of it, the agent sees a line like "3 of this turn's 8 seconds for
waiting on approval are left", so it can ask for the one thing it most
needs rather than finding out when it is refused
([notes/80](notes/80-the-time-left-told.md)). A helper agent it starts
(a sub-agent) is told the same thing, and uses up the same time: you
are the one waiting either way, so starting a helper does not reset the
clock ([notes/91](notes/91-one-clock-for-the-child.md)).

**Every refusal carries a machine token beside the sentence**, so a host
can branch without matching on English:

```python
for event in agent.run_streaming("tidy up the logs"):
    if isinstance(event, ToolExecuted) and event.refusal is not None:
        metrics.increment(f"refused.{event.refusal}")   # timeout / user / ...
```

`user`, `unattended`, `timeout`, `policy`, `unspecified` ship here; the
field is a plain string, so a host names its own. The token never reaches
the model. ([notes/39](notes/39-a-clock-and-a-word.md))

Two demos worth running, because reading about a suspended gate is much
less convincing than watching one:

```bash
uv run python examples/async_gate_demo.py      # a gate waits for a person while
                                               # a second conversation finishes
uv run python examples/gate_deadline_demo.py   # same question twice: deny, then
                                               # allow -- one word, opposite ends
```

---

# Act V — surfaces and the boring reliability

## 17 · The browser UI

```bash
uv sync --extra web
uv run yantra --web                        # http://127.0.0.1:8321
uv run yantra --provider ollama --web      # local model + browser UI
```

It is the **same loop**, not a reimplementation: each turn runs on a
worker thread pulling `run_streaming()`, and a queue bridge carries human
questions — permission prompts and `ask_user` — to the browser over one
websocket.

What is in it: streamed replies and thinking rendered as markdown by a
~150-line escape-first renderer (no library), tool cards, permission
prompts with approve/deny/**edit**, image attachments, save/load/compact
and model switches, a permission-mode chip (ask ⇄ yolo, switchable
mid-turn), a tools panel with every registered tool switchable off and on
mid-run, an MCP servers section above it with health dots and add-by-form
or paste-JSON, a live context-pressure meter (amber at 60%, red at 80% —
where auto-compaction starts caring), the budget bar beside it, and a
■ Stop button that lands mid-sentence rather than between tool calls.

One token-driven design system across the whole surface, light and dark
from your OS or from the appearance button, in-page dialogs rather than
the browser's own, and a layout that reflows to one column on a phone.
([notes/22](notes/22-web-ui.md))

## 18 · One-command starts, and a container

```bash
./start.sh                    # menu — pick by number
./start.sh local              # free & private: Ollama, no key needed
./start.sh cloud              # uses whichever API key your .env has
./start.sh web                # chat in your browser (foreground)
./start.sh local-web          # Ollama + browser UI

./start.sh web-start          # browser UI in the BACKGROUND
./start.sh web-stop           #   plus web-stop / web-status /
./start.sh web-status         #   web-restart / web-logs
./start.sh container start    # podman/docker: build + run + health check
```

Everything after the preset passes straight through: `./start.sh local
--yolo`, `./start.sh web-start --port 9000`.

The container is handy for keeping the agent's hands off your real
filesystem, or for putting it on an always-on box. The image carries code
only; keys arrive at run time.

```bash
podman build --format docker -t localhost/yantra-web .

# cloud road: mount .env read-only
podman run -d --name yantra-web --userns=keep-id -p 8400:8321 \
    -v ./.env:/app/.env:ro localhost/yantra-web

# local road: reach Ollama on the HOST
podman run -d --name yantra-local -p 8401:8321 \
    -e OLLAMA_BASE_URL=http://host.containers.internal:11434/v1 \
    -e OLLAMA_MODEL=qwen3.8 \
    localhost/yantra-web --web --host 0.0.0.0 --provider ollama
```

Port 8321 is fixed inside; publish it wherever you like. The tools run
*in the container*, so mount a workspace if you want it to touch your
files. Sessions live in `/app/.yantra/` and vanish with the container
unless you mount a volume. Docker users swap `podman` → `docker` and
`host.containers.internal` → `host.docker.internal`.

## 19 · The things that keep a long session alive

* **Sessions.** `/save` and `--resume` persist to
  `.yantra/session.sqlite3` as append-only versions. A checkpoint holds
  two different things — the conversation (`history`, `total_usage`) and
  the agent's identity (`provider`, `model`, `system`, `max_iterations`).
  `/load` at a keyboard wants both; a host that rebuilds its agent every
  turn from a package on disk wants only the first:

  ```python
  apply_payload(agent, payload, history_only=True)
  ```

  That one argument is what lets a prompt you fixed this morning take
  effect this afternoon. ([notes/38](notes/38-giving-it-back.md))

* **Compaction.** Mask before summarize: old tool-result *output* is
  elided first (reversible, the calls stay verbatim), and only if the
  context is still in the red zone does a model summarize the middle —
  goal message intact, every tool call accounted for. Auto-fires at 80%
  of the window; `/compact` forces it.

* **Retry the opening, never the stream.** 429s, 5xx and connection
  errors back off with jitter up to hard budgets. A 200 stream that dies
  *before* its first event is re-opened under the same budgets. Once one
  event has reached the caller there is no safe replay.
  ([notes/07](notes/07-reliability-and-scale.md))

* **Fallback across providers.** Retry fixes *time* problems; fallback
  fixes *place* problems. Opening-only, and closing one closes every
  provider it holds — including the venue it never fell back to.

* **Sandboxing.** `--sandbox` picks bubblewrap when usable — network off
  by default, host filesystem invisible outside read-only system paths,
  env scrubbed of secrets, timed-out process trees fully reaped — and
  falls back to a scrubbed-env subprocess otherwise. Gates *decide*;
  sandboxes *contain*. They are different jobs and neither substitutes
  for the other. ([notes/16](notes/16-sandboxing.md))

* **Giving connections back.** A provider owns two HTTP pools. If your
  process does not exit, hand them back:

  ```python
  provider.close()                 # the sync pool
  await provider.aclose()          # both (an AsyncClient needs a live loop)

  with get_provider("anthropic", load_settings("anthropic")) as provider:
      ...                          # async with ... also works
  ```

* **Builder mode**, for the case where the agent writes a project rather
  than answering about one: spec → files → acceptance checks re-run
  **independently**, never trusting the model's word. A red verification
  feeds back into the same conversation for a bounded repair round, then
  BUILD GREEN/RED and an exit code CI can read. Test files are checksummed,
  so a repair that edits its own tests fails the build even if the suite
  then passes. ([notes/18](notes/18-builder-mode.md))

  ```bash
  uv run yantra --build "CLI that converts between temperature units"
  ```

## 20 · Many conversations at once

```python
import asyncio
from yantra import get_provider, load_settings
from yantra.async_agent import AsyncAgent

async def main():
    provider = get_provider("anthropic", load_settings("anthropic"))
    agents = [AsyncAgent(provider, model="claude-sonnet-5") for _ in range(4)]
    replies = await asyncio.gather(*(a.run(q) for a, q in zip(agents, QUESTIONS)))

asyncio.run(main())
```

Async here is **cores and skins, not a rewrite**. The protocol logic lives
in incremental sync classes — SSE framing, stream routers, response
folding, compaction arithmetic — and `acollect` / `astream` / `AsyncAgent`
are thin awaited skins over the same rules. Zero duplicated wire logic.
Tools keep one blocking implementation and the async loop pushes them to
threads, instead of pretending syscalls are non-blocking.

```bash
uv run python examples/async_demo.py    # 4 conversations, sequential vs concurrent
```

([notes/11](notes/11-async.md))

---

# Act VI — dvara, the door your agents live behind

Yantra ends at a keyboard. You run `yantra --agent ./researcher`, you talk
to it, you close the laptop and it is gone — and the whole framework
quietly assumes exactly that: one person, present and trusted, one
conversation, one process that dies when they walk away.

dvara is what happens when you take those assumptions away one at a time.

```
                 ┌──────────────┐
  Telegram ──┐   │              │   ┌─ greeter/      (agent.toml)
  HTTP ──────┼──▶│    dvara     │──▶├─ researcher/   (agent.toml)
  your CLI ──┘   │              │   └─ ops/          (agent.toml)
                 └──────┬───────┘
                        │  actors.toml · sessions · runs · workspaces
```

## 21 · Setup and a first turn

```bash
cd ../dvara
uv sync                          # dvara + Yantra from the checkout next door
cp .env.example .env
```

An owner needs two things: a directory of agent packages, and a file of
people.

```bash
# what this service can offer
dvara --root examples/agents --actors examples/actors.toml agents

# one turn, in process -- no HTTP, no bot token
dvara --root examples/agents --actors examples/actors.toml \
      --provider ollama --model qwen3.8-64k:latest \
      say --actor guest --agent greeter "who are you, in one sentence?"

# what it has been doing
dvara --root examples/agents --actors examples/actors.toml runs
```

`--root`, `--actors` and `--state` also read `$DVARA_ROOT`,
`$DVARA_ACTORS` and `$DVARA_STATE`, so the rest of this act drops them.

`dvara say` exists for a reason worth stating: it drives the service
**directly, in process**, with no HTTP and no channel. It is how you
rehearse a package against a local model before any bot token exists, and
it is how every receipt in the dvara notes was produced.

## 22 · The three nouns

A terminal never had to answer these. A service answers all three before
a single token is spent.

**Actor** — who is talking. **Agent** — which package. **Thread** — which
conversation.

```
session key = (actor, agent, thread)      →      mahen/greeter/chat-42
```

Not `(actor, thread)`. If two people use the same chat id with two
different agents, `(actor, thread)` gives them one shared history and the
agents start reading each other's mail. Fixing that later is a migration;
getting it right on day one is a tuple.

Each part is percent-escaped before it is joined, and that is not
decoration: join them raw and an actor named `a/b` in thread `c` produces
the same key as actor `a` in thread `b/c` — one person's conversation
opening inside another's because of how they happened to be named. It
stays readable rather than hashed on purpose, so
`select distinct session_id from checkpoints` answers your question
without a decoder ring. The same escaping gives each conversation its own
scratch directory, and the dot segments (`..`) are neutralised explicitly,
because `quote()` leaves a dot alone and a scratch directory that becomes
its own parent is a bad afternoon.

## 23 · An actor is assigned, never asserted

This is the sentence the whole security posture hangs on. Nothing arriving
from outside gets to say who it is. The owner writes a file — and it holds
no secrets, so it is a thing you commit:

```toml
[actor.owner]
# no keys at all: every agent in the roster, no ceilings, and this is
# somebody the service may wake up to approve a tool call

[actor.guest]
agents           = ["greeter"]     # a COMPLETE whitelist; omit for all
max_usd_per_turn = 0.02
max_usd_per_day  = 0.10
permissions      = "read_only"     # served, but never asked to approve
```

An identity with no name here is not served, and learns nothing about who
else exists:

```
$ dvara say --actor stranger --agent greeter "hello"
you are not on this service's list of people
[refused]
```

`agents` follows the convention `tools.allow` already set in Yantra:
absent means everything, a list is a complete whitelist, and an empty list
is an error rather than a silent "this person may reach nothing" — an
empty allowlist is far likelier to be a typo than a decision. Unknown keys
are errors too:

```
error: ~/dvara/actors.toml: [actor.guest] has unknown key(s) max_usd_per_dayz;
known: agents, channel, max_usd_per_day, max_usd_per_turn, permissions,
receipt
```

A misspelled ceiling that quietly means "no ceiling" is exactly the
failure a ceiling exists to prevent.

### An actor is a person, not a seat

The same file says where a person can be *reached*, which is the half
that lets a bot be a client of this rather than a second roster:

```toml
[[actor.owner.channel]]
kind = "telegram"
id   = 8675309       # a number is fine; stored and compared as text
```

A channel adapter does not carry its own `{chat_id: actor}` table — it
hands over the identity it has, and this file says whose it is. That is
the same sentence as the section title, extended one step: half an
assignment living in a bot's environment is half an assignment nobody
diffs.

The argument for one actor instead of two is not the tidiness. Write the
same person down twice — `mahen` and `mahen_tg` — and their
`max_usd_per_day = 2.00` is now **$4.00**, because the allowance is a sum
over runs keyed by actor id. A ceiling that doubles when you install a
bot is not a ceiling. The pending-questions queue and the agent whitelist
split the same way, and neither is as bad as that.

So: one person, one allowance, one queue, several doors — and a question
raised anywhere is delivered to every channel they hold, answerable from
any of them. dvara checks that a `(kind, id)` pair belongs to at most one
actor, and otherwise never branches on which channel `kind` names.

```bash
dvara say --as telegram:8675309 --agent greeter "who are you?"
```

One thing joining the actor *creates*, rather than fixes: two channels
whose thread ids collide would now share a conversation, where before it
was the differing actor ids keeping them apart — by accident. A turn that
arrives through a channel is keyed under `kind:thread`, so they stay two
conversations, and keys made by naming an actor directly are untouched.

### What follows the answer

```toml
receipt = "cost"        # $0.0013 under each answer
receipt = "remaining"   # $0.0489 left today
```

Absent — the default — is silence. Two words rather than one boolean,
because **two readers want two different numbers.** An owner is watching
a bill accumulate and wants what the turn cost. A guest has no bill, only
an allowance, and the one figure they can act on is what is *left* of it:
told `$0.0013`, they would have to know their ceiling and their spend and
subtract. That is [note 43](notes/43-a-bar-and-a-deadline.md)'s "what is
left, not what is spent", arriving one layer up.

Two rules fall out. Under a provider that bills nothing, both render
nothing — half this book's readers run Ollama, where a receipt is a meter
that cannot move. And a hosted model with no list price renders
`unpriced` rather than `$0.00`, because an owner who asked for a receipt
asked to watch a bill, and a zero there is a guess wearing a number's
clothes.

The line is a separate field, never appended to the reply text: `run.reply`
is the archive of what the agent *said*, and a channel gets to pick how a
footer looks in its own medium.

## 24 · Agents are named, never pathed

Act III said it in passing and this is where it is load-bearing: **loading
a package runs its Python.** That is fine for a package you chose. It stops
being fine the instant a package path could come from a message.

So dvara resolves agents by NAME, from one directory the owner controls,
and a name has to survive three checks: it matches a conservative pattern
(rejecting `..`, `/etc/passwd`, `~`, hidden directories, anything with a
newline in it), it joins to the root, and — after `resolve()` — it is
still inside that root. The third check is the one a pattern cannot make:
a symlink is a path that lies about where it goes.

The property that makes a roster safe to *list* at all belongs to Yantra:
`load_package` imports nothing. Code runs later, in `spec.build_async()`,
at the moment a request actually reached an agent. There is a test with a
package whose `tools/boom.py` raises on import; listing the roster and
reading its manifest both leave it sleeping.

Two more rules from the same family:

* **dvara never parses `agent.toml`.** It calls Yantra's `load_package`.
  One parser, in the framework, with the tests.
* **Nothing here is sandboxed by pretending.** Yantra's sandbox confines
  an agent's tool *calls*; it has nothing to say about a package's
  import-time side effects, and this service does not imply otherwise.

## 25 · Two ceilings, and a refusal to build a second meter

Yantra meters one turn. A service has to answer a different question: what
may *this person* spend, today, across every turn they have had?

| | |
|---|---|
| package | `max_usd_per_turn` — what the author thinks a task costs |
| actor | `max_usd_per_turn` — what the owner lets this person spend |
| today | `max_usd_per_day` minus what they have already spent |

The design decision is a refusal. **dvara does not build a second meter.**
It takes the minimum of whichever are set, hands that one number to
Yantra's existing `Budget`, and inherits the whole apparatus — the mid-turn
stop, the warning, and the shared meter that stops a sub-agent from
clearing its parent's spend. A daily allowance is therefore enforced by a
per-turn ceiling that shrinks as the day is spent. That is a strange
sentence and a correct design: every extra meter is another place the
arithmetic can disagree with itself, and the first place it would disagree
is sub-agents.

Live, against a local `qwen3.8-64k:latest` on Ollama, priced against
itself through `$YANTRA_PRICES` so a ceiling could be rehearsed without an
account with a card behind it:

```
$ dvara say --actor guest --agent greeter --thread money "hello"
Hello, come on in.
[end_turn · $0.0468 · 76in/41out · run c4a882d657b6]

$ dvara say --actor guest --agent greeter --thread money "and again?"
Well, hello again, friend.
[end_turn · $0.0584 · 99in/47out · run fe4843a1a381]

$ dvara say --actor guest --agent greeter --thread money "one more?"
your daily allowance is spent; it comes back at 00:00 UTC on 16 Sep
[refused · run 28bfabe88231]

$ dvara say --actor owner --agent greeter --thread money "still open?"
Yes, I'm still here—what can I help you with?
[end_turn · $0.0660 · 78in/87out · run 46add8708249]
```

Two turns came to $0.1052 against a $0.10 allowance; the third never
reached the model, and the owner — who has no allowance — was unaffected.
The day boundary is the UTC calendar day rather than a rolling 24 hours,
because the person you have just cut off needs a time they can plan
around.

**The tradeoff, named.** Look again at that first turn: $0.0468 against
the guest's $0.02 per-turn ceiling. It ran anyway. A per-turn meter can
only bite *between* model calls — there is nothing to weigh before the
first one — so a single expensive call always gets through. The daily
allowance is what makes that bounded rather than infinite, and it is the
honest reason a service needs both numbers.

## 26 · Three rungs, and the tightest wins

Yantra has two permission modes. Once "ask" can actually reach a person,
two is not enough — an owner needs to say *do not wake me up for this one*
about a guest without saying it about themselves. So the ladder grows a
rung at the bottom:

```
read_only  <  ask  <  yolo
```

and three parties each name one:

| who | where | what it means |
|---|---|---|
| the package | `[permissions] mode` in `agent.toml` | what the author thinks this agent needs |
| the owner | `Policy(mode=...)`, or `--yolo` | what this machine allows at all |
| the actor | `permissions` in `actors.toml` | what this person may be asked to approve |

The composition is a minimum, not a paragraph of if-statements:

```python
mode = stricter(package, owner, actor)
```

which makes *tighten, never loosen* a property of the arithmetic rather
than a promise in a comment. Write `permissions = "yolo"` beside a guest's
name and it grants them nothing at all. An unrecognised mode ranks below
every real rung, so a typo and a mode from a future version of the format
both fail closed.

**Two kinds of silence**, and this is the part that took a rewrite:

* **An actor who names no mode has no opinion** and drops out of the
  comparison entirely — exactly how `max_usd_per_turn` composes in the
  same file. The alternative would have quietly broken `--yolo` for every
  actor nobody had edited.
* **A package that names no mode is treated as naming the tightest.** An
  author who ships code and leaves `[permissions]` out has not asked to be
  escalated for, and a service escalating on their behalf would be putting
  a stranger's tool call in front of a person on no authority at all.

## 27 · Escalation — a route, not a setting

With nobody attached, a service refuses anything that could change
something and tells the model why. That is the right default at three in
the morning and infuriating at three in the afternoon, when you are
holding your phone and would happily have said yes.

The obvious fix is obviously wrong. A gate that blocks until you answer
does not block *you* — it blocks the event loop, and one person's
unanswered question becomes an outage for everybody else. That is the seam
Yantra had to grow first (§16), and this is what is built on it.

The switch is not a setting. It is an **ask desk**: somewhere a question
can be put, and somewhere an answer can land.

```python
from dvara import AskDesk, Service

service = Service(roster=..., actors=..., state=...,
                  asks=AskDesk(timeout=120, notify=send_it_to_them))
```

With a desk, `mode = "ask"` means ask: the turn suspends — it does not
block, so every other conversation keeps running — until a person answers
or the deadline passes. With no desk it means read-only tools only, which
is exactly how the service behaved before any of this existed. **The
absence of a route is not a hang and not an approval.** It is a denial
that says nobody could be asked, which is a fact the model can act on.

**Three refusals, three sentences**, because they call for three different
next moves:

* **They said no.** Somebody was asked and answered. Do not re-run this
  call; a different approach may be worth proposing.
* **Nobody answered.** The deadline passed. This is silence, not a
  refusal — try again later.
* **Nobody could be reached.** The notifier raised: the bot is down, the
  token expired. Asking again will not help. (And a delivery that fails is
  a denial *immediately*, not after the deadline — if the question never
  left the building, the two minutes that follow are two minutes of
  nothing.)

### The terminal is a channel

There is no bot yet, and there does not need to be one. A front end
supplies a way to put the question and a way to take the answer; a
terminal has both.

```bash
dvara --root examples/agents --actors examples/actors.toml \
      --provider ollama --model qwen3.8-64k:latest \
      --ask say --actor owner --agent scribe \
      "write a two-line haiku about doors into haiku.txt"
```

Live, against a local `qwen3.8-64k:latest`. Approved:

```
scribe wants to run write_file:
  NEW FILE haiku.txt (1 lines)
approve? [y/N] [end_turn · $0.0000 · 2065in/961out · run 1bf114e57ff4]
Done — here's the two-line haiku in `haiku.txt`:

> The wooden door stands (5)
> and whispers of rooms gone by (7)
```

The same question, refused:

```
scribe wants to run write_file:
  NEW FILE hello.txt (1 lines)
approve? [y/N] [end_turn · $0.0000 · 1229in/192out · run c5f9066eadcd]
That write was refused because you (the owner) were asked and said no —
so I've left `hello.txt` unwritten and won't retry that call. If you'd
like it done a different way (different filename, different content, or
somewhere else), just let me know and I'm happy to propose that instead.
```

Two things there were the point of the sentence the gate wrote: the model
named *who* refused, and it did not retry — it offered a different
approach, which is what a model does when it knows a reachable person said
no rather than that it is shouting into an empty room.

With nobody at the keyboard and an eight-second deadline, the model can
tell silence apart from refusal:

```
The write attempt timed out — nobody confirmed it, so hello.txt doesn't
exist yet. Just let me know when you're back and I'll write it.
```

And a guest with `permissions = "read_only"` beside their name never
generates a question at all. No prompt is printed, because nobody was
asked.

### Two rules about answers

**Only the person it was put to.** A question id is unguessable, and
answering it still requires naming the actor it was put to. Either check
alone is weaker than it looks — ids travel out through a channel and can
be forwarded; an actor id is a name off a roster anyone can type. Together,
a leaked question is useless to whoever it leaked to. A wrong answer
resolves nothing; the question stays standing.

**Answers do not arrive as messages.** A turn holds its conversation's
lock while it waits, so typing "yes" into the chat queues up *behind the
very turn it was meant to release* — and sits there until the deadline
passes, at which point the turn is refused for silence and *then* your
"yes" is delivered to a model with no idea what it refers to. Answers come
through the desk, or through `POST /asks/{id}`. An approval is a decision
about a call already in flight, not a sentence for the model to read.

Questions live in memory and die with the process, on purpose: a pending
question is a promise that a turn is still standing there waiting, and no
turn survives a restart. A persisted question would outlive the only thing
that could act on it.

**Unless you tell silence to wait.** Start the service with
`--ask --on-timeout hold` and an unanswered question stops the turn
instead of refusing the call. Nothing after it runs, and the turn waits,
for up to a day, for you to come back. This is the same hold the browser
has above, and it *can* survive a restart, because Yantra saves it
inside the conversation. A service keeps a list of them:

```
$ dvara held
VamBTs5Z_X3GzDPNY6UYeQ  owner/scribe  thread cli  run efa292db101a
    call_fq5canuu  write_file: NEW FILE a.txt (1 lines)
    call_b6dmlw42  write_file: NEW FILE b.txt (1 lines)
    held 30s ago. What you approve runs against things as they are now, not as they were then.

$ dvara resume VamBTs5Z_X3GzDPNY6UYeQ --actor owner \
      --call call_fq5canuu=yes --call "call_b6dmlw42=leave b.txt alone"
```

In a chat, the reply that says the turn is waiting has two buttons under
it, **approve all** and **refuse all**, and pressing one carries the turn
on. Only the person the turn ran as can answer it. Sending a new message
instead means "never mind", and the waiting calls are set aside.

## 28 · What a service does that a session never had to

* **A fresh agent per turn**, not a pool of warm ones. Warm agents buy
  latency and cost three things: memory that grows with every actor who
  ever said hello, a spec that goes stale the moment you edit a package,
  and a crash that loses history nobody wrote down. Rebuilding is also the
  only version where a restart is a non-event.
* **One provider, held.** The opposite decision for the opposite reason:
  constructing a provider opens two httpx connection pools, so resolving
  one per turn would leak pools for as long as the process lived.
* **History is restored; identity is rebuilt.** This is `history_only=True`
  from §19 doing its job. What comes out of the store is the conversation.
  Everything else comes out of the package — so a prompt you fixed this
  morning takes effect this afternoon.
* **One lock per conversation.** Two messages in one thread serialize.
  Interleaving them would put two user messages into one history with a
  single assistant reply between them.
* **An agent writes in a workspace, not in its package.** The package
  directory is read-only input; each conversation gets its own scratch
  directory under the service's state. An agent that edits the folder you
  review and commit is an agent whose package has stopped being
  reviewable — and being reviewable is the one property the whole format
  exists to have.

## 29 · Every turn leaves a row

Including the ones that failed. A script forgets; a service that forgets
cannot answer the owner's first question.

```
$ dvara runs
2026-09-15 17:46  guest/greeter  end_turn          $0.0584  'and again?'
2026-09-15 17:46  guest/greeter  end_turn          $0.0468  'hello'
2026-09-18 01:19  owner/scribe   end_turn          $0.0007  'write a haiku…'
                  write_file -> write_file(refused)  [answered from terminal]
```

That second line is what the turn **did**, and it is a different fact
from how the turn ended. `end_turn` says it finished; the line under it
says it wrote a file, tried to write it again, and was told no by
somebody standing at a keyboard.

**A turn that crashes still pays for what it spent.** Three model calls
and then a 500 is still three model calls, and the accounting happens in
the same `finally` that saves the session. Leave it out and a crash erases
its own cost, so the daily allowance never sees it and somebody with a $2
day can spend the afternoon in failing turns. The other direction matters
too: a turn that never reached a model costs `$0.0000`, not "unpriced" —
"unpriced" is the store admitting a doubt, and about a turn that never
happened there is no doubt to admit.

Three later features are queries over this one table, which is why it
exists in the first slice rather than the fourth. Money over time is a
`SUM`. An audit is a `SELECT`. And a failed run is a *trace* — which is
the shape Yantra's eval machinery turns into a case in the package that
produced it. That is the loop the whole arc was built to close: the agent
fails in production, the failure becomes a case, the package's own gate
stops it coming back.

**And the case can assert how the turn went**, because the row remembers.
`required_tools` is filled from the calls that actually ran, which is
`case_from_trace`'s own parameter finally having a source — so an agent
that "fixes" a bad turn by doing nothing at all no longer passes:

```
  FAIL  trace-4e183287  24.8s · 2245 tok · 2 it · no tools
        required tool not used: write_file
```

Two lines are drawn here and both are worth carrying away. **Names, never
arguments**: `write_file` is recorded and the path it was given is not,
because the assertions take names, a row that grows with an argument is a
row that can hold a file, and `dvara case` prints into a file somebody
commits. And **a trajectory is a description; a prohibition is a
judgement** — the service watched the turn happen, so it will say what
was called, but whether the fixed agent should stop *trying* something
the gate refused is a line the owner writes. The refused calls are
printed beside the block so they know what to write.

## 30 · The door itself

```
POST /message      {actor, agent, thread, text}  -> {text, ok, run_id, receipt, ...}
GET  /agents                                     -> {agents: [...]}
GET  /health
GET  /asks?actor=                                -> {asks: [{id, tool, summary, ...}]}
POST /asks/{id}    {actor, approve}              -> {answered, approved}
```

```bash
DVARA_TOKEN=$(openssl rand -hex 24) dvara serve --port 8765
```

Every request carries `Authorization: Bearer $DVARA_TOKEN`. **The token
authenticates the caller, not the person.** A caller is a channel adapter
running inside the owner's trust boundary. The `actor` field in the body
is an assertion *by a trusted caller* — which is precisely why the token
is mandatory rather than optional. A service with no token refuses to
start, binds to localhost unless told otherwise, and compares the token
in constant time.

An adapter may instead send the identity it actually has, and let the
roster map it — the form it cannot get wrong:

```
POST /message   {"channel": {"kind": "telegram", "id": 8675309}, ...}
             -> {..., "actor": "owner"}
```

Exactly one of `actor` or `channel` per request, on all three endpoints
that name a person. Both is a `400`: honouring it would mean picking a
winner, and then a bridge with a stale hard-coded actor id either quietly
overrules the roster or quietly does not.

A refusal comes back as a `200` with a reason, not a `500`. A channel
adapter has to be able to deliver "you are not on this list" as a message;
an exception is a reply that silently never arrives. On the asks
endpoints, `approve` must be a JSON boolean — anything truthy would make
the string `"no"` an approval, which is the exact shape of the bug that
ends with a command nobody agreed to. A question put to somebody else is a
`403`; one already answered or expired is a `404`.

## 31 · A bot at the door

Everything up to here was built so that this part could be small. The
roster already says who a chat id is; the desk already knows where a
question goes; the actors file already decides what goes under an answer.
What a chat app adds is the medium, and the medium has three opinions.

```bash
export TELEGRAM_TOKEN=...          # BotFather gives you one per bot
dvara --ask telegram --agent researcher
```

There is no `--token` flag: a credential on a command line is in your
shell history and readable in every `ps` on the box. And **one bot is one
agent** — a token is an identity with a name, a picture and an @handle, so
a second agent is a second token and a second process rather than a prefix
on every message you type.

**A reply has a bottom at 4096, and it is measured in UTF-16.** Telegram
counts a message in UTF-16 code units; Python counts a string in code
points. They agree for ASCII, which is why this bug survives every test
written by hand and appears the first time an answer has an emoji in it —
one Python character, two of Telegram's units, and a 3000-character reply
that is 4200 units and is rejected *whole*. So a long reply is **split,
never truncated**: a brief cut off at the cap still reads like a finished
answer, and the citations that would tell you otherwise are at the bottom.
Cuts land on a blank line, then a newline, then a space.

**The poll loop never awaits a turn.** A turn waiting for approval is
released by a button press, and button presses arrive through the same
long poll. Await the turn in the loop and the answer can only come down
the pipe the turn is holding shut — every escalated call waits out its
deadline and is refused for a silence that had somebody pressing the
button. Updates become tasks; the service's per-conversation lock is
already holding the line that matters.

**An approval is a button.** §27's rule — answers do not arrive as
messages — is not a limitation of the terminal, it is a property of the
lock, and a chat app has exactly one other affordance:

```
  ┌─ message to chat 8675309
  │ scribe wants to run write_file:
  │
  │ NEW FILE haiku.txt (2 lines)
  │ [approve] [refuse]
  └─
  ← pressed: y:Rbo_PR_OLYsQsVEMGMm86A
  ← the question now reads: ...— approved
```

The summary is Yantra's, built so that what the person approves is what
runs. The press arrives as a `callback_query`, which does not touch the
session lock, and the message is edited afterwards so it cannot be pressed
twice.

Two more decisions worth knowing before you point one at a real chat.
Nothing is sent with a `parse_mode`, because Markdown mode makes the
*model's own punctuation* a syntax error — one unmatched `*` and the whole
answer comes back as a 400. And somebody who is not in `actors.toml` gets
**silence**, while you get the line that says how to add them.

## 32 · Leaving it running

The difference between a service and a program you run is what happens
when nobody is watching the terminal. Three things.

**One dvara per state directory**, and a second one is refused. The
visible symptom of running two is SQLite contention — `database is
locked` — and fixing *that* is three lines and the worst available
outcome, because it silences the only signal while leaving the real
problem alone. **A SERVICE IS A PROCESS.** The lock that serializes two
messages in one conversation (§28) and the queue of questions waiting for
a person (§27) both live in memory, so two processes would both rehydrate
one checkpoint, both save, and lose a turn without anything raising at
all.

```
$ dvara --state ~/dvara/state say --actor owner --agent greeter "hello"
error: another dvara is already using ~/dvara/state (pid 3641987 running
dvara serve). A service is a PROCESS, not a directory: …
```

It is an `flock`, not a pid file, because **the kernel releases it** — a
service killed with `SIGKILL` leaves no stale lock and no "is 4032 still
the same process?" heuristic to get wrong. A refusal needs a way out, so
a bot *and* an HTTP surface is one process:

```bash
dvara serve --telegram researcher
```

And a command that only *reads* — `runs`, `case`, `agents` — claims
nothing, because looking at your own ledger while the bot answers
somebody is the most ordinary thing an owner does.

**Edit the actors file while it runs.** It is reread when it changes, so
adding the guest standing in front of you holding your bot's @handle is
one edit and no restart. What decides the feature is the failure case:
**A BAD FILE KEEPS THE LAST GOOD ONE.** An owner adding somebody at
midnight who leaves a bracket off is one typo away from a service that
refuses everybody — including themselves, including the person who would
fix it — so a file that has stopped parsing is a complaint on their
terminal and nothing more:

```
~/dvara/actors.toml: Expected ']' at the end of a table declaration
  -- keeping the roster already loaded; nothing changed for anybody
     talking right now
```

At *startup* the opposite is right, and that is what happens there: a
broken file is exit 2 and nothing serves. The difference between the two
answers is whether there is already something to lose. The parsers decide
none of it — they parse, and the host decides — which is the same
division as the ask deadline in §27.

**And a question you can walk away from.** A terminal question that times
out used to leave a thread parked in `input()`, and the interpreter joins
those at exit, so the process sat there wanting a keypress nobody had a
reason to give. The fix is not a bigger hammer on the thread; it is
`loop.add_reader`, and not using one.

## 33 · What decided this

Three questions an owner asks months later, when the thing that could
have answered them has gone.

**Which rule stopped that — and which one has never done anything?** The
asymmetry is the problem: a deny announces itself, because the model is
told and the turn changes shape. An allow is invisible *by construction*
— the call simply runs, exactly as it would have if you had been woken up
and said yes. So a policy file fills with lines you cannot tell apart by
looking:

```
$ dvara rules
policy.toml  ·  3 rule(s)  ·  calls settled over the last 30 days
      2  allow  write_file path=haiku.txt
      1  deny   write_file path=*.env|*/.ssh/*
      ·  allow  bash command=git status|git diff
```

A rule is named by what it **says** — a hash of tool, verdict and
patterns — not by where it sits, so inserting a line at the top does not
shuffle the counts. Edit a rule and it starts at zero, which is right:
you changed the standing answer.

**Where were you when you approved this?** This is the one that sent a
feature back into the framework. A gate's answer and the `ToolExecuted`
it produces had nothing joining them, so a host could only pair them by
counting — which *works*, because gates run once per call in submission
order. That is three properties of the loop that no caller was ever
promised, and a drift in any of them files one person's approval against
a different call: wrong, confident, and silent. So `PermissionRequest`
carries the `call_id` it is deciding (§16), and the two halves of a turn
meet on a string neither had to agree about:

```
write_file[rule:c0a621fc] -> write_file[rule:c0a621fc] -> read_file
```

Only the interesting ones are recorded. A call the rung simply allowed —
`read_file` above — says nothing, because "nothing in particular decided
this" nine times in ten is a field nobody reads.

**And which version of the agent was that?** Agents are rebuilt per turn
(§28), so a package edited on disk takes effect on a live conversation's
next turn. Desirable when you are fixing a prompt; alarming when a
conversation changes personality mid-sentence. The resolution is not to
choose: **the alarming part was never the change, it was that nothing
said it happened.**

```
2026-09-18 03:18  owner/scribe  end_turn  $0.0007  'write API_KEY=hunter2 into…'
                  scribe changed after this turn: 0.1.0 -> 0.2.0
```

Pinning a version per thread is the alternative, and it is refused for a
reason rather than for want of a decision: a pinned thread is a
conversation that does not get the prompt fix you made *because of it*.

## 34 · Embedding it

```python
from pathlib import Path
from dvara import ActorBook, Roster, Service

service = Service(
    roster=Roster(Path("~/agents")),            # owner-controlled, always
    actors=ActorBook.from_toml(Path("~/actors.toml")),
    state=Path("~/dvara/state"),
)
reply = await service.deliver(actor="mahen", agent="researcher",
                              thread="cli", text="what changed today?")
print(reply.text, reply.cost_usd)
```

| module | what it holds |
|---|---|
| `service.py` | `Service.deliver` — one message in, one reply out |
| `roster.py` | agents resolved by NAME from one owner-controlled root |
| `actors.py` | who is served, what they may reach, what they may spend |
| `keys.py` | the `(actor, agent, thread)` session key and its escaping |
| `money.py` | package ∧ actor ∧ what is left of today |
| `gate.py` | three rungs, and the tightest wins |
| `asks.py` | questions waiting for a person, and the deadline on them |
| `holds.py` | turns that stopped because nobody answered, kept until somebody does |
| `runs.py` | every turn that happened, what it cost, and which tools it called |
| `http.py` | the endpoints and a bearer token (`[http]` extra) |
| `telegram.py` | the long poll, the 4096-character cap and the button |
| `claim.py` | one dvara per state directory, and why |
| `cli.py` | `agents`, `say`, `runs`, `held`, `resume`, `rules`, `case`, `telegram`, `serve` |
| `errors.py` | `Refused` (answer the person) vs `ConfigProblem` (tell the owner) |

---

# Act VII — the one-hour path

A checklist, in an order where each step explains the next. Everything
here runs on the local road; swap `--provider ollama` for a key if you
have one. Times are rough.

### 0 · Five minutes — get a model answering

```bash
cd yantra && uv sync
ollama pull qwen3.8                                  # or your tag of choice
uv run yantra --provider ollama --model qwen3.8 "what files are in notes/?"
```

You have just watched steps 1–4 of the loop. Nothing else in this document
is harder than that.

### 1 · Five minutes — watch the loop, event by event

```bash
uv run python examples/agent_loop_demo.py            # press Ctrl-C mid-turn
uv run python examples/agent_loop_demo.py --deny-all # denial-as-data
```

The Ctrl-C is the interesting one: the history invariant recovers rather
than leaving a tool call without a result.

### 2 · Ten minutes — a permission gate you can feel

```bash
uv run yantra --provider ollama "write a haiku into haiku.txt"
```

Answer `e` at the prompt and change the filename. What you approve is
what runs. Then try `--yolo`, and then `/yolo` mid-session to flip it back.

### 3 · Ten minutes — run the example package

```bash
uv run yantra --agent examples/agents/researcher --provider ollama \
  "which tools is this agent allowed to use, and where did you read that?"
```

Read [examples/agents/researcher/agent.toml](examples/agents/researcher/agent.toml)
alongside the answer. It is commented as a tutorial in its own right, and
between it and the reply you will see the admission policy, the package's
own `outline` tool, and the declared `fact_checker` child all in one turn.

### 4 · Five minutes — the free half of a gate

```bash
uv run yantra --agent examples/agents/researcher --eval --case "has-no-way*"
```

No key, no provider, no tokens. Now add `write_file` to that package's
`tools.allow`, run it again, and watch it go red without a model being
involved. Put it back.

### 5 · Ten minutes — the whole gate, on your own hardware

```bash
uv run yantra --agent examples/agents/researcher --eval --provider ollama
uv run yantra --agent examples/agents/researcher --eval --provider ollama \
      --report /tmp/run-a.json
```

Change one line of the package's `prompt.md`, run it again with
`--against /tmp/run-a.json`, and read what moved.

### 6 · Five minutes — a gate that waits

```bash
uv run python examples/async_gate_demo.py
uv run python examples/gate_deadline_demo.py
```

One conversation suspends on a question while another runs to completion
in the same event loop. This is the mechanism every part of Act VI is
built on.

### 7 · Ten minutes — put an agent behind a door

```bash
cd ../dvara && uv sync
dvara --root examples/agents --actors examples/actors.toml agents

dvara --root examples/agents --actors examples/actors.toml \
      --provider ollama --model qwen3.8 \
      say --actor guest --agent greeter --thread demo "who are you, in one sentence?"

dvara --root examples/agents --actors examples/actors.toml \
      --provider ollama --model qwen3.8 \
      say --actor guest --agent greeter --thread demo "what did I just ask you?"
```

Two separate invocations, no process shared between them, and the second
one remembers. Then try three things that should fail, and read the
sentence each one gives you:

```bash
# ... --actor stranger ...            not on the list
# ... --actor guest --agent scribe "write hello into hello.txt"
#                                     refused: nobody available to ask
# ... --ask --actor owner --agent scribe "write hello into hello.txt"
#                                     a question, at your keyboard
dvara --root examples/agents --actors examples/actors.toml runs
```

### 8 · The rest of the hour — your own package

```
mine/
├── agent.toml      start with the two-line version
├── prompt.md
└── evals/cases.toml
```

Write one roster case first (`lacks_tools`, costs nothing), then one
behaviour case. If the package declares a sub-agent, add
`subagent_lacks_tools = { "*" = [...] }` — also free, and it is the only
thing watching the child's list. Run the gate. Drop the directory into a
dvara root and say something to it.

---

# Act VIII — where to read next

The notes are the argument behind every feature: what the gap was, what
was built, what was deliberately *not* built, and what is still missing.
Most carry a live receipt from a real run.

### Yantra — the harness underneath

| | |
|---|---|
| [00](notes/00-guided-tour.md) | the plain-English tour, with diagrams — start here if any of Act I felt fast |
| [01](notes/01-anatomy-of-a-request.md) [02](notes/02-wire-formats.md) [03](notes/03-sse-and-collect.md) | one request, the wire dialects, SSE parsing and the fold |
| [04](notes/04-tools.md) | tools: schemas, sandboxes, honest summaries |
| [05](notes/05-agent-loop.md) [06](notes/06-cli.md) | the loop, cancellation, the history invariant; the CLI around it |
| [07](notes/07-reliability-and-scale.md) | retry, parallel tools, persistence, compaction |
| [08](notes/08-sub-agents.md) | sub-agents: agent-as-tool |
| [09](notes/09-mcp.md) | MCP by hand, both transports, OAuth 2.1 |
| [10](notes/10-evals.md) | evals: measuring right behaviour, not just working code |
| [11](notes/11-async.md) | one event loop, many conversations |
| [12](notes/12-builder.md) [18](notes/18-builder-mode.md) | an agent that builds a project, and verification that does not trust it |
| [13](notes/13-caching.md) [21](notes/21-cost-accounting.md) | prompt caching; token counters into dollars |
| [14](notes/14-hooks.md) [20](notes/20-approve-with-edits.md) [56](notes/56-not-like-that-like-this.md) | hooks watch, gates decide; the gate that talks back, and the person who can |
| [15](notes/15-images.md) [27](notes/27-read-image.md) | images in, and the loop growing eyes |
| [16](notes/16-sandboxing.md) [17](notes/17-tool-selection.md) | containment; the tool cliff and BM25 |
| [19](notes/19-responses-api.md) | the third dialect |
| [22](notes/22-web-ui.md) | the browser UI |
| [23](notes/23-glob.md) [24](notes/24-todo-lists.md) [25](notes/25-web-fetch.md) [26](notes/26-background-bash.md) [28](notes/28-browser-tools.md) | the self-reliance tools, one note each |
| [29](notes/29-environment-awareness.md) [30](notes/30-skills.md) | knowing where it is; teaching it your procedures |
| **[31](notes/31-agent-packages.md)** | **an agent you can hand to someone** — the hinge |
| [32](notes/32-package-tools.md) [53](notes/53-a-tool-that-arrives-by-pip.md) | a package brings its own tools — from its own folder, or from pip |
| [33](notes/33-evals-as-a-gate.md) [35](notes/35-roster-and-pass-rates.md) [41](notes/41-a-gate-you-can-point.md) [42](notes/42-two-runs-of-the-same-suite.md) [44](notes/44-a-ceiling-and-a-floor.md) [46](notes/46-the-cases-that-were-red.md) [47](notes/47-what-seven-of-ten-is-evidence-of.md) [49](notes/49-three-runs-side-by-side.md) [57](notes/57-a-turn-written-down.md) [62](notes/62-the-reports-you-already-have.md) [63](notes/63-the-whole-turn-written-down.md) [65](notes/65-the-turn-behind-the-red-line.md) [66](notes/66-what-each-case-cost.md) [67](notes/67-the-agent-or-the-vendor.md) [68](notes/68-the-version-nobody-bumped.md) [70](notes/70-a-list-to-choose-from.md) [72](notes/72-what-changed-under-a-name.md) [74](notes/74-a-verdict-you-write-down.md) [75](notes/75-what-answered.md) [77](notes/77-a-mark-taken-back.md) [79](notes/79-scrubbed-before-it-is-written.md) [81](notes/81-the-turns-the-page-never-saw.md) [83](notes/83-a-table-that-fits.md) [84](notes/84-a-verdict-already-reached.md) [90](notes/90-what-it-said.md) | the acceptance gate, and everything that grew on it |
| [34](notes/34-budgets.md) [36](notes/36-a-warning-before-the-stop.md) [43](notes/43-a-bar-and-a-deadline.md) [48](notes/48-what-the-run-cost.md) [69](notes/69-a-reply-the-turn-has-seen-before.md) [73](notes/73-the-turn-before.md) [76](notes/76-remembered-and-capped.md) [71](notes/71-a-limit-on-waiting-not-on-work.md) [82](notes/82-what-each-line-cost.md) | the ceiling, the warning, the two readers of one meter, and what a run cost |
| [37](notes/37-a-gate-that-can-wait.md) [39](notes/39-a-clock-and-a-word.md) [51](notes/51-a-turns-worth-of-waiting.md) [52](notes/52-the-word-for-what-happened.md) [78](notes/78-a-clock-on-the-page.md) [80](notes/80-the-time-left-told.md) [91](notes/91-one-clock-for-the-child.md) | a gate that waits; a clock, a machine-readable word, and a turn's worth of patience |
| [38](notes/38-giving-it-back.md) | two things that assumed the process would exit |
| [45](notes/45-the-road-with-no-key.md) [54](notes/54-the-word-for-a-road.md) | the road with no key, how it says its name, and what to call it |
| [40](notes/40-a-package-that-delegates.md) [44](notes/44-a-ceiling-and-a-floor.md) [50](notes/50-the-rest-of-what-a-child-is.md) [55](notes/55-two-at-a-time.md) | a package that declares its children, and the gate that watches what they were given |

### dvara — the door

In the dvara repository, alongside its own README:

| | |
|---|---|
| `notes/01-the-door.md` | the three nouns, the security rules, and why a daily allowance is enforced by a per-turn ceiling that shrinks |
| `notes/02-a-question-that-can-wait.md` | escalation, and why a deadline that denies belongs in the service rather than in the framework |
| `notes/03-standing-answers.md` | a rung is per turn, a rule is per call, and why patterns may widen a refusal but never a permission |
| `notes/04-the-failure-loop.md` | a bad turn becomes a case in the package that produced it — and why only a person can say a turn *answered* badly |
| `notes/05-one-person-two-channels.md` | an actor is a person, not a seat; and the allowance that silently doubled when it was not |
| `notes/06-a-number-you-can-act-on.md` | what follows an answer — and why an owner and a guest want two different numbers |
| `notes/07-four-thousand-and-ninety-six.md` | the Telegram bot: a cap measured in units nobody counts by hand, a poll loop that must not wait, and an approval that has to be a button |
| `notes/08-what-the-turn-actually-did.md` | the trajectory on a run — names and not arguments, and why the service describes a turn but will not judge one |
| `notes/09-a-process-you-walk-away-from.md` | one dvara per state directory, a roster you can edit while it runs, and the fix that would have hidden the bug |
| `notes/10-what-decided-this.md` | counting the standing answer that leaves no trace by working, and why counting by ORDER is the wrong thing to depend on |

### The two READMEs

[README.md](README.md) is the reference: every flag, every config key,
every module with a line saying what it holds. dvara's README is the same
shape, one layer up.

---

# Act IX — what is deliberately not here

A feature list that only says what exists is half a map. These are refusals
and gaps, and each one is argued in the note that owns it.

**In the framework:**

* **No `@tool` decorator.** The hand-written JSON Schema is what makes the
  model call your tool correctly.
* **A package tool's `read_only` is the author's word** and nothing checks
  it. Read a package's `tools/` before you run it.
* **A trajectory check cannot see everything.** `forbidden_tools` catches
  an agent *writing*, never an agent being *able to* — which is the gap
  `lacks_tools` exists to fill.
* **The API is not stable.** The harness underneath is settled; the
  framework layer on top is in use and still moving.

**In the service:**

* **No Slack app, and no webhook.** The Telegram bot long-polls, which
  needs no public address, no TLS and no reverse proxy. A second channel
  is an adapter and three lines of TOML, because nothing in the service
  branches on which channel a person is reachable on.
* **No per-tool policy ladder.** Tool and argument globs → allow / deny /
  ask is a real thing to want, and inventing that dialect twice is how two
  incompatible dialects are born.
* **No approve-with-edits over a channel.** The round trip is long enough
  that the edit and the thing being edited drift apart in a person's head.
  Two buttons: approve or refuse.
* **No streaming, no web UI, no registry, no scheduling.** Channels are
  turn-shaped, and each of the others is a service of its own wearing this
  one's clothes.
* **Locks are never evicted** — one `asyncio.Lock` per session key the
  process has ever served. A few hundred bytes against a correctness
  property, and the reason two dvaras may not share a state directory:
  that lock is in memory, so a second process does not see it.
* **No preferred channel, and no taking a question back.** A person
  reachable three ways gets the question three times, in no order, and
  answering on one leaves the other two sitting there — the bot edits the
  copy that was pressed, and only that one. Ranking channels
  means a second deadline inside the first; retracting means every
  adapter implements editing.
* **Two dvaras may not share a state directory.** A service is a process:
  the lock serializing one conversation and the queue of pending
  questions are in memory, so a second one is refused rather than made to
  work. `serve --telegram` is how one process does both jobs.
* **A package edited on disk changes a live conversation's next turn** —
  desirable when you are fixing a prompt, alarming when a conversation
  changes personality mid-sentence. Settled as a decision rather than left
  as a consequence: the edit applies, and the version is recorded on every
  Run, so the change appears in the ledger instead of being guessed at.
  Pinning a version per thread stays refused, because a pinned thread is
  one that does not get the prompt fix you made *because of it*.

---

# The shape of it, in one page

If you remember nothing else:

1. **The model is a text-in, text-out function.** Everything else is a
   postal service around it.
2. **Errors are data; provider failures are exceptions.** A tool can never
   crash the loop, and the model reads what went wrong and works around it.
3. **Gates decide, sandboxes contain, hooks watch.** Three different jobs;
   none substitutes for another.
4. **An agent is a directory.** Named, versioned, reviewable, and portable
   because every field is optional and flags beat the manifest.
5. **Unknown keys are errors.** A typo that quietly leaves a tool armed is
   the worst bug a config format can have.
6. **A package ships the proof that it works**, and the free half of that
   proof needs no key and no tokens.
7. **A ceiling is a stop, not a cap**, and the person and the model want
   opposite things from the same meter.
8. **An actor is assigned, never asserted. An agent is named, never
   pathed.** Both are the same rule: nothing arriving from outside gets to
   choose what code runs or who it runs as.
9. **Permission composes as a minimum** across the package, the owner and
   the actor, so nothing anybody writes can loosen what somebody else
   allowed.
10. **"Ask" with nobody present is not a question, it is a hang** — so
    presence is modelled as a *route* rather than a setting, and with no
    route the answer is a denial that says so.

---

*Yantra: 1589 offline tests passing (1 skipped) — no network, no key.
dvara: 323. Both copyright 2026 Mahen Singh, Apache License 2.0.*
