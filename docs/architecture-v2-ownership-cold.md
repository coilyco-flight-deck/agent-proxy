# Cold path, Ward, and evidence boundaries

Part of [architecture-v2.md](architecture-v2.md).

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
