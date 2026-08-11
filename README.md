# Agent Proxy

Agent Proxy is the **observation, trajectory collection, and data-processing plane** for the agentic operations stack. It protects the model request path with Agent Proxy-specific policy and safety behavior while turning operational work into trustworthy trajectory evidence. LiteLLM is the intended commodity inference gateway underneath it.

The repository is in transition. The OpenAI-compatible reliability proxy remains the first collection tap. It now supports a standalone LiteLLM Proxy as an authenticated inner gateway while retaining direct tower routing as the deployment rollback. The [parity decision](docs/litellm-parity.md) keeps current queueing, retry, fallback, and circuit behavior until joined live evidence proves which responsibilities can move.

## Stack ownership

- **LiteLLM** will own provider protocols, routing, retry, fallback, keys, budgets, and inference cost accounting.
- **Agent Proxy hot path** owns identity, policy, correlation, context safety, cheap structural detectors, and asynchronous event emission.
- **Agent Proxy cold path** owns ingestion, normalization, durable raw retention, replay, trajectory assembly, evaluation joins, and dataset materialization.
- **Ward** owns authorization, execution, lifecycle, recovery, and governance. Agent Proxy supplies evidence and never becomes an execution authority.
- **SigNoz and OTLP** provide operational traces, logs, metrics, and joins. They are not the sole durable training-data store.

Heavy data processing stays out of the latency-sensitive model request path. The target architecture and migration dispositions are in [`docs/architecture-v2.md`](docs/architecture-v2.md). Independent producers and consumers implement against [`docs/trajectory-contract-v1.md`](docs/trajectory-contract-v1.md).

## Model I/O capture contract

Model body capture is opt-in and defaults off. When enabled, Agent Proxy
captures the complete normalized request and response body for every routed
model call. Complete means every model I/O field present at the Agent Proxy
boundary. There is no selected-field or request-only capture mode.

Agent Proxy owns that capture so callers and wrappers do not duplicate prompt,
response, or tool payloads in their own logs. Transport credentials and
hop-by-hop headers are not model I/O and are never included.

When capture is disabled, application logs, OTLP spans, and SigNoz remain
metadata-only. When capture is enabled, Agent Proxy writes the complete bodies
to its structured logs and trace attributes. Any receiving OTLP or SigNoz sink
therefore requires the restricted controls appropriate for model content.

