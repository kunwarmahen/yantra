# 60 — The tool that explained itself

*Continues [notes/17](17-tool-selection.md), which made choosing tools a
retrieval problem. This is about what that retrieval was quietly
rewarding.*

## The symptom

Ask a local model to open a web page and it answers that it has no
browser. It is not confused and it is not hallucinating a limitation —
it is reading its tool list correctly. The browser tools were
installed, registered and working. Three of the four had been handed to
it. The fourth, `browser_open`, had not, and `browser_open` is the only
one that can start a session.

So the model held `browser_click`, `browser_fill` and `browser_close` —
three verbs that all begin "on the open browser page" — with no way to
open a page. It went looking, found nothing it could use, and did what
a sensible agent does with no tool for the job: it said so.

## Why retrieval dropped exactly the wrong one

Past about twenty tools, Yantra stops sending all of them and retrieves
the best twelve per turn ([notes/17](17-tool-selection.md)). The ranking
is BM25: score each tool's text against a query built from the
conversation, take the top scorers. Two details of that scoring worked
together here, and each looks harmless alone.

**Snake_case names are one word.** The tokenizer splits on anything
outside `[a-z0-9_]`, so underscores survive and `browser_open` is a
single term. That is deliberate and it earns its keep: when the model
has already called `mcp__slack__post_message`, that exact string lands
in the transcript, becomes a query term, and retrieves itself. Names
are the vocabulary.

But it cuts the other way too. **Nobody types `browser_open`.** They
type "open the browser". Kept whole, a tool's name — the most
deliberate two words it owns — matches nothing a human ever wrote, and
the tool is left to be found entirely by its description.

**BM25 divides by length.** Its length normalization exists to stop a
long document winning on sheer surface area, and the textbook strength
is `b=0.75`. That assumes a document's length is incidental to how
relevant it is. Tool descriptions are the opposite: an author chooses
the length, and the longest one in a family is usually the one doing the
most work — the entry point, the one that has to explain what the whole
family is for.

Here is the scoring, on the real catalog, for the real query that
failed:

| tool | matched terms | description | score |
|---|---|---|---|
| `browser_click` | `open`, `and`, `from` | 29 tokens | **4.08** |
| `todo_read` | `x` | 25 tokens | 3.04 |
| `browser_close` | `open`, `and` | 21 tokens | 2.69 |
| `browser_fill` | `open`, `and` | 39 tokens | 2.09 |
| `browser_open` | `open`, `and` | **77 tokens** | **1.42** |

`browser_open` and `browser_close` matched the *same two terms*. The
only difference between them was that `browser_open` had explained
itself, and it was ranked eighth for it — outside the four retrieval
slots left after the pins. Its siblings, meanwhile, got in partly by
naming it: "the page opened by `browser_open`" is a free hit on `open`
in a description short enough to be cheap.

Note `todo_read` at second place, on one match of the token `x` — from
"x.com". A single rare character in a short document beat the tool the
turn was about.

The general shape: **a tool was being punished for explaining itself,
and its siblings were free-riding on the explanation.** That is the
wrong incentive to put in front of whoever writes the next tool.

## Two changes

**Index each name both ways.** `name_parts("browser_open")` gives
`["browser", "open"]`, added to the indexed document alongside the whole
name, weighted `NAME_PART_WEIGHT = 2`. The whole name still retrieves
itself from the transcript; the pieces give it a way into ordinary
English. `mcp__slack__post_message` becomes findable by "post a slack
message" without losing the exact match that makes mid-task pivots
converge.

**Damp the length normalization**, `b` from 0.75 to 0.30. Not to zero —
a genuinely rambling description should still pay something — but far
enough that matching a term counts for roughly what it is worth
regardless of how thoroughly the tool was documented.

Neither change touches what the query is or how pins work. The score
floor still excludes anything that matches nothing, because a filler
tool ranked last still teaches the model that a wrong tool exists.

## What it moved

Same catalog, same query, after:

```
before                       after
  1. browser_click  4.08       1. browser_click  3.91
  2. todo_read      3.04       2. browser_open   2.91   <- retrieved
  3. browser_close  2.69       3. todo_read      2.80
  4. recall_notes   2.23       4. browser_close  2.38
  5. browser_fill   2.09       5. recall_notes   2.23
  ...                          6. browser_fill   2.18
  8. browser_open   1.42
```

`browser_open` goes from eighth to second and lands inside the twelve
that get sent. It is not first, and it does not need to be: retrieval
owes the turn a workable set, not a podium. Which sibling edges which
inside the budget is noise that moves with the catalog — that is why
the test for this asserts admission to the set rather than first place,
and why the test corpus carries filler tools, so that "browser" has the
rarity it has in a real registry instead of being a word every document
shares.

## The discovery hatch was not the answer here

Yantra already has the escape route for exactly this: the model can
call `list_available_tools`, see the full catalog, and use what it
finds on the next turn ([notes/17](17-tool-selection.md)). In the
failing run it did — correctly, having spotted `browser_open` named
inside `browser_close`'s description — and the hatch worked: on the
next turn `browser_open` scored 77.6, first by a distance, and was
sent.

The model used `bash` anyway. It had already spent a turn concluding it
had no browser, and it had already started building a workaround.

That is worth sitting with, because it is the real lesson. The hatch
closes the hole in *retrieval*; it cannot close the hole in a turn the
model has already reasoned its way past. And by the third turn, two
bash calls had filled the query with shell vocabulary and pushed
`browser_open` back out of the top twelve — so the model's report that
it had no browser tool, which had been wrong when it first made it, had
become true by the time it said it out loud.

A recovery path is not a substitute for getting the first turn right,
especially with a smaller local model, which has fewer turns of patience
before it commits to a plan.

## Receipt

`qwen3.8:latest` on Ollama, the same kind of request that failed:

```
$ uv run yantra --provider ollama --yolo --prompt \
    "open https://www.bing.com and tell me the page title"

· thinking
The user is asking me to open bing.com and report the page title.
Let's use browser_open.

→ browser_open()
  title: Search - Microsoft Bing

The page title is **"Search - Microsoft Bing"**.
-- end_turn · 3042 in / 77 out · 2 iteration(s)
```

Two iterations. No `list_available_tools` detour, no bash, no essay
about what it cannot do — the tool was in front of it, and it used the
tool. The failing run took five iterations to arrive at the wrong
answer.

Continues [notes/17](17-tool-selection.md).
