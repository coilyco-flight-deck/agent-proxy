# Trajectory retention and replay

How raw contract-v1 envelopes reach durable storage, what the append-only ledger
guarantees, and how replay and recovery work.

Agent Proxy retains contract-v1 trajectory deliveries in a file-backed SQLite
database using WAL mode and full synchronous commits. SQLite is the first
storage boundary because the current service is single-writer, the complete raw
ledger remains portable, and replay does not depend on a separate live service.
The schema and replay API keep a later storage migration possible.

## Contents

- [Intake and ledger](trajectory-retention-intake.md)
- [Retention, deployment, and replay](trajectory-retention-operations.md)
