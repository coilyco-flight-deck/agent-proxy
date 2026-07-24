# Agent-compose trajectory ingestion

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
ward exec ingest-agent-compose -- \
  --bundle <verified-bundle-dir> \
  --db <trajectory.sqlite3>
```

Optional Ward run, agent session, repository, issue, and workflow arguments add
join fields. They do not grant authorization. Reprocessing the same immutable
bundle produces duplicate receipts without a second logical event.

The producer source contract remains owned by agent-compose. Agent Proxy
accepts the `agent-compose.bundle` manifest marker and
`agent-compose.trace` decision trace documented by that project.

## See also

* [trajectory-contract-v1.md](trajectory-contract-v1.md) - event and privacy contract.
* [trajectory-retention.md](trajectory-retention.md) - durable ingestion and replay.
