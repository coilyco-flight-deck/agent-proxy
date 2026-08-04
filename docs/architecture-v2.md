# Agent Proxy architecture v2

## Charter

Agent Proxy is the **observation, trajectory collection, and data-processing plane** for the agentic operations stack. LiteLLM supplies the commodity inference gateway beneath it. The current reliability proxy is retained as the first collection tap until LiteLLM parity is proven.

This document defines the target ownership boundary. The durable cold path and the authenticated LiteLLM inner-gateway client have landed. Deployment evidence and the remaining commodity-behavior dispositions are tracked separately from this ownership contract. The implementation sequence is in [`work-graph.md`](work-graph.md).

## Ownership boundary

### LiteLLM

LiteLLM is the provider-facing commodity layer. After the parity decision, it owns:

- Provider protocol adapters and provider credentials.
- Model routing and provider selection.
- Retries, fallbacks, and provider-level failure behavior.
- Gateway key management and authorization for inference access.
- Budgets, spend limits, and inference cost accounting.
- Gateway-level usage and provider observability.

Agent Proxy does not fork this commodity behavior unless the parity decision identifies a documented Agent Proxy-specific gap.

### Agent Proxy hot path

The hot path is latency-sensitive. It owns only work that must happen before, during, or immediately after an inference request:

- Resolve caller and workload identity.
- Enforce Agent Proxy policy that is not commodity provider routing.
- Capture correlation with trace, span, Ward run, episode, agent session, request, repository, issue, and workflow identities.
- Apply context-safety controls and cheap structural detectors.
- When body capture is enabled, capture every field in the complete normalized
  model request and response bodies, excluding transport credentials and
  hop-by-hop headers.
- Normalize small operational facts such as outcome, tokens, latency, retry, fallback, and finish information.
- Emit bounded events asynchronously with explicit failure handling.

When full-I/O capture is enabled, the hot path may wait for a bounded capture
acknowledgement so it cannot silently degrade to partial or request-only
evidence. It never synchronously waits for trajectory materialization,
evaluation, training export, bulk body processing, or expensive ML analysis.

The commodity gateway integration is a standalone LiteLLM Proxy, not an
embedded SDK. Agent Proxy authenticates from a mounted key file, validates
Deploy-owned logical routes, forwards their LiteLLM aliases with a safe
`num_ctx`, and carries body-safe correlation metadata. Physical model routing
and fallback stay in LiteLLM configuration.
[`litellm-parity.md`](litellm-parity.md) records the decision and the live gates
that must pass before current gateway behavior can retire.

### Agent Proxy cold path

The cold path turns emitted evidence into a governed dataset builder:

- Validate and ingest versioned events.
- Normalize records while preserving their raw envelopes.
- Durably retain append-only raw evidence for replay.
- Assemble episodes and trajectories from correlated events.
- Join automated evaluations, verifier results, annotations, and human intervention.
- Materialize versioned datasets and held-out evaluation sets with provenance.
- Serve controlled operational queries and harness-fit comparisons.

Cold-path components may run asynchronously, in workers, or in a separate data service. Their exact deployment and durable storage technology are deliberately deferred to the implementation work.

### Ward

Ward remains the authority for:

- Authorization.
- Execution.
- Lifecycle management.
- Recovery.
- Governance.

Agent Proxy may receive Ward lifecycle and execution evidence, supply controlled dossier inputs, and correlate data with Ward runs. It must not approve actions, execute work, or become a second authority.

### Operational evidence surfaces

OTLP and SigNoz receive logs, metrics, and traces for live operational visibility. They can be joined with trajectory records by trace and correlation identifiers. They are not the only durable trajectory store, replay source, or dataset provenance system.

## Data and request flow

1. A harness invokes the OpenAI-compatible surface with a logical route key and available Ward correlation metadata.
2. Agent Proxy validates the mounted route, then performs identity, policy, correlation, context safety, and cheap structural checks.
3. The commodity gateway sends inference work to the selected provider. The current in-repository gateway remains in this role until issue #41 proves LiteLLM parity.
4. When body capture is enabled, Agent Proxy captures every field in the
   complete normalized request and response bodies as separately restricted
   content and emits versioned events containing their references. With capture
   disabled, it emits metadata-only evidence. It does not wait for cold-path
   materialization.
5. The cold path validates, durably retains, and replays raw events as needed.
6. Materializers assemble episodes and trajectories. Evaluators and annotation systems join evidence to those records.
7. Dataset exporters and operational views consume versioned, access-controlled derived records with their provenance.

## Evaluation plane

Evaluators consume:

- Materialized trajectories and their raw evidence references.
- Expected outcome, policy decision, execution observation, and state-change references where available.
- Model, provider, token, latency, cost, retry, fallback, and finish facts.
- Human annotations and intervention records with author role, rubric or verifier version, and confidence.

Evaluators produce:

- A versioned score, label, or verifier result.
- Explanation or evidence references, not mandatory copied content.
- Evaluator identity, implementation or rubric version, timestamp, confidence, and supersession links.
- Dataset eligibility decisions that remain reproducible from retained inputs.

Evaluation is evidence collection and analysis. It does not authorize or execute an action.

## Content privacy and governance

- **Metadata tier** retains correlation, operational measurements, identifiers, hashes, policy outcomes, and references. It is the default event capture tier.
- **Redacted content tier** retains content only after configured redaction. Access is limited to approved operational and dataset-building roles.
- **Restricted body tier** retains every field in complete normalized model
  request and response bodies only when body capture is explicitly enabled.
  Capture defaults off. Transport credentials and hop-by-hop headers are never
  part of the captured model I/O.
- Event envelopes record `redaction.status`, `redaction.policy_version`, `capture.body`, and content references so consumers do not assume body availability.
- Raw retention has a documented retention class and access tier. Derived datasets retain source ids, hashes, transform versions, and their applicable redaction policy.
- Callers retain correlation and operational metadata but do not duplicate
  model payloads. stdout, OTLP, and SigNoz remain metadata-only when capture is
  disabled. Body-bearing records require restricted handling when it is enabled.
- Deletion, legal hold, retention expiry, and access-audit implementation details are future work. Producers must make these controls enforceable by avoiding implicit body copies.
- Runtime enforcement of opt-in complete request and response capture remains tracked in
  [issue #77](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/77).

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

Retry, fallback, backend circuit-breaking, and provider dispatch migrate onto LiteLLM after parity. Response validation, delivered-context verification, self-verification checks, and policy-oriented structural detectors are Agent Proxy-specific behavior and remain on the hot path where justified.

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
