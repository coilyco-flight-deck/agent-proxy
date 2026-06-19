# agent-proxy

A highly-instrumented reliability proxy between agent harnesses and local LLM backends.

One OpenAI-compatible API in front of the local model fleet (ollama on kai-tower-3026, 8 GB-tower siblings, kai-server CPU, API fallbacks). It injects per-model `num_ctx`, guards the context budget, validates and retries capricious model output, and falls back across backends. An in-memory queue is the resilience core. Full o11y (Prometheus, OpenTelemetry to Arize Phoenix, Sentry, structlog). Two replicas behind a Caddy hard-rule front selector.

It exists because raw ollama served a 256k-capable model at a silent 32k ceiling, and every `/v1` harness (opencode, crush, openclaw, openwebui) degraded mid-loop while goose, on the native API with context hygiene, did not. This proxy drags every harness up to goose's reliability.

## Source of truth

- Plan and design: `coilyco-bridge/agentic-os-hardware` (aosh), `docs/plan/`. The headless build doc there is the build spec.
- Tracking issue: coilysiren/inbox#118.

## Status

Seeded. Implementation is per the aosh headless proxy-build leg.
