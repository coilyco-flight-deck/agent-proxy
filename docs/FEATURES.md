# Features

This is the living inventory of shipped behavior. Agent Proxy is transitioning from a reliability proxy into the observation, trajectory collection, and data-processing plane. Planned architecture-v2 work is kept separate so this document never presents it as landed.

Status legend:

- **landed** means implemented and verified in this repository.
- **planned** means accepted work that is not implemented here.

## Landed reliability collection tap

- **OpenAI-compatible request surface** - landed - `/v1/chat/completions`, `/v1/completions`, and `/v1/models`, including streaming and normalized reasoning content.
- **Real-tag context safety** - landed - backend catalog discovery, safe `num_ctx` derivation and injection, `OLLAMA_NUM_PARALLEL` compensation, context-budget trimming, and loud delivered-context truncation detection.
- **Current gateway resilience** - landed - bounded in-memory queue and workers, queue backpressure, response validation, self-verification checks, retry with backoff, fallback chains, and per-backend circuit breakers.
- **Operational evidence** - landed - structured JSON logs, Prometheus metrics, OpenTelemetry traces, Sentry initialization, request spans, and opt-in trace-body capture.
- **Ward correlation** - landed - request, Ward run, workflow, repository, issue, and agent-session metadata joins in logs and spans.
- **Skill-use artifact observation** - landed - ward reap `skill-usage.json` parsing, normalization, structured event logging, and the `ward_skill_use_total` Prometheus counter. This is not durable trajectory retention.
- **Runtime and delivery checks** - landed - SSM-backed configuration, `/healthz`, `/metrics`, daemonless boot probing, container probing, CI quality checks, and a reliability harness.

## Planned architecture v2

- **LiteLLM commodity gateway integration** - planned - provider integration, routing, retries, fallbacks, keys, budgets, and cost accounting move behind a parity-proven LiteLLM boundary. See [#41](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/41).
- **Versioned trajectory contract and schema package** - planned - durable producer and consumer validation for `trajectory-contract-v1.md`. See [#42](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/42).
- **Durable append-only raw ingestion and replay** - planned - the existing event signals become replayable retained evidence. See [#43](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/43).
- **Episode and trajectory materialization** - planned - assemble raw correlated events, including partial and late records. See [#44](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/44).
- **Evaluation and annotation joins** - planned - evaluator, verifier, and human intervention records. See [#45](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/45).
- **Versioned dataset exports** - planned - SFT, preference, verifier, reward, and held-out-evaluation datasets with provenance. See [#46](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/46).
- **Operational and governance views** - planned - controlled operational queries, Ward dossier inputs, and harness-fit comparisons. See [#47](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/47).

## References

- [`architecture-v2.md`](architecture-v2.md) states ownership boundaries and the migration inventory.
- [`trajectory-contract-v1.md`](trajectory-contract-v1.md) specifies the event contract.
- [`ROADMAP.md`](ROADMAP.md) and [`work-graph.md`](work-graph.md) define execution order.
- [`proxy.md`](proxy.md) remains the detailed guide to the currently landed reliability behavior.
