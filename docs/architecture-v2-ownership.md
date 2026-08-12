# Charter and ownership boundary

Part of [architecture-v2](architecture-v2.md).

## Charter


Agent Proxy is the **observation, trajectory collection, and data-processing plane** for the agentic operations stack. LiteLLM supplies the commodity inference gateway beneath it. The current reliability proxy is retained as the first collection tap until LiteLLM parity is proven.

This document defines the target ownership boundary. The durable cold path and the authenticated LiteLLM inner-gateway client have landed. Deployment evidence and the remaining commodity-behavior dispositions are tracked separately from this ownership contract. The implementation sequence is in [`work-graph.md`](work-graph.md).

## Ownership boundary


### LiteLLM

LiteLLM is the provider-facing commodity layer. After the parity decision, it owns:

- Provider protocol adapters and provider credentials.
- Model routing and provider selection.
- Retries, fallbacks, and provider-level failure behavior.
- Gateway key management and authorization for inference access.
- Budgets, spend limits, and inference cost accounting.
- Gateway-level usage and provider observability.

Agent Proxy does not fork this commodity behavior unless the parity decision identifies a documented Agent Proxy-specific gap.

### Agent Proxy hot path

The hot path is latency-sensitive. It owns only work that must happen before, during, or immediately after an inference request:

- Resolve caller and workload identity.
- Enforce Agent Proxy policy that is not commodity provider routing.
- Capture correlation with trace, span, Ward run, episode, agent session, request, repository, issue, and workflow identities.
- Apply context-safety controls and cheap structural detectors.
- When body capture is enabled, capture every field in the complete normalized
  model request and response bodies, excluding transport credentials and
  hop-by-hop headers.
- Normalize small operational facts such as outcome, tokens, latency, retry, fallback, and finish information.
- Emit bounded events asynchronously with explicit failure handling.

When full-I/O capture is enabled, the hot path may wait for a bounded capture
acknowledgement so it cannot silently degrade to partial or request-only
evidence. It never synchronously waits for trajectory materialization,
evaluation, training export, bulk body processing, or expensive ML analysis.

The commodity gateway integration is a standalone LiteLLM Proxy, not an
embedded SDK. Agent Proxy authenticates from a mounted key file, validates
Deploy-owned logical routes, forwards their LiteLLM aliases with a safe
`num_ctx`, and carries body-safe correlation metadata. Physical model routing
and fallback stay in LiteLLM configuration.
[`litellm-parity.md`](litellm-parity.md) records the decision and the live gates
that must pass before current gateway behavior can retire.

Cold path, Ward, and operational evidence boundaries continue in
[architecture-v2-ownership-cold.md](architecture-v2-ownership-cold.md).
