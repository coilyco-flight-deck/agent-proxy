---
name: agent-proxy-compare-harness-fit
description: Compare observed Agent Proxy harness and model outcomes using trajectory count, completion, retries, fallbacks, latency, cost, freshness, and source evidence. Use when choosing or reviewing harness and model fit without claiming causal proof or routing authority.
---

# Compare Agent Proxy harness fit

Use the repo-owned read-only query helper to inspect the governed `harness_fit` view.

1. Run `just trajectory-query harness-fit` with optional exact `--harness` and `--model` filters.
2. Compare completion rate only beside trajectory count, retries, fallbacks, latency, cost by currency, access tier, and freshness.
3. State that the current aggregate has no time-window or repo filter. Treat the result as observational evidence, not causal proof or routing authority.

For command behavior and flags, run `just trajectory-query --help`.
