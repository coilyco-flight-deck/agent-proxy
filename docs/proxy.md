# The reliability proxy (phase 1)

This is the walkthrough for the phase-1 reliability proxy built per aosh leg
`04-headless-proxy-build.md` against the locked leg-02 architecture. It covers
the request path, the `app/` modules, configuration, how to run it, and how to
prove the core `num_ctx` fix. The design is locked upstream and not re-argued
here - see the source-of-truth pointers in the README.

## Request path

A harness sends an OpenAI-shaped request carrying the **real ollama tag** (e.g.
`qwen3:4b`) as `model` - no logical indirection (issue #32). The proxy:

1. **resolves** the tag against the backend catalog (`app/models.py`): it reads
   the backend's `/api/tags` once (cached), confirms the tag exists, and derives
   a safe `num_ctx = min(context_length, ceiling) - headroom` from the model's
   *real* `context_length`. An unknown tag (absent from a catalog it read) is a
   404; if the backend is unreachable the tag is served fail-open with a
   conservative window so a transient outage surfaces as a 502, not a false 404.
   The ordered backend chain (tower plus any configured fallbacks) rides along.
2. **guards the context budget** (`app/analysis.py`): counts prompt tokens and,
   if the prompt exceeds `num_ctx - headroom`, trims the oldest non-system turns,
   always keeping the system framing and the live turn. Increments
   `llm_truncation_avoided_total` when it actually drops a turn.
3. **enqueues** the job on a bounded `asyncio.Queue` and awaits its future
   (`app/queue.py`). A full queue returns HTTP 429 (`llm_queue_depth`,
   `llm_queue_rejected_total`).
4. a **worker** dispatches under the resilience policies (`app/resilience.py`):
   walk the fallback chain, retry each live backend with backoff, and validate
   every response. Transport errors trip a per-backend circuit breaker; a merely
   bad generation is rerolled but does not.
5. the **upstream client** (`app/upstream.py`) forwards to the backend's native
   API. Ollama backends use `/api/chat` with `options.num_ctx` injected. OpenAI
   backends like the llama-server gpt-oss target use `/v1/chat/completions`
   without injection, then normalize their response back to the proxy's
   canonical shape.
6. the result is shaped back to the OpenAI schema (`app/main.py`). Reasoning-model
   thought is surfaced as `reasoning_content`.

Streaming requests take the same fallback chain and circuit breaker but skip the
reroll (a token stream cannot be validated after the fact), so a harness that
wants the full resilience guarantee uses the non-streaming path.

## Endpoints

* `POST /v1/chat/completions` - streaming and non-streaming.
* `POST /v1/completions` - modeled as a single user turn so it rides the same
  resilience path.
* `GET /v1/models` - lists the tags actually present on the backend (live from
  `/api/tags`), not a static alias list.
* `GET /healthz` - liveness for Caddy / k8s probes.
* `GET /metrics` - prometheus exposition.

## Validation

A response is *usable* when it is non-empty, any emitted tool call has parseable
arguments, it does not hallucinate an unsupported "I did the thing" claim, and
it is not degenerate repetition. Three deliberate refinements, all surfaced by
live testing against the tower:

* a legitimately short word answer (`OK`, `42`, `no`) is **not** truncation
  garbage - only a 1-3 char *non-word* reply (a stray symbol) is.
* a first-person completion claim like "I have filed the issue" is rejected if
  the response has no tool evidence behind it, so the router can kick the turn
  back instead of trusting a hallucinated done-state.
* a reasoning model that emitted `thinking` but ran out of token budget before
  final content did real work - it is surfaced as a length-limited response, not
  rerolled into a 502.

## Configuration

All settings read from the environment with prefix `PROXY_` and fall back to AWS
SSM for the tower FQDN and secrets (`app/config.py`). Nothing is hardcoded and no
secret is committed. Key knobs:

* `PROXY_TOWER_BASE_URL` - the primary ollama base URL. If unset, the tower FQDN
  resolves from SSM `/coilysiren/kai-tower-3026/tailnet-fqdn` at boot. This is the
  backend whose `/api/tags` is the catalog source of truth.
* `PROXY_NUM_CTX_CEILING` - the VRAM-safe upper bound on the injected `num_ctx`
  (default **49152**). A model advertising a huge window (`qwen3:4b` = 262144)
  never allocates more KV cache than the tower can carry.
* `PROXY_NUM_CTX_HEADROOM` - tokens reserved below the ceiling for the completion
  (default **1024**).
* `PROXY_BACKENDS_JSON` / `PROXY_BACKENDS_FILE` - a JSON array of backend specs
  (`{"name","url","dialect"?,"chat_path"?,...}`, no tag - the tag comes from the
  request) to supply a fallback chain beyond the single built-in tower backend.
* `PROXY_WORKER_COUNT`, `PROXY_QUEUE_MAXSIZE` - queue / worker sizing.
* `PROXY_MAX_RETRIES`, `PROXY_CIRCUIT_FAIL_THRESHOLD`, `PROXY_CIRCUIT_COOLDOWN` -
  resilience knobs.
* `PROXY_SENTRY_DSN`, `PROXY_OTEL_EXPORTER_OTLP_ENDPOINT` - observability. Both
  degrade to no-ops when unset.
* `PROXY_TRACE_BODIES` - opt-in request/response body capture for local OTLP
  backends. Defaults to off so exported spans and logs stay metadata-only.

### Auto num_ctx from the model's real context window

The proxy no longer guesses `num_ctx` from a hand-maintained table (issue #32).
Ollama's `/api/tags` reports each model's real `context_length` in
`details.context_length` (confirmed live: `qwen3:8b` = 40960, `qwen3:4b` =
262144). The proxy reads it once (cached), and injects

```
num_ctx = min(context_length, PROXY_NUM_CTX_CEILING) - PROXY_NUM_CTX_HEADROOM
```

so a model rides its own real window up to the VRAM-safe ceiling. With the
defaults (ceiling 49152, headroom 1024): `qwen3:8b` -> 39936, `qwen3:4b` ->
48128. The **caller can never override `num_ctx`** - upstream forces the derived
value even if a client sends its own - which is the whole point of the proxy.

The larger litellm-as-core re-core that supersedes this routing layer entirely
is tracked in `coilyco-bridge/agentic-os-hardware#25` and is compatible with this
phase-1 change.

## Running and proving locally

```
ward exec sync                                             # uv sync (installs app + dev)
PROXY_TOWER_BASE_URL=http://<tower>:11434 ward exec serve  # proxy on 127.0.0.1:8080
ward exec test                                             # offline suite, tower not required

# with the proxy running and a tower reachable (pass a real ollama tag via MODEL):
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
`llm_prompt_tokens`, `llm_upstream_latency_seconds`.

## Out of scope here

Deploy to kai-server (leg 09), the Caddy front selector and 2-replica manifests
(leg 09), and the capability phases (tool injection, MCP credential passthrough,
RAG, upskilling). This leg stays tightly the reliability proxy.
