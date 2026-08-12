# Event taxonomy and model execution facts

Part of [trajectory-contract-v1](trajectory-contract-v1.md).

## Event taxonomy and payload requirements


`event_type` is one of the following values. The event-specific facts live in `payload` and retain the common envelope.

- `actor.observed`
  - `payload.actor_ref`, identity source, role, and capability claims as references or metadata.
- `action.proposed`
  - `payload.action_kind`, `payload.action_ref`, target references, intent, and `before_state_ref` when known.
- `policy.decided`
  - `payload.decision` with `allow`, `deny`, `require_review`, or `defer`, policy name and version, reason code, and `action_ref`.
- `execution.started`, `execution.completed`, `execution.failed`
  - `payload.execution_id`, executor reference, action reference, outcome, error class where applicable, and `after_state_ref` when known.
- `observation.recorded`
  - `payload.observation_kind`, `payload.observation_ref`, subject reference, and measured facts.
- `state.changed`
  - `payload.before_state_ref`, `payload.after_state_ref`, change kind, and the action or execution reference that caused it.
- `evaluation.recorded`
  - `payload.evaluation_id`, evaluator or rubric version, input references, output label or score, confidence, and supersedes reference when applicable.
- `human.intervened`
  - `payload.intervention_kind`, human role or opaque actor reference, rationale reference, and the affected action or trajectory reference.
- `artifact.created`, `artifact.observed`
  - `payload.artifact_ref`, artifact kind, media type, content hash, size, and retention class.

Large state, prompts, responses, file bodies, and tool outputs belong in `*_ref` fields or `content.body_ref`. Producers must not duplicate them into every envelope.

### Agent Proxy model I/O profile

Agent Proxy model body capture is opt-in and defaults off. Enabling it captures
every field in the complete normalized request and response bodies. The two
directions are separate restricted content artifacts with their own references
and hashes. They include messages or prompts, tool definitions and calls,
model-visible options, generated content, reasoning content, usage, and finish
state when present. They exclude transport credentials and hop-by-hop headers.

Retries, fallbacks, tool continuations, repair turns, streaming assembly, and
terminal failures preserve enough separate content references and causation to
reconstruct what the model received and produced at each attempt. When capture
is enabled, a successful response requires acknowledgement of both its complete
request and response content. Capture must not silently degrade to selected
fields or request-only evidence.

Other producers and Agent Proxy calls with capture disabled remain metadata-only
unless their own contract opts into body capture. Agent Proxy callers must not
duplicate model payloads into their logs. Runtime enforcement and the
restricted ser8 deployment opt-in landed under
[issue #77](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/77).

Model execution fact requirements are in
[trajectory-contract-v1-model-facts.md](trajectory-contract-v1-model-facts.md).
