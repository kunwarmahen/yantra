# 102 — Kept somewhere else

[Note 100](100-what-it-knows-about-you.md) gave the agent a memory of
the person, kept in one sqlite file with word-overlap search. That store
is there so memory works for everyone with nothing installed. It is not
clever. *"Somewhere warm"* finds nothing, and one laptop's file is not
the phone, the other laptop, or the team's server.

A store that *is* clever — embeddings, a graph, a server the whole house
uses — is somebody else's project. This note is how one plugs in
without Yantra ever learning its name.

## The store runs an MCP server; Yantra names it

```
yantra --memory smritikosh --mcp-config smritikosh.json
```

or, in a package:

```toml
[[mcp]]
name    = "smritikosh"
command = "smritikosh-mcp"
env     = { SMRITIKOSH_API_KEY = "${SMRITIKOSH_API_KEY}", SMRITIKOSH_USER_ID = "asha" }

[memory]
via = "smritikosh"            # local | off | the name of an MCP server
```

`--memory` (and `YANTRA_MEMORY`, and `[memory] via`) took `local` or
`off`. Anything else is now the name of an MCP server, and memory goes
through it. Yantra already speaks MCP ([note 09](09-mcp.md)). So there is
no second protocol, no HTTP spec for stores to implement, and none of
the store's code installed in Yantra's environment. A store in Go, on
another machine, behind a login, plugs in the same way.

Three roads were on the table:

* **a Python entry point**, like tool packs ([note 87](87-which-release-and-what-its-called.md)):
  typed and fast, but it needs the store's Python in Yantra's
  environment, in Python only;
* **a Yantra-owned HTTP contract**: any language, but now Yantra owns a
  spec that others have to implement and keep up with;
* **MCP**: any language, the client already exists, and the same server
  serves Claude Desktop or any other MCP client too.

MCP won. What it costs: Yantra calls the store's tools itself, outside
the model's loop, and a stdio server takes a moment to start.

## Four verbs, one map

`MemoryStore` is four verbs: remember, recall, forget, list. A server's
tools are named whatever its author chose. `[memory] verbs` says which
tool is which:

```toml
[memory]
via   = "kosh"
verbs = { remember = "store_statement", list = "all_memories" }
```

**A verb left out is a tool of the same name.** `remember` calls
`remember`, `recall` calls `recall`, `forget` calls `forget`. The one
exception is `list`, which calls `list_memories`, because a tool called
`list` says nothing about what it lists. A store written with Yantra in
mind needs no map at all.

**What is sent is fixed**, so a store author knows what arrives:

| verb | arguments |
|---|---|
| remember | `statement`, `user_id`, `metadata` (kind, package) |
| recall | `query`, `limit`, `user_id` |
| forget | `memory_id`, `user_id` |
| list | `limit`, `user_id` |

**What comes back is read leniently**, because it is the store's shape,
not ours. JSON is expected, with the id under `id`, `memory_id` or
`event_id`, and the text under `statement`, `content`, `text` or
`raw_text`. Several come back as a list, either bare or under
`memories`, `results`, `items` or `events`. `forget` answers `forgotten`
or `deleted`. A store that already has a search tool probably fits
without changing anything.

## `user_id` is always sent

Note 100's rule was *no identity, no memory*. Whose memories these are is
settled when the session starts: `$YANTRA_USER`, or the login name, or
what a service passes per end user. It goes on every call.

A store's MCP server usually has a default user of its own, set when it
was started. Falling back on that would be convenient for one person and
a leak for a service: every end user's *"lives near RDU"* would land in
one person's memory. So Yantra never leaves it out. If the store's
credential can only act as one user, set `YANTRA_USER` to that user's
name there.

## A store without every verb still works

A server may have `remember` and `recall` and nothing else. **A verb it
has no tool for is unsupported, not an error.** The session starts, the
prompt layer fills from `recall` alone, and both `/memory` and the page
say what it can't do:

```
memory: kosh for asha -- connected; cannot forget, list (/memory; --memory off)
```

The page hides its *forget* buttons. `/memory find WORDS` still
searches.

## Bound late, and failing open

Memory is attached while the agent is built, and MCP servers connect
afterwards, in the terminal or the page. So the store starts **unbound**.
Once the servers are up, the package's `[[mcp]]` among them
([note 104](104-the-servers-it-came-with.md)), the host binds it to the
named one. Until then, or after
the server goes away, a call to the store fails, and the memory layer
does what it always does: the turn goes on without it, with a line
saying why.

