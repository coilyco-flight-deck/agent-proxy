# Backend catalog and prompt measurement

How the proxy discovers what a backend serves and how it measures a prompt
against that. Implementation lives in [`app/models.py`](../app/models.py) and
[`app/analysis.py`](../app/analysis.py).

## The `/api/tags` catalog cache

`/api/tags` on the primary backend is the single source of truth for which tags
exist and each tag's real `context_length`.

It is read once per base URL and cached. A cached value of `None` means the tag
is present but not reporting a context length, so `derive_num_ctx` falls back to
the configured ceiling.

The lookup also returns whether the fetch succeeded. That second element is what
makes an unreachable backend fail **open**: a real request is still served with
a conservative window, rather than every tag turning into a 404.

Only successful reads are cached. A failed fetch is retried on the next request,
so a tower that comes up after the proxy started is still discovered.

## Token counting

Prompt sizing uses tiktoken's `cl100k_base`, which is an approximation of
ollama's tokenizer rather than a match. The proxy reserves headroom instead of
aiming for an exact count, so drift between the two never turns a prompt that
fits into one that overflows.

Each message also carries a small fixed overhead approximating the chat
template's role and delimiter tokens. The same overhead is applied by both the
budget guard and the caller-facing `count_tokens` surface, so the two never
disagree about how large a prompt is.

That deliberate slack is also why the delivered-context check
([context-safety-settings.md](context-safety-settings.md)) needs a tolerance: it
compares the proxy's estimate against the backend's real `prompt_eval_count`.

## See also

- [`context-safety-settings.md`](context-safety-settings.md) - the `num_ctx`
  settings this feeds.
- [`route-registry.md`](route-registry.md) - logical route resolution above it.
