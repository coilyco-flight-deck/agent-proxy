# Saturation stickiness

Part of [proxy.md](proxy.md).

Issue #111 asked for an automatic fallback when a model is *persistently*
unavailable, and for that choice to *stick*. It also said both words needed
defining. This is the threshold half of that answer. The explicit signal - a
caller telling the proxy to prefer the fallback outright - is still open on
that issue and needs a product decision.

## The two definitions

Saturation counts and cools on its own terms, because a busy backend is not a
broken one. `PROXY_SATURATION_THRESHOLD` (default 2) is the consecutive count
that opens the breaker, lower than the failure threshold because each saturation
costs a full slow-path wait. `PROXY_SATURATION_COOLDOWN` (default 900) is how
long it sticks, because the condition behind it is a play session rather than a
blip and a 30-second cooldown would re-probe a busy GPU roughly 120 times an
hour. The count is consecutive, so one good turn clears it, and half-open
probing still recovers without a human.

## What is still open

Nothing here lets `sirens-echo` *tell* the proxy to switch. That was the
literal ask, and the two shapes for it - a request header or a small admin
pin endpoint - differ enough that #111 carries the choice rather than this
document. The thresholds below are what works without one.

## See also

- [`saturation-failover.md`](saturation-failover.md) - what counts as saturated.
