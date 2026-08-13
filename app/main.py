"""
FastAPI entrypoint: the OpenAI-compatible surface plus health and metrics
(leg 02 "web server", leg 04 steps 1 and 6).

Clients send a Deploy-owned logical ``<namespace>/<alias>`` route. The proxy
validates it against the mounted registry, derives a safe context, and
dispatches without adding route metadata to model-visible messages.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from prometheus_client import CONTENT_TYPE_LATEST

from . import resilience, upstream
from .analysis import PromptPairingError, apply_context_budget
from .body_capture import BodyCaptureError, CaptureReason, CaptureStatus, ModelBodyCapture
from .config import get_settings
from .models import RouteUnavailable, list_tags, resolve
from .skill_use import ingest_skill_use_source
from .obs import (
    HEALTH_TRACE_EXCLUDED_URLS,
    RequestTraceContext,
    agent_proxy_health_endpoint_requests_total,
    get_current_trace_span,
    get_tracer,
    is_trace_bodies_enabled,
    llm_prompt_tokens,
    llm_route_requests_total,
    llm_requests_total,
    log,
    log_on_span,
    metrics_text,
    record_error,
    record_prompt_cache_usage,
)
from .readiness import UnknownRoute, check_route_readiness
from .queue import QueueBusy, get_queue
from .resilience import AllBackendsFailed, ContextTruncated
from .route_registry import initialize_route_registry
from .trajectory.api import router as trajectory_router
from .trajectory.request_events import RequestLifecycle, RequestOutcome
from .trajectory.schema import TrajectoryEvent
from .trajectory.store import AsyncTrajectoryEmitter, TrajectoryStore

# obs is wired at import (app.obs runs setup_observability at module load).

_TRACE_METADATA_FIELDS: dict[str, tuple[str, ...]] = {
    "agentproxy.request_id": ("x-request-id", "request_id", "agentproxy.request_id"),
    "ward.run_id": ("x-ward-run-id", "ward.run_id"),
    "ward.container_name": ("x-ward-container-name", "ward.container_name"),
    "ward.role": ("x-ward-role", "ward.role"),
    "ward.harness": ("x-ward-harness", "ward.harness"),
    "ward.target_repo": ("x-ward-target-repo", "ward.target_repo"),
    "ward.issue_ref": ("x-ward-issue-ref", "ward.issue_ref"),
    "ward.workflow": ("x-ward-workflow", "ward.workflow"),
    "ward.context_level": ("x-ward-context-level", "ward.context_level"),
    "ward.version": ("x-ward-version", "ward.version"),
    "agent.session_id": ("x-agent-session-id", "agent.session_id"),
}

_trajectory_emitter: AsyncTrajectoryEmitter | None = None

_settings = get_settings()
mcp_server = FastMCP(
    name="agent-proxy",
    instructions=(
        "Use list_models to discover the local inference catalog, then use "
        "send_prompt to submit one non-streaming prompt through Agent Proxy."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_settings.resolved_mcp_allowed_hosts(),
        allowed_origins=_settings.resolved_mcp_allowed_origins(),
    ),
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _trajectory_emitter
    settings = get_settings()
    initialize_route_registry()
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass

    async with mcp_server.session_manager.run():
        trajectory_store = TrajectoryStore(settings.trajectory_db_path)
        if settings.trajectory_request_emission_enabled:
            _trajectory_emitter = AsyncTrajectoryEmitter(
                trajectory_store,
                maxsize=settings.trajectory_ingest_queue_size,
            )
            await _trajectory_emitter.start()
        await get_queue().start()
        ingest_skill_use_source(
            settings.ward_skill_use_input,
            trajectory_store,
        )
        # Tags are read live from the backend's /api/tags on first request, not at
        # boot - the tower need not be reachable for the proxy to start.
        log.info("startup.complete")
        try:
            yield
        finally:
            await get_queue().stop()
            if _trajectory_emitter is not None:
                await _trajectory_emitter.stop()
                _trajectory_emitter = None
            await upstream.aclose()


app = FastAPI(title="agent-proxy", lifespan=lifespan)
app.include_router(trajectory_router)


def _instrument_fastapi(application: FastAPI) -> None:
    """Install inbound tracing before Starlette freezes the middleware stack."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            application,
            excluded_urls=HEALTH_TRACE_EXCLUDED_URLS,
        )
    except Exception:
        # Observability remains best-effort and must never block process startup.
        pass


_instrument_fastapi(app)


# Health + metrics


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    agent_proxy_health_endpoint_requests_total.labels(endpoint="healthz", outcome="ready").inc()
    return {"status": "ok"}