```
memory: mcp server 'smritikosh' is not connected (add it with --mcp-config, the page's MCP panel, or [[mcp]] in agent.toml)
```

## One road to memory

The server's own tools would reach the model too, as
`mcp__smritikosh__remember` and so on, next to Yantra's `remember`. That
is two ways to write the same thing, and one of them skips the ask-first
prompt and the identity rule. **Binding takes the mapped tools out of
the model's list.** The server's other tools stay. The model still
writes through `remember`, which asks first, and nothing it writes can
go under somebody else's name.

## The store's side

The design needed three things on the store's side, and Smritikosh, the
first store to plug in, now has them:

* **Store what the agent says, as-is.** Its `store_memory` tool runs
  the text through its own LLM to pull out facts. That is the right
  tool for a client that hands over a raw transcript. It is the wrong
  one here, where Yantra's model has already decided what to keep and
  put it in one sentence. A new `remember` tool stores the statement as
  given: embedded for search, with no LLM. (On the REST side:
  `POST /memory/event` with `"extract": false`, or `ENCODE_EXTRACT=false`
  to turn extraction off for the whole server.)
* **`list_memories` and `forget`**, so `/memory` can show what is kept
  and remove what stopped being true. `forget` checks the memory belongs
  to the user it was asked for before deleting it. An admin key acting
  for one person can't delete another's.
* **Its tools use Yantra's verb names**, so it plugs in with no map.

Its embeddings run wherever Smritikosh is configured to run them,
including Ollama. That is the store's business. Yantra sends sentences
and gets sentences back.

## Receipt

Smritikosh's real MCP server (`smritikosh-mcp`, over stdio), started by
Yantra from `--mcp-config`. The REST API behind it was a small stand-in
answering the five routes those tools call, so the run needs no Postgres
or Neo4j. What is being tested is the wire between the two programs:

```
$ YANTRA_USER=asha yantra --memory smritikosh --mcp-config smritikosh.json \
    --provider ollama --model qwen3.8:latest
memory: smritikosh for asha -- 0 remembered (/memory; --memory off)
yantra · provider=ollama · model=qwen3.8:latest · tools=27
> /memory add Lives near RDU
remembered #eb60751b-ae4a-42b3-a253-7dfc2af331de
> /memory
memory: smritikosh, for asha -- 1 remembered
   #eb60751b-ae4a-42b3-a253-7dfc2af331de  Lives near RDU
```

Driving the store directly over the same wire: remember twice, recall
*"flights from RDU"*, list, fill the prompt layer for *"find me flights
to Austin"*, then forget one:

```
tools left for the model: ['mcp__smritikosh__get_context', 'mcp__smritikosh__store_memory']
recall: ['Lives near RDU (Raleigh-Durham airport)']
list: ['Uses uv, not pip', 'Lives near RDU (Raleigh-Durham airport)']
in prompt: ['Uses uv, not pip', 'Lives near RDU (Raleigh-Durham airport)'] notice: None
forget: True again: False
list after: ['Uses uv, not pip']
```

The stand-in rejected any store call that didn't carry
`"extract": false`, and none did. With the server left out of
`--mcp-config`, the session still starts. It prints the *not connected*
line and carries on without memory.

How often a fact said in passing comes back, on `local` and on
Smritikosh, and what changes once the fact is buried under newer ones,
is measured in [note 103](103-said-once-found-later.md).

`tests/test_memory_mcp.py` pins the rest against a fake session:

* the map renames what it names, and a verb left out keeps its own name;
* `user_id` rides on every call, and one person never sees another's;
* a tool error or a dropped server is a store error, which the layer
  survives;
* a server without `forget` or `list` is reported, not fatal;
* binding connects the package's server, and takes only the mapped
  tools away from the model.

## What was deliberately not built

* ~~**A package's `[[mcp]]` servers in an ordinary session.**~~ Every
  session now starts them, after the person's own servers, and binding
  no longer connects anything ([note 104](104-the-servers-it-came-with.md)).
* **Renaming arguments.** The map renames tools, not arguments. A store
  whose `remember` wants `content` instead of `statement` needs a small
  tool of its own. A map for arguments, too, would be a spec that grows
  with every store.
* **A verb map from the command line.** `--memory NAME` uses the default
  tool names. A store that needs a map is reached through a package's
  `[memory] verbs`.
* **Scoped keys per end user.** A service that passes each end user's id
  needs a store credential allowed to act for them. How a store grants
  that (an admin key, or a key per user) is decided on the store's side.
