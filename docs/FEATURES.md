# Features

This is the living inventory of shipped behavior. Agent Proxy is transitioning from a reliability proxy into the observation, trajectory collection, and data-processing plane. Planned architecture-v2 work is kept separate so this document never presents it as landed.

Status legend:

- **landed** means implemented and verified in this repository.
- **planned** means accepted work that is not implemented here.

## Landed reliability collection tap

- **OpenAI-compatible request surface** - landed - `/v1/chat/completions`, `/v1/completions`, and `/v1/models`, including streaming and normalized reasoning content.
- **Remote MCP prompt surface** - landed - stateless Streamable HTTP at `/mcp`
  exposes model discovery and non-streaming prompt tools through the existing
  Agent Proxy policy, reliability, telemetry, and trajectory path. See
  [mcp.md](mcp.md).
- **Logical route registry** - landed - strict Deploy-mounted logical lanes hide
  physical backends from governed clients, route aliases through LiteLLM, and
  fail closed when direct rollback cannot serve a runtime. See
  [route-registry.md](route-registry.md).
- **Backend-derived context safety** - landed - safe `num_ctx` derivation and injection, `OLLAMA_NUM_PARALLEL` compensation, context-budget trimming, and loud delivered-context truncation detection.
- **Current gateway resilience** - landed - bounded in-memory queue and workers, queue backpressure, response validation, self-verification checks, retry with backoff, fallback chains, and per-backend circuit breakers.
- **Operational evidence** - landed - trace-correlated structured JSON logs, Prometheus metrics, OpenTelemetry traces, Sentry initialization, request spans, opt-in trace-body capture, and Ollama final-response token plus phase-duration measurements for streaming and non-streaming requests.
- **Ward correlation** - landed - request, Ward run, workflow, repository, issue, and agent-session metadata joins in logs and spans.
- **Skill-use artifact observation** - landed - Ward reap `skill-usage.json`
  parsing durably retains metadata-only skill observations with run and
  engineering correlations while preserving structured logs and the
  `ward_skill_use_total` Prometheus counter.
- **Versioned trajectory schema package** - landed - Pydantic producer and consumer validation, stable canonical serialization, compatibility fixtures, and a committed JSON Schema implement contract v1. Durable intake is the separate landed retention capability below.
- **Append-only trajectory retention** - landed - an internal cold-path API validates and idempotently commits raw contract-v1 envelopes to SQLite, retains duplicate and quarantine receipts, blocks mutation with database triggers, and replays into fresh consumers. A bounded emitter keeps storage waits off the model hot path.
- **Request lifecycle trajectory emission** - landed - an opt-in hot-path tap
  offers metadata-only model actions and terminal execution outcomes to the
  bounded emitter without waiting for storage or retaining request and response
  bodies. Deployment enablement remains planned with durable storage.
- **Episode and trajectory materialization** - landed - deterministic connected-component assembly preserves every correlation dimension, orders events, exposes partial and late state, records retries, fallbacks, and human interventions, and appends content-hashed derived revisions.
- **Evaluation and annotation records** - landed - automatic evaluations, verifiers, human annotations, and interventions join to stable trajectories with immutable evidence, supersession, disagreement, late-arrival, replay, redaction, and access-tier semantics.
- **Versioned dataset exports** - landed - SFT, preference, verifier, reward, and held-out evaluation schemas produce write-once manifests with source provenance, content hashes, deterministic trajectory-level splits, reproducibility, and opt-in restricted body references.
- **Operational and governance views** - landed - internal reliability, cost and latency, policy, evaluation, and harness-fit query contracts join durable trajectories to OTLP context, enforce access tiers, publish freshness and reconstruction limits, and provide evidence-only Ward dossier inputs. The read-only `agent-proxy-query` helper filters and joins those views for repository-owned investigation and harness-fit skills.
- **LiteLLM parity decision and runner** - landed - a machine-readable comparison selects standalone LiteLLM, while an executable endpoint probe gates model discovery, chat shape, streaming, finish reasons, usage, and error mapping.
- **Authenticated LiteLLM inner-gateway client** - landed - mounted-file bearer authentication, service-key model filtering, tower-backed context metadata, safe top-level `num_ctx`, OpenAI option translation, and body-safe Ward correlation support a standalone LiteLLM hop without weakening Agent Proxy policy or trajectory ownership.
- **Agent-compose trajectory ingestion** - landed - a cold-path adapter maps the
  immutable manifest and public-safe decision trace into actor, artifact, and
  observation events without copying the opaque context tree or granting
  execution authority. See [agent-compose-ingestion.md](agent-compose-ingestion.md).
- **Guard trajectory ingestion** - landed - cold-path adapters map cli-guard
  audit rows into action, policy, and execution events, and hash specgen
  guardfiles and locks into linked policy evidence without retaining sensitive
  argv, diagnostics, paths, or hosts. See [guard-ingestion.md](guard-ingestion.md).
- **Runtime and delivery checks** - landed - SSM-backed configuration, local
  `/healthz`, metrics-only non-generating route readiness, `/metrics`,
  daemonless boot probing, container probing, and a reliability harness. Route
  readiness verifies configured LiteLLM and Ollama control surfaces without
  claiming that GPU execution or completion validity was proven. See
  [readiness.md](readiness.md).

## Planned architecture v2

- **LiteLLM commodity behavior retirement** - planned - joined live evidence must still prove context delivery, trace continuity, retry ownership, and rollback before Agent Proxy provider routing, retries, fallbacks, queueing, or circuit behavior can retire.
## References

- [`architecture-v2.md`](architecture-v2.md) states ownership boundaries and the migration inventory.
- [`trajectory-contract-v1.md`](trajectory-contract-v1.md) specifies the event contract.
- [`trajectory-schema-package.md`](trajectory-schema-package.md) documents package compatibility and producer or consumer use.
- [`trajectory-retention.md`](trajectory-retention.md) documents intake, raw retention, replay, and recovery.
- [`trajectory-materialization.md`](trajectory-materialization.md) documents deterministic assembly and re-materialization.
- [`evaluation-records.md`](evaluation-records.md) documents evaluation, annotation, and intervention evidence.
- [`dataset-exports.md`](dataset-exports.md) documents schemas, manifests, split safety, and reproducibility.
- [`operational-views.md`](operational-views.md) documents queries, dossiers, access, freshness, and backfill.
- [`litellm-parity.md`](litellm-parity.md) documents the standalone decision and cutover gates.
- [`route-registry.md`](route-registry.md) documents logical routing and rollback.
- [`ROADMAP.md`](ROADMAP.md) and [`work-graph.md`](work-graph.md) define execution order.
- [`proxy.md`](proxy.md) remains the detailed guide to the currently landed reliability behavior.
