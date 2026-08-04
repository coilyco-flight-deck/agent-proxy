# Trajectory contract v1

## Purpose and status

This is the interoperable contract for events that describe an agentic trajectory. It is concrete enough for independent producers and consumers to implement. It is a design contract, not a claim that durable ingestion or materialization is already implemented.

The canonical envelope name is `agentproxy.trajectory.event`. A producer emits schema version `1.0` until a compatible revision is published. Unknown optional fields are preserved where possible. Unknown required fields or incompatible major versions are rejected to a quarantined delivery path.

## Normative envelope

Every event is a UTF-8 JSON object with these fields. Names ending in `_ref` identify external or separately retained content rather than embedding large state.

```json
{
  "event_id": "018f6d1d-5e54-7c20-bf7e-5bd1ca1e8198",
  "schema_name": "agentproxy.trajectory.event",
  "schema_version": "1.0",
  "event_type": "execution.completed",
  "occurred_at": "2026-07-23T05:30:34.123Z",
  "observed_at": "2026-07-23T05:30:34.401Z",
  "source": {
    "name": "agent-proxy",
    "version": "0.1.0",
    "instance_id": "proxy-pod-opaque-id"
  },
  "idempotency_key": "source-event-or-deterministic-key",
  "correlation": {
    "trace_id": "otel-trace-id",
    "span_id": "otel-span-id",
    "ward_run_id": "ward-run-id",
    "episode_id": "episode-id",
    "agent_session_id": "agent-session-id",
    "request_id": "request-id",
    "repository": "owner/repository",
    "issue_ref": "owner/repository#40",
    "workflow": "merge-remote-main"
  },
  "actor": {
    "type": "agent",
    "id": "opaque-agent-id",
    "role": "engineer"
  },
  "attributes": {
    "service.name": "agent-proxy",
    "gen_ai.operation.name": "chat",
    "gen_ai.request.model": "model-tag",
    "agentproxy.policy.decision": "allow"
  },
  "payload": {},
  "content": {
    "capture": "metadata_only",
    "body_ref": "",
    "body_sha256": "",
    "redaction": {
      "status": "not_captured",
      "policy_version": "v1"
    }
  },
  "provenance": {
    "producer_event_ids": [],
    "input_refs": [],
    "transform": "",
    "transform_version": "",
    "content_sha256": ""
  }
}
```

## Identity and time

- `event_id` is a stable UUIDv7 generated once by the producing system. Retransmission preserves it.
- `schema_name` is exactly `agentproxy.trajectory.event` for this contract.
- `schema_version` is a semantic version string. A major-version change is incompatible.
- `occurred_at` is the RFC 3339 UTC timestamp when the represented fact happened.
- `observed_at` is the RFC 3339 UTC timestamp when the producer or ingestion service observed the fact. It can be later than `occurred_at`.
- `source.name`, `source.version`, and `source.instance_id` identify the producer without exposing secrets or unstable hostnames.
- `idempotency_key` is stable for a producer's logical event. Consumers deduplicate by `(source.name, idempotency_key)` and retain event-id aliases when a producer rekeys an event during recovery.

## Correlation

`correlation` is an object whose string fields are optional when unknown and must be present as empty or omitted only when the producer cannot know them.

- `trace_id` and `span_id` use the active OpenTelemetry context when available.
- `ward_run_id` joins the Ward execution lifecycle.
- `episode_id` joins a multi-event unit of agent work.
- `agent_session_id` joins a harness or agent conversation.
- `request_id` joins one model or tool request.
- `repository`, `issue_ref`, and `workflow` join the engineering and governance context.
- Producers may add `parent_event_id`, `causation_event_id`, and `correlation_id` when their source has them.

No consumer may infer authorization from correlation. These fields are joins, not authority grants.

## Event taxonomy and payload requirements

`event_type` is one of the following values. The event-specific facts live in `payload` and retain the common envelope.

- `actor.observed`
  - `payload.actor_ref`, identity source, role, and capability claims as references or metadata.
- `action.proposed`
  - `payload.action_kind`, `payload.action_ref`, target references, intent, and `before_state_ref` when known.
- `policy.decided`
  - `payload.decision` with `allow`, `deny`, `require_review`, or `defer`, policy name and version, reason code, and `action_ref`.
- `execution.started`, `execution.completed`, `execution.failed`
  - `payload.execution_id`, executor reference, action reference, outcome, error class where applicable, and `after_state_ref` when known.
- `observation.recorded`
  - `payload.observation_kind`, `payload.observation_ref`, subject reference, and measured facts.
- `state.changed`
  - `payload.before_state_ref`, `payload.after_state_ref`, change kind, and the action or execution reference that caused it.
- `evaluation.recorded`
  - `payload.evaluation_id`, evaluator or rubric version, input references, output label or score, confidence, and supersedes reference when applicable.
- `human.intervened`
  - `payload.intervention_kind`, human role or opaque actor reference, rationale reference, and the affected action or trajectory reference.
- `artifact.created`, `artifact.observed`
  - `payload.artifact_ref`, artifact kind, media type, content hash, size, and retention class.

Large state, prompts, responses, file bodies, and tool outputs belong in `*_ref` fields or `content.body_ref`. Producers must not duplicate them into every envelope.