@app.get("/readyz/{namespace}/{alias}")
async def readyz(namespace: str, alias: str) -> JSONResponse:
    logical_route = f"{namespace}/{alias}"
    try:
        result = await check_route_readiness(logical_route)
    except UnknownRoute:
        agent_proxy_health_endpoint_requests_total.labels(
            endpoint="readyz", outcome="unknown_route"
        ).inc()
        return JSONResponse(status_code=404, content={"status": "unknown_route"})
    content: dict[str, Any] = {
        "status": "ready" if result.ready else "not_ready",
        "route": result.route,
    }
    if result.failed_checks:
        content["failed_checks"] = list(result.failed_checks)
    agent_proxy_health_endpoint_requests_total.labels(
        endpoint="readyz", outcome="ready" if result.ready else "not_ready"
    ).inc()
    return JSONResponse(status_code=200 if result.ready else 503, content=content)


@app.get("/metrics")
async def metrics() -> Response:
    agent_proxy_health_endpoint_requests_total.labels(endpoint="metrics", outcome="served").inc()
    return Response(content=metrics_text(), media_type=CONTENT_TYPE_LATEST)


# OpenAI <-> ollama translation helpers


def _options_from_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Map the OpenAI sampling params onto ollama ``options`` (num_ctx is injected
    later by upstream, never here)."""
    opts: dict[str, Any] = {}
    if (v := body.get("temperature")) is not None:
        opts["temperature"] = v
    if (v := body.get("top_p")) is not None:
        opts["top_p"] = v
    if (v := body.get("max_tokens")) is not None:
        opts["num_predict"] = v
    if (v := body.get("stop")) is not None:
        opts["stop"] = v if isinstance(v, list) else [v]
    return opts


def _finish_reason(result: upstream.UpstreamResult) -> str:
    if result.tool_calls:
        return "tool_calls"
    # A backend-cut context (issue #33) is a length limit, surfaced as such so a
    # harness reading finish_reason sees the short read instead of a silent "stop".
    if result.context_truncated or result.done_reason == "length":
        return "length"
    return "stop"


def _openai_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ollama tool calls (arguments as a dict) -> OpenAI shape (arguments as a
    JSON string, each with a synthetic id)."""
    out = []
    for i, call in enumerate(tool_calls):
        fn = call.get("function", call)
        args = fn.get("arguments", {})
        args_str = args if isinstance(args, str) else json.dumps(args)
        out.append(
            {
                "id": f"call_{uuid.uuid4().hex[:12]}_{i}",
                "type": "function",
                "function": {"name": fn.get("name", ""), "arguments": args_str},
            }
        )
    return out


def _usage_block(result: upstream.UpstreamResult) -> dict[str, Any]:
    """The OpenAI usage block, carrying provider cache accounting when it exists.

    ``prompt_tokens_details.cached_tokens`` is the OpenAI-canonical spelling, so
    a caller reading the proxy's compatible surface finds the cache read where it
    expects it whichever provider served the turn. The fields appear only when
    the provider reported them, which keeps a reported zero (a real cache miss)
    distinguishable from an Ollama route that never accounts for caching at all.
    """
    usage: dict[str, Any] = {
        "prompt_tokens": result.prompt_eval_count,
        "completion_tokens": result.eval_count,
        "total_tokens": result.prompt_eval_count + result.eval_count,
    }
    if result.cache_usage_reported:
        usage["prompt_tokens_details"] = {"cached_tokens": result.cache_read_tokens}
        if result.cache_write_tokens:
            usage["cache_creation_input_tokens"] = result.cache_write_tokens
    return usage


def _record_prompt_cache(logical_model: str, result: upstream.UpstreamResult | None) -> None:
    """Publish one served turn's cache accounting to the proxy's metrics."""

    if result is None:
        return
    record_prompt_cache_usage(
        logical_model,
        reported=result.cache_usage_reported,
        read_tokens=result.cache_read_tokens,
        miss_tokens=result.cache_miss_tokens,
        write_tokens=result.cache_write_tokens,
    )


def _request_shape_attrs(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
) -> dict[str, int]:
    """Size the request's fixed prefix so roster trimming has numbers behind it.

    Issue #101 measured a 53 KB system block against a four-byte user turn, with
    17 tool schemas taking a large share of the 61 KB total. Neither share was
    visible from the proxy, which is the one place every governed route passes
    through, so the trade between a narrower default roster and the tool calls it
    would lose could only be argued from the caller's own instrumentation.
    """
    system_bytes = sum(
        len(json.dumps(message, ensure_ascii=False))
        for message in messages
        if isinstance(message, dict) and message.get("role") == "system"
    )
    tool_list = tools if isinstance(tools, list) else []
    return {
        "gen_ai.request.system_bytes": system_bytes,
        "gen_ai.request.tool_count": len(tool_list),
        "gen_ai.request.tool_bytes": (
            len(json.dumps(tool_list, ensure_ascii=False)) if tool_list else 0
        ),
    }


