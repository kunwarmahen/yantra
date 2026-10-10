# 124 — A question with no step behind it

[Note 119](119-a-phone-and-what-asks.md) made `confirm` the one way
through a phone's held step, asked every time, and [note
123](123-a-tap-you-say-yes-to-by-looking.md) gave its card a picture.
Both assumed the card is the question. A real chat over Telegram showed
what happens when the model asks a different way.

## "Shall I send it?"

The person, on Telegram, to Sarathi's `minder` agent on
`qwen3.8-64k:latest`: *"Text 555-0123 from my phone: hello"*. The model
opened Messages, typed the text, tapped Send, and Sparsh held the tap.
The model then did what its own prompt tells it to do before making a
schedule: it asked in words and waited.

```
The message "hello" is typed into the conversation with 555-0123, ready
to go. Sending it will actually deliver the SMS — shall I send it?
```

The turn ended there, and Dvara stops a turn's MCP servers when it ends,
so the held step went with it. The person answered *"Yes"*, and the
model's `confirm` named a hold that a new Sparsh had never heard of. The
card for it still went to the person:

```
minder wants to run mcp__sparsh__confirm:

Do a step on the phone that was held for your yes (h48b2e5).
  SPARSH CANNOT DESCRIBE IT: Not done: no step is waiting under 'h48b2e5' (a hold lasts 10 minutes) -- look again and ask again
```

They pressed yes. Nothing happened, because nothing could. The model
then did the step again and asked properly, and the person was asked a
third time for one message.

## Not asked at all

**A QUESTION WITH NO STEP BEHIND IT IS NOT ASKED.** A tool may now offer
a `nothing_to_ask` hook beside `card_picture`. Before a request is built,
both agent loops call it, and if it returns a sentence, the call is
refused to the model with that sentence and nobody is asked.
`sparsh_link` wires it on `confirm`: it asks Sparsh to describe the hold,
and only "no step is waiting" counts as gone. Every other failure still
asks, as the card always did ("SPARSH CANNOT DESCRIBE IT" stays, for a
hold that exists but can't be put into words). A hook that raises asks
as usual, so a broken check can never stop a real yes.

The model reads:

```
Not asked, and nothing was done: … no step is waiting under 'h9' (a hold
lasts 10 minutes, and only while these tools run) … Do the step again,
and when it is held call confirm in that same answer -- the confirm is
the question; don't ask in words first.
```

That fixes the card, but the habit is the cause, so three places now say
the same thing. Sparsh's held message says *"Call confirm … now, in this
same answer: that is how they are asked. Do not ask them in words
first"*. Yantra's phone prompt layer says the confirm call *is* the
question, answered with a button, and that a reply in words comes too
late. And Dvara's `phone` example says it in its own words. Sarathi's
`minder` prompt asks for a yes in the person's own words before a
schedule, which is right for schedules, so a person giving that agent
the phone adds one paragraph saying the phone is different (Dvara's
tutorial, §18, has it).

## Live, on a Nexus 6P

The same words to the same agent and model, through `dvara say` (the
terminal standing in for the chat), with the new prompt and the hook:

```
minder wants to run mcp__sparsh__confirm:
  Do this on the phone?
On the phone 84B7N16128001616: tap item "Send SMS" in com.google.android.apps.messaging -- held because it says "send".
…
4 field "hello" [tap, type, focused]
…
approve? [y/N] [end_turn · $0.0000 · 16256in/442out · run 299e377bc14f]
You declined the send, so no text was sent to 555-0123. The message "hello" is still sitting in the Messages app ready to go if you change your mind
```

One question, as a card, in the same answer. No "shall I?" in words.

The other half of that chat was a rename that stopped at the lock
screen. That one is Sparsh's: the screen timed out while the model
thought, and Sparsh now keeps it on while an agent works
(its note 09).

## What the tests hold

`tests/test_sparsh_link.py`: a confirm for a hold Sparsh no longer has
reaches no gate, comes back to the model as an error that says to
confirm in the same answer, and a hold that is there is still asked; a
hook that raises, or no hook, means ask as usual. 2741 tests pass.

## Not here yet

* **Holds that outlive a turn.** Sparsh could keep them on disk, so a
  yes in words in the next message still found its step. That would make
  the words a second way to say yes, beside the card, and the card is
  the one that shows exactly what will happen. Not built on purpose.
