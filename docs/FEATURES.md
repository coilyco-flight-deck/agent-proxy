# Features

The living feature inventory for `agent-proxy`, grouped by phase. Phase 1 - the locked reliability proxy - is built and runs locally, with the core `num_ctx` fix proven end to end against the live tower. The later phases are planned capability work and are not implemented until their phase opens (see `docs/ROADMAP.md`).

Status legend used below: **planned** means specified but not built, **building** means in progress, **landed** means implemented and verified.

## Phase 1 - reliability proxy

The locked scope. Each feature maps to an aosh leg `04-headless-proxy-build.md` build step or a leg `02-reference-architecture.md` component.

* **Skeleton + observability** - landed - the `app/` tree with obs wired before any logic: structlog JSON, prometheus `/metrics`, OpenTelemetry tracer, Sentry init from config, and `/healthz` returning 200. (leg 04 step 1, leg 02 observability component)
* **Backend registry** - landed - a logical-model table (`app/models.py`) mapping each name to a per-model `num_ctx` and an ordered fallback chain of `{name, url, ollama_tag}` backends, overridable via `PROXY_MODELS_JSON` / `PROXY_MODELS_FILE`. (leg 04 step 2, leg 02 upstream client)
* **num_ctx injection** - landed - the httpx client (`app/upstream.py`) forwards to the backend's native ollama `/api/chat` with `options.num_ctx` injected, and the caller can never override it. Proven end to end against the live tower: a 55k-token request to `fast-think` returns `prompt_eval_count=49151`, not 32767 (`scripts/truncation_proof.py`). The highest-value fix. (leg 04 step 2, leg 02 num_ctx injection)
* **In-memory queue + worker pool** - landed - a bounded `asyncio.Queue` and worker pool (`app/queue.py`) as the resilience core. The route enqueues and awaits a future, a worker dispatches, backpressure returns 429 when full, and `llm_queue_depth` is exported. (leg 04 step 3, leg 02 in-memory queue)
* **Response validation** - landed - reject empty completions, malformed tool-call JSON, and degenerate repetition before returning; a legitimately short word answer ("OK") is never rerolled. (leg 04 step 4, leg 02 resilience policies)
* **Self-verification guard** - landed - a lightweight semantic check rejects assistant claims that it already did an action when there is no tool evidence behind the claim, so a router can kick the turn back instead of trusting a hallucinated "done". (issue #4)
* **Retry with backoff** - landed - a capricious single bad generation becomes a reroll on the same live backend, not a user-visible failure. Increments `llm_retries_total`. (leg 04 step 4)
* **Fallback chain** - landed - on transport failure, advance to the next backend in the logical model's chain. Increments `llm_fallbacks_total`. The default table ships the tower primary; siblings/CPU/API entries are added via config override. (leg 04 step 4, leg 02 backends)
* **Per-backend circuit breaker** - landed - a breaker with cooldown and a half-open probe stops the proxy hammering a dead backend and protects tail latency. Tracks `llm_circuit_state`. (leg 04 step 4)
* **Context-budget guard** - landed - count prompt tokens (`app/analysis.py`), and when a request exceeds the model's safe `num_ctx`, trim oldest non-system turns before forward while always keeping the system framing and the live turn. Increments `llm_truncation_avoided_total`. (leg 04 step 5, leg 02 language analysis)
* **OpenAI-compatible surface** - landed - `/v1/chat/completions` (streaming and non-streaming), `/v1/completions`, and `/v1/models` listing the logical names, shaped to the OpenAI schema. Reasoning-model thought is surfaced as `reasoning_content`. (leg 04 step 6, leg 02 web server)
* **Logical model routing** - landed - logical names (`fast-think`, `fast`, `ctx-think`, `ctx`, `tune`, `gpt-oss-120b`) map to a backend plus that model's safe `num_ctx` (`fast-think` = 49152, others 32768 until the leg-03 benchmark locks values). The gpt-oss target rides llama-server on `:8080` through the OpenAI dialect adapter, with `gpt-oss:120b` kept as an alias for direct harness use. (leg 02 logical routing)
* **SSM-sourced config** - landed - `app/config.py` reads from env (prefix `PROXY_`) with a best-effort SSM fallback; the tower FQDN, Sentry DSN, and any API-fallback key resolve at boot. No secret in a tracked file. (leg 04 secrets and config)
* **Container boot validation** - landed - two repeatable checks prove the image boots and serves, not just that it builds. `ward test-container` (`./test-container.sh`) builds the image, runs it, and asserts `/healthz` + `/v1/models` + `/metrics` respond and the container stays up (needs a Docker daemon). `ward boot-probe` (`./boot_probe.sh`) validates the same `uv sync --frozen --no-dev` + `python -m app.main` boot path with no daemon, for a ward feature container or daemonless CI. Both share `scripts/probe_endpoints.sh`. (agent-proxy#24, follows the agent-proxy#22 dependency fix)
* **2-replica topology behind Caddy** - planned - 2 self-contained replicas behind a Caddy hard-rule front selector keying on a path prefix or query-param override. The queue is per-pod and ephemeral by design. The deploy manifests are leg 09, not built here. (leg 02 topology)

## Later phases - capability enhancement

Planned capability work, sequenced in `docs/ROADMAP.md`. None is implemented until its phase opens, and each stays out of the phase-1 reliability scope.

* **Model upskilling** - planned (future) - improve weak model behavior, particularly tool use. Relates to the aosh `tune` wildcard fine-tune (leg 12).
* **Tool injection** - planned (future) - inject tools like web search and API calls into harness requests.
* **Credential injection** - planned (future) - scoped credentials via MCP pass-through, so harnesses get credentials without holding them.
* **Knowledge management / RAG** - planned (future) - retrieval-augmented knowledge for requests.
* **Data formatting / data management / persistence** - planned (future) - conventional capability enhancement with durable state.
* **Single-shot to multi-turn** - planned (future) - turn a single-shot capability into a multi-turn one.
* **i/o validation + formatting** - planned (future) - validate and shape request and response i/o, extending the phase-1 resilience validation.

## Walkthrough

The phase-1 request path, module map, configuration, and how to run and prove the
proxy locally are in [`docs/proxy.md`](proxy.md).

## Source of truth

The design is locked in aosh and is not duplicated here. See `docs/plan/02-reference-architecture.md` and `docs/plan/04-headless-proxy-build.md` in `coilyco-bridge/agentic-os-hardware`, and the tracking issue `coilysiren/inbox#118`.
