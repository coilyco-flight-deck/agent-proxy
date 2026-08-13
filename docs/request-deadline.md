# Request deadline and abandoned work

Part of [proxy.md](proxy.md).

## The inverted ladder

Issue #112 measured a 24-hour window and found every layer below the caller
allowed to run longer than the caller would wait:

| Layer | Ceiling observed |
| --- | --- |
| `sirens-echo` turn | 180 s |
| `agent-proxy` `POST /v1/chat/completions` | 240 s |
| `litellm` `litellm_request` | 600 s |
| `litellm` `Received Proxy Server Request` | ~1004 s |

The caller gives up first and the abandoned upstream request runs on for another
7 to 13 minutes with nobody to receive its answer. Issue #106 records the same
shape from the other end: litellm generated for 207 seconds after agent-proxy
stopped waiting, then returned 500.

Raising the caller's deadline does not fix this. Whatever the number is, a
caller that goes away should stop costing inference.

## What agent-proxy now bounds

`PROXY_REQUEST_TIMEOUT` is per attempt. With retries and a fallback chain, one
caller request could hold `max_retries + 1` attempts against each backend, so
the per-attempt number never bounded the request.

`PROXY_REQUEST_DEADLINE` is a wall clock for the whole request: queue wait, every
retry, and every fallback. `0` leaves it off, which is the default. Past the
deadline no further attempt starts, and the in-flight attempt is cancelled by
`asyncio.wait_for` - which closes the httpx request, and with it the upstream
connection. The route answers `504` with `request_deadline_exceeded`.

## The caller can shorten it

A caller that knows its own budget sends `X-Request-Deadline-Ms` (or
`X-Request-Timeout-Ms`). The value may only **shorten** the configured deadline.
A caller cannot buy itself more upstream time than the operator allowed, and an
operator ceiling a caller can talk past is not a ceiling.

Set below the caller's own timeout, this closes the connection while the caller
is still listening, instead of leaving the upstream generating for a caller that
has already given up.

## Disconnect still cancels

Both `POST /v1/chat/completions` and `POST /v1/completions` watch for
`http.disconnect` while the work runs and cancel it when the caller hangs up.
The completions surface did not, which was a real gap: it is the same resilience
path and had none of the cancellation.

The body is read to completion before the watcher starts, because both read from
the same ASGI receive channel.

## Status fidelity

Every terminal response stamps `http.response.status_code` on the request span.
An upstream non-2xx does the same on the upstream span and records an error -
issue #106 found a trace where all six agent-proxy spans read `has_error: false`
against an upstream 500, which is why the reported 0.74% error rate could not be
trusted.

## See also

- [`upstream-error-classification.md`](upstream-error-classification.md)
- [`proxy-request-path.md`](proxy-request-path.md)
