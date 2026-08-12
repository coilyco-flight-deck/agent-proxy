# Model I/O capture contract

Part of [proxy](proxy.md).

## Model I/O capture contract


Body capture is opt-in and defaults off. When enabled, Agent Proxy captures
every field in both the complete normalized request body and the complete
normalized response body. This includes messages or prompts, tool definitions
and calls, model-visible options, generated content, reasoning content, usage,
and finish state when present. There is no selected-field or request-only
capture mode. Transport credentials and hop-by-hop headers are not model I/O
and remain excluded.

Agent Proxy is the single capture owner. Upstream callers, including
orchestration services such as Sirens Echo, keep correlation and operational
metadata but do not duplicate model payloads in their logs. With capture off,
stdout, OTLP spans, and SigNoz stay metadata-only. With capture on, body-bearing
structured logs and trace attributes contain the complete request and response
bodies. The configured OTLP or SigNoz sink must be governed as restricted model
content accordingly.

The repository enforces this condition for non-streaming chat, reconstructed
streaming chat, text completions, and MCP prompt calls. The restricted ser8
deployment opt-in and live SigNoz verification completed under
[issue #77](https://forgejo.coilysiren.me/coilyco-flight-deck/agent-proxy/issues/77).

The stable log events, fields, and viewing flow are in
[proxy-signoz-viewing.md](proxy-signoz-viewing.md).
