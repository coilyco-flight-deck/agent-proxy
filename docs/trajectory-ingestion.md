# Trajectory ingestion adapters

Cold-path importers that feed trajectory contract v1, one section per source.

## Agent-compose bundles

Agent Proxy can ingest the stable machine-readable surface of an immutable
agent-compose bundle into trajectory contract v1. The adapter reads only
`manifest.json` and `trace.json`. It does not traverse or duplicate the opaque
context tree.

The adapter emits:

* one `actor.observed` event for the resolved role, personalities, sources, and
  selected skill claims
* one `artifact.observed` event for the immutable bundle evidence
* one `observation.recorded` event for each public-safe selection decision in
  the retained trace

All events are metadata-only. Their provenance joins the bundle and decision
hashes without retaining instructions, skill bodies, private overlays, host
paths, or credentials. The bundle role and selected skills are observations,
not execution authority. Consumers must not infer permission from them.

Run the cold-path importer through Ward:

```text
just ingest-agent-compose \
  --bundle <verified-bundle-dir> \
  --db <trajectory.sqlite3>
```

Optional Ward run, agent session, repository, issue, and workflow arguments add
join fields. They do not grant authorization. Reprocessing the same immutable
bundle produces duplicate receipts without a second logical event.

The producer source contract remains owned by agent-compose. Agent Proxy
accepts the `agent-compose.bundle` manifest marker and
`agent-compose.trace` decision trace documented by that project.

## Cli-guard and specgen

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
just ingest-guard-data \
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
* [trajectory-retention.md](trajectory-retention.md) - durable ingestion and replay.
* [operational-views.md](operational-views.md) - governed policy projections.
