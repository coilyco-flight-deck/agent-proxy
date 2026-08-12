# Intake and ledger

Part of [trajectory-retention](trajectory-retention.md).

## Intake


`POST /v1/trajectory/events` is the internal cold-path intake:

* `202` means the canonical envelope was accepted and committed.
* `200` means the event was an idempotent duplicate. The new receipt remains in
  the ledger, but no second logical event is created.
* `422` means validation failed. The raw delivery, safe validation errors, and
  receipt metadata remain in quarantine.

The endpoint grants no execution authority. Deployment policy controls who can
reach it, and Ward remains the authorization and lifecycle authority.

Hot-path producers use `AsyncTrajectoryEmitter.emit_nowait()`. The emitter has a
fixed queue bound, returns `False` instead of waiting when full, and moves the
SQLite commit onto a worker thread. A model response never waits for
materialization, evaluation, or a cold-path storage write. This describes the
current metadata-only emitter, not the opt-in full-I/O capture contract.

## Restricted model I/O


Model body capture is opt-in and defaults off. When enabled, Agent Proxy captures
every field in the complete normalized request and response bodies as separate
restricted content artifacts. The trajectory ledger carries their content
references and hashes, not copied bodies. Callers retain correlation and
operational metadata only.

With capture disabled, stdout, OTLP, and SigNoz remain metadata-only. With
capture enabled, the configured body-bearing sink requires restricted handling.
The repository implementation acknowledges complete request and response bodies
through paired structured-log events and canonical request-span attributes
without silently degrading to selected fields or request-only evidence. The
restricted ser8 deployment opt-in and live sink verification completed under
[issue #77](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/77).
Durable restricted artifact retention and trajectory-ledger content references
remain separate cold-path work and are not implied by the operational capture
events.

## Immutable ledger


The database contains four append-only surfaces:

* `events` stores one canonical validated envelope per logical event.
* `receipts` stores every accepted, duplicate, quarantined, and replay delivery
  with receipt time, raw bytes, and content digest.
* `event_aliases` maps a producer-rekeyed duplicate to its canonical event.
* `quarantine` records validation failures without rewriting the raw delivery.

Database triggers reject update and delete statements on all four surfaces.
Duplicate detection uses either `event_id` or
`(source.name, idempotency_key)`. Events retain domain and observed timestamps,
while receipts retain ingestion time.
