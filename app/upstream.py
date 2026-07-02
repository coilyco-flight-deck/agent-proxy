"""
Upstream client (leg 02 "upstream client", leg 04 step 2).

Forwards a chat/completion to a backend's native API with the safe model
context injected when that backend accepts it. Ollama backends speak
``/api/chat`` or ``/api/generate`` and take ``options.num_ctx``. OpenAI-shaped
backends like llama-server speak ``/v1/chat/completions`` and carry the context
at launch, so the client skips injection and normalizes the response back to the
proxy's canonical internal shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from .config import get_settings
from .models import Backend
from .obs import get_tracer


class UpstreamError(Exception):
    """A backend call failed at the transport/HTTP level (not a bad generation)."""


@dataclass
class UpstreamResult:
    """A normalized non-streaming result from a backend."""

    model: str
    content: str
    thinking: str = ""  # reasoning models (qwen3 *-think) emit this separately.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_eval_count: int = 0
    eval_count: int = 0
    done_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)


_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Shared async httpx client. Instrumented by OTel's httpx integration when wired."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _inject_options(base: dict[str, Any] | None, num_ctx: int) -> dict[str, Any]:
    """Return a fresh options dict with the model's safe ``num_ctx`` injected.

    The injected ``num_ctx`` always wins - it is the whole point of the proxy, so
    a caller-supplied value never overrides the model's proven-safe ceiling.
    """
    opts = dict(base or {})
    opts["num_ctx"] = num_ctx
    return opts


def _chat_path(backend: Backend) -> str:
    if backend.chat_path:
        return backend.chat_path
    return "/api/chat" if backend.dialect == "ollama" else "/v1/chat/completions"


def _health_path(backend: Backend) -> str:
    if backend.health_path:
        return backend.health_path
    return "/api/version" if backend.dialect == "ollama" else "/health"


def _timeout(backend: Backend) -> httpx.Timeout:
    t = backend.timeout if backend.timeout is not None else get_settings().request_timeout
    # No read timeout cap beyond t; connect kept short so a dead backend fails fast.
    return httpx.Timeout(t, connect=5.0)


def _parse_chat_response(data: dict[str, Any]) -> UpstreamResult:
    message = data.get("message") or {}
    return UpstreamResult(
        model=data.get("model", ""),
        content=message.get("content", "") or "",
        thinking=message.get("thinking", "") or "",
        tool_calls=message.get("tool_calls", []) or [],
        prompt_eval_count=int(data.get("prompt_eval_count", 0) or 0),
        eval_count=int(data.get("eval_count", 0) or 0),
        done_reason=data.get("done_reason", "stop") or "stop",
        raw=data,
    )


def _parse_openai_chat_response(data: dict[str, Any]) -> UpstreamResult:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return UpstreamResult(
        model=data.get("model", ""),
        content=message.get("content", "") or "",
        thinking=message.get("reasoning_content", "") or message.get("thinking", "") or "",
        tool_calls=message.get("tool_calls", []) or [],
        prompt_eval_count=int((data.get("usage") or {}).get("prompt_tokens", 0) or 0),
        eval_count=int((data.get("usage") or {}).get("completion_tokens", 0) or 0),
        done_reason=choice.get("finish_reason", "stop") or "stop",
        raw=data,
    )


async def chat(
    backend: Backend,
    num_ctx: int,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    span_attrs: dict[str, Any] | None = None,
) -> UpstreamResult:
    """Non-streaming upstream call with the safe context applied where valid."""
    body: dict[str, Any] = {"model": backend.ollama_tag, "messages": messages, "stream": False}
    if backend.injects_num_ctx:
        body["options"] = _inject_options(options, num_ctx)
    elif options:
        body.update(options)
    if tools:
        body["tools"] = tools
    tracer = get_tracer()
    attrs = {
        "agentproxy.backend": backend.name,
        "agentproxy.backend_dialect": backend.dialect,
        "agentproxy.resolved_backend": backend.url,
        "agentproxy.logical_num_ctx": num_ctx,
    }
    if span_attrs:
        attrs.update(span_attrs)
    if tracer is None:
        try:
            resp = await get_client().post(f"{backend.url}{_chat_path(backend)}", json=body, timeout=_timeout(backend))
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{backend.name}: {exc}") from exc
        return _parse_chat_response(resp.json()) if backend.dialect == "ollama" else _parse_openai_chat_response(resp.json())
    with tracer.start_as_current_span("upstream.chat") as span:
        for key, value in attrs.items():
            span.set_attribute(key, value)
        try:
            resp = await get_client().post(f"{backend.url}{_chat_path(backend)}", json=body, timeout=_timeout(backend))
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            span.record_exception(exc)
            span.set_attribute("agentproxy.upstream.error", str(exc))
            raise UpstreamError(f"{backend.name}: {exc}") from exc
        result = _parse_chat_response(resp.json()) if backend.dialect == "ollama" else _parse_openai_chat_response(resp.json())
        span.set_attribute("gen_ai.usage.input_tokens", result.prompt_eval_count)
        span.set_attribute("gen_ai.usage.output_tokens", result.eval_count)
        span.set_attribute("response.finish_reasons", [result.done_reason] if result.done_reason else [])
        return result


async def chat_stream(
    backend: Backend,
    num_ctx: int,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
    span_attrs: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streaming upstream call, normalized to the proxy's internal chunk shape."""
    body: dict[str, Any] = {"model": backend.ollama_tag, "messages": messages, "stream": True}
    if backend.injects_num_ctx:
        body["options"] = _inject_options(options, num_ctx)
    elif options:
        body.update(options)
    if tools:
        body["tools"] = tools
    tracer = get_tracer()
    attrs = {
        "agentproxy.backend": backend.name,
        "agentproxy.backend_dialect": backend.dialect,
        "agentproxy.resolved_backend": backend.url,
        "agentproxy.logical_num_ctx": num_ctx,
    }
    if span_attrs:
        attrs.update(span_attrs)
    span_cm = tracer.start_as_current_span("upstream.chat_stream") if tracer else None
    try:
        if span_cm is not None:
            span = span_cm.__enter__()
            for key, value in attrs.items():
                span.set_attribute(key, value)
        async with get_client().stream(
            "POST", f"{backend.url}{_chat_path(backend)}", json=body, timeout=_timeout(backend)
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    try:
                        payload = json.loads(line.removeprefix("data:").strip())
                    except json.JSONDecodeError:
                        continue
                if backend.dialect == "ollama":
                    yield payload
                    continue
                choice = (payload.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                message: dict[str, Any] = {}
                if content := delta.get("content"):
                    message["content"] = content
                if reasoning := delta.get("reasoning_content"):
                    message["thinking"] = reasoning
                if tool_calls := delta.get("tool_calls"):
                    message["tool_calls"] = tool_calls
                out: dict[str, Any] = {"message": message, "done": choice.get("finish_reason") is not None}
                if choice.get("finish_reason"):
                    out["done_reason"] = choice.get("finish_reason")
                yield out
    except httpx.HTTPError as exc:
        if span_cm is not None:
            span.record_exception(exc)
            span.set_attribute("agentproxy.upstream.error", str(exc))
        raise UpstreamError(f"{backend.name}: {exc}") from exc
    finally:
        if span_cm is not None:
            span_cm.__exit__(None, None, None)


async def generate(
    backend: Backend,
    num_ctx: int,
    prompt: str,
    *,
    options: dict[str, Any] | None = None,
) -> UpstreamResult:
    """Non-streaming native ``/api/generate`` for the ``/v1/completions`` surface."""
    body: dict[str, Any] = {"model": backend.ollama_tag, "prompt": prompt, "stream": False}
    if backend.injects_num_ctx:
        body["options"] = _inject_options(options, num_ctx)
    elif options:
        body.update(options)
    try:
        resp = await get_client().post(f"{backend.url}/api/generate", json=body, timeout=_timeout(backend))
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise UpstreamError(f"{backend.name}: {exc}") from exc
    data = resp.json()
    return UpstreamResult(
        model=data.get("model", ""),
        content=data.get("response", "") or "",
        prompt_eval_count=int(data.get("prompt_eval_count", 0) or 0),
        eval_count=int(data.get("eval_count", 0) or 0),
        done_reason=data.get("done_reason", "stop") or "stop",
        raw=data,
    )


async def health(backend: Backend) -> bool:
    """Cheap liveness probe used by the circuit breaker's half-open recovery."""
    try:
        resp = await get_client().get(f"{backend.url}{_health_path(backend)}", timeout=httpx.Timeout(5.0))
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
