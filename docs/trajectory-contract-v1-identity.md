# Identity, time, and correlation

Part of [trajectory-contract-v1.md](trajectory-contract-v1.md).

## Identity and time


- `event_id` is a stable UUIDv7 generated once by the producing system. Retransmission preserves it.
- `schema_name` is exactly `agentproxy.trajectory.event` for this contract.
- `schema_version` is a semantic version string. A major-version change is incompatible.
- `occurred_at` is the RFC 3339 UTC timestamp when the represented fact happened.
- `observed_at` is the RFC 3339 UTC timestamp when the producer or ingestion service observed the fact. It can be later than `occurred_at`.
- `source.name`, `source.version`, and `source.instance_id` identify the producer without exposing secrets or unstable hostnames.
- `idempotency_key` is stable for a producer's logical event. Consumers deduplicate by `(source.name, idempotency_key)` and retain event-id aliases when a producer rekeys an event during recovery.

## Correlation


`correlation` is an object whose string fields are optional when unknown and must be present as empty or omitted only when the producer cannot know them.

- `trace_id` and `span_id` use the active OpenTelemetry context when available.
- `ward_run_id` joins the Ward execution lifecycle.
- `episode_id` joins a multi-event unit of agent work.
- `agent_session_id` joins a harness or agent conversation.
- `request_id` joins one model or tool request.
- `repository`, `issue_ref`, and `workflow` join the engineering and governance context.
- Producers may add `parent_event_id`, `causation_event_id`, and `correlation_id` when their source has them.

No consumer may infer authorization from correlation. These fields are joins, not authority grants.
