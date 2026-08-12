# Response validation

Part of [proxy](proxy.md).

## Validation


A response is *usable* when it is non-empty, any emitted tool call has parseable
arguments, and it is not degenerate repetition. Every check keys off
**structurally** broken output; none of them judge the meaning of the text. Two
deliberate refinements, both surfaced by live testing against the tower:

* a legitimately short word answer (`OK`, `42`, `no`) is **not** truncation
  garbage - only a 1-3 char *non-word* reply (a stray symbol) is.
* a reasoning model that emitted `thinking` but ran out of token budget before
  final content did real work - it is surfaced as a length-limited response, not
  rerolled into a 502.

### Removed: the self-verification claim check

An earlier check rejected first-person completion claims ("I have filed the
issue") when the response carried no tool calls, on the theory that the router
could kick the turn back rather than trust a hallucinated done-state. It was
removed. It inferred intent from a regex over English, so a **correct** turn
that merely narrated its work was rerolled through every retry and every backend
in the chain and then returned a 502. A validator that rejects correct output
costs more than the hallucination it was aimed at.

The benchmark harness keeps an equivalent-looking `missed_toolcall` rule
(`scripts/reliability_loop.py`). That one is sound because it is gated on
`expect_tool`: the harness knows out-of-band that the turn required a tool call,
which the proxy never does.

The prompt-budget guard and the delivered-context check now both use the shared
instrumentation wrapper. Prompt trimming emits a structured `request.prompt_trimmed`
event, increments `llm_truncation_avoided_total`, and adds a span event with the
trimmed token counts and drop count when tracing is active. Delivered-context
truncation keeps the existing `dispatch.context_truncated` warning and metric,
and records the same action through the wrapper so the log, metric, and span
stay aligned.
