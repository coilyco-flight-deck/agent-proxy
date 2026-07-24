# LiteLLM parity decision

Agent Proxy selects a **standalone LiteLLM Proxy** as its inner commodity
gateway. The Python SDK is not selected.

This decision does not cut traffic over or delete current reliability behavior.
The standalone boundary must pass the executable tower parity gates first.

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

## Executable parity gate

`ward exec litellm-parity -- --baseline-url URL --candidate-url URL --model TAG`
compares the current public surface with a candidate. It checks:

* live model discovery
* OpenAI chat completion shape and numeric usage
* finish reasons
* streamed chunk shape and `[DONE]`
* unknown-model error mapping

The command writes an optional JSON artifact with `--json PATH` and exits
nonzero on surface divergence. A passing report sets `surface_parity_passed`
while `cutover_authorized` remains false. Unit fixtures run without the tower
and prove that every gate detects its expected failure.

## Cutover blockers

Standalone is selected, but cutover is not yet authorized:

* The real tower model catalog must pass and expose enough metadata for Agent
  Proxy safe-context policy.
* `num_ctx` derivation, injection, and delivered-context verification must stay
  green through the new inner hop.
* Tool calls, reasoning fields, finish reasons, token usage, and streaming must
  pass with resident models.
* Configured retry and fallback paths need deterministic provider-failure
  evidence.
* Virtual key isolation, budget rejection, spend attribution, and its required
  Postgres dependency need deployment evidence.
* Trace context must join Ward, Agent Proxy, and LiteLLM without raw body
  attributes.

These are parity gates for the later integration and deployment. They are not
reasons to embed the SDK or to weaken current behavior.
