# Agent Proxy

Agent Proxy is the **observation, trajectory collection, and data-processing plane** for the agentic operations stack. It protects the model request path with Agent Proxy-specific policy and safety behavior while turning operational work into trustworthy trajectory evidence. LiteLLM is the intended commodity inference gateway underneath it.

The repository is in transition. The existing OpenAI-compatible reliability proxy is implemented and remains the first collection tap. LiteLLM is **not yet a runtime dependency**. The [parity decision](docs/litellm-parity.md) selects a standalone LiteLLM Proxy as the inner gateway, with live tower, key, budget, spend, and trace gates still blocking cutover. No current reliability behavior is removed until those gates prove replacement behavior.

## Stack ownership

- **LiteLLM** will own provider protocols, routing, retry, fallback, keys, budgets, and inference cost accounting.
- **Agent Proxy hot path** owns identity, policy, correlation, context safety, cheap structural detectors, and asynchronous event emission.
- **Agent Proxy cold path** owns ingestion, normalization, durable raw retention, replay, trajectory assembly, evaluation joins, and dataset materialization.
- **Ward** owns authorization, execution, lifecycle, recovery, and governance. Agent Proxy supplies evidence and never becomes an execution authority.
- **SigNoz and OTLP** provide operational traces, logs, metrics, and joins. They are not the sole durable training-data store.

Heavy data processing stays out of the latency-sensitive model request path. The target architecture and migration dispositions are in [`docs/architecture-v2.md`](docs/architecture-v2.md). Independent producers and consumers implement against [`docs/trajectory-contract-v1.md`](docs/trajectory-contract-v1.md).

## Implemented today

- An OpenAI-compatible `/v1/chat/completions`, `/v1/completions`, and `/v1/models` surface over the current gateway implementation.
- Real-tag model discovery and safe Ollama context-window derivation with `num_ctx` injection and delivered-context verification.
- A bounded in-memory worker queue, response validation, retry, fallback, and per-backend circuit breaking.
- Context-budget protection and a small self-verification detector for unsupported action claims.
- Structured logs, Prometheus metrics, OpenTelemetry traces, Sentry initialization, Ward correlation metadata, and health endpoints.
- Ward skill-use artifact parsing that emits structured events and Prometheus counts. It does not persist raw trajectory records.
- An executable trajectory contract v1 package with producer and consumer validation, compatibility fixtures, and a JSON Schema for non-Python consumers.
- Append-only SQLite trajectory ingestion with idempotent receipts, quarantine, replay, and a bounded asynchronous hot-path emitter.
- Deterministic cold-path episode and trajectory reconstruction with explicit partial and late status, source-event provenance, content hashes, and append-only revisions.
- Immutable evaluation, verifier, human annotation, and intervention records with supersession, disagreement, late-arrival, privacy, and replay semantics.
- Reproducible SFT, preference, verifier, reward, and held-out evaluation exports with write-once manifests and trajectory-level leakage prevention.
- Governed reliability, cost and latency, policy, evaluation, and harness-fit views with OTLP joins, freshness metadata, and evidence-only Ward dossier inputs.
- A machine-readable standalone-versus-SDK LiteLLM decision and executable endpoint parity runner. LiteLLM runtime integration has not landed.
- Cold-path agent-compose bundle ingestion that retains role, selected-skill,
  artifact, and decision evidence without copying the opaque context tree.
- Cold-path cli-guard audit and specgen policy ingestion that links guarded
  actions, decisions, execution outcomes, and content-addressed policy evidence.

`docs/FEATURES.md` is the complete current inventory. Planned v2 components are deliberately marked planned there.

## Current request path

1. A harness sends an OpenAI-compatible request and Ward correlation metadata.
2. Agent Proxy resolves the model, applies context safety and cheap structural checks, and emits operational evidence.
3. The current reliability gateway queues and dispatches to an upstream provider.
4. Agent Proxy returns the normalized response and emits bounded request evidence.
5. Future cold-path workers durably ingest and materialize that evidence. They never block the response path.

## Development

Run tests:

```bash
uv sync --extra dev
uv run pytest
```

Run the quality gate:

```bash
ward exec test
uv run ruff check .
uv run black --check .
uv run mypy app
./boot_probe.sh
./test-fixes.sh
```

Build and run the current proxy:

```bash
docker build -t agent-proxy .
docker run -p 8080:8080 agent-proxy
```

The proxy uses port 8080 by default. Set `PROXY_HOST`, `PROXY_PORT`, or `LOG_LEVEL` to override its host, port, or log level.

## Container validation

- `ward test-container` or `./test-container.sh` needs a Docker daemon. It builds the image, starts it, and probes `/healthz`, `/v1/models`, and `/metrics`.
- `ward boot-probe` or `./boot_probe.sh` is daemonless. It reproduces the Dockerfile installation and command path, then probes the same endpoints.

## Planning

- [`docs/architecture-v2.md`](docs/architecture-v2.md) defines the target stack, data boundaries, privacy model, and current-code migration inventory.
- [`docs/trajectory-contract-v1.md`](docs/trajectory-contract-v1.md) defines the versioned event envelope and delivery semantics.
- [`docs/trajectory-retention.md`](docs/trajectory-retention.md) defines durable intake, append-only storage, replay, and recovery.
- [`docs/trajectory-materialization.md`](docs/trajectory-materialization.md) defines correlation, ordering, watermarks, and derived revisions.
- [`docs/evaluation-records.md`](docs/evaluation-records.md) defines evaluator, annotation, supersession, and disagreement evidence.
- [`docs/dataset-exports.md`](docs/dataset-exports.md) defines export schemas, manifests, splits, reproducibility, and privacy.
- [`docs/operational-views.md`](docs/operational-views.md) defines query contracts, Ward dossier boundaries, access, and freshness.
- [`docs/litellm-parity.md`](docs/litellm-parity.md) records the standalone decision, capability matrix, runner, and cutover blockers.
- [`docs/work-graph.md`](docs/work-graph.md) links the new dependency-ordered implementation issues.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) explains sequencing without treating future capabilities as prohibited work.
