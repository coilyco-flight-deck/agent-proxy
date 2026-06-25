# agent-proxy

A capability platform in front of the local agent and LLM fleet. One OpenAI-compatible API that every harness (opencode, crush, openclaw, openwebui, goose) points at unchanged, with reliability as phase 1 and a sequence of capability phases behind it.

## What it is

`agent-proxy` is the single front door to Kai's local model fleet. Harnesses stop talking to ollama directly and talk to this proxy instead, so one layer can fix reliability, inject capabilities, and observe every request without any harness changing its config beyond a base URL.

It is a platform, not a single feature. **Phase 1 is the reliability proxy** and it is the only phase with a locked, build-ready spec today. The broader capabilities (tool injection, credential injection, knowledge management, model upskilling, and more) are real, named, future phases. They are documented in `docs/ROADMAP.md` and are not built until their phase opens.

## Why it exists

Kai measured the local fleet at roughly 75 percent steady-state, idle-system, mid-loop reliability across opencode, crush, openclaw, and openwebui. Goose was the only harness that did not degrade. Same model, same ollama, same ceiling under every `/v1` harness, so the predictor was the harness, not the backend.

The root cause is silent context truncation. The tower served a 256k-capable model (`qwen3moe.context_length = 262144`) at a hard `OLLAMA_CONTEXT_LENGTH=32768` ceiling. A 55k-token prompt capped at exactly `prompt_eval_count=32767` and the overflow was left-truncated without warning, dropping the system prompt and tool definitions and leaving the model to answer from a headless tail. The same prompt with a per-request `num_ctx=49152` kept 49151 tokens, loaded in 4.4 seconds, and did not OOM. The override lever works.

Goose survives because it points at the native ollama API, passes context options, compacts context, and parses tool calls robustly. The `/v1` OpenAI-compatible harnesses ride the bare 32k default with no clean way to pass `num_ctx`. This proxy drags every harness up to goose-level reliability.

The full measured diagnosis lives in aosh leg `01-reference-diagnosis.md`, tracked in `coilysiren/inbox#118`.

## Phase 1 - reliability proxy (the locked, build-ready scope)

The first deliverable kills the two failure modes a middle layer must kill: silent context overflow and capricious model output.

- **num_ctx injection** - inject the correct per-model `num_ctx` so every `/v1` harness escapes the silent 32k truncation at once. This is the highest-value fix.
- **in-memory queue + worker pool** - a bounded `asyncio.Queue` plus a worker pool is the resilience core. The web layer accepts, enqueues, and awaits a future while workers dispatch under the full policy. Backpressure returns 429 when the queue is full.
- **validate / retry / fallback** - validate responses (non-empty, well-formed tool-call JSON, no degenerate repetition), retry with backoff, and fall back across a per-logical-model backend chain.
- **per-backend circuit breaker** - a breaker with cooldown stops the proxy hammering a dead backend and protects tail latency.
- **context-budget guard** - count prompt tokens and trim oldest non-system turns or summarize before forwarding when a request exceeds a model's safe `num_ctx`, so the model never silently truncates.
- **full observability** - structlog JSON to stdout, prometheus-client metrics, OpenTelemetry traces with LLM-span detail to Arize Phoenix, and errors to Sentry.
- **OpenAI-compatible surface** - `/v1/chat/completions`, `/v1/completions`, and `/v1/models`, plus `/healthz`, so every harness points at it unchanged.
- **topology** - 2 replicas, each self-contained, behind a Caddy hard-rule front selector that keys on a path prefix or query-param override. The queue is per-pod and ephemeral by design.

The stack is Python (FastAPI / Starlette async, httpx, uvicorn). The proxy is I/O-bound, so Python is correct. The full phase-1 inventory is in `docs/FEATURES.md` and the phased plan is in `docs/ROADMAP.md`.

## Later phases - capability enhancement

These are named, sequenced, and durable in `docs/ROADMAP.md`. None is implemented until its phase opens.

- **Model upskilling** - improve weak model behavior, particularly tool use.
- **Tool injection** - inject tools like web search and API calls into harness requests.
- **Credential injection** - scoped credentials via MCP pass-through, so harnesses never hold them.
- **Knowledge management / RAG** - retrieval-augmented knowledge for requests.
- **Data formatting / data management / persistence** - conventional capability enhancement with durable state.
- **Single-shot to multi-turn** - turn a single-shot capability into a multi-turn one.
- **i/o validation + formatting** - validate and shape request and response i/o, extending the phase-1 resilience validation.

## Status

Seeded. Phase 1 is not yet implemented. The first implementer follows aosh leg `04-headless-proxy-build.md`.

## Source of truth and pointers

The design is locked and lives in aosh. This repo does not duplicate it, it points to it.

- **Locked architecture** - `coilyco-bridge/agentic-os-hardware` `docs/plan/02-reference-architecture.md`.
- **Build leg for this repo** - `coilyco-bridge/agentic-os-hardware` `docs/plan/04-headless-proxy-build.md`.
- **Measured diagnosis** - `coilyco-bridge/agentic-os-hardware` `docs/plan/01-reference-diagnosis.md`.
- **Sequencing and milestones (M0-M8)** - `coilyco-bridge/agentic-os-hardware` `docs/plan/90-sequencing-and-milestones.md`.
- **Tracking issue** - `coilysiren/inbox#118`. Its two locked-design comments supersede parts of the original brief.
