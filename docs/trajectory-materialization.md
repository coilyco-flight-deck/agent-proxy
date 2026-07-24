# Episode and trajectory materialization

Materialization is a deterministic cold-path projection over the immutable raw
ledger. `TrajectoryMaterializer` performs reconstruction in memory and
`MaterializationStore` appends changed revisions to SQLite. Neither component is
imported by the model request path.

## Correlation

Events form a connected component when they share a strong execution identity:

* episode id
* Ward run id
* agent session id
* request id
* trace id

Repository, issue, workflow, span, causation, parent, and general correlation ids
remain sorted dimensions on the assembled record. Repository or issue alone
does not collapse unrelated runs into one trajectory.

The trajectory id is a stable digest of the component's strong identities. An
event without any strong identity receives an event-scoped trajectory id and an
explicit `missing_primary_correlation` partial reason.

## Ordering and watermarks

Events sort by domain occurrence time, then observed time, then event id. The
materializer sets its watermark to the maximum observed time minus the declared
allowed lateness, which defaults to five minutes. An event whose observation
delay exceeds that bound is listed in `late_event_ids`.

A trajectory stays `partial` when it lacks a terminal event or a primary
correlation. Missing facts are never replaced with fabricated completion.
Retries, fallbacks, human intervention, event-type counts, access tier, and
every correlation dimension remain explicit on the record.

## Re-materialization and provenance

Every record uses schema `agentproxy.trajectory.materialized` version `1.0`,
lists its ordered `source_event_ids`, and carries a SHA-256 digest of its
canonical representation.

The derived store is append-only. The same semantic reconstruction reuses its
existing revision. A late event or other raw evidence change appends the next
revision with a new content hash. Earlier revisions remain queryable and
database triggers reject update or delete operations.

`materialize_retained_events()` rebuilds the complete projection from the raw
ledger. The repository fixtures cover retries, fallbacks, human intervention,
late arrivals, missing terminal facts, and missing correlations.
