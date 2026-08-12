# Flow, evaluation, and governance

Part of [architecture-v2](architecture-v2.md).

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
- Runtime enforcement and the restricted ser8 deployment opt-in landed under
  [issue #77](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/77).
