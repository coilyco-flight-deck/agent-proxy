# Configuration

Part of [proxy](proxy.md).

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
* `PROXY_OLLAMA_NUM_PARALLEL` - the backend's `OLLAMA_NUM_PARALLEL` (default **1**).
  ollama divides an injected `num_ctx` across this many slots, so the proxy injects
  `derived_num_ctx * num_parallel` to keep each request's window intact. Set it to
  match the backend; a per-backend override rides in `PROXY_BACKENDS_JSON` as
  `"num_parallel"`. See the coupling section below.
* `PROXY_CONTEXT_TRUNCATION_TOLERANCE` - slack (default **0.15**) that absorbs
  tokenizer drift in the fail-loud delivered-context check, so a prompt that merely
  filled its window is never mistaken for a clip.
* `PROXY_FAIL_ON_CONTEXT_TRUNCATION` - when set, a detected short-context delivery
  502s loud instead of returning the marked short read (default **off**).
* `PROXY_BACKENDS_JSON` / `PROXY_BACKENDS_FILE` - a JSON array of backend specs
  (`{"name","url","dialect"?,"chat_path"?,"num_parallel"?,...}`) that supplies
  transport endpoints separately from logical route data.
* `PROXY_ROUTE_REGISTRY_FILE` - the Deploy-mounted logical route registry.
* `PROXY_ROUTE_UPSTREAM_MODE` - `litellm` for aliases or `direct` for rollback.
* `PROXY_ROUTE_REGISTRY_COMPATIBILITY_MODE` - permits the legacy physical tag
  catalog only when no registry path is configured. Production disables it.
* `PROXY_READINESS_TIMEOUT` - per-dependency timeout for non-generating route
  readiness checks. The default is 3 seconds.
* `PROXY_WORKER_COUNT`, `PROXY_QUEUE_MAXSIZE` - queue / worker sizing.
* `PROXY_MAX_RETRIES`, `PROXY_CIRCUIT_FAIL_THRESHOLD`, `PROXY_CIRCUIT_COOLDOWN` -
  resilience knobs.
* `PROXY_SENTRY_DSN`, `PROXY_OTEL_EXPORTER_OTLP_ENDPOINT` - observability. Both
  degrade to no-ops when unset.
* `PROXY_TRACE_BODIES` - opt-in model I/O capture, defaulting to off. The
  repository implementation captures every request and response body field when
  enabled and fails hard on capture loss. The restricted ser8 deployment and
  live verification completed under
  [issue #77](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/77).
* `PROXY_WARD_SKILL_USE_INPUT` - optional path to a ward reap archive directory
  or a single `skill-usage.json` artifact. When set, the proxy ingests it at
  startup, durably retains metadata-only trajectory observations, and increments
  dashboard-friendly skill counts by skill and harness.
* `PROXY_TRAJECTORY_REQUEST_EMISSION_ENABLED` - offers metadata-only request
  action and terminal execution events to the bounded trajectory queue. It
  defaults off until `PROXY_TRAJECTORY_DB_PATH` points at durable mounted
  storage.

How the injected `num_ctx` is derived, and the `OLLAMA_NUM_PARALLEL`
coupling, are in [proxy-num-ctx.md](proxy-num-ctx.md).
