# Per-model context budget

Part of [proxy.md](proxy.md).

## `num_ctx` binds local routes only (issue #115)

`num_ctx` is an Ollama parameter: the window you allocate when loading a model
into VRAM. It has no meaning for a hosted OpenAI-compatible provider, which has
no VRAM to run out of. The proxy nonetheless derived every route's prompt budget
from it, so `sirens-echo/deepseek` - a route with no `direct` target - inherited
48128 and trimmed at 47104, roughly 4.7% of the window DeepSeek's v4 line
advertises. Eleven trims over seven days, all at that same invariant number, and
every one of them manufactured the tool-pairing 400 in issue #113.

The discriminator already lives in the registry. A route with a `direct` target
is served out of the tower's VRAM and the ceiling is correct for it; a route
without one is served by a hosted provider and the ceiling is meaningless.
`models.derive_context_budget` returns the bound and which of four things
produced it:

* `model_window` - the route's declared `context_window`, or Ollama's own
  `/api/tags` figure for a local target below the ceiling.
* `vram_ceiling` - `PROXY_NUM_CTX_CEILING`, local routes only.
* `cost_ceiling` - `PROXY_CONTEXT_COST_CEILING`, a deliberate budget below the
  model's window for spend or latency reasons. Named for what it is, so a trim
  log never presents a cost decision as a hardware limit.
* `unbounded` - a hosted route with no declared window and no cost ceiling. The
  proxy trims nothing and injects no `num_ctx`, and the provider enforces its own
  limit and reports it in an error the caller can read.

The bound rides `request.prompt_trimmed` as `budget_bound_by`.
