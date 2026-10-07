# Giving Yantra a bigger memory: Smritikosh, step by step

Yantra remembers things about you out of the box: where you live, which
tools you prefer, how you like answers. It keeps them in one small file
on your computer (`~/.local/state/yantra/memory.sqlite`).

That file matches **words**. Ask *"find me flights to Austin"* and it
won't think of *"lives near RDU"*, because the two share no word.
[Smritikosh](https://github.com/kunwarmahen/smritikosh) is a memory server
that searches by **meaning**, so *"aeroplane seating"* finds *"prefers a
window seat on flights"*. With a lot of memories, that's the difference
between Yantra remembering you and forgetting you. In Yantra's own trial,
with a fact buried under thirty newer ones, the word-matching file found
it 2 times in 7 and Smritikosh found it 6 times in 7
([notes/103](notes/103-said-once-found-later.md)). Later, `gemma4:12b` on
Smritikosh used it in 33 of 35 answers
([notes/107](notes/107-a-fact-that-says-what-it-is.md)).

You don't need it to start. Come back here when Yantra's memory starts
missing things you know you told it.

**What changes when you switch:** nothing you do. `remember`, `/memory`,
the look back when you finish a conversation, and the bookmark chip on
the page all work the same. Only where the memories are kept changes.

**Time:** about 30 minutes, once.

There are two roads, the same as for Yantra itself. Each step says when
they differ.

| | Local road (Ollama) | Cloud road |
|---|---|---|
| Turns memories into searchable numbers ("embeddings") | `nomic-embed-text` on Ollama | an OpenAI embeddings key |
| Leaves your computer | nothing | each memory, sent once to be turned into numbers |
| Costs | nothing | a fraction of a cent per memory |

---

## Step 1 · What you need

* Yantra, already working ([TUTORIAL.md](TUTORIAL.md) section 1).
* [uv](https://docs.astral.sh/uv/) (Yantra's setup already installed it).
* Podman, for the database. Docker works too: change `podman` to `docker`
  in the commands below and drop the `:Z` at the end of `-v` options.
* **Local road:** Ollama running, and its embedding model:

  ```
  ollama pull nomic-embed-text
  ```

  Smritikosh also wants a chat model for its own extra features (Yantra
  doesn't use them). Any small one you already have will do, for example
  `gemma4:e4b`.

* **Cloud road:** an OpenAI API key for embeddings, and an API key for the
  chat model you'd like Smritikosh to use (Anthropic, OpenAI or Gemini).

## Step 2 · Get Smritikosh

```
cd ~/src            # or wherever you keep projects
git clone https://github.com/kunwarmahen/smritikosh
cd smritikosh
uv sync --extra mcp
```

`--extra mcp` matters: it installs `smritikosh-mcp`, the small program
Yantra talks to. Check that it's there:

```
ls .venv/bin/smritikosh-mcp
```

## Step 3 · Start its database

Smritikosh needs one database, PostgreSQL with the `pgvector` add-on. Use
the `pgvector` image: the plain `postgres` image is missing the add-on, and
Step 5 fails without it.

```
podman run -d --name smritikosh-postgres \
  -e POSTGRES_USER=smritikosh -e POSTGRES_PASSWORD=smritikosh \
  -e POSTGRES_DB=smritikosh \
  -p 5432:5432 -v smritikosh-pgdata:/var/lib/postgresql/data:Z \
  docker.io/pgvector/pgvector:pg17
```

Smritikosh can also use Neo4j, MongoDB and Redis. **You can skip all
three.** Without them it says it's "degraded" and works fine for Yantra.

## Step 4 · Tell Smritikosh which models to use

```
cp .env.example .env
```

Open `.env` in an editor and change the top two blocks.

**Local road:**

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=gemma4:e4b
LLM_BASE_URL=http://localhost:11434

EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BASE_URL=http://localhost:11434
EMBEDDING_DIMENSIONS=768
```

**Cloud road:**

```dotenv
LLM_PROVIDER=claude
LLM_MODEL=claude-haiku-4-5-20251001
LLM_API_KEY=your-anthropic-key

EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_API_KEY=your-openai-key
EMBEDDING_DIMENSIONS=1536
```

**Get `EMBEDDING_DIMENSIONS` right now.** It must match the embedding
model: 768 for `nomic-embed-text`, 1536 for `text-embedding-3-small`. The
database is built for that size in the next step, so changing it later
means starting with an empty database.

Also set a secret for its logins. Any long random string will do:

```
python3 -c "import secrets; print('JWT_SECRET=' + secrets.token_hex(32))" >> .env
```

## Step 5 · Build the database tables

```
uv run alembic upgrade head
```

It prints a list of `Running upgrade ...` lines and stops. An error that
mentions `vector` means the database came from the plain `postgres`
image: go back to Step 3.

## Step 6 · Start Smritikosh, and make an admin account

The very first account can be made without logging in, only while
`BOOTSTRAP_ADMIN=1` is set:

```
echo "BOOTSTRAP_ADMIN=1" >> .env
uv run uvicorn smritikosh.api.main:app --port 8080
```

Leave that running. In a **second terminal**, make the admin (choose your
own password):

```
curl -s -X POST http://localhost:8080/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "pick-a-long-password", "role": "admin"}'
```

Now close that door again: stop the server (Ctrl+C in the first
terminal), remove the line, and start it again:

```
sed -i '/BOOTSTRAP_ADMIN/d' .env
uv run uvicorn smritikosh.api.main:app --port 8080
```

Check it from the second terminal:

```
curl -s http://localhost:8080/health
```

You should see `"postgres":"ok"`. `"status":"degraded"` is fine: that's
the Neo4j you skipped.

## Step 7 · Make your own account, named the way Yantra knows you

Yantra sends **whose** memories it means with every request: your login
name, or `$YANTRA_USER` if you've set it. Your Smritikosh account must
have **exactly that name**. Find it:

```
echo "${YANTRA_USER:-$(whoami)}"
```

Say it printed `asha`. In the second terminal (replace `asha`, and the
passwords, with your own):

```
ME=asha

ADMIN=$(curl -s -X POST http://localhost:8080/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "pick-a-long-password"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -X POST http://localhost:8080/auth/register \
  -H "Content-Type: application/json" -H "Authorization: Bearer $ADMIN" \
  -d "{\"username\": \"$ME\", \"password\": \"another-long-password\", \"role\": \"user\", \"app_ids\": [\"default\"]}"
```

## Step 8 · Make a key for Yantra

Log in as yourself and make a long-lived key:

```
TOKEN=$(curl -s -X POST http://localhost:8080/auth/token \
  -H "Content-Type: application/json" \
  -d "{\"username\": \"$ME\", \"password\": \"another-long-password\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s -X POST http://localhost:8080/keys \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "yantra", "app_ids": ["default"]}'
```

The reply contains a key that starts with `sk-smriti-`. **It's shown only
this once.** Copy it.

Use your own account's key, not the admin's. It can only read and write
your memories, which is all Yantra needs.

## Step 9 · Point Yantra at it

Everything from here happens in **the folder you start Yantra from**.

**a. The key goes in Yantra's `.env`**, next to your other keys:

```dotenv
SMRITIKOSH_API_KEY=sk-smriti-...the key you copied...
```

**b. Tell Yantra how to start `smritikosh-mcp`.** Create
`.yantra/mcp.json` (Yantra connects to every server listed there when it
starts). Change the path to where you cloned Smritikosh, and `asha` to your
name from Step 7:

```json
{"servers": {"smritikosh": {
  "command": "/home/asha/src/smritikosh/.venv/bin/smritikosh-mcp",
  "env": {"SMRITIKOSH_API_KEY": "${SMRITIKOSH_API_KEY}",
          "SMRITIKOSH_USER_ID": "asha",
          "SMRITIKOSH_BASE_URL": "http://localhost:8080"}}}}
```

`${SMRITIKOSH_API_KEY}` is filled in from `.env` when Yantra starts, so the
key itself never sits in this file.

If `.yantra/mcp.json` already lists other servers, add `"smritikosh"`
inside the same `"servers"` block, next to them.

**c. Make Smritikosh Yantra's memory.** Add this line to Yantra's `.env`:

```dotenv
YANTRA_MEMORY=smritikosh
```

The name is the server's name from step b.

## Step 10 · Check it

Start Yantra the way you usually do. Among the first lines it prints:

```
mcp 'smritikosh' (remembered): 6 tool(s)
memory: smritikosh for asha -- 0 remembered (/memory; --memory off)
```

Type `/memory`:

```
> /memory
memory: smritikosh, for asha -- 0 remembered
  nothing yet -- tell the agent about yourself, or /memory add TEXT
```

`smritikosh, for asha` means it worked. Now try a search by meaning:

```
> /memory add Prefers a window seat on flights
remembered #2ad6b855-0ef2-43ce-8d0c-2a3ff5f78585
> /memory find aeroplane seating
memory: smritikosh, for asha -- 1 match 'aeroplane seating'
   #2ad6b855-0ef2-43ce-8d0c-2a3ff5f78585  Prefers a window seat on flights
```

No word in *"aeroplane seating"* is in the memory, and it was found
anyway. That's the whole point. (That's real output, on the local road
with `nomic-embed-text`.)

To remove a memory, give its whole id: `/memory forget
2ad6b855-0ef2-43ce-8d0c-2a3ff5f78585`. A Smritikosh id is long. The short
numbers in the tutorial (`/memory forget 1`) belong to the built-in file.

On the page (`yantra --web`), the bookmark chip in the header shows the
same list.

You may see lines starting `INFO ... HTTP Request:` in the terminal. That's
Smritikosh's program logging its own work. It's harmless.

## Every day after this

Smritikosh has to be running before Yantra starts. After a restart:

```
podman start smritikosh-postgres
cd ~/src/smritikosh && uv run uvicorn smritikosh.api.main:app --port 8080
```

If you forget, Yantra still works. It says why memory is missing and goes
on without it:

```
memory: mcp server 'smritikosh' is not connected (add it with --mcp-config, the page's MCP panel, or [[mcp]] in agent.toml)
```

To keep the server running without a terminal, make it a user service.
Save this as `~/.config/systemd/user/smritikosh.service`, with your own
paths:

```ini
[Unit]
Description=Smritikosh memory server

[Service]
WorkingDirectory=%h/src/smritikosh
ExecStartPre=-/usr/bin/podman start smritikosh-postgres
ExecStart=%h/.local/bin/uv run uvicorn smritikosh.api.main:app --port 8080
Restart=on-failure

[Install]
WantedBy=default.target
```

```
systemctl --user daemon-reload
systemctl --user enable --now smritikosh
loginctl enable-linger "$USER"     # keep it running when you're logged out
```

## Good to know

**Memories don't move over by themselves.** What the built-in file
already holds stays there. Open `/memory` with the old setting
(`yantra --memory local`), and copy anything you want into Smritikosh with
`/memory add`.

**Going back is one setting.** `YANTRA_MEMORY=local` in `.env` goes back to
the built-in file, and `--memory off` turns memory off for one session.
Nothing is deleted either way.

**An agent package** gets memory only if its `agent.toml` asks for it. To
use Smritikosh there:

```toml
[[mcp]]
name    = "smritikosh"
command = "/home/asha/src/smritikosh/.venv/bin/smritikosh-mcp"
env     = { SMRITIKOSH_API_KEY = "${SMRITIKOSH_API_KEY}", SMRITIKOSH_USER_ID = "asha",
            SMRITIKOSH_BASE_URL = "http://localhost:8080" }

[memory]
via = "smritikosh"
```

**Privacy.** On the local road nothing leaves your computer. On the cloud
road, each memory goes to OpenAI once, to be turned into numbers. And on a
cloud chat model, whatever Yantra remembers about you is part of every
request, whichever store keeps it.

## When something's wrong

| You see | It means | Do this |
|---|---|---|
| `memory: mcp server 'smritikosh' is not connected` | Yantra couldn't start or reach `smritikosh-mcp` | Is the server up (`curl -s http://localhost:8080/health`)? Is the `command` path in `.yantra/mcp.json` right? |
| `SMRITIKOSH_API_KEY is not set` | The key didn't reach the program | Is the line in the `.env` of the folder you start Yantra from? |
| `403` in an error | The key's account and Yantra's name for you differ | Your Smritikosh user name must be exactly `echo "${YANTRA_USER:-$(whoami)}"` |
| `memory: local, for ...` | Yantra is still on the built-in file | Is `YANTRA_MEMORY=smritikosh` in `.env`? A `--memory` flag on the command line wins over `.env` |
| An error about dimensions when a memory is saved | `EMBEDDING_DIMENSIONS` doesn't match the model | Fix it in Smritikosh's `.env`, then start from an empty database (Steps 3 and 5) |
| `"postgres":"error"` in the health check | The database isn't running | `podman start smritikosh-postgres` |

How it works inside, and why Yantra itself never depends on Smritikosh,
is in [notes/102](notes/102-kept-somewhere-else.md).
