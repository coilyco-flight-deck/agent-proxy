# Running, proving, and metrics

Part of [proxy](proxy.md).

## Running and proving locally


```
ward exec sync                                             # uv sync (installs app + dev)
PROXY_TOWER_BASE_URL=http://<tower>:11434 ward exec serve  # proxy on 127.0.0.1:8080
ward exec test                                             # offline suite, tower not required

# compatibility-mode proof with the proxy and tower reachable:
TOWER=<tower> MODEL=qwen3-coder:30b ward exec proof        # 32767 (direct) vs num_ctx-injected (proxy)
TOWER=<tower> MODEL=qwen3-coder:30b ward exec reliability -- --target both --turns 6 --json reliability.json
```

The invocation is `ward exec <verb>`, defined in `.ward/ward.yaml`. Bare
`ward <verb>` also resolves (ward's unknown-verb fallback rewrites it to
`ward exec <verb>`), but the explicit `exec` form is unambiguous and is what
these docs use. Script arguments ride after a `--` so ward hands them to the
verb rather than parsing them itself.

`scripts/truncation_proof.py` reproduces the leg-01 truncation test through the
proxy. `scripts/reliability_loop.py` is the leg-05 reliability harness: it scores
a context-growing, tool-using loop with the proxy's own `validate_response` and
emits a reliability percentage and failure histogram. `--target both` runs the
`direct` baseline and the `proxy` after in one pass and prints the comparison;
`--json PATH` writes a durable, machine-readable artifact (stable schema, no FQDN
inside) so a future before/after check re-runs the same command and diffs the
JSON. The measured result and its reproduction command live in
`docs/reliability_baseline.md`. Both scripts resolve the tower via `TOWER` /
`PROXY_TOWER_BASE_URL` / SSM and never write the FQDN into a file.

## Metrics


`llm_requests_total`, `llm_queue_depth`, `llm_queue_rejected_total`,
`llm_retries_total`, `llm_fallbacks_total`, `llm_circuit_state`,
`llm_truncation_avoided_total`, `llm_validation_failures_total`,
`llm_context_truncated_total`, `llm_prompt_tokens`, `llm_upstream_latency_seconds`,
`ward_skill_use_total`.

## Out of scope here


Deploy to kai-server (leg 09), the Caddy front selector and 2-replica manifests
(leg 09), and the capability phases (tool injection, MCP credential passthrough,
RAG, upskilling). This leg stays tightly the reliability proxy.
