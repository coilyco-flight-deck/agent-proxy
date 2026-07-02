"""
FastAPI entrypoint: the OpenAI-compatible surface plus health and metrics
(leg 02 "web server", leg 04 steps 1 and 6).

Every harness points here unchanged. Requests carry a *logical* model name
(``fast-think`` etc.); the proxy resolves it to a backend and the model's safe
``num_ctx``, guards the context budget, and dispatches through the queue and the
resilience policies. Responses are shaped to the OpenAI schema so no harness
needs special handling.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST

from . import resilience, upstream
from .analysis import apply_context_budget
from .config import get_settings
from .models import get_registry
from .obs import (
    RequestTraceContext,
    get_tracer,
    is_trace_bodies_enabled,
    llm_prompt_tokens,
    llm_requests_total,
    log,
    metrics_text,
)
from .queue import QueueBusy, get_queue
from .resilience import AllBackendsFailed

# obs is wired at import (app.obs runs setup_observability at module load).


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Best-effort auto-instrumentation; degrades silently if the SDK is absent.
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        pass
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass

    await get_queue().start()
    log.info("startup.complete", models=get_registry().names())
    try:
        yield
    finally:
        await get_queue().stop()
        await upstream.aclose()


app = FastAPI(title="agent-proxy", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Health + metrics
# --------------------------------------------------------------------------- #


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=metrics_text(), media_type=CONTENT_TYPE_LATEST)


# --------------------------------------------------------------------------- #
# OpenAI <-> ollama translation helpers
# --------------------------------------------------------------------------- #


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
    return "length" if result.done_reason == "length" else "stop"


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
        "usage": {
            "prompt_tokens": result.prompt_eval_count,
            "completion_tokens": result.eval_count,
            "total_tokens": result.prompt_eval_count + result.eval_count,
        },
    }


def _error(status: int, message: str, err_type: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"message": message, "type": err_type}}
    )


def _trace_context(
    model_name: str,
    request_model: str,
    request_kind: str,
    request_id: str = "",
    *,
    extra: dict[str, object] | None = None,
) -> RequestTraceContext:
    return RequestTraceContext(
        logical_model=model_name,
        request_model=request_model,
        request_kind=request_kind,
        trace_bodies=is_trace_bodies_enabled(),
        request_id=request_id,
        extra=extra or {},
    )


# --------------------------------------------------------------------------- #
# OpenAI surface
# --------------------------------------------------------------------------- #


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    created = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model", "created": created, "owned_by": "agent-proxy"}
            for name in get_registry().names()
        ],
    }


async def _stream_chat(model, messages, tools, options, model_name: str) -> StreamingResponse:
    """Translate ollama's NDJSON stream into OpenAI ``chat.completion.chunk`` SSE."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    trace_ctx = _trace_context(
        model.name,
        model_name,
        "chat",
        extra=(
            {
                "agentproxy.messages": messages,
                "agentproxy.tools": tools or [],
                "agentproxy.options": options,
            }
            if is_trace_bodies_enabled()
            else None
        ),
    )
    tracer = get_tracer()

    async def gen() -> AsyncIterator[str]:
        base = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
        }
        # Prime with the assistant role delta (OpenAI clients expect it first).
        first = {
            **base,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(first)}\n\n"
        finish = "stop"
        try:
            if tracer is None:
                async for chunk in resilience.dispatch_stream(
                    model, messages, tools=tools, options=options, trace_ctx=trace_ctx
                ):
                    msg = chunk.get("message") or {}
                    piece = msg.get("content") or ""
                    if piece:
                        delta = {
                            **base,
                            "choices": [
                                {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                            ],
                        }
                        yield f"data: {json.dumps(delta)}\n\n"
                    if chunk.get("done"):
                        finish = "length" if chunk.get("done_reason") == "length" else "stop"
            else:
                with tracer.start_as_current_span("request.chat") as span:
                    for key, value in trace_ctx.attrs().items():
                        span.set_attribute(key, value)
                    async for chunk in resilience.dispatch_stream(
                        model, messages, tools=tools, options=options, trace_ctx=trace_ctx
                    ):
                        msg = chunk.get("message") or {}
                        piece = msg.get("content") or ""
                        if piece:
                            delta = {
                                **base,
                                "choices": [
                                    {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                                ],
                            }
                            yield f"data: {json.dumps(delta)}\n\n"
                        if chunk.get("done"):
                            finish = "length" if chunk.get("done_reason") == "length" else "stop"
        except AllBackendsFailed as exc:
            log.warning("stream.failed", **trace_ctx.attrs(), error=str(exc), outcome="failed")
            finish = "stop"
        final = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid JSON body", "invalid_request_error")

    model_name = body.get("model")
    model = get_registry().get(model_name) if model_name else None
    if model is None:
        return _error(404, f"unknown model '{model_name}'", "model_not_found")

    messages = body.get("messages") or []
    tools = body.get("tools")
    options = _options_from_openai(body)
    stream = bool(body.get("stream", False))

    settings = get_settings()
    messages, prompt_tokens, _trimmed = apply_context_budget(
        model.name, messages, model.num_ctx, settings.num_ctx_headroom
    )
    llm_prompt_tokens.labels(logical_model=model.name).observe(prompt_tokens)
    trace_ctx = _trace_context(
        model.name,
        model_name,
        "chat",
        request.headers.get("x-request-id", ""),
        extra=(
            {
                "agentproxy.messages": messages,
                "agentproxy.tools": tools or [],
                "agentproxy.options": options,
            }
            if is_trace_bodies_enabled()
            else None
        ),
    )
    tracer = get_tracer()

    if stream:
        llm_requests_total.labels(logical_model=model.name, outcome="stream").inc()
        return await _stream_chat(model, messages, tools, options, model.name)

    try:
        if tracer is None:
            result = await get_queue().submit(model, messages, tools, options, trace_ctx=trace_ctx)
        else:
            with tracer.start_as_current_span("request.chat") as span:
                for key, value in trace_ctx.attrs().items():
                    span.set_attribute(key, value)
                result = await get_queue().submit(
                    model, messages, tools, options, trace_ctx=trace_ctx
                )
                span.set_attribute("gen_ai.usage.input_tokens", result.prompt_eval_count)
                span.set_attribute("gen_ai.usage.output_tokens", result.eval_count)
                span.set_attribute(
                    "response.finish_reasons", [result.done_reason] if result.done_reason else []
                )
    except QueueBusy:
        llm_requests_total.labels(logical_model=model.name, outcome="rejected").inc()
        log.warning("request.rejected", **trace_ctx.attrs(), outcome="rejected")
        return _error(429, "proxy queue is full, retry shortly", "rate_limit_error")
    except AllBackendsFailed as exc:
        llm_requests_total.labels(logical_model=model.name, outcome="failed").inc()
        log.warning("request.failed", **trace_ctx.attrs(), outcome="failed", error=str(exc))
        return _error(502, str(exc), "upstream_error")

    llm_requests_total.labels(logical_model=model.name, outcome="ok").inc()
    log.info("request.ok", **trace_ctx.attrs(), outcome="ok")
    return JSONResponse(content=_chat_completion_response(model.name, result))


@app.post("/v1/completions")
async def completions(request: Request) -> Response:
    """Legacy text-completion surface. Modeled as a single user turn so it rides
    the same resilience path, then shaped back to the ``text_completion`` schema."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid JSON body", "invalid_request_error")

    model_name = body.get("model")
    model = get_registry().get(model_name) if model_name else None
    if model is None:
        return _error(404, f"unknown model '{model_name}'", "model_not_found")

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
    trace_ctx = _trace_context(
        model.name,
        model_name,
        "completions",
        request.headers.get("x-request-id", ""),
        extra=(
            {"agentproxy.prompt": prompt, "agentproxy.options": options}
            if is_trace_bodies_enabled()
            else None
        ),
    )
    tracer = get_tracer()

    try:
        if tracer is None:
            result = await get_queue().submit(model, messages, None, options, trace_ctx=trace_ctx)
        else:
            with tracer.start_as_current_span("request.completions") as span:
                for key, value in trace_ctx.attrs().items():
                    span.set_attribute(key, value)
                result = await get_queue().submit(
                    model, messages, None, options, trace_ctx=trace_ctx
                )
                span.set_attribute("gen_ai.usage.input_tokens", result.prompt_eval_count)
                span.set_attribute("gen_ai.usage.output_tokens", result.eval_count)
                span.set_attribute(
                    "response.finish_reasons", [result.done_reason] if result.done_reason else []
                )
    except QueueBusy:
        llm_requests_total.labels(logical_model=model.name, outcome="rejected").inc()
        log.warning("request.rejected", **trace_ctx.attrs(), outcome="rejected")
        return _error(429, "proxy queue is full, retry shortly", "rate_limit_error")
    except AllBackendsFailed as exc:
        llm_requests_total.labels(logical_model=model.name, outcome="failed").inc()
        log.warning("request.failed", **trace_ctx.attrs(), outcome="failed", error=str(exc))
        return _error(502, str(exc), "upstream_error")

    llm_requests_total.labels(logical_model=model.name, outcome="ok").inc()
    log.info("request.ok", **trace_ctx.attrs(), outcome="ok")
    return JSONResponse(
        content={
            "id": f"cmpl-{uuid.uuid4().hex}",
            "object": "text_completion",
            "created": int(time.time()),
            "model": model.name,
            "choices": [
                {"index": 0, "text": result.content or "", "finish_reason": _finish_reason(result)}
            ],
            "usage": {
                "prompt_tokens": result.prompt_eval_count,
                "completion_tokens": result.eval_count,
                "total_tokens": result.prompt_eval_count + result.eval_count,
            },
        }
    )


# --------------------------------------------------------------------------- #
# Container entrypoint
# --------------------------------------------------------------------------- #


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
    asyncio.run(serve(app, config))


if __name__ == "__main__":
    main()
