# Rate limiting: a working reference

Notes assembled for people who have to put a limiter in front of
something and would rather not discover the interesting cases in
production. Opinionated where the evidence is clear, hedged where it is
not.

## Why limit at all

Three distinct reasons, and they pull in different directions. Conflating
them is how a limiter ends up serving none of them well.

### Protecting a scarce resource

The database has a connection pool, the downstream API has a quota, the
worker fleet has so many cores. A limiter here is a valve: its job is to
keep arrival rate under service rate so queues stay bounded. The number
comes from measurement, not from policy.

### Fairness between clients

One noisy client should not be able to consume the capacity of a hundred
quiet ones. This is a different job: even a system with capacity to spare
wants it, and the number comes from how many clients you have and how
much of the whole any one of them may hold.

### Billing and plan enforcement

"Your plan includes 10,000 requests a day." Here the number is a
commercial decision and the limiter is an accountant. Treating this as a
protection limit is a category error — the system is usually perfectly
capable of serving request 10,001.

## The algorithms

### Fixed window

Count requests in the current clock minute; reset at the boundary. One
counter per client, trivially cheap, and wrong at the edges: a client
can send its whole allowance in the last second of one window and again
in the first second of the next, producing twice the intended rate
across a two-second span.

Fine for billing. Poor for protection.

### Sliding window log

Keep a timestamp per request and count those inside the trailing window.
Exact, and the memory cost is proportional to the limit — at a thousand
requests a minute per client, times a hundred thousand clients, this is
no longer a small number.

### Sliding window counter

Interpolate between the previous window's count and the current one,
weighted by how far into the current window you are. Approximate, off by
a few percent under bursty traffic, and a small constant per client.
This is the usual right answer for HTTP APIs.

### Token bucket

A bucket holds up to `burst` tokens and refills at `rate` per second;
each request takes one. Allows a burst up to the bucket size and then
settles to the steady rate — which matches how real clients behave, since
traffic arrives in clumps.

Two knobs rather than one is the feature, not the complexity: `rate` is
the sustainable throughput and `burst` is how much clumping you tolerate.

### Leaky bucket

Requests enter a queue that drains at a fixed rate; a full queue rejects.
Equivalent to token bucket for admission decisions, and different in what
it does to latency — a leaky bucket *delays* where a token bucket
*refuses*. Delay is worse than refusal for anything a human is waiting on.

### Concurrency limits

Not a rate at all: a cap on requests in flight. Often the limit that
actually matters, because what exhausts a connection pool is
simultaneity, not throughput. A system can happily serve a thousand
requests a second and fall over at fifty concurrent ones.

Worth having alongside a rate limit rather than instead of it.

## Where to put it

### At the edge

Cheapest to run, coarsest in what it knows. An edge proxy can limit by IP
and by API key and knows nothing about which endpoint is expensive.

### At the service

Knows what the request costs and can weight accordingly — a search that
scans a million rows should not cost the same token as a health check.
Costs a round trip before the rejection, which is exactly the round trip
you were trying to avoid under load.

### Both

The usual production answer. A coarse edge limit that stops obvious
abuse cheaply, plus a service-level limit that understands cost.

## Distributed counting

### The shared-store approach

Every instance increments a counter in Redis or equivalent. Correct, and
it puts a network round trip in the path of every request plus a hard
dependency on the store being up.

Decide in advance what happens when the store is unreachable. Failing
open means an outage in the limiter becomes unlimited traffic; failing
closed means an outage in the limiter becomes a total outage. Both are
defensible and the choice must be explicit.

### Local buckets with a global budget

Each instance gets `limit / instance_count` and enforces locally. No
round trip, no dependency, and wrong whenever traffic is unevenly
balanced across instances — which it always is.

### Approximate consensus

Instances enforce locally and reconcile asynchronously, converging on the
global number within a few seconds. More moving parts, and the right
trade at high request rates where a round trip per request is not
affordable.

## What to send back

### The status code

`429 Too Many Requests`. Not `503`, which means the service is broken
rather than the client being ahead of itself, and not `403`, which means
the client will never be allowed to do this.

### Retry-After

The single most useful header, and the most frequently omitted. Without
it every client invents its own backoff, and the inventions are usually
"retry immediately, forever".

Send seconds, not a date, unless the wait is long enough that clock skew
stops mattering.

### The limit headers

`RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset` let a
well-behaved client pace itself rather than discovering the wall. Send
them on successful responses too — a client that only learns its budget
by exceeding it cannot avoid exceeding it.

### The body

Say which limit was hit and what it is. "Too many requests" tells a
developer nothing; "per-key limit of 100/min exceeded; resets in 34s"
tells them what to change.

## What a client should do when throttled

### Back off exponentially, with jitter

Double the wait on each attempt, and randomize it. Without jitter, every
client throttled in the same second retries in the same second, and the
limiter's job becomes harder rather than easier — a thundering herd the
limiter itself created.

### Honour Retry-After over your own schedule

If the server said 30 seconds, waiting 30 seconds is both polite and
faster than guessing.

### Give up eventually

A retry loop with no ceiling is an outage amplifier. Cap the attempts,
surface the failure, and let something above decide.

### Do not retry non-idempotent requests blindly

A throttled `POST` may or may not have had an effect. Retrying it needs
an idempotency key, or it needs not to be retried.

## Testing a limiter

### The boundary

Send exactly the limit, then one more. Both outcomes must be the
documented ones, and the "one more" must be rejected rather than merely
slow.

### The window edge

For anything window-based, send a burst across a boundary and assert on
the rate over the whole span, not per window. This is the test that
catches fixed-window doubling.

### The store being down

Kill the shared counter and assert on whichever of open or closed you
chose. An untested failure mode is a decision nobody has actually made.

### Clock skew

Instances disagree about the time by tens of milliseconds at best. Any
scheme that compares timestamps across instances needs a test with a
deliberately skewed clock.

## Numbers people actually use

Public read APIs commonly sit between 60 and 600 requests per minute per
key. Write endpoints an order of magnitude lower. Authentication attempts
lower still, and measured per account rather than per IP, because the
attack you care about targets one account from many addresses.

These are starting points to measure against, not defaults to ship.
