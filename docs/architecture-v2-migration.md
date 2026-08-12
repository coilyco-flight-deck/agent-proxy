# Migration inventory and invariants

Part of [architecture-v2](architecture-v2.md).

## Migration inventory


### `app/models.py` - migrate onto LiteLLM

Physical backend selection, provider dialects, retry, and fallback are
commodity gateway concerns. Agent Proxy retains logical-route validation,
backend-derived safe-context policy, correlation labels, and the direct
rollback adapter. See [`route-registry.md`](route-registry.md).

### `app/upstream.py` - migrate onto LiteLLM

Native Ollama and OpenAI request shaping, response normalization, health probing, and provider transport are gateway concerns. Retire this module after parity once the LiteLLM integration exposes the facts Agent Proxy needs for its event envelope and context-safety behavior.

### `app/queue.py` - retire after parity

The bounded in-memory queue and worker pool currently protect this repository's gateway dispatch. Re-evaluate it after #41. Retire it if LiteLLM provides the accepted admission and overload behavior. It must not be reused as durable trajectory storage. Any future event buffer requires durable ingestion semantics rather than this ephemeral per-pod queue.

### `app/resilience.py` - migrate commodity behavior, retain Agent Proxy-specific detectors

Retry, fallback, backend circuit-breaking, and provider dispatch migrate onto LiteLLM after parity. Response validation, delivered-context verification, and policy-oriented structural detectors are Agent Proxy-specific behavior and remain on the hot path where justified. Detectors stay structural: a hot-path check that infers intent from the meaning of assistant text is out of scope, having been retired once already (see [proxy.md](proxy.md#removed-the-self-verification-claim-check)).

### `app/analysis.py` - retain as Agent Proxy-specific behavior

Context budgeting, token estimation used for safety policy, context-truncation detection, and cheap structural checks belong to Agent Proxy. Keep their decisions observable through the trajectory contract. Avoid expanding this module into heavyweight model evaluation on the request path.

### `app/obs.py` - retain unchanged, then extend

Structured logs, Prometheus metrics, OpenTelemetry setup, and correlation
propagation remain operational evidence. The opt-in request tap now offers
metadata-only lifecycle events to bounded contract-v1 ingestion without making
logs, Prometheus, OTLP, or SigNoz the durable trajectory source of truth.

### `app/skill_use.py` - move off the hot path

Ward reap artifact parsing is asynchronous to model serving. Its normalized
records now enter append-only contract-v1 retention as metadata-only
observations. Logging and `ward_skill_use_total` remain useful operational
projections, not the persistence mechanism.

## Transition invariants


- No current reliability behavior is deleted before the LiteLLM parity spike is accepted.
- LiteLLM does not absorb Agent Proxy identity, policy, correlation, context safety, or trajectory responsibilities.
- Agent Proxy does not absorb Ward authorization or execution authority.
- Heavy processing stays off the request path.
- When capture is enabled, every successful Agent Proxy model response has a
  capture acknowledgement for the complete restricted request and response
  bodies. Capture must not silently degrade to selected fields or request-only
  evidence.
- SigNoz and OTLP never become the sole durable trajectory store.
- Historical issues are evidence only. New v2 implementation work uses the fresh issue graph.
