# Agent instructions - agent-proxy

The reliability proxy in front of the local LLM fleet. Read this, then read the build spec before writing code.

## Build spec lives in aosh

Do not invent the design here. The locked design and the step-by-step headless build leg live in `coilyco-bridge/agentic-os-hardware` under `docs/plan/`:

- `02-reference-architecture.md` - the locked architecture (components, topology, resilience semantics).
- `04-headless-proxy-build.md` - the self-contained build steps for this repo.
- `01-reference-diagnosis.md` - why this exists, with the measurements.

Tracking issue: coilysiren/inbox#118.

## Shape

- Python. FastAPI / Starlette async, httpx, uvicorn. The proxy is I/O-bound, so Python is correct. Rust plus a Python sidecar is a documented fallback only if a profiler shows a real CPU bottleneck.
- OpenAI-compatible surface so every harness points at it unchanged.
- In-memory bounded `asyncio.Queue` plus worker pool is the resilience core.
- 2 replicas. The queue is per-pod and ephemeral by design.

## Conventions

- Route dev commands through ward once a `.ward/ward.yaml` exists.
- Opaque ids, tokens, and tailnet FQDNs go in AWS SSM, never in tracked files. Resolve at runtime. The tower FQDN is `/coilysiren/kai-tower-3026/tailnet-fqdn`.
- She/her for Kai in every artifact. No em-dashes, no semicolons in prose, bold for anchors, flat bullets over prose tables.
- Keep `docs/FEATURES.md` current as features land (the README / AGENTS / FEATURES trifecta).

## Status

Seeded, not yet implemented. The first implementer follows the aosh headless proxy-build leg.
