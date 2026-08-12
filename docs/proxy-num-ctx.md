# Auto num_ctx and the NUM_PARALLEL coupling

Part of [proxy.md](proxy.md).

### Auto num_ctx from the backend's real context window

The proxy no longer guesses `num_ctx` from a hand-maintained table (issue #32).
Ollama's `/api/tags` reports each model's real `context_length` in
`details.context_length` (confirmed live: `qwen3:8b` = 40960, `qwen3:4b` =
262144). The proxy reads it once (cached), and injects

```
num_ctx = min(context_length, PROXY_NUM_CTX_CEILING) - PROXY_NUM_CTX_HEADROOM
```

so a model rides its own real window up to the VRAM-safe ceiling. With the
defaults (ceiling 49152, headroom 1024): `qwen3:8b` -> 39936, `qwen3:4b` ->
48128. The **caller can never override `num_ctx`** - upstream forces the derived
value even if a client sends its own - which is the whole point of the proxy.

The larger litellm-as-core re-core that supersedes this routing layer entirely
is tracked in `coilyco-bridge/agentic-os-hardware#25` and is compatible with this
phase-1 change.

### The OLLAMA_NUM_PARALLEL coupling (issue #33)

The `num_ctx` the proxy injects is the model's **total** context. ollama then
**divides it across `OLLAMA_NUM_PARALLEL` slots**, so a single request's usable
window is `num_ctx / NUM_PARALLEL`. On a backend running `OLLAMA_NUM_PARALLEL=2`,
an injected `num_ctx=49152` delivers only ~24576 tokens per request - the flagship
fix silently halved, one layer down. Measured live (Windows tower, `qwen3:4b`,
`OLLAMA_NUM_PARALLEL=2`): `num_ctx=49152 -> prompt_eval_count=24578`,
`num_ctx=65536 -> 32770`, each exactly `num_ctx/2 + 2`.

The proxy defends in depth:

* **Compensate** - it injects `derived_num_ctx * num_parallel`
  (`PROXY_OLLAMA_NUM_PARALLEL`, or per-backend `num_parallel`), so each slot still
  delivers the intended per-request window. Note the VRAM cost: total KV cache
  scales with `num_ctx * num_parallel`, so a >1-slot backend that keeps the full
  window per request needs proportionally more VRAM - which is why the *real* fix
  is pinning the backend to one slot.
* **Fail loud** - after every ollama call it compares the backend's
  `prompt_eval_count` against both the sent prompt size and the target window
  (`app/analysis.detect_context_truncation`). A materially short delivery marks the
  result (`finish_reason=length`), increments `llm_context_truncated_total`, and
  logs `dispatch.context_truncated`; `PROXY_FAIL_ON_CONTEXT_TRUNCATION` makes it a
  hard 502. This catches a misconfigured `num_parallel` - the compensation being
  wrong - instead of reporting a silent short read as success.

The deployment half - pinning `OLLAMA_NUM_PARALLEL=1` on the proxy's ollama
backends so a slot gets the whole window - belongs in the ansible ollama role, not
this repo.
