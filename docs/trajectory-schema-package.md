# Trajectory schema package

`app.trajectory` is the executable producer and consumer contract for
[`trajectory-contract-v1.md`](trajectory-contract-v1.md). Python callers use
`validate_event()` and non-Python callers use
`schemas/trajectory-event-v1.schema.json`.

## Compatibility rules

* **Major versions** - consumers reject an event whose schema major is not `1`.
  Rejected deliveries belong in quarantine with their receipt metadata.
* **Compatible revisions** - producers may add optional fields within version
  `1.x`. Consumers preserve unknown fields when validating, retaining, replaying,
  or re-emitting the envelope.
* **Required fields** - removing a required field, changing its meaning, or
  narrowing an accepted value requires a new major version.
* **Event types** - a new event type requires a contract revision and updated
  producer and consumer fixtures. Consumers do not silently reinterpret an
  unknown event type.
* **Stable identity** - retransmission preserves `event_id`, `source`, and
  `idempotency_key`. Replay preserves the original identity in provenance and
  adds a distinct replay receipt.
* **Content capture** - metadata-only records cannot contain body references.
  Redacted and restricted bodies require an external reference, a SHA-256
  digest, and the matching redaction status.

## Producer use

Producers create a UTF-8 JSON object, validate it before delivery, and retain the
resulting stable identity for retries:

```python
from app.trajectory import canonical_event_bytes, validate_event

event = validate_event(payload)
wire_bytes = canonical_event_bytes(event)
```

`canonical_event_bytes()` sorts keys and preserves compatible extensions. It is
the hashing and append-only retention representation.

## Consumer use

Consumers validate before materialization and treat validation failures as
quarantined deliveries. They deduplicate on `event_id` or
`(source.name, idempotency_key)`, distinguish domain time from observed time,
and retain unknown optional fields.

The fixtures under `tests/fixtures/trajectory/` cover valid, invalid, duplicate,
late, partial, replay, redacted-body, and restricted-body behavior. Every
producer or consumer can replay the same fixtures as its compatibility gate.

## Schema export

`ward exec schema` regenerates the committed JSON Schema from the Pydantic
models. A clean regeneration is required whenever the package changes.
