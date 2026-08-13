# Request path and endpoints

Part of [proxy](proxy.md).

## Request path


A governed client sends an OpenAI-shaped request carrying a Deploy-owned
`<namespace>/<alias>` key as `model`. The proxy:

1. **resolves** the key against Deploy's mounted registry (`app/models.py`).
   LiteLLM mode sends the configured alias. Direct rollback sends a supported
   physical target and fails closed for an unsupported runtime. Backend context
   metadata derives `num_ctx = min(context_length, ceiling) - headroom`.
2. **guards the context budget** (`app/analysis.py`): counts prompt tokens and,
   if the prompt exceeds `num_ctx - headroom`, trims the oldest non-system turns,
   always keeping the system framing and the live turn. Increments
   `llm_truncation_avoided_total` when it actually drops a turn.
3. **enqueues** the job on a bounded `asyncio.Queue` and awaits its future
   (`app/queue.py`). A full queue returns HTTP 429 (`llm_queue_depth`,
   `llm_queue_rejected_total`). The `queue.wait` span closes the moment a worker
   claims the job, so it measures admission delay and nothing else. It used to
   stay open for the whole request and tracked `request.chat` to within a
   millisecond, which made a saturated proxy and a slow model look identical and
   sent issue #105 chasing a 16.6s median wait that did not exist. It carries
   `agentproxy.queue.admitted`, false when the job ended before any worker took
   it. Cancelling the downstream request removes a
   waiting job or cancels its active dispatch task so worker capacity is released
   without starting another retry or fallback.
4. a **worker** dispatches under the resilience policies (`app/resilience.py`):
   walk the fallback chain, retry each live backend with backoff, and validate
   every response. Transport errors trip a per-backend circuit breaker; a merely
   bad generation is rerolled but does not. A settled upstream 4xx is neither
   retried nor failed over and reaches the caller with its own status - see
   [upstream error classification](upstream-error-classification.md).
5. the **upstream client** (`app/upstream.py`) forwards to the backend's native
   API. Ollama backends use `/api/chat` with `options.num_ctx` injected. OpenAI
   backends like the llama-server gpt-oss target use `/v1/chat/completions`
   without injection, then normalize their response back to the proxy's
   canonical shape. Downstream disconnects cancel the in-flight httpx request
   and close an active response stream while recording a bounded `cancelled`
   outcome.
6. the result is shaped back to the OpenAI schema (`app/main.py`). Reasoning-model
   thought is surfaced as `reasoning_content`.

A streaming request also carries SSE comment lines reporting attempt and backend
state, which a spec-compliant client ignores and a curious one parses. See
[SSE heartbeats](sse-heartbeats.md).

Streaming requests take the same fallback chain and circuit breaker but skip the
reroll (a token stream cannot be validated after the fact), so a harness that
wants the full resilience guarantee uses the non-streaming path.

## Endpoints


* `POST /v1/chat/completions` - streaming and non-streaming.
* `POST /v1/completions` - modeled as a single user turn so it rides the same
  resilience path.
* `GET /v1/models` - lists enabled logical route keys and hides physical models.
* `GET /healthz` - liveness for Caddy / k8s probes.
* `GET /readyz/{namespace}/{alias}` - non-generating structural readiness for one
  governed logical route. See [readiness.md](readiness.md).
* `GET /metrics` - prometheus exposition.