def _chat_completion_response(model_name: str, result: upstream.UpstreamResult) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": result.content or ""}
    if result.thinking:
        # Reasoning-model thought, surfaced under the widely-used field name so
        # harnesses that render it (qwen3/deepseek style) can.
        message["reasoning_content"] = result.thinking
    if result.tool_calls:
        message["tool_calls"] = _openai_tool_calls(result.tool_calls)
        if not result.content:
            message["content"] = None
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "message": message, "finish_reason": _finish_reason(result)}],
        "usage": _usage_block(result),
    }


def _error_body(message: str, err_type: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": err_type}}


def _error(status: int, message: str, err_type: str) -> JSONResponse:
    record_error(err_type)
    return JSONResponse(status_code=status, content=_error_body(message, err_type))


def _emit_capture_request(capture: ModelBodyCapture, span: Any | None) -> None:
    try:
        capture.emit_request(span)
    except BodyCaptureError:
        record_error("body_capture_failed", span)
        raise


def _emit_capture_response(
    capture: ModelBodyCapture,
    span: Any | None,
    body: dict[str, Any],
    *,
    status: CaptureStatus = "complete",
    reason: CaptureReason | None = None,
) -> None:
    try:
        capture.emit_response(span, body, status=status, reason=reason)
    except BodyCaptureError:
        record_error("body_capture_failed", span)
        raise


def _capture_response_body(
    capture: ModelBodyCapture,
    body: dict[str, Any],
    transform: Callable[[dict[str, Any]], dict[str, Any]] | None,
) -> dict[str, Any]:
    if not capture.enabled or transform is None:
        return body
    try:
        return transform(body)
    except Exception as exc:
        raise BodyCaptureError("failed to normalize boundary response capture") from exc


def _request_trace_extra(headers, metadata: Any = None) -> dict[str, object]:
    extra: dict[str, object] = {}
    if isinstance(metadata, dict):
        for target_key, source_keys in _TRACE_METADATA_FIELDS.items():
            for source_key in source_keys:
                if source_key not in metadata:
                    continue
                value = metadata.get(source_key)
                if value is not None:
                    extra[target_key] = value
                    break
    for target_key, source_keys in _TRACE_METADATA_FIELDS.items():
        for source_key in source_keys:
            value = headers.get(source_key)
            if value:
                extra[target_key] = value
                break
    return extra


def _trace_context(
    model_name: str,
    request_model: str,
    request_kind: str,
    request_id: str = "",
    *,
    upstream_mode: str = "",
    extra: dict[str, object] | None = None,
) -> RequestTraceContext:
    return RequestTraceContext(
        logical_model=model_name,
        request_model=request_model,
        request_kind=request_kind,
        upstream_mode=upstream_mode,
        trace_bodies=is_trace_bodies_enabled(),
        request_id=request_id,
        extra=extra or {},
    )


def _emit_trajectory_event(event: TrajectoryEvent) -> bool:
    """Offer one event to the bounded queue without delaying request handling."""

    if _trajectory_emitter is None:
        return False
    accepted = _trajectory_emitter.emit_nowait(event.model_dump(mode="json", exclude_none=True))
    if not accepted:
        record_error("trajectory_event_dropped")
        log.warning(
            "trajectory.event.dropped",
            event_type=event.event_type,
            dropped=_trajectory_emitter.dropped,
        )
    return accepted


def _emit_request_terminal(
    lifecycle: RequestLifecycle,
    outcome: RequestOutcome,
    *,
    started: float,
    result: upstream.UpstreamResult | None = None,
) -> None:
    latency_ms = max(0, int((time.perf_counter() - started) * 1000))
    _emit_trajectory_event(
        lifecycle.execution_event(
            outcome,
            result=result,
            latency_ms=latency_ms,
        )
    )


def _mark_cancelled_span(span: Any | None, event: str) -> None:
    """Attach one closed-set cancellation outcome without dynamic diagnostics."""

    if span is None:
        return
    try:
        span.set_attribute("agentproxy.outcome", "cancelled")
        span.add_event(event, {"outcome": "cancelled"})
    except Exception:
        # Telemetry remains best-effort and never interferes with cancellation.
        pass


# OpenAI surface


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    created = int(time.time())
    tags = await list_tags()
    return {
        "object": "list",
        "data": [
            {"id": tag, "object": "model", "created": created, "owned_by": "agent-proxy"}
            for tag in tags
        ],
    }


def _merge_stream_tool_calls(
    calls: list[dict[str, Any]],
    assembled: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize tool-call deltas and merge their fragments by OpenAI index."""

    deltas: list[dict[str, Any]] = []
    for fallback_index, call in enumerate(calls):
        raw_index = call.get("index", fallback_index)
        index = raw_index if isinstance(raw_index, int) else fallback_index
        current = assembled.setdefault(
            index,
            {
                "id": f"call_{uuid.uuid4().hex[:12]}_{index}",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            },
        )
        if call_id := call.get("id"):
            current["id"] = str(call_id)
        if call_type := call.get("type"):
            current["type"] = str(call_type)

        function = call.get("function", call)
        delta_function: dict[str, str] = {}
        if isinstance(function, dict):
            if name := function.get("name"):
                name_piece = str(name)
                current["function"]["name"] += name_piece
                delta_function["name"] = name_piece
            if "arguments" in function:
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    current["function"]["arguments"] += arguments
                    delta_function["arguments"] = arguments
                else:
                    encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
                    current["function"]["arguments"] = encoded
                    delta_function["arguments"] = encoded

        delta: dict[str, Any] = {
            "index": index,
            "id": current["id"],
            "type": current["type"],
        }
        if delta_function:
            delta["function"] = delta_function
        deltas.append(delta)
    return deltas


def _stream_chat_response(
    model_name: str,
    completion_id: str,
    created: int,
    content_parts: list[str],
    reasoning_parts: list[str],
    tool_calls: dict[int, dict[str, Any]],
    finish_reason: str,
    terminal_result: upstream.UpstreamResult | None,
) -> dict[str, Any]:
    """Reconstruct the complete normalized response represented by an SSE stream."""

    message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
        if not message["content"]:
            message["content"] = None

    usage = (
        _usage_block(terminal_result)
        if terminal_result is not None
        else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    )
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }


async def _stream_chat(
    model,
    messages,
    tools,
    options,
    model_name: str,
    *,
    trace_ctx: RequestTraceContext,
    request_span: Any | None,
    lifecycle: RequestLifecycle,
    started: float,
    capture: ModelBodyCapture,
    shape_attrs: dict[str, int],
) -> StreamingResponse:
    """Translate ollama's NDJSON stream into OpenAI ``chat.completion.chunk`` SSE."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    tracer = get_tracer()

    async def gen() -> AsyncIterator[str]:
        base = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
        }
        finish = "stop"
        outcome = "ok"
        terminal_result: upstream.UpstreamResult | None = None
        terminal_span = request_span
        span_cm = tracer.start_as_current_span("request.chat") if tracer is not None else None
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        try:
            if span_cm is not None:
                terminal_span = span_cm.__enter__()
            for key, value in {**trace_ctx.attrs(), **shape_attrs}.items():
                if terminal_span is not None:
                    terminal_span.set_attribute(key, value)
            _emit_capture_request(capture, terminal_span)
            log.info("request.accepted", **trace_ctx.attrs(), outcome="accepted")

            # Prime with the assistant role delta only after capture is guaranteed.
            first = {
                **base,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(first)}\n\n"

            async for chunk in resilience.dispatch_stream(
                model, messages, tools=tools, options=options, trace_ctx=trace_ctx
            ):
                msg = chunk.get("message") or {}
                response_delta: dict[str, Any] = {}
                piece = msg.get("content") or ""
                if piece:
                    content_piece = str(piece)
                    content_parts.append(content_piece)
                    response_delta["content"] = content_piece
                reasoning = msg.get("thinking") or msg.get("reasoning_content") or ""
                if reasoning:
                    reasoning_piece = str(reasoning)
                    reasoning_parts.append(reasoning_piece)
                    response_delta["reasoning_content"] = reasoning_piece
                raw_tool_calls = msg.get("tool_calls") or []
                if isinstance(raw_tool_calls, list) and raw_tool_calls:
                    response_delta["tool_calls"] = _merge_stream_tool_calls(
                        raw_tool_calls, tool_calls
                    )
                if response_delta:
                    delta = {
                        **base,
                        "choices": [{"index": 0, "delta": response_delta, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(delta)}\n\n"
                if chunk.get("done"):
                    finish = "length" if chunk.get("done_reason") == "length" else "stop"
                    terminal_result = upstream.parse_stream_result(chunk, model_name)
                    if tool_calls and finish == "stop":
                        finish = "tool_calls"
                    if terminal_span is not None:
                        upstream.set_result_span_attributes(terminal_span, terminal_result)
        except (asyncio.CancelledError, GeneratorExit) as exc:
            try:
                capture_reason: CaptureReason = (
                    "cancelled" if isinstance(exc, asyncio.CancelledError) else "interrupted"
                )
                _mark_cancelled_span(terminal_span, "request.cancelled")
                partial = _stream_chat_response(
                    model_name,
                    completion_id,
                    created,
                    content_parts,
                    reasoning_parts,
                    tool_calls,
                    finish,
                    terminal_result,
                )
                _emit_capture_response(
                    capture,
                    terminal_span,
                    partial,
                    status="incomplete",
                    reason=capture_reason,
                )
                log_on_span(
                    terminal_span,
                    "request.completed",
                    **trace_ctx.attrs(),
                    outcome="cancelled",
                )
                _emit_request_terminal(lifecycle, "cancelled", started=started)
            finally:
                if span_cm is not None:
                    span_cm.__exit__(None, None, None)
            raise
        except AllBackendsFailed as exc:
            record_error("stream_failed", terminal_span)
            log.warning("stream.failed", **trace_ctx.attrs(), error=str(exc), outcome="failed")
            finish = "stop"
            outcome = "failed"
        except BodyCaptureError:
            if span_cm is not None:
                span_cm.__exit__(None, None, None)
            raise
        except Exception:
            try:
                partial = _stream_chat_response(
                    model_name,
                    completion_id,
                    created,
                    content_parts,
                    reasoning_parts,
                    tool_calls,
                    finish,
                    terminal_result,
                )
                _emit_capture_response(
                    capture,
                    terminal_span,
                    partial,
                    status="incomplete",
                    reason="stream_failed",
                )
            finally:
                if span_cm is not None:
                    span_cm.__exit__(None, None, None)
            raise
        try:
            response_body = _stream_chat_response(
                model_name,
                completion_id,
                created,
                content_parts,
                reasoning_parts,
                tool_calls,
                finish,
                terminal_result,
            )
            _emit_capture_response(
                capture,
                terminal_span,
                response_body,
                status="complete" if outcome == "ok" else "incomplete",
                reason=None if outcome == "ok" else "stream_failed",
            )
            log_on_span(
                terminal_span,
                "request.completed",
                **trace_ctx.attrs(),
                outcome=outcome,
            )
            if outcome == "ok":
                _record_prompt_cache(trace_ctx.logical_model, terminal_result)
            _emit_request_terminal(
                lifecycle,
                "succeeded" if outcome == "ok" else "stream_failed",
                started=started,
                result=terminal_result,
            )
            final = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            if span_cm is not None:
                span_cm.__exit__(None, None, None)

    return StreamingResponse(gen(), media_type="text/event-stream")


async def _wait_for_disconnect(request: Request) -> None:
    """Wait for the ASGI disconnect message after the request body is consumed."""

    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


async def _chat_completion_until_disconnect(request: Request, body: dict[str, Any]) -> Response:
    """Cancel non-streaming request work as soon as its client disconnects."""

    operation = asyncio.create_task(_chat_completions(body, request.headers))
    disconnected = asyncio.create_task(_wait_for_disconnect(request))
    try:
        done, _ = await asyncio.wait(
            {operation, disconnected},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if disconnected in done:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            raise asyncio.CancelledError
        return operation.result()
    finally:
        for task in (operation, disconnected):
            if not task.done():
                task.cancel()
        await asyncio.gather(operation, disconnected, return_exceptions=True)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid JSON body", "invalid_request_error")
    if not isinstance(body, dict):
        return _error(400, "JSON body must be an object", "invalid_request_error")
    if body.get("stream"):
        return await _chat_completions(body, request.headers)
    return await _chat_completion_until_disconnect(request, body)


async def _chat_completions(
    body: dict[str, Any],
    headers,
    *,
    request_kind: str = "chat",
    capture_request_body: dict[str, Any] | None = None,
    capture_response_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Response:
    requested_model = body.get("model")
    model_name = requested_model if isinstance(requested_model, str) else ""
    try:
        model = await resolve(model_name) if model_name else None
    except RouteUnavailable as exc:
        return _error(503, str(exc), "model_unavailable")
    if model is None:
        return _error(404, f"unknown model '{requested_model}'", "model_not_found")
    llm_route_requests_total.labels(
        logical_model=model.name,
        upstream_mode=model.upstream_mode,
    ).inc()

    messages = body.get("messages") or []
    tools = body.get("tools")
    options = _options_from_openai(body)
    stream = bool(body.get("stream", False))

    settings = get_settings()
    try:
        messages, prompt_tokens, _trimmed = apply_context_budget(
            model.name, messages, model.num_ctx, settings.num_ctx_headroom
        )
    except PromptPairingError as exc:
        # Locally detected and locally named, rather than an opaque upstream
        # 400 three retries later (issue #113).
        return _error(400, str(exc), "invalid_request_error")
    llm_prompt_tokens.labels(logical_model=model.name).observe(prompt_tokens)
    trace_extra = _request_trace_extra(headers, body.get("metadata"))
    request_id = (
        headers.get("x-request-id", "")
        or str(trace_extra.get("agentproxy.request_id", ""))
        or str(uuid.uuid4())
    )
    trace_ctx = _trace_context(
        model.name,
        model_name,
        request_kind,
        request_id,
        upstream_mode=model.upstream_mode,
        extra=trace_extra,
    )
    normalized_request = dict(capture_request_body if capture_request_body is not None else body)
    if capture_request_body is None:
        normalized_request["messages"] = messages
    capture = ModelBodyCapture(
        enabled=trace_ctx.trace_bodies,
        request_id=request_id,
        request_body=normalized_request,
        expected_span_name="request.chat",
    )
    tracer = get_tracer()
    request_span = get_current_trace_span()
    lifecycle = RequestLifecycle.from_trace_context(
        trace_ctx,
        occurred_at=datetime.now(timezone.utc),
    )
    started = time.perf_counter()
    shape_attrs = _request_shape_attrs(messages, tools)
    _emit_trajectory_event(lifecycle.action_event())

    if stream:
        llm_requests_total.labels(logical_model=model.name, outcome="stream").inc()
        return await _stream_chat(
            model,
            messages,
            tools,
            options,
            model.name,
            trace_ctx=trace_ctx,
            request_span=request_span,
            lifecycle=lifecycle,
            started=started,
            capture=capture,
            shape_attrs=shape_attrs,
        )

    span_cm = tracer.start_as_current_span("request.chat") if tracer is not None else None
    if span_cm is not None:
        request_span = span_cm.__enter__()
    try:
        for key, value in {**trace_ctx.attrs(), **shape_attrs}.items():
            if request_span is not None:
                request_span.set_attribute(key, value)
        _emit_capture_request(capture, request_span)
        log.info("request.accepted", **trace_ctx.attrs(), outcome="accepted")
        try:
            result = await get_queue().submit(model, messages, tools, options, trace_ctx=trace_ctx)
        except asyncio.CancelledError:
            _emit_request_terminal(lifecycle, "cancelled", started=started)
            llm_requests_total.labels(logical_model=model.name, outcome="cancelled").inc()
            _mark_cancelled_span(request_span, "request.cancelled")
            _emit_capture_response(
                capture,
                request_span,
                _capture_response_body(capture, {}, capture_response_transform),
                status="incomplete",
                reason="cancelled",
            )
            log_on_span(
                request_span,
                "request.completed",
                **trace_ctx.attrs(),
                outcome="cancelled",
            )
            raise
        except QueueBusy:
            error_body = _error_body("proxy queue is full, retry shortly", "rate_limit_error")
            _emit_request_terminal(lifecycle, "queue_rejected", started=started)
            llm_requests_total.labels(logical_model=model.name, outcome="rejected").inc()
            _emit_capture_response(
                capture,
                request_span,
                _capture_response_body(capture, error_body, capture_response_transform),
                status="incomplete",
                reason="queue_rejected",
            )
            log_on_span(
                request_span,
                "request.completed",
                "warning",
                **trace_ctx.attrs(),
                outcome="rejected",
            )
            return _error(429, "proxy queue is full, retry shortly", "rate_limit_error")
        except ContextTruncated as exc:
            error_body = _error_body(str(exc), "context_truncated")
            _emit_request_terminal(lifecycle, "context_truncated", started=started)
            llm_requests_total.labels(logical_model=model.name, outcome="context_truncated").inc()
            _emit_capture_response(
                capture,
                request_span,
                _capture_response_body(capture, error_body, capture_response_transform),
                status="incomplete",
                reason="context_truncated",
            )
            log_on_span(
                request_span,
                "request.completed",
                "warning",
                **trace_ctx.attrs(),
                outcome="context-truncated",
                error=str(exc),
            )
            return _error(502, str(exc), "context_truncated")
        except AllBackendsFailed as exc:
            error_body = _error_body(str(exc), "upstream_error")
            _emit_request_terminal(lifecycle, "upstream_failed", started=started)
            llm_requests_total.labels(logical_model=model.name, outcome="failed").inc()
            _emit_capture_response(
                capture,
                request_span,
                _capture_response_body(capture, error_body, capture_response_transform),
                status="incomplete",
                reason="upstream_failed",
            )
            log_on_span(
                request_span,
                "request.completed",
                "warning",
                **trace_ctx.attrs(),
                outcome="failed",
                error=str(exc),
            )
            return _error(502, str(exc), "upstream_error")
        except BodyCaptureError:
            raise
        except Exception:
            _emit_capture_response(
                capture,
                request_span,
                _capture_response_body(capture, {}, capture_response_transform),
                status="incomplete",
                reason="upstream_failed",
            )
            raise

        try:
            if request_span is not None:
                upstream.set_result_span_attributes(request_span, result)
            response_body = _chat_completion_response(model.name, result)
            _emit_capture_response(
                capture,
                request_span,
                _capture_response_body(capture, response_body, capture_response_transform),
            )
        except BodyCaptureError:
            raise
        except Exception:
            _emit_capture_response(
                capture,
                request_span,
                {},
                status="incomplete",
                reason="response_failed",
            )
            raise
        llm_requests_total.labels(logical_model=model.name, outcome="ok").inc()
        _record_prompt_cache(model.name, result)
        _emit_request_terminal(lifecycle, "succeeded", started=started, result=result)
        log_on_span(
            request_span,
            "request.completed",
            **trace_ctx.attrs(),
            outcome="ok",
        )
        return JSONResponse(content=response_body)
    finally:
        if span_cm is not None:
            span_cm.__exit__(None, None, None)


@mcp_server.tool(name="list_models")
async def mcp_list_models() -> dict[str, list[str]]:
    """List model names currently available through Agent Proxy."""

    return {"models": await list_tags()}


def _mcp_prompt_response(payload: dict[str, Any]) -> dict[str, Any]:
    if "error" in payload:
        return payload
    choice = payload["choices"][0]
    message = choice["message"]
    return {
        "model": payload["model"],
        "content": message.get("content"),
        "reasoning_content": message.get("reasoning_content", ""),
        "tool_calls": message.get("tool_calls", []),
        "finish_reason": choice.get("finish_reason"),
        "usage": payload.get("usage", {}),
    }


@mcp_server.tool(name="send_prompt")
async def mcp_send_prompt(
    prompt: str,
    model: str,
    system_prompt: str = "",
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Send one prompt through Agent Proxy's policy and reliability path."""

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    request_id = str(uuid.uuid4())
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "metadata": {"request_id": request_id, "ward.harness": "mcp"},
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature

    capture_request = {
        "prompt": prompt,
        "model": model,
        "system_prompt": system_prompt,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    response = await _chat_completions(
        body,
        {},
        request_kind="mcp_prompt",
        capture_request_body=capture_request,
        capture_response_transform=_mcp_prompt_response,
    )
    payload = json.loads(bytes(response.body))
    if response.status_code >= 400:
        error = payload.get("error", {})
        raise ToolError(str(error.get("message", "Agent Proxy request failed")))

    return _mcp_prompt_response(payload)


app.mount("/mcp", mcp_server.streamable_http_app())


def _text_completion_response(model_name: str, result: upstream.UpstreamResult) -> dict[str, Any]:
    return {
        "id": f"cmpl-{uuid.uuid4().hex}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {"index": 0, "text": result.content or "", "finish_reason": _finish_reason(result)}
        ],
        "usage": _usage_block(result),
    }


@app.post("/v1/completions")
async def completions(request: Request) -> Response:
    """Legacy text-completion surface. Modeled as a single user turn so it rides
    the same resilience path, then shaped back to the ``text_completion`` schema."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid JSON body", "invalid_request_error")
    if not isinstance(body, dict):
        return _error(400, "JSON body must be an object", "invalid_request_error")

    requested_model = body.get("model")
    model_name = requested_model if isinstance(requested_model, str) else ""
    try:
        model = await resolve(model_name) if model_name else None
    except RouteUnavailable as exc:
        return _error(503, str(exc), "model_unavailable")
    if model is None:
        return _error(404, f"unknown model '{requested_model}'", "model_not_found")
    llm_route_requests_total.labels(
        logical_model=model.name,
        upstream_mode=model.upstream_mode,
    ).inc()

    prompt = body.get("prompt", "")
    if isinstance(prompt, list):
        prompt = "\n".join(str(p) for p in prompt)
    messages = [{"role": "user", "content": prompt}]
    options = _options_from_openai(body)

    settings = get_settings()
    messages, prompt_tokens, _trimmed = apply_context_budget(
        model.name, messages, model.num_ctx, settings.num_ctx_headroom
    )
    llm_prompt_tokens.labels(logical_model=model.name).observe(prompt_tokens)
    trace_extra = _request_trace_extra(request.headers, body.get("metadata"))
    request_id = (
        request.headers.get("x-request-id", "")
        or str(trace_extra.get("agentproxy.request_id", ""))
        or str(uuid.uuid4())
    )
    trace_ctx = _trace_context(
        model.name,
        model_name,
        "completions",
        request_id,
        upstream_mode=model.upstream_mode,
        extra=trace_extra,
    )
    normalized_request = dict(body)
    normalized_request["prompt"] = prompt
    capture = ModelBodyCapture(
        enabled=trace_ctx.trace_bodies,
        request_id=request_id,
        request_body=normalized_request,
        expected_span_name="request.completions",
    )
    tracer = get_tracer()
    request_span = get_current_trace_span()
    lifecycle = RequestLifecycle.from_trace_context(
        trace_ctx,
        occurred_at=datetime.now(timezone.utc),
    )
    started = time.perf_counter()
    _emit_trajectory_event(lifecycle.action_event())

    shape_attrs = _request_shape_attrs(messages, None)

    span_cm = tracer.start_as_current_span("request.completions") if tracer is not None else None
    if span_cm is not None:
        request_span = span_cm.__enter__()
    try:
        for key, value in {**trace_ctx.attrs(), **shape_attrs}.items():
            if request_span is not None:
                request_span.set_attribute(key, value)
        _emit_capture_request(capture, request_span)
        log.info("request.accepted", **trace_ctx.attrs(), outcome="accepted")
        try:
            result = await get_queue().submit(model, messages, None, options, trace_ctx=trace_ctx)
        except asyncio.CancelledError:
            _emit_request_terminal(lifecycle, "cancelled", started=started)
            llm_requests_total.labels(logical_model=model.name, outcome="cancelled").inc()
            _mark_cancelled_span(request_span, "request.cancelled")
            _emit_capture_response(
                capture,
                request_span,
                {},
                status="incomplete",
                reason="cancelled",
            )
            log_on_span(
                request_span,
                "request.completed",
                **trace_ctx.attrs(),
                outcome="cancelled",
            )
            raise
        except QueueBusy:
            error_body = _error_body("proxy queue is full, retry shortly", "rate_limit_error")
            _emit_request_terminal(lifecycle, "queue_rejected", started=started)
            llm_requests_total.labels(logical_model=model.name, outcome="rejected").inc()
            _emit_capture_response(
                capture,
                request_span,
                error_body,
                status="incomplete",
                reason="queue_rejected",
            )
            log_on_span(
                request_span,
                "request.completed",
                "warning",
                **trace_ctx.attrs(),
                outcome="rejected",
            )
            return _error(429, "proxy queue is full, retry shortly", "rate_limit_error")
        except ContextTruncated as exc:
            error_body = _error_body(str(exc), "context_truncated")
            _emit_request_terminal(lifecycle, "context_truncated", started=started)
            llm_requests_total.labels(logical_model=model.name, outcome="context_truncated").inc()
            _emit_capture_response(
                capture,
                request_span,
                error_body,
                status="incomplete",
                reason="context_truncated",
            )
            log_on_span(
                request_span,
                "request.completed",
                "warning",
                **trace_ctx.attrs(),
                outcome="context-truncated",
                error=str(exc),
            )
            return _error(502, str(exc), "context_truncated")
        except AllBackendsFailed as exc:
            error_body = _error_body(str(exc), "upstream_error")
            _emit_request_terminal(lifecycle, "upstream_failed", started=started)
            llm_requests_total.labels(logical_model=model.name, outcome="failed").inc()
            _emit_capture_response(
                capture,
                request_span,
                error_body,
                status="incomplete",
                reason="upstream_failed",
            )
            log_on_span(
                request_span,
                "request.completed",
                "warning",
                **trace_ctx.attrs(),
                outcome="failed",
                error=str(exc),
            )
            return _error(502, str(exc), "upstream_error")
        except BodyCaptureError:
            raise
        except Exception:
            _emit_capture_response(
                capture,
                request_span,
                {},
                status="incomplete",
                reason="upstream_failed",
            )
            raise

        try:
            if request_span is not None:
                upstream.set_result_span_attributes(request_span, result)
            response_body = _text_completion_response(model.name, result)
            _emit_capture_response(capture, request_span, response_body)
        except BodyCaptureError:
            raise
        except Exception:
            _emit_capture_response(
                capture,
                request_span,
                {},
                status="incomplete",
                reason="response_failed",
            )
            raise
        llm_requests_total.labels(logical_model=model.name, outcome="ok").inc()
        _record_prompt_cache(model.name, result)
        _emit_request_terminal(lifecycle, "succeeded", started=started, result=result)
        log_on_span(
            request_span,
            "request.completed",
            **trace_ctx.attrs(),
            outcome="ok",
        )
        return JSONResponse(content=response_body)
    finally:
        if span_cm is not None:
            span_cm.__exit__(None, None, None)


# Container entrypoint


def main() -> None:
    """Serve ``app`` under hypercorn - the container CMD (``python -m app.main``)
    and the ``agent-proxy`` console script both land here. ``ward serve`` runs the
    same ``app`` object under uvicorn instead. Hypercorn drives the ASGI lifespan,
    so the queue starts and stops with the server."""
    import asyncio

    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    settings = get_settings()
    config = Config()
    config.bind = [f"{settings.proxy_host}:{settings.proxy_port}"]
    config.loglevel = settings.log_level.upper()

    log.info("serve.start", bind=config.bind[0])
    # hypercorn types `serve` against a narrow ASGIFramework protocol. FastAPI
    # is a valid ASGI3 callable at runtime, so this is a stub false positive.
    asyncio.run(serve(app, config))  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
