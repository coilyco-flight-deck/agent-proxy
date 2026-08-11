# Agent instructions - agent-proxy

## Scope

Agent Proxy is the **observation, trajectory collection, and data-processing plane** for the agentic operations stack. LiteLLM is the commodity inference gateway beneath that plane. The current reliability proxy is a valuable first collection tap and remains in service until LiteLLM parity is proven.

Read before changing anything here:

- [`README.md`](README.md) explains the current product charter and what is implemented today.
- [`docs/architecture-v2.md`](docs/architecture-v2.md) defines ownership boundaries and the migration inventory.
- [`docs/trajectory-contract-v1.md`](docs/trajectory-contract-v1.md) is the producer and consumer contract for trajectory events.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) and [`docs/work-graph.md`](docs/work-graph.md) define the dependency-ordered implementation work.

The aosh build documents remain evidence for the delivered reliability behavior. They are not a prohibition on the v2 work in this repository.

## Project shape

- **`app/`** - the service. Request path, resilience policy, upstream clients, body capture, and the `app/trajectory/` package for storage, materialization, evaluation, and export.
- **`docs/`** - the design and contract documents listed above.
- **`tests/`** - pytest suites mirroring the `app/` modules.
- **`scripts/`** - tracked programs invoked by workflows and Ward verbs, never inlined into a workflow step.

## Repo boundaries

- LiteLLM owns provider integration, routing, retries, fallbacks, keys, budgets, and inference cost accounting after parity is accepted.
- Agent Proxy owns identity, policy, correlation, context safety, cheap structural detection, event emission, ingestion, durable raw retention, replay, trajectory assembly, evaluation joins, and dataset materialization.
- Ward owns authorization, execution, lifecycle, recovery, and governance. Agent Proxy must not become an execution authority.
- SigNoz and OTLP are operational evidence surfaces. They are not the sole durable trajectory store.
- Keep expensive processing, materialization, evaluation, and export work off the latency-sensitive model request path.

## Commands

Route development commands through Ward, which reads [`.ward/ward.yaml`](.ward/ward.yaml). Use `ward exec <verb>` rather than bare `uv`, `pytest`, or `docker`.

There is no Makefile. `ward exec` runs each `run:` argv directly through uv.

## Validation

- `ward exec format-check`, `ward exec lint`, `ward exec typecheck`, and `ward exec test` are the offline gates.
- `pre-commit run --all-files` runs the catalog suite. Never pass `--no-verify`.
- `ward exec test-container` needs a Docker daemon. `ward exec boot-probe` is the daemonless equivalent.

## Safety

- Opaque ids, tokens, and tailnet FQDNs go in AWS SSM, never in tracked files. Resolve at runtime. The tower FQDN is `/coilysiren/kai-tower-3026/tailnet-fqdn`.
- Body capture is opt-in and defaults off. When it is on, structured logs and span attributes carry complete model I/O, so the configured sink is governed as restricted model content.
- Do not delete or weaken the current reliability behavior until issue #41 demonstrates LiteLLM parity.

## Cross-repo contracts

- **coilyco-bridge/deploy** deploys this service to ser8 and owns the mounted route registry. It references this repository without this repository referencing it.
- **coilyco-bridge/agentic-os-hardware#36** is the canonical companion architecture decision. Keep this repository aligned with it.
- [`docs/trajectory-contract-v1.md`](docs/trajectory-contract-v1.md) is the producer and consumer contract other services build against.

## Release

`.ward/ward.yaml` declares `workflow: merge-remote-main`. Canonical history lives on Forgejo.

Do not claim an architecture-v2 component is landed until code and verification land, and keep [`docs/FEATURES.md`](docs/FEATURES.md) current when a shipped capability changes.

## Agent rules

- Python, FastAPI or Starlette async, httpx, and uvicorn or Hypercorn remain appropriate for the I/O-bound Agent Proxy surface.
- Preserve the OpenAI-compatible surface while LiteLLM parity is evaluated.
- She/her for Kai in every artifact. No em dashes or semicolons in prose. Use bold anchors and flat bullets over prose tables.
- Name the actor in action sentences.

## See also

- [README.md](README.md) - human-facing intro and current charter.
- [docs/FEATURES.md](docs/FEATURES.md) - inventory of what ships today.
- [.ward/ward.yaml](.ward/ward.yaml) - allowlisted commands and catalog metadata.
