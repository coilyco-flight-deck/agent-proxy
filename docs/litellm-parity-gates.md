# Gates, boundary, and blockers

Part of [litellm-parity](litellm-parity.md).

## Executable parity gate


`just litellm-parity --baseline-url URL --candidate-url URL --model TAG`
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

## Landed integration boundary


Agent Proxy can now use a standalone LiteLLM service without copying gateway
credentials into tracked configuration:

* A backend spec names only a mounted API-key file.
* LiteLLM's authenticated `/v1/models` response supplies the authorized model
  set. Agent Proxy intersects it with tower `/api/tags` context metadata.
* Agent Proxy forwards its derived safe `num_ctx` as a top-level LiteLLM
  extension and retains delivered-context verification.
* OpenTelemetry HTTP instrumentation carries W3C trace context. A filtered
  metadata block carries Ward and Agent Proxy correlation fields without body
  attributes.
* Direct tower routing remains a deployment rollback until joined evidence
  authorizes retirement.

## Retirement blockers


Standalone is selected and the client boundary is implemented, but current
gateway behavior cannot retire yet:

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
