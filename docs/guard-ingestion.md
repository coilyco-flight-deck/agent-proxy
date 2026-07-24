# Cli-guard and specgen trajectory ingestion

Agent Proxy can ingest runtime governance evidence from cli-guard’s append-only
JSONL audit trail and static policy evidence from a specgen project. Both
adapters run in the cold path.

For each audit row, the adapter emits:

* `action.proposed` for the guarded verb
* `policy.decided` for the cli-guard or profile outcome
* `execution.completed` or `execution.failed` only when policy allowed the
  command to run

The adapter retains the verb, decision, exit code, duration, profile
coordinates, policy flags, and aggregate egress counts. It hashes but does not
retain argv, stderr, policy reasons, working-tree details, absolute paths, or
egress hosts.

For a specgen source tree, the adapter hashes KDL guardfiles and committed lock
artifacts. It emits metadata-only artifact events plus one content-addressed
policy-snapshot observation. When both sources are supplied in one batch, every
audit event links that snapshot through attributes and provenance.

Run the importer through Ward:

```text
ward exec ingest-guard-data -- \
  --db <trajectory.sqlite3> \
  --audit-jsonl <ward-audit.jsonl> \
  --specgen-root <specgen-project>
```

Either source may be supplied alone. Optional actor-role and trajectory
correlation arguments add joins. These events report Ward and cli-guard
decisions. Agent Proxy does not authorize execution, reinterpret a guardfile,
or grant authority from a correlation field.

Cli-guard owns the audit wire format. Specgen owns guardfile discovery,
committed locks, and generated policy semantics. The adapter treats those
artifacts as opaque evidence and records hashes instead of copying their
contents.

## See also

* [trajectory-contract-v1.md](trajectory-contract-v1.md) - event and privacy contract.
* [operational-views.md](operational-views.md) - governed policy projections.
