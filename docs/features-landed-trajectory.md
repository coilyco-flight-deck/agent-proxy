# Landed trajectory and data plane

Part of [FEATURES.md](FEATURES.md).

- **Versioned trajectory schema package** - landed - Pydantic producer and consumer validation, stable canonical serialization, compatibility fixtures, and a committed JSON Schema implement contract v1. Durable intake is the separate landed retention capability below.
- **Append-only trajectory retention** - landed - an internal cold-path API validates and idempotently commits raw contract-v1 envelopes to SQLite, retains duplicate and quarantine receipts, blocks mutation with database triggers, and replays into fresh consumers. A bounded emitter keeps storage waits off the model hot path.
- **Request lifecycle trajectory emission** - landed - an opt-in hot-path tap
  offers metadata-only model actions and terminal execution outcomes to the
  bounded emitter without waiting for storage or retaining request and response
  bodies. Deployment enablement remains planned with durable storage.
- **Episode and trajectory materialization** - landed - deterministic connected-component assembly preserves every correlation dimension, orders events, exposes partial and late state, records retries, fallbacks, and human interventions, and appends content-hashed derived revisions.
- **Evaluation and annotation records** - landed - automatic evaluations, verifiers, human annotations, and interventions join to stable trajectories with immutable evidence, supersession, disagreement, late-arrival, replay, redaction, and access-tier semantics.
- **Versioned dataset exports** - landed - SFT, preference, verifier, reward, and held-out evaluation schemas produce write-once manifests with source provenance, content hashes, deterministic trajectory-level splits, reproducibility, and opt-in restricted body references.
- **Operational and governance views** - landed - internal reliability, cost and latency, policy, evaluation, harness-fit, and skill-fit query contracts join durable trajectories to OTLP context, enforce access tiers, publish freshness and reconstruction limits, and provide evidence-only Ward dossier inputs. The read-only `agent-proxy-query` helper filters and joins those views for repository-owned investigation, harness-fit, and skill-use skills. Skill-fit preserves skill identity through materialization and keeps selection claims and observed use as separate facts.

LiteLLM gateway integration and cold-path ingestion continue in
[features-landed-gateway.md](features-landed-gateway.md).
