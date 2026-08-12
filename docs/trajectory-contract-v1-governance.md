# Content, delivery, and producer rules

Part of [trajectory-contract-v1](trajectory-contract-v1.md).

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
