# SigNoz content viewing contract

Part of [proxy.md](proxy.md).

### SigNoz content viewing contract

When capture is enabled, Agent Proxy emits exactly one structured request-body
event and one structured response-body event for each boundary model call:

* `model.request.captured` stores the complete normalized JSON object under
  `request.body`.
* `model.response.captured` stores the complete normalized JSON object under
  `response.body`.

Both events carry `agentproxy.capture.schema_version=1`,
`agentproxy.capture.status`, `agentproxy.request_id`, `trace_id`, and `span_id`.
Each event also carries a projection of the fields worth reading without
parsing the whole body. `model.request.captured` carries
`agentproxy.user_message`, the verbatim text of the final user turn.
`model.response.captured` carries `agentproxy.finish_reason`,
`agentproxy.assistant_message`, `agentproxy.completion_tokens`, and
`agentproxy.prompt_tokens`, taken from the first choice and the usage block.

Why those events also carry a field projection, and how it behaves when a
value is absent, is in
[proxy-capture-projections.md](proxy-capture-projections.md).
