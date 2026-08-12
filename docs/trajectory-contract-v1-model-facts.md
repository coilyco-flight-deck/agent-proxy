# Model execution facts

Part of [trajectory-contract-v1.md](trajectory-contract-v1.md).

## Model execution facts


Model-request, model-response, and execution events include `payload.model_execution` when applicable:

```json
{
  "model": "qwen3:4b",
  "provider": "ollama",
  "provider_model": "qwen3:4b",
  "request_tokens": 1200,
  "response_tokens": 340,
  "total_tokens": 1540,
  "latency_ms": 812,
  "cost": {
    "amount": "0.000000",
    "currency": "USD",
    "calculation_version": "gateway-cost-v1"
  },
  "retry_count": 1,
  "fallback_count": 0,
  "fallback_from": [],
  "finish_reason": "stop"
}
```

- Use OpenTelemetry and OpenTelemetry GenAI semantic convention names in `attributes` where they fit, including `service.name`, `gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.response.model`, and applicable `gen_ai.usage.*` fields.
- Record domain joins and policy facts under `agentproxy.*`, including `agentproxy.policy.decision`, `agentproxy.ward.run_id`, `agentproxy.episode.id`, `agentproxy.context.safe_limit`, and `agentproxy.context.truncated`.
- `retry_count`, `fallback_count`, and `finish_reason` describe the final observed attempt. Individual attempts can be emitted as separate execution or observation events with their own ids.
- Token counts, latency, and cost may be `null` when unavailable. A consumer must distinguish unavailable from zero.
