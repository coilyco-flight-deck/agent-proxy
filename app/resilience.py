"""
Resilience policies (leg 02 "resilience policies", leg 04 step 4).

Wraps upstream dispatch with:

* **response validation** - non-empty, tool-call JSON parses, no degenerate
  repetition / truncation garbage.
* **retry with backoff** - a capricious single bad generation becomes a reroll,
  not a user-visible failure (``llm_retries_total``).
* **fallback chain** - a down or busy backend advances to the next backend in the
  logical model's chain (``llm_fallbacks_total``).
* **per-backend circuit breaker** - stops hammering a dead backend and protects
  tail latency (``llm_circuit_state``).

Transport/HTTP errors count against a backend's breaker; a merely bad generation
does not (the backend is alive, the token draw was unlucky) but still triggers a
reroll.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, AsyncIterator

from .config import get_settings
from .analysis import verify_action_claim
from .models import Backend, LogicalModel, resolve
from .obs import (
    RequestTraceContext,
    llm_circuit_state,
    llm_fallbacks_total,
    llm_retries_total,
    llm_upstream_latency_seconds,
    llm_validation_failures_total,
    log,
    request_log_fields,
    get_tracer,
)
from . import upstream
from .upstream import UpstreamError, UpstreamResult


class AllBackendsFailed(Exception):
    """Every backend in the chain was exhausted or open."""


class UnknownModel(Exception):
    """``dispatch_resilient`` was handed a tag the backend catalog does not know."""


# --------------------------------------------------------------------------- #
# Response validation
# --------------------------------------------------------------------------- #

_SHORT_REPLY_CHARS = 3  # leg-01 truncation garbage is 1-3 chars of non-word junk.


def _tool_calls_parse(tool_calls: list[dict[str, Any]]) -> bool:
    """Every emitted tool call must carry parseable arguments (dict or JSON str)."""
    for call in tool_calls:
        fn = call.get("function", call)
        args = fn.get("arguments")
        if args is None:
            return False
        if isinstance(args, str):
            try:
                json.loads(args)
            except json.JSONDecodeError:
                return False
        elif not isinstance(args, dict):
            return False
    return True


def _is_degenerate_repetition(text: str) -> bool:
    """Heuristic loop detector: a long output dominated by one short token."""
    stripped = text.strip()
    if len(stripped) < 40:
        return False
    tokens = stripped.split()
    if len(tokens) >= 20 and len(set(tokens)) <= 2:
        return True
    # A single character/short substring repeated to fill the reply.
    if len(set(stripped)) <= 2 and len(stripped) >= 40:
        return True
    # A whole multi-word line echoed far past any legitimate need - a stuck
    # decoder loop. Distinct from the token check above, which a line carrying
    # several distinct words slips past. ">20" tracks the "~20x" acceptance
    # threshold; distinct lines (a real list, numbered steps) never collapse
    # onto one bucket and so never trip it.
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if len(lines) > 20:
        _, count = Counter(lines).most_common(1)[0]
        if count > 20:
            return True
    return False


def validate_response(result: UpstreamResult) -> tuple[bool, str]:
    """Return ``(ok, reason)``. ``reason`` is one of the leg-05 failure labels."""
    has_tools = bool(result.tool_calls)
    has_thinking = bool((result.thinking or "").strip())
    content = (result.content or "").strip()

    # Truly empty means nothing at all. A reasoning model that emitted `thinking`
    # but ran out of budget before final content did real work - surface it as a
    # length-limited response rather than rerolling it into a 502.
    if not content and not has_tools and not has_thinking:
        return False, "empty"
    if has_tools and not _tool_calls_parse(result.tool_calls):
        return False, "malformed_toolcall"
    ok, reason = verify_action_claim(result.content or "", result.tool_calls)
    if not ok:
        return False, reason
    # leg-01 truncation garbage is a 1-3 char *non-word* reply (a stray symbol,
    # punctuation, whitespace remnant). A short but real answer ("OK", "42",
    # "no") contains alphanumerics and is legitimate - never reroll that.
    if (
        not has_tools
        and 0 < len(content) <= _SHORT_REPLY_CHARS
        and not any(c.isalnum() for c in content)
    ):
        return False, "truncation_garbage"
    if _is_degenerate_repetition(content):
        return False, "repetition"
    return True, "ok"


# --------------------------------------------------------------------------- #
# Circuit breaker
# --------------------------------------------------------------------------- #


class CircuitState(IntEnum):
    CLOSED = 0
    OPEN = 1
    HALF_OPEN = 2


@dataclass
class _Breaker:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0


class CircuitBreakerRegistry:
    """Per-backend breakers. Open after N consecutive transport failures, cool
    down, then admit a single half-open probe before fully closing."""

    def __init__(self) -> None:
        self._breakers: dict[str, _Breaker] = {}

    def _get(self, backend: Backend) -> _Breaker:
        b = self._breakers.get(backend.name)
        if b is None:
            b = _Breaker()
            self._breakers[backend.name] = b
            llm_circuit_state.labels(backend=backend.name).set(CircuitState.CLOSED)
        return b

    def allow(self, backend: Backend) -> bool:
        """Whether a request may be sent to this backend right now."""
        b = self._get(backend)
        if b.state == CircuitState.OPEN:
            cooldown = get_settings().circuit_cooldown
            if _now() - b.opened_at >= cooldown:
                b.state = CircuitState.HALF_OPEN
                llm_circuit_state.labels(backend=backend.name).set(CircuitState.HALF_OPEN)
                return True  # admit one probe
            return False
        return True

    def record_success(self, backend: Backend) -> None:
        b = self._get(backend)
        b.consecutive_failures = 0
        if b.state != CircuitState.CLOSED:
            log.info("circuit.close", backend=backend.name)
        b.state = CircuitState.CLOSED
        llm_circuit_state.labels(backend=backend.name).set(CircuitState.CLOSED)

    def record_failure(self, backend: Backend) -> None:
        b = self._get(backend)
        b.consecutive_failures += 1
        threshold = get_settings().circuit_fail_threshold
        if b.state == CircuitState.HALF_OPEN or b.consecutive_failures >= threshold:
            b.state = CircuitState.OPEN
            b.opened_at = _now()
            llm_circuit_state.labels(backend=backend.name).set(CircuitState.OPEN)
            log.warning("circuit.open", backend=backend.name, failures=b.consecutive_failures)


def _now() -> float:
    return time.monotonic()


# Module-wide breaker registry (per process, matching the per-pod queue).
breakers = CircuitBreakerRegistry()


# --------------------------------------------------------------------------- #
# Dispatch with resilience
# --------------------------------------------------------------------------- #


async def dispatch(
    model: LogicalModel,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    trace_ctx: RequestTraceContext | None = None,
) -> UpstreamResult:
    """Dispatch a non-streaming chat with full resilience. Walks the fallback
    chain, retrying each live backend with backoff, validating every response."""
    settings = get_settings()
    last_error: str = "no backends"
    tracer = get_tracer()
    trace_attrs = trace_ctx.attrs() if trace_ctx else None

    for idx, backend in enumerate(model.backends):
        if not breakers.allow(backend):
            last_error = f"{backend.name} circuit open"
            if idx + 1 < len(model.backends):
                llm_fallbacks_total.labels(logical_model=model.name, backend=backend.name).inc()
            log.warning(
                "dispatch.circuit_open",
                **request_log_fields(trace_ctx, backend=backend.name, outcome="fallback"),
            )
            continue

        for attempt in range(settings.max_retries + 1):
            # The attempt span is driven manually (not `with`) because the body
            # spans an `await` and can `continue`/`break`/`return` out of the loop.
            # A `finally` closes it on *every* exit path - a leaked span here also
            # leaks its OTel context token, and the next attempt's detach then
            # raises "token created in a different Context".
            attempt_span_cm = tracer.start_as_current_span("resilience.attempt") if tracer else None
            attempt_span = attempt_span_cm.__enter__() if attempt_span_cm is not None else None
            try:
                if attempt_span is not None:
                    attempt_span.set_attribute("agentproxy.logical_model", model.name)
                    attempt_span.set_attribute("agentproxy.backend", backend.name)
                    attempt_span.set_attribute("agentproxy.backend_dialect", backend.dialect)
                    attempt_span.set_attribute("agentproxy.resolved_backend", backend.url)
                    attempt_span.set_attribute("agentproxy.attempt", attempt)
                    if trace_attrs is not None:
                        for key, value in trace_attrs.items():
                            attempt_span.set_attribute(key, value)
                try:
                    start = _now()
                    result = await upstream.chat(
                        backend,
                        model.num_ctx,
                        messages,
                        tools=tools,
                        options=options,
                        span_attrs=trace_attrs,
                    )
                    llm_upstream_latency_seconds.labels(
                        logical_model=model.name, backend=backend.name
                    ).observe(_now() - start)
                    if attempt_span is not None:
                        attempt_span.set_attribute(
                            "gen_ai.usage.input_tokens", result.prompt_eval_count
                        )
                        attempt_span.set_attribute("gen_ai.usage.output_tokens", result.eval_count)
                        attempt_span.set_attribute(
                            "response.finish_reasons",
                            [result.done_reason] if result.done_reason else [],
                        )
                except UpstreamError as exc:
                    breakers.record_failure(backend)
                    last_error = str(exc)
                    log.warning(
                        "dispatch.transport_error",
                        backend=backend.name,
                        attempt=attempt,
                        error=str(exc),
                    )
                    if attempt_span is not None:
                        attempt_span.record_exception(exc)
                        attempt_span.set_attribute("agentproxy.outcome", "failed")
                    if attempt < settings.max_retries:
                        llm_retries_total.labels(
                            logical_model=model.name, backend=backend.name
                        ).inc()
                        log.info(
                            "dispatch.retry",
                            **request_log_fields(
                                trace_ctx, backend=backend.name, attempt=attempt, outcome="retry"
                            ),
                        )
                        await asyncio.sleep(settings.retry_base_delay * (2**attempt))
                        continue
                    break  # exhausted this backend's retries -> fall back

                ok, reason = validate_response(result)
                if ok:
                    breakers.record_success(backend)
                    log.info(
                        "dispatch.ok",
                        **request_log_fields(
                            trace_ctx, backend=backend.name, attempt=attempt, outcome="ok"
                        ),
                    )
                    if attempt_span is not None:
                        attempt_span.set_attribute("agentproxy.outcome", "ok")
                        attempt_span.set_attribute(
                            "gen_ai.usage.input_tokens", result.prompt_eval_count
                        )
                        attempt_span.set_attribute("gen_ai.usage.output_tokens", result.eval_count)
                        attempt_span.set_attribute(
                            "response.finish_reasons",
                            [result.done_reason] if result.done_reason else [],
                        )
                    return result

                # Bad generation: reroll on this live backend, do not trip the breaker.
                llm_validation_failures_total.labels(logical_model=model.name, reason=reason).inc()
                last_error = f"validation:{reason}"
                log.warning(
                    "dispatch.validation_failed",
                    backend=backend.name,
                    reason=reason,
                    attempt=attempt,
                )
                if attempt_span is not None:
                    attempt_span.set_attribute("agentproxy.outcome", "validation-failure")
                    attempt_span.set_attribute("agentproxy.validation_reason", reason)
                if attempt < settings.max_retries:
                    llm_retries_total.labels(logical_model=model.name, backend=backend.name).inc()
                    log.info(
                        "dispatch.retry",
                        **request_log_fields(
                            trace_ctx,
                            backend=backend.name,
                            attempt=attempt,
                            outcome="validation-failure",
                        ),
                    )
                    await asyncio.sleep(settings.retry_base_delay * (2**attempt))
                    continue
                breakers.record_success(backend)  # backend is alive, just unlucky
                outcome = (
                    "truncation-avoided" if reason == "truncation_garbage" else "validation-failure"
                )
                log.info(
                    "dispatch.validation_terminal",
                    **request_log_fields(
                        trace_ctx,
                        backend=backend.name,
                        attempt=attempt,
                        outcome=outcome,
                        reason=reason,
                    ),
                )
                if attempt_span is not None:
                    attempt_span.set_attribute("agentproxy.outcome", "validation-failure")
                    attempt_span.set_attribute("agentproxy.validation_reason", reason)
                    attempt_span.set_attribute(
                        "gen_ai.usage.input_tokens", result.prompt_eval_count
                    )
                    attempt_span.set_attribute("gen_ai.usage.output_tokens", result.eval_count)
            finally:
                if attempt_span_cm is not None:
                    attempt_span_cm.__exit__(None, None, None)

        if idx + 1 < len(model.backends):
            llm_fallbacks_total.labels(logical_model=model.name, backend=backend.name).inc()
            log.warning(
                "dispatch.fallback",
                **request_log_fields(trace_ctx, backend=backend.name, outcome="fallback"),
            )

    raise AllBackendsFailed(f"{model.name}: all backends failed ({last_error})")


async def dispatch_resilient(
    model_name: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    trace_ctx: RequestTraceContext | None = None,
) -> UpstreamResult:
    """Tag-based front door to the resilient dispatch engine (leg 04 step 10).

    Resolves the real ollama ``model_name`` against the backend catalog (deriving
    its safe ``num_ctx`` and fallback chain) and runs :func:`dispatch`:
    retry-with-backoff on each live backend (``llm_retries_total``), fallback to
    the next backend on exhaustion (``llm_fallbacks_total``), and response
    validation on every attempt. A backend that always fails walks the chain and
    then raises :class:`AllBackendsFailed` - a clean error, never a hang.

    Callers that already hold a resolved :class:`LogicalModel` - the worker, which
    resolves it at the route boundary for the 404 guard - call :func:`dispatch`
    directly; callers that only carry the tag string use this entry point.
    """
    model = await resolve(model_name)
    if model is None:
        raise UnknownModel(model_name)
    # The engine's retry/fallback logging always reads a trace context; a bare
    # caller that only had the name gets a minimal one built from it here.
    if trace_ctx is None:
        trace_ctx = RequestTraceContext(
            logical_model=model.name, request_model=model_name, request_kind="chat"
        )
    return await dispatch(model, messages, tools=tools, options=options, trace_ctx=trace_ctx)


async def dispatch_stream(
    model: LogicalModel,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    trace_ctx: RequestTraceContext | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a chat with connect-time fallback across the chain.

    Full content validation cannot apply to a token stream, so streaming gets the
    fallback chain and the circuit breaker (a backend that errors *before* the
    first chunk falls back), but not the reroll. Harnesses that want the full
    resilience guarantees use the non-streaming path.
    """
    last_error = "no backends"
    trace_attrs = trace_ctx.attrs() if trace_ctx else None
    for backend in model.backends:
        if not breakers.allow(backend):
            continue
        try:
            first = True
            async for chunk in upstream.chat_stream(
                backend,
                model.num_ctx,
                messages,
                tools=tools,
                options=options,
                span_attrs=trace_attrs,
            ):
                if first:
                    first = False
                yield chunk
            breakers.record_success(backend)
            return
        except UpstreamError as exc:
            breakers.record_failure(backend)
            last_error = str(exc)
            log.warning(
                "stream.transport_error",
                **request_log_fields(
                    trace_ctx, backend=backend.name, error=str(exc), outcome="failed"
                ),
            )
            # Only safe to fall back if nothing was emitted yet.
            if not first:
                raise AllBackendsFailed(
                    f"{model.name}: stream broke mid-flight ({last_error})"
                ) from exc
            continue
    raise AllBackendsFailed(f"{model.name}: all backends failed ({last_error})")
