# Agent instructions - agent-proxy

Agent Proxy is the **observation, trajectory collection, and data-processing plane** for the agentic operations stack. LiteLLM is the commodity inference gateway beneath that plane. The current reliability proxy is a valuable first collection tap and remains in service until LiteLLM parity is proven.

## Read first

- [`README.md`](README.md) explains the current product charter and what is implemented today.
- [`docs/architecture-v2.md`](docs/architecture-v2.md) defines ownership boundaries and the migration inventory.
- [`docs/trajectory-contract-v1.md`](docs/trajectory-contract-v1.md) is the producer and consumer contract for trajectory events.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) and [`docs/work-graph.md`](docs/work-graph.md) define the dependency-ordered implementation work.
- `coilyco-bridge/agentic-os-hardware#36` is the canonical companion architecture decision. Keep this repository aligned with it.

The aosh build documents remain evidence for the delivered reliability behavior. They are not a prohibition on the v2 work in this repository.

## Ownership boundaries

- LiteLLM owns provider integration, routing, retries, fallbacks, keys, budgets, and inference cost accounting after parity is accepted.
- Agent Proxy owns identity, policy, correlation, context safety, cheap structural detection, event emission, ingestion, durable raw retention, replay, trajectory assembly, evaluation joins, and dataset materialization.
- Ward owns authorization, execution, lifecycle, recovery, and governance. Agent Proxy must not become an execution authority.
- SigNoz and OTLP are operational evidence surfaces. They are not the sole durable trajectory store.
- Keep expensive processing, materialization, evaluation, and export work off the latency-sensitive model request path.

## Current implementation guardrail

- Do not delete or weaken the current reliability behavior until issue #41 demonstrates LiteLLM parity.
- Do not claim an architecture-v2 component is landed until code and verification land. Keep `docs/FEATURES.md` current.
- Current skill-use ingestion emits structured logs and a Prometheus counter. It does not durably retain trajectories yet.

## Shape and conventions

- Python, FastAPI or Starlette async, httpx, and uvicorn or Hypercorn remain appropriate for the I/O-bound Agent Proxy surface.
- Preserve the OpenAI-compatible surface while LiteLLM parity is evaluated.
- Opaque ids, tokens, and tailnet FQDNs go in AWS SSM, never in tracked files. Resolve at runtime. The tower FQDN is `/coilysiren/kai-tower-3026/tailnet-fqdn`.
- She/her for Kai in every artifact. No em dashes or semicolons in prose. Use bold anchors and flat bullets over prose tables.
- Route development commands through ward when `.ward/ward.yaml` is available.
