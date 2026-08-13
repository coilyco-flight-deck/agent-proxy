# Admission rate limits

Part of [proxy.md](proxy.md).

## Why a rate and not just the queue

The bounded queue already gives backpressure once work is accepted, but
backpressure is not a rate. A caller can fill and drain the queue as fast as the
backend serves and nothing bounds the arrival rate itself. Issue #110 asks for a
rate, and for the excess to be **shed with 429** rather than buffered, so a
caller learns immediately instead of waiting behind work it cannot see.

## The knobs

- `PROXY_RATE_LIMIT_PER_SECOND` - sustained admissions per second per logical
  route. Default `1.0`. `0` turns shedding off entirely.
- `PROXY_RATE_LIMIT_BURST` - bucket capacity, the number of requests that may
  arrive back to back before shedding starts. Default `1`.

One token bucket per logical route, so a busy route cannot starve a quiet one.
Changing either setting rebuilds the affected bucket on the next request.

## Burst is the setting that matters

At the shipped default, `rate=1.0` and `burst=1`, two requests arriving in the
same second means the second one sheds. That is the literal reading of "1 per
second", and it is the right sustained number against measured traffic of
0.0047 req/s.

It is also the setting most likely to be wrong for a **tool-using turn**, which
issues its model rounds back to back rather than spread over seconds - the turn
in issue #113 ran six rounds inside 97 seconds, and several of those rounds
followed one another immediately. A deployment serving such a caller should
raise `PROXY_RATE_LIMIT_BURST` to at least the number of rounds a turn can
issue, and leave the per-second rate where it is. The rate bounds sustained
load; the burst decides whether one legitimate turn survives.

## Where it sits in the path

After route resolution, before admission:

- an unknown model still answers 404 without spending a token, so a caller
  probing bad names cannot exhaust the bucket for a real one
- a shed request never occupies the queue it was shed to protect
- `/healthz`, `/readyz`, and `/metrics` are never shed

## What the caller sees

```
429 {"error":{"message":"request rate exceeded for this route, retry shortly",
     "type":"rate_limit_error"}}
Retry-After: 1
```

`Retry-After` is the whole number of seconds until the next token, minimum 1.
The recorded error code is `rate_limited`, distinct from `rate_limit_error`,
which stays reserved for the queue-full path. The counter is
`llm_rate_limited_total` by `logical_model`, and the structured log event is
`request.rate_limited`.

## Testing note

`tests/conftest.py` pins the rate to 0 for the suite, because every other test
fires far above 1/s and would otherwise be testing this instead.
`tests/test_ratelimit.py` turns it back on and is the one place the behaviour is
exercised.

## See also

- [`proxy-request-path.md`](proxy-request-path.md) - where admission sits.
- [`exception-taxonomy.md`](exception-taxonomy.md) - the recorded codes.
