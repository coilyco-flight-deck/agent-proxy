# Features

The living feature inventory for `agent-proxy`, grouped by phase. Be honest about status: nothing ships yet. Phase 1 is the locked, build-ready reliability proxy. The later phases are planned capability work and are not implemented until their phase opens (see `docs/ROADMAP.md`).

Status legend used below: **planned** means specified but not built, **building** means in progress, **landed** means implemented and verified.

## Phase 1 - reliability proxy

The locked scope. Each feature maps to an aosh leg `04-headless-proxy-build.md` build step or a leg `02-reference-architecture.md` component.

* **Skeleton + observability** - planned - the `app/` tree with obs wired before any logic: structlog JSON, prometheus `/metrics`, OpenTelemetry tracer, Sentry init from config, and `/healthz` returning 200. (leg 04 step 1, leg 02 observability component)
* **Backend registry** - planned - a logical-model table mapping each name to `{backend_url, ollama_tag, num_ctx}`. (leg 04 step 2, leg 02 upstream client)
* **num_ctx injection** - planned - the httpx client forwards to the backend's native ollama `/api/chat` with `options.num_ctx` injected, so a 55k-token request returns `prompt_eval_count` near the injected value, not 32767. The highest-value fix. (leg 04 step 2, leg 02 num_ctx injection)
* **In-memory queue + worker pool** - planned - a bounded `asyncio.Queue` and worker pool as the resilience core. The route enqueues and awaits a future, a worker dispatches, backpressure returns 429 when full, and `llm_queue_depth` is exported. (leg 04 step 3, leg 02 in-memory queue)
* **Response validation** - planned - reject empty completions, malformed tool-call JSON, and degenerate repetition before returning. (leg 04 step 4, leg 02 resilience policies)
* **Retry with backoff** - planned - turn a capricious single bad generation into a reroll, not a user-visible failure. Increments `llm_retries_total`. (leg 04 step 4)
* **Fallback chain** - planned - on failure, advance to the next backend in the logical model's chain (3026 primary, old-tower sibling, kai-server CPU llama.cpp, API fallback). Increments `llm_fallbacks_total`. (leg 04 step 4, leg 02 backends)
* **Per-backend circuit breaker** - planned - a breaker with cooldown stops the proxy hammering a dead backend and protects tail latency. Tracks `llm_circuit_state`. (leg 04 step 4)
* **Context-budget guard** - planned - count prompt tokens, and when a request exceeds the model's safe `num_ctx`, trim oldest non-system turns or summarize before forward. Never pass an over-budget prompt to the model. Increments `llm_truncation_avoided_total`. (leg 04 step 5, leg 02 language analysis)
* **OpenAI-compatible surface** - planned - `/v1/chat/completions` (streaming and non-streaming), `/v1/completions`, and `/v1/models` listing the logical names, shaped to the OpenAI schema so harnesses need no special handling. (leg 04 step 6, leg 02 web server)
* **Logical model routing** - planned - logical names (`fast-think`, `fast`, `ctx-think`, `ctx`, `tune`) map to a backend plus that model's safe `num_ctx`. (leg 02 logical routing)
* **SSM-sourced config** - planned - `config.py` reads from env and SSM, never hardcodes. The Sentry DSN and any API-fallback keys come from SSM at boot. No secret in a tracked file. (leg 04 secrets and config)
* **2-replica topology behind Caddy** - planned - 2 self-contained replicas behind a Caddy hard-rule front selector keying on a path prefix or query-param override. The queue is per-pod and ephemeral by design. (leg 02 topology)

## Later phases - capability enhancement

Planned capability work, sequenced in `docs/ROADMAP.md`. None is implemented until its phase opens, and each stays out of the phase-1 reliability scope.

* **Model upskilling** - planned (future) - improve weak model behavior, particularly tool use. Relates to the aosh `tune` wildcard fine-tune (leg 12).
* **Tool injection** - planned (future) - inject tools like web search and API calls into harness requests.
* **Credential injection** - planned (future) - scoped credentials via MCP pass-through, so harnesses get credentials without holding them.
* **Knowledge management / RAG** - planned (future) - retrieval-augmented knowledge for requests.
* **Data formatting / data management / persistence** - planned (future) - conventional capability enhancement with durable state.
* **Single-shot to multi-turn** - planned (future) - turn a single-shot capability into a multi-turn one.
* **i/o validation + formatting** - planned (future) - validate and shape request and response i/o, extending the phase-1 resilience validation.

## Source of truth

The design is locked in aosh and is not duplicated here. See `docs/plan/02-reference-architecture.md` and `docs/plan/04-headless-proxy-build.md` in `coilyco-bridge/agentic-os-hardware`, and the tracking issue `coilysiren/inbox#118`.