### Agent Proxy model I/O profile

Agent Proxy model body capture is opt-in and defaults off. Enabling it captures
every field in the complete normalized request and response bodies. The two
directions are separate restricted content artifacts with their own references
and hashes. They include messages or prompts, tool definitions and calls,
model-visible options, generated content, reasoning content, usage, and finish
state when present. They exclude transport credentials and hop-by-hop headers.

Retries, fallbacks, tool continuations, repair turns, streaming assembly, and
terminal failures preserve enough separate content references and causation to
reconstruct what the model received and produced at each attempt. When capture
is enabled, a successful response requires acknowledgement of both its complete
request and response content. Capture must not silently degrade to selected
fields or request-only evidence.

Other producers and Agent Proxy calls with capture disabled remain metadata-only
unless their own contract opts into body capture. Agent Proxy callers must not
duplicate model payloads into their logs. Runtime enforcement is tracked in
[issue #77](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/77).

## Model execution facts

Model-request, model-response, and execution events include `payload.model_execution` when applicable:

```json
{
  "model": "qwen3:4b",
  "provider": "ollama",
  "provider_model": "qwen3:4b",
  "request_tokens": 1200,
  "response_tokens": 340,
  "total_tokens": 1540,
  "latency_ms": 812,
  "cost": {
    "amount": "0.000000",
    "currency": "USD",
    "calculation_version": "gateway-cost-v1"
  },
  "retry_count": 1,
  "fallback_count": 0,
  "fallback_from": [],
  "finish_reason": "stop"
}
```

- Use OpenTelemetry and OpenTelemetry GenAI semantic convention names in `attributes` where they fit, including `service.name`, `gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.response.model`, and applicable `gen_ai.usage.*` fields.
- Record domain joins and policy facts under `agentproxy.*`, including `agentproxy.policy.decision`, `agentproxy.ward.run_id`, `agentproxy.episode.id`, `agentproxy.context.safe_limit`, and `agentproxy.context.truncated`.
- `retry_count`, `fallback_count`, and `finish_reason` describe the final observed attempt. Individual attempts can be emitted as separate execution or observation events with their own ids.
- Token counts, latency, and cost may be `null` when unavailable. A consumer must distinguish unavailable from zero.

## Content, redaction, and access tiers

- `content.capture` is one of `metadata_only`, `redacted_body`, or `restricted_body`.
- `content.body_ref` points to separately retained content only when body capture is explicitly enabled. Enabling Agent Proxy body capture requires complete restricted request and response capture for that call.
- `content.body_sha256` hashes the retained canonical byte sequence. It is empty when no body is retained.
- `content.redaction.status` is one of `not_captured`, `redacted`, `restricted`, or `withheld`.
- `content.redaction.policy_version` identifies the redaction rules applied at capture or ingestion.
- `payload.retention_class` and `payload.access_tier` are required for artifacts and recommended for every event that has retained content.
- Consumers must honor the source access tier and cannot upgrade access through a derived dataset.

## Delivery, ordering, and replay

- Delivery is at least once. Consumers must be idempotent.
- A duplicate has the same `event_id` or the same `(source.name, idempotency_key)`. The raw store retains receipt metadata and the canonical accepted envelope without silently inventing a second logical event.
- Events can arrive out of order. `occurred_at` orders domain time and `observed_at` orders receipt time. Materializers use a documented watermark and can re-materialize when a late event arrives.
- A trajectory is partial when required correlations or terminal facts are missing. Materializers expose an explicit partial status instead of fabricating completion.
- Replay reads immutable raw envelopes in receipt order or a declared bounded time range. Replayers preserve the original event id, schema version, source, occurred time, and provenance while adding a separate replay receipt record.
- Invalid events are quarantined with validation errors and source receipt metadata. They are not transformed into valid events silently.

## Provenance and derived datasets

- `provenance.producer_event_ids` identifies raw events used to derive this event or record.
- `provenance.input_refs` identifies other source artifacts or state references.
- `provenance.transform` and `provenance.transform_version` identify the materializer, evaluator, or export transform.
- `provenance.content_sha256` is the hash of the canonical derived representation when one exists.
- Dataset manifests additionally record the schema version, selection policy, split policy, redaction policy, source event ids or immutable query boundary, and content hashes.

## Producer and consumer rules

- Producers generate stable ids before delivery and do not put secrets in the envelope.
- Producers emit the smallest sufficient metadata-only record when body capture is not opted in. Agent Proxy capture is an explicit deployment opt-in, not an automatic consequence of routing through the proxy.
- Consumers validate schema version and required fields before materialization.
- Consumers preserve unknown optional fields for forward compatibility.
- Consumers never use trace, Ward, repository, or issue correlation as an authorization mechanism.
- Contract fixtures must cover valid events, invalid events, duplicate delivery, late delivery, partial trajectories, replay, redacted bodies, and restricted bodies.

## Executable package

The contract is implemented by `app.trajectory`, exported for non-Python
consumers as `schemas/trajectory-event-v1.schema.json`, and exercised by the
shared fixtures under `tests/fixtures/trajectory/`. Compatibility and producer
or consumer use are documented in
[`trajectory-schema-package.md`](trajectory-schema-package.md).
