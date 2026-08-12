# Retention, deployment, and replay

Part of [trajectory-retention](trajectory-retention.md).

## Retention and access


Artifact events carry required `retention_class` and `access_tier` values.
Other events default to `standard` retention and `internal` access. Restricted
body capture always defaults to the `restricted` tier. Derived consumers may
narrow access but cannot upgrade it.

This slice records retention and access policy. It does not implement expiry,
legal hold, or destructive compaction. Those operations require a separately
authorized lifecycle surface because the raw ledger is deliberately immutable.

## Deployment


`PROXY_TRAJECTORY_DB_PATH` selects the database and defaults to
`./data/trajectory.sqlite3`. A deployment must mount that path on durable
storage. A container layer is not durable retention. Backups copy the database
through SQLite's online backup mechanism or from a quiesced volume, never by
editing ledger rows.

## Replay and recovery


`TrajectoryStore.replay_into()` reads canonical envelopes in receipt order or a
declared occurrence-time range. It preserves event identity, schema version,
source, domain time, and provenance. The source ledger appends a distinct
`replayed` receipt for each delivery.

Recovery creates a fresh store or consumer, replays the retained ledger, and
compares accepted plus duplicate counts with the attempted count. Quarantined
replay results stop promotion until the consumer contract is corrected. The
repository test suite demonstrates reconstruction into an empty database.