The repository implementation enforces this contract for non-streaming chat,
reconstructed streaming chat, text completions, and MCP prompt calls. Enabled
capture fails hard instead of returning success after field loss or an omitted
response. The restricted ser8 deployment opt-in and live SigNoz proof completed
under
[issue #77](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/77).
The stable log events, fields, pairing keys, and SigNoz
viewing flow are defined in
[`docs/proxy.md`](docs/proxy.md#signoz-content-viewing-contract).

## Implemented today

- An OpenAI-compatible `/v1/chat/completions`, `/v1/completions`, and `/v1/models` surface over the current gateway implementation.
- A stateless Streamable HTTP MCP surface at `/mcp` with model discovery and
  prompt tools backed by the same policy, reliability, and evidence path.
- Deploy-mounted logical route discovery with physical backend names kept
  behind the proxy boundary, plus backend-derived safe context handling.
- A bounded in-memory worker queue, response validation, retry, fallback, and per-backend circuit breaking.
- Context-budget protection and a small self-verification detector for unsupported action claims.
- Structured logs, Prometheus metrics, OpenTelemetry traces, Sentry initialization, Ward correlation metadata, and metrics-only health endpoints.
- Non-generating logical-route readiness that checks the LiteLLM and Ollama
  control surfaces without loading model weights or extending VRAM residency.
- Ward skill-use artifact parsing that durably retains metadata-only trajectory
  observations and preserves structured-log and Prometheus projections.
- An executable trajectory contract v1 package with producer and consumer validation, compatibility fixtures, and a JSON Schema for non-Python consumers.
- Append-only SQLite trajectory ingestion with idempotent receipts, quarantine, replay, and a bounded asynchronous hot-path emitter.
- Opt-in request lifecycle emission that offers metadata-only action and terminal
  execution evidence to the bounded queue without waiting on storage.
- Deterministic cold-path episode and trajectory reconstruction with explicit partial and late status, source-event provenance, content hashes, and append-only revisions.
- Immutable evaluation, verifier, human annotation, and intervention records with supersession, disagreement, late-arrival, privacy, and replay semantics.
- Reproducible SFT, preference, verifier, reward, and held-out evaluation exports with write-once manifests and trajectory-level leakage prevention.
- Governed reliability, cost and latency, policy, evaluation, and harness-fit views with OTLP joins, freshness metadata, evidence-only Ward dossier inputs, and a read-only query helper for agent skills.
- A machine-readable standalone-versus-SDK LiteLLM decision, executable endpoint parity runner, and an authenticated inner-gateway client that intersects the LiteLLM service-key catalog with tower context metadata, forwards safe `num_ctx`, and carries body-safe correlation metadata.
- Cold-path agent-compose bundle ingestion that retains role, selected-skill,
  artifact, and decision evidence without copying the opaque context tree.
- Cold-path cli-guard audit and specgen policy ingestion that links guarded
  actions, decisions, execution outcomes, and content-addressed policy evidence.

`docs/FEATURES.md` is the complete current inventory. Planned v2 components are deliberately marked planned there.

## Current request path

1. A client sends a Deploy-owned logical `<namespace>/<alias>` key and Ward correlation metadata.
2. Agent Proxy validates the route, applies context safety and cheap structural checks, and emits operational evidence.
3. The current reliability gateway queues and dispatches through the configured inner gateway. Deployments can select direct tower access or authenticated standalone LiteLLM.
4. Agent Proxy returns the normalized response and emits bounded request evidence.
5. Future cold-path workers durably ingest and materialize that evidence. They never block the response path.

## Development

Run tests and quality checks through Ward:

```bash
ward exec test
ward exec lint
ward exec typecheck
ward exec format-check
ward exec boot-probe
ward exec smoke
```

Run the current proxy or its container acceptance test:

```bash
ward exec serve
ward exec test-container
```

Inspect governed trajectory evidence without mutating Agent Proxy:

```bash
ward exec trajectory-query -- --help
```

The proxy uses port 8080 by default. Set `PROXY_HOST`, `PROXY_PORT`, or `LOG_LEVEL` to override its host, port, or log level.

The MCP endpoint and remote connector setup are documented in
[`docs/mcp.md`](docs/mcp.md). A deployment must allowlist its public hostname
with `PROXY_MCP_ALLOWED_HOSTS` and put authenticated ingress in front of
`/mcp` before making it internet-reachable.

## Container validation

- `ward exec test-container` needs a Docker daemon. It builds the image, starts it, and probes `/healthz`, `/v1/models`, and `/metrics`.
- `ward exec boot-probe` is daemonless. It reproduces the Dockerfile installation and command path, then probes the same endpoints.

## Planning

- [`docs/architecture-v2.md`](docs/architecture-v2.md) defines the target stack, data boundaries, privacy model, and current-code migration inventory.
- [`docs/trajectory-contract-v1.md`](docs/trajectory-contract-v1.md) defines the versioned event envelope and delivery semantics.
- [`docs/trajectory-retention.md`](docs/trajectory-retention.md) defines durable intake, append-only storage, replay, and recovery.
- [`docs/trajectory-materialization.md`](docs/trajectory-materialization.md) defines correlation, ordering, watermarks, and derived revisions.
- [`docs/evaluation-records.md`](docs/evaluation-records.md) defines evaluator, annotation, supersession, and disagreement evidence.
- [`docs/dataset-exports.md`](docs/dataset-exports.md) defines export schemas, manifests, splits, reproducibility, and privacy.
- [`docs/operational-views.md`](docs/operational-views.md) defines query contracts, Ward dossier boundaries, access, and freshness.
- [`docs/litellm-parity.md`](docs/litellm-parity.md) records the standalone decision, capability matrix, runner, and cutover blockers.
- [`docs/route-registry.md`](docs/route-registry.md) defines the mounted logical
  route contract, startup behavior, and direct rollback.
- [`docs/readiness.md`](docs/readiness.md) defines liveness, structural route
  readiness, inference evidence, and the no-log health contract.
- [`docs/work-graph.md`](docs/work-graph.md) links the new dependency-ordered implementation issues.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) explains sequencing without treating future capabilities as prohibited work.

## See also

- [AGENTS.md](AGENTS.md) - agent instructions, ownership boundaries, and conventions.
- [docs/FEATURES.md](docs/FEATURES.md) - inventory of what ships today.
- [.ward/ward.yaml](.ward/ward.yaml) - allowlisted commands and catalog metadata.
