# 131 — Cleared at once

When you clear a conversation, Yantra looks back over it once for
things worth remembering about you ([note 101](101-looking-back.md)).
That look is a whole model call. It used to happen *before* the clear:
you typed `/clear`, or pressed *clear* on the page, or sent `/new` in a
chat through Dvara, and then you waited. On a local model that was half
a minute or more of nothing, for a question you hadn't asked.

The terminal had a second wait. After every answer that took real
work, it asked the model whether the way it found was worth saving as a
skill ([note 96](96-solve-it-once.md)), and then asked *you*, before
you could type again. The page had already stopped doing that
([note 113](113-after-the-answer.md)). The terminal hadn't.

Now neither waits. The clear happens at once, and the look runs
behind whatever you do next.

## A copy, set aside

Clearing throws the conversation away, so the look needs something to
read. Before the history goes, Yantra copies the part of it that hasn't
been looked at yet and puts the copy aside. Then it clears. The look
reads the copy, not the live conversation, so it can take as long as it
needs while you start the next one.

The copy is the only thing that changes about the look itself. It is
the same one call, with the same rules: what you said, not what the
model thought; secrets scrubbed first; nothing kept without a yes.
Reading a copy also leaves the new conversation alone. Yantra keeps
count of how much of a conversation it has looked at, and a look over
an old copy must not mark any of the new one as read.

## Where what it finds goes

Nothing it finds turns into a question in your way:

* **In the terminal**, a line appears before your next prompt:

  ```
  · a thing worth remembering about you -- /keep to see
  · found a skill worth keeping: greet-someone -- /keep to see it
  ```

  `/keep` shows each one: a skill as its usual save question, a site's
  guide, then the facts, *keep [a]ll, numbers, or [N]one*. Typing
  `/keep` while the look is still running waits for it, with a spinner.
  Ctrl-C stops it.

* **On the page**, they go to the *to keep* chip, like everything else
  a look finds ([note 113](113-after-the-answer.md)).

* **Through Dvara** (Telegram, the home page's chat), `/new` answers
  straight away. If the look finds something, it comes a little later
  as a message of its own, numbered for `/remember`, the same way the
  look after a quiet half hour does. If it finds nothing, nothing is
  sent.

`--reflect auto` still keeps what it finds without asking, and says so
in one line. `--reflect off` sets nothing aside.

## One model, one call at a time

This is the catch, and it's a local-model catch. Ollama on one GPU
answers one request at a time. A look running in the background doesn't
make the model faster. If you send a message while the look is still
going, your message queues behind it at the model, and the wait just
moves.

So the terminal and the page use the rule the page's skill look
already had: **your message comes first.** Whatever you type next stops
the look. It reads its answer as a stream, word by word, and checks
between words, so it can stop part way through, and closing the stream
tells a local model to stop generating. The copy isn't lost: it stays
set aside, and the look over it runs again the next time the model is
free, after your next answer, or at `/quit`.

A few things wait for the look instead of stopping it. `/keep` and
`/quit` want what it finds. `/clear` only adds another copy for it to
read. A skill look already running when you `/clear` read its turn
when it started, so the history going doesn't touch it, and it finishes.

On a cloud model none of this matters much. The look and your next
turn can run side by side, and the rule costs nothing.

Dvara does it differently. It serves several people from one model,
so queueing is how it always works there. Its look behind `/new` just
takes its place in the queue and isn't stopped.

## Receipt

All three on `gemma4:12b` through Ollama, with one question said in
passing: *"I'm flying out of RDU to Denver in March. One line: what
should I pack?"*

**Terminal**, driven through a real pseudo-terminal:

```
> /clear
history cleared
> /keep
worth remembering about you, for later conversations?
  1. fact: Lives near RDU (Raleigh-Durham International Airport).
  2. preference: Prefers concise, one-line answers.
keep [a]ll, numbers (1 3), or [N]one > a
remembered 2 (#1, #2 -- from the next conversation on; /memory to see or forget)
```

`/clear` came back in 0.00s. `/keep`, typed straight after, had both
facts 6.7s later. That's the look finishing, not a wait you'd see
unless you went straight there.

**The page**: the clear request answered in 0.01s, the look finished
31.8s later, and the tray held *Lives near RDU (Raleigh-Durham
International Airport)*.

**Dvara**: `/new` answered in 0.02s with its usual *"Starting fresh…"*.
The notice came 42.0s later:

```
greeter: Looking back over the conversation we just ended, I noticed this about you:
1. Lives near RDU (Raleigh-Durham International Airport).
2. Prefers concise, one-line responses.
Send /remember with the numbers to keep them (/remember 1 2), or /remember all. …
```

Before this change, that 42 seconds came between `/new` and *"Starting
fresh"*.

The tests (`tests/test_cleared_at_once.py`) hold a scripted model in
the middle of its answer and check that `/clear` and the page's clear
come back while it's still held. They also check that a message sent
meanwhile stops the look and leaves the copy set aside, that the next
free moment looks at it, that the skill question waits for `/keep`, and
that a look over a copy marks none of the new conversation as read.

## What was deliberately not built

* **Printing the moment the look finishes.** The terminal reads a line
  with plain `input()`. Text printed from another thread while you
  type lands in the middle of what you're typing. So the news waits
  for the next prompt. If you sit at an empty prompt after `/clear`,
  you won't hear about it until you press Enter or type `/keep`.
* **Keeping the terminal's finds past `/quit`.** The page's tray is
  saved to disk. The terminal's isn't: `/quit` waits for the look and
  asks about everything it found before it exits, so there's nothing
  left over to keep.
* **Stopping Dvara's look for the next message.** Dvara serves many
  people from one model, so one person's next message has no claim to
  go first. Its look waits its turn like everyone's.
* **A skill look over a cleared conversation.** The skill look reads
  the last turn. After a clear there's no last turn to read, and a
  copy of one isn't something anyone asked to be made into a skill.
