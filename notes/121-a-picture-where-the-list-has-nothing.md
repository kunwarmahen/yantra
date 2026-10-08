# 121 — A picture where the list has nothing

[Note 120](120-thirteen-tasks-on-a-phone.md) left one task undone on
both local models: renaming the phone. Its setting lives on Settings'
*About phone* page, which counts "Up time" every second, so Android
never describes it. The model got "can't be read", and nothing else.
The same page decided the version question by luck.

Sparsh can now attach a screenshot to such a screen
([its note 05](https://github.com/kunwarmahen/sparsh/blob/main/notes/05-a-picture-where-the-list-has-nothing.md)):
`sparsh mcp --shots`, a picture only where the list has nothing. This
note is Yantra's half: passing an MCP server's picture to the model at
all, and deciding when.

## Pictures from an MCP server

Until now `mcp.py` flattened every tool result to text. An image block
became `[1 non-text content block(s) omitted]`. Yantra already had the
other half: `read_image` returns a `ToolOutput` with images, and the
loop carries them to the model right after the results, on both wire
dialects ([note on read_image](../src/yantra/tools/read_image.py)).

So the change is small. `call_tool_full` keeps a result's images, and
`MCPToolWrapper.images` says whether to. **OFF FOR EVERY SERVER UNLESS
WHOEVER WIRED IT SAYS SO.** A server's pictures reaching the model is a
decision about that model (can it see?) and about the person's data
(should this leave the machine?), and the server knows neither. So the
default stays as it was: noted, not sent. Only kinds a provider takes
(png, jpeg, gif, webp) and only up to 5 MB get through; anything else is
noted as before.

## When the phone's pictures go to the model

`YANTRA_PHONE_SHOTS` decides, in `sparsh_link.shots`:

| setting | local model that can see | local model that can't | cloud model |
|---|---|---|---|
| `auto` (default) | yes | no | no |
| `on` | yes | yes | yes |
| `off` | no | no | no |

**A CLOUD MODEL GETS NO PICTURE OF THE PHONE UNLESS THE PERSON SAYS SO.**
That was decision 4 when Sparsh was planned, and here is where it's
kept. A phone's screen is messages, names and one-time codes. The
numbered list goes to a cloud model already, but the list is what the
phone chose to describe; a picture is everything on the glass.

"Can see" is asked of the model, not guessed from its name: Ollama's
`/api/show` lists `"vision"` among its capabilities. A server that
doesn't answer that question (another local server, no answer in three
seconds) counts as can't. A picture sent to a model that can't take one
fails the turn, which is worse than no picture.

**ASKED AGAIN ON EVERY CALL.** The model can change mid-session
(`/model`, `/provider`, the page). A session that started on Ollama and
moved to a cloud model must stop sending pictures at once. So the wrapper's
`images` is a function, asked on each call with the provider and model
in use now; the answer is kept only for the model it was asked about.
The other direction doesn't hold: Sparsh was started without `--shots`
for a cloud model, and a switch to a local one doesn't restart it. That
side errs toward fewer pictures, and the startup line says what was
decided:

```
sparsh: 9 tool(s); phone emulator-5554 (sdk_gphone64_x86_64); screenshots on (local model) -- via …/sparsh
sparsh: 9 tool(s); phone emulator-5554 (…); no screenshots to a cloud model (YANTRA_PHONE_SHOTS=on to allow) -- via …
```

An older Sparsh that doesn't name `shots` in its status gets no flag,
and the line says to update it.

The phone prompt layer gains one sentence when pictures are on: a
screen that can't be read as a list comes with a screenshot; read it,
but only numbered things can be tapped.

## `auto:PATH`

Sarathi finds Sparsh and starts Yantra's page. Passing `--sparsh PATH`
would turn it **on**: tools even with no phone attached, which undoes
the dormant rule from note 119 (no phone at the start, no tools, until
the person says to use one). `YANTRA_SPARSH=auto:/path/to/sparsh` (or
`--sparsh auto:/path`) means auto with that program instead of the one
on `PATH`. Sarathi passes that.

## Live receipt

`gemma4:26b` on Ollama, the emulator on Settings' About page, asked for
the IMEI (fifteen digits nobody can guess):

```
sparsh: 9 tool(s); phone emulator-5554 (sdk_gphone64_x86_64), emulator-5556
(sdk_gphone64_x86_64); screenshots on (local model) -- via .../sparsh
╭─ mcp__sparsh__look ──────────────────────────────────────────────╮
│ App: com.android.settings                                        │
│ (this screen can't be read as a list: ...)                       │
│ (A screenshot of this screen is attached: ...)                   │
│ [1 image(s) attached to this result]                             │
╰──────────────────────────────────────────────────────────────────╯
The IMEI number shown on the phone's screen is 867400022047199.
── end_turn · 5179 in / 286 out · 2 iteration(s)
```

Right, from one look.

Note 120's two About-page tasks, with `examples/phone_trial.py --shots`
(a `pictures` count per row):

| `gemma4:26b` | without pictures (note 120) | with pictures |
|---|---|---|
| which Android version | done in round 1, not in round 3 | done, 2 of 2 |
| rename the phone | not done | not done, 0 of 4 |

The version is now read from the first visit instead of by luck. The
rename still can't be done: the picture shows "Device name", but there
is no number to tap it by, and Sparsh doesn't tap by position (if it
ever does, each such tap waits for the person's yes, the spot marked on
the picture). In one run gemma renamed the phone's Bluetooth name instead
and said the phone was renamed. Reading the phone afterwards caught it.

## What the tests hold

`tests/test_sparsh_link.py::TestScreenshots`: a local model that can see
gets `--shots` and the picture; a cloud model doesn't, until
`YANTRA_PHONE_SHOTS=on`; a switch to a cloud model mid-session stops
them; a model that can't see, or an old Sparsh, gets none; `off` and an
unknown word are no. `auto:PATH` stays dormant with no phone.
`tests/test_mcp.py`: images kept only when asked, and only kinds a
provider takes. 2717 tests before, 2725 after (and 1 skipped, as before).

## What is not here yet

* Pictures from other MCP servers. The switch exists (`images`), but
  nothing turns it on for them: each needs the same two answers (can
  the model see, may the picture leave).
* The cloud road with pictures, untried, like the rest of the cloud
  trial.
* Acting on what only the picture shows.
