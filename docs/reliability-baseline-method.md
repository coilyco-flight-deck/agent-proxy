# Status and method

Part of [reliability_baseline](reliability_baseline.md).

## Measurement status


**PENDING - not yet measured against the tower.** The harness, its durable JSON
artifact, and the offline scoring tests all landed under agent-proxy#19, but the
engineer container that did that work had **no tailnet reachability to the
tower** (no `tailscaled`, no `WARD_TOWER_OLLAMA` bridge, no SSM credentials), and
both targets require a live tower LLM. So the numbers below are deliberately left
unfilled rather than fabricated - recording an invented percentage is exactly the
failure this milestone exists to fix. Taking the measurement is a single
reproducible command (below) from any tower-reachable host.

Do **not** copy a number into the results table from anywhere but a real harness
run's output or its `--json` artifact.

## How to take the measurement


From a host with the tower reachable, with the proxy running for the `proxy`
target:

```bash
# 1. start the proxy (a second shell); resolves the tower FQDN from SSM if unset
PROXY_TOWER_BASE_URL=http://<tower>:11434 ward exec serve

# 2. run both targets in one pass and write the durable artifact
TOWER=<tower> ward exec reliability -- --target both --turns 6 --json docs/reliability_m2.json
```

`ward exec reliability` runs `scripts/reliability_loop.py`; bare
`ward reliability` also works via ward's unknown-verb fallback. Args ride after
`--`. The tower FQDN is resolved at runtime and never written into the artifact
or this file.

## Run shape


- **Turns**: 6 (default; `--turns N` to change). Odd turns must call the
  `get_line_count` tool; even turns ask for a one-line summary.
- **Context growth**: each turn appends a ~1200-line file-shaped blob (~8k
  tokens), so the accumulated prompt crosses the 32k default `num_ctx` cliff
  within a couple of turns - the condition that produces silent truncation on
  the direct target.
- **Scoring**: `app.resilience.validate_response` decides usable vs garbage
  (empty, malformed tool call, truncation garbage, degenerate repetition), plus
  a harness-only `missed_toolcall` rule for a turn that ignored the tool
  contract. The harness rule is sound where the proxy-side one was not: the
  harness knows out-of-band that the turn required a tool call.
- **Targets**:
  - `direct` - tower `/v1` (`qwen3-coder:30b`), no `num_ctx` (the opencode/crush shape).
  - `proxy` - local proxy, same real tag (`qwen3-coder:30b`), derived `num_ctx`
    injected (issue #32: the `fast-think` alias was retired).
