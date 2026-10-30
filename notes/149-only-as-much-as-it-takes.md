# 149 — Only as much as it takes

[Note 07](07-reliability-and-scale.md) made the window a resource:
when a turn reaches the red zone (80% of what's usable), older tool
results are elided first, and a summary is made only if that wasn't
enough. The rule was "every result but the newest three". It was
written for a coding agent that rereads a file when it needs it again.

A morning brief run through Dvara on `qwen3.8-64k:latest` showed what
that rule does to a job that gathers and then writes. Thirty-three
tool calls in, the window went red, and every result but three went
at once. The step log shows each one as it was left:

```
mcp__robinhood-personal__get_portfolio   [tool result elided ...; original 2007 chars ...]
mcp__robinhood-personal__get_portfolio   [tool result elided ...; original 1932 chars ...]
mcp__robinhood-personal__get_equity_positions  [tool result elided ...; original 5779 chars ...]
mcp__sparsh__phone_status                [tool result elided ...; original 68 chars ...]
mcp__samay__list_schedules               [tool result elided ...; original 168 chars ...]
```

The placeholder says "re-run the tool if you need this output again".
The model didn't. It wrote the brief from what was left: the phone
part said *No such tool in this session*, last night's tests *No
list_schedules tool available*, and the money lines were made up. Three
briefs in three days gave three different portfolio totals.

The stored conversation of that run settles where they came from. When
the model wrote, 24 of its 33 results were placeholders: every mail
search and every brokerage read. The brief opened with a to-do, *Red
Hat access approval from Pradeep — 14 approvals pending; he asked
before 6pm ET*, that its person had no such mail for, and a cash line
of *≈ $5.2M*. Neither is in any result it could still see. Its own
thinking, just before it wrote, called a figure "$3,729,970 total
cash, which is the authoritative figure I should use". It was
remembering text it no longer had, and remembering it wrong.

## Two rules

**A SHORT RESULT IS NEVER ELIDED.** The placeholder is about 125
characters. Eliding a 68-character battery reading frees nothing and
loses the fact, and the model then reports the tool as missing. A
result under `SHORT_RESULT` (500 characters) stays as it is.

**ONLY AS MUCH AS IT TAKES, OLDEST FIRST.** Results are elided from
the oldest until the window is back under yellow (60%), and then it
stops. In the brief, the two 20,000-character tool listings at the
start of the turn were most of the weight. They go first, and the
portfolio a minute old stays.

The yellow target counts what every request carries besides the
history: the system prompt and the tool schemas (`fixed_tokens`, the
same figure the agent forecasts with). Without it, masking would aim
under yellow by its own count while the real request was still red,
and every call after would compact again.

**THE START STAYS.** An elided result keeps its first few lines, up
to `HEAD_KEPT` (300 characters, and never more than a quarter of the
result), after words that say what is missing:

```
[tool result elided to save context; call_id=call_xe1vvcca; original
4929 chars, of which only the start is below. Call the tool again
before you state anything else from it]
## mine (kunwarmahen@gmail.com)
3 threads since Oct 29 ...
[...]
```

A model that re-runs nothing still has the heading and first figures,
and the words say plainly that the rest isn't there to quote.

The newest three are still kept whatever happens. A summary is still
made only when masking alone leaves the window red.

## Live receipt

The same brief, run again with `samay run-now` on `qwen3.8-64k:latest`
after the two rules above (before the start was kept): 31 tool
results, 7 elided, all of them older listings and mail searches of
1,500 characters or more. Every phone reading (61-88 characters) and
both schedule listings (168) were still there when it wrote:

```
**Phone** — Nexus: 100 %, charging. iPhone: battery not reported;
missed-call read not available on either.

**Tests** — no test-running schedule on Samay other than this brief
itself.
```

Each line of the brief traces to a result it could see. Its money
line said one account held $0 and the other returned nothing, and
that is what those two calls returned (`"total_value":"0"`, and
`API error 404`). It had read the wrong two accounts, a choice made
from the account list early in the turn, but it no longer invented a
total.

## The tradeoff

Masking now happens more often and less each time. A turn that crosses
red again masks a few more, where before one pass cleared most of the
window. Each pass changes the start of the history, so a provider's
prompt cache is invalidated once per pass, not once per turn. Losing a
cache prefix costs money on a cloud model. Losing the facts the answer
is made from costs the answer.

## What is not here yet

* **A model that re-runs what was elided.** The placeholder asks, and
  `qwen3.8-64k:latest` ignored it. Elided results are now fewer and
  older and keep their start, but a fact past the first lines of one
  the answer needs can still go on a long enough turn.
* ~~**Keeping the shape of an elided result**~~ -- the start stays, above.
