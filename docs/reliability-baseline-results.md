# Results and interpretation

Part of [reliability_baseline](reliability_baseline.md).

## Results


Fill from the harness output / `--json` artifact of a real run. One row per
target; the histogram sums to the turn count.

| Target | Model           | Turns | Usable | Reliability | Failure histogram |
|--------|-----------------|-------|--------|-------------|-------------------|
| direct | qwen3-coder:30b | _pending_ | _pending_ | _pending_ | _pending_ |
| proxy  | qwen3-coder:30b | _pending_ | _pending_ | _pending_ | _pending_ |

Baseline-to-after delta (proxy reliability minus direct reliability): _pending_.

### Failure histogram reasons

The reason labels the histogram can contain, all emitted by the scoring path:

- `ok` - usable response.
- `empty` - no content, no tool call, no thinking.
- `missed_toolcall` - text validated but the required tool call was absent.
- `malformed_toolcall` - a tool call whose arguments do not parse.
- `truncation_garbage` - a 1-3 char non-word reply (the leg-01 truncation tell).
- `repetition` - degenerate decoder loop.
- `upstream_5xx` / `http_<code>` - backend HTTP error.
- `timeout` - transport error or timeout reaching the endpoint.

## Artifact schema


`--json PATH` writes:

```json
{
  "harness": "reliability_loop",
  "generated_at": "<iso8601, seconds>",
  "run_shape": {"turns": 6, "blob_lines": 1200, "tool_rule": "...", "scored_by": "..."},
  "results": {
    "direct": {"target": "...", "model": "...", "turns": 6, "usable": N,
               "reliability_pct": P, "failure_histogram": {...}, "turns_detail": [...]},
    "proxy":  { ... }
  }
}
```

The schema is stable across runs and the histogram is sorted, so two artifacts
diff cleanly for a before/after check. Commit the artifact (e.g.
`docs/reliability_m2.json`) alongside the filled table above when the
measurement is taken.

## Interpreting it


M2 ("resilience core measured") is complete when this table carries real numbers
and the delta shows the proxy lifting reliability over the direct baseline. Until
then M2 is **harness-ready, measurement-pending**: the code, artifact, and tests
exist and are proven offline, but the milestone number is not yet recorded.
