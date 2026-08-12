# Follow-through, delivery rules, and history

Part of [ROADMAP](ROADMAP.md).

## Producer follow-through


- [#51 Agent-compose evidence](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/51) - landed - maps immutable manifests and public-safe selection traces without copying context bodies.
- [#52 Guard evidence](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/52) - landed - maps cli-guard audit rows and specgen policy snapshots without retaining sensitive arguments, diagnostics, paths, or hosts.
- [#54 Ward skill-use evidence](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/54) - landed - persists normalized reap artifacts while preserving log and metric projections.
- [#55 Request lifecycle evidence](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/55) - landed - adds opt-in bounded action and terminal execution emission without storage waits or body capture.
- [#77 Opt-in full-I/O capture](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/77) - landed - captures every field in complete normalized request and response bodies without allowing selected-field or request-only degradation, with the restricted ser8 deployment and live SigNoz proof complete.

## Delivery rules


- Keep expensive ingestion, materialization, evaluation, and export processing off the model request path.
- When full-I/O capture is enabled, permit only the bounded acknowledgement required to prevent partial or request-only capture on the model request path.
- Use durable raw event retention as the replay source. Do not treat SigNoz or OTLP as the training-data system of record.
- Make raw and derived data privacy-aware, redacted where required, and access-tiered.
- Preserve source event ids, schema versions, provenance, content hashes, and transform versions through every derived dataset.
- Keep Ward as the authorization, execution, lifecycle, recovery, and governance authority.
- Record actual shipped capabilities in `docs/FEATURES.md`. Planning documents must not imply that unimplemented v2 components are landed.

## Historical context


The aosh architecture correction in `coilyco-bridge/agentic-os-hardware#36` is the companion source for this reset. Earlier reliability-proxy work remains useful evidence. It is not a substitute for the new work graph and must not be reopened or repurposed.
