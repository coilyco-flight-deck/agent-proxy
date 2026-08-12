# Decision and capability result

Part of [litellm-parity](litellm-parity.md).

## Why standalone


LiteLLM documents both Proxy and SDK modes as OpenAI-compatible provider
interfaces with streaming, routing, retry, fallback, error normalization,
callbacks, and cost facts. The Proxy additionally owns the capabilities that
belong at the commodity gateway boundary:

* independent deployment, health, upgrade, and rollback lifecycle
* virtual keys and model access
* budgets and rate limits
* persistent spend accounting
* centralized routing configuration

Embedding the SDK would make Agent Proxy own the commodity gateway process,
configuration, and lifecycle again. That contradicts
[`architecture-v2.md`](architecture-v2.md).
The selected boundary returns cost, token, retry, fallback, latency, and finish
facts into the versioned
[`trajectory-contract-v1.md`](trajectory-contract-v1.md) model-execution fields.

Primary sources:

* [LiteLLM mode comparison and streaming](https://docs.litellm.ai/)
* [Proxy virtual keys and spend tracking](https://docs.litellm.ai/docs/proxy/virtual_keys)
* [Proxy fallback behavior](https://docs.litellm.ai/docs/proxy/reliability)
* [SDK Router](https://docs.litellm.ai/docs/routing)
* [OpenTelemetry integration](https://docs.litellm.ai/docs/observability/opentelemetry_integration)
* [Proxy spend tracking](https://docs.litellm.ai/docs/proxy/cost_tracking)
* [Proxy model management](https://docs.litellm.ai/docs/proxy/model_management)

## Capability result


`app.litellm_parity.capability_matrix()` is the machine-readable decision:

* **Both modes** - OpenAI chat and completion shapes, streaming, provider
  invocation, routing, retries, fallbacks, callback cost facts, and
  OpenTelemetry hooks.
* **Standalone advantage** - independent lifecycle, virtual keys, budgets, rate
  limits, centralized model access, and persisted spend.
* **Agent Proxy retains** - identity, policy, Ward correlation, Ollama context
  derivation, safe `num_ctx`, delivered-context verification, structural
  detectors, trajectory emission, and privacy.
