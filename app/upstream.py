"""
Upstream client (leg 02 "upstream client", leg 04 step 2).

Forwards a chat/completion to a backend's native API with the safe model
context injected when that backend accepts it. Ollama backends speak
``/api/chat`` or ``/api/generate`` and take ``options.num_ctx``. OpenAI-shaped
backends like LiteLLM speak ``/v1/chat/completions`` and take a top-level
``num_ctx`` extension. Both normalize back to the proxy's canonical internal
shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, TypedDict

import httpx

from .config import get_settings
from .models import Backend
from .obs import get_tracer, log


class UpstreamError(Exception):
    """A backend call failed at the transport/HTTP level (not a bad generation)."""


class RequestAuthKwargs(TypedDict, total=False):
    headers: dict[str, str]


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
    # Set by dispatch (issue #33) when the backend delivered a shorter context than
    # asked for - the OLLAMA_NUM_PARALLEL division. Surfaced loud, never silent.
    context_truncated: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


_client: httpx.AsyncClient | None = None

_CORRELATION_METADATA_KEYS = (
    "agentproxy.logical_model",
    "agentproxy.upstream_mode",
    "agentproxy.request_kind",
    "agentproxy.request_id",
    "ward.run_id",
    "ward.container_name",
    "ward.role",
    "ward.harness",
    "ward.target_repo",
    "ward.issue_ref",
    "ward.workflow",
    "ward.context_level",
    "ward.version",
    "agent.session_id",
)


def _correlation_log_fields(span_attrs: dict[str, Any] | None) -> dict[str, Any]:
    """Keep request joins in upstream logs without copying opt-in body fields."""
    if not span_attrs:
        return {}

    fields: dict[str, Any] = {}
    mapped_keys = {
        "agentproxy.logical_model": "logical_model",
        "gen_ai.request.model": "request_model",
        "agentproxy.request_kind": "request_kind",
        "agentproxy.request_id": "request_id",
    }
    for source, target in mapped_keys.items():
        if source in span_attrs:
            fields[target] = span_attrs[source]

    for key in (
        "ward.run_id",
        "ward.container_name",
        "ward.role",
        "ward.harness",
        "ward.target_repo",
        "ward.issue_ref",
        "ward.workflow",
        "ward.context_level",
        "ward.version",
        "agent.session_id",
    ):
        if key in span_attrs:
            fields[key] = span_attrs[key]
    return fields


def _gateway_metadata(span_attrs: dict[str, Any] | None) -> dict[str, Any]:
    """Return the body-safe correlation fields forwarded through LiteLLM."""
    if not span_attrs:
        return {}
    return {key: span_attrs[key] for key in _CORRELATION_METADATA_KEYS if key in span_attrs}


def request_auth_kwargs(backend: Backend) -> RequestAuthKwargs:
    """Build an authenticated request kwarg without exposing the key.

    The backend spec contains only a mounted file path. The secret value never
    enters tracked configuration, argv, logs, exception text, or span
    attributes.
    """
    if not backend.api_key_file:
        return {}
    try:
        key = Path(backend.api_key_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise UpstreamError(f"{backend.name}: authentication key unavailable") from exc
    if not key:
        raise UpstreamError(f"{backend.name}: authentication key unavailable")
    return {"headers": {"Authorization": f"Bearer {key}"}}


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


def _inject_options(
    base: dict[str, Any] | None, num_ctx: int, num_parallel: int = 1
) -> dict[str, Any]:
    """Return a fresh options dict with the model's safe ``num_ctx`` injected.

    The injected ``num_ctx`` always wins - it is the whole point of the proxy, so
    a caller-supplied value never overrides the model's proven-safe ceiling.

    ollama loads ``num_ctx`` as the model's *total* context and splits it across
    ``OLLAMA_NUM_PARALLEL`` slots, so the usable per-request window is
    ``num_ctx / NUM_PARALLEL`` (issue #33). To keep each request's window equal to
    the intended ``num_ctx``, the injected value is scaled by the backend's
    ``num_parallel``; with the default (1) the injected value is unchanged.
    """
    opts = dict(base or {})
    opts["num_ctx"] = num_ctx * max(num_parallel, 1)
    return opts


def _openai_options(options: dict[str, Any] | None) -> dict[str, Any]:
    """Translate the proxy's Ollama-shaped sampling options back to OpenAI."""
    mapped = dict(options or {})
    if "num_predict" in mapped:
        mapped["max_tokens"] = mapped.pop("num_predict")
    mapped.pop("num_ctx", None)
    return mapped


def _chat_body(
    backend: Backend,
    num_ctx: int,
    messages: list[dict[str, Any]],
    *,
    stream: bool,
    tools: list[dict[str, Any]] | None,
    options: dict[str, Any] | None,
    span_attrs: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shape one request while preserving Agent Proxy's safe-context policy."""
    body: dict[str, Any] = {
        "model": backend.ollama_tag,
        "messages": messages,
        "stream": stream,
    }
    if backend.dialect == "ollama":
        if backend.injects_num_ctx:
            body["options"] = _inject_options(options, num_ctx, backend.num_parallel)
        elif options:
            body["options"] = dict(options)
    else:
        body.update(_openai_options(options))
        if backend.injects_num_ctx:
            body["num_ctx"] = num_ctx
        if metadata := _gateway_metadata(span_attrs):
            body["metadata"] = metadata
    if tools:
        body["tools"] = tools
    return body


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
    body = _chat_body(
        backend,
        num_ctx,
        messages,
        stream=False,
        tools=tools,
        options=options,
        span_attrs=span_attrs,
    )
    auth_kwargs = request_auth_kwargs(backend)
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
            resp = await get_client().post(
                f"{backend.url}{_chat_path(backend)}",
                json=body,
                timeout=_timeout(backend),
                **auth_kwargs,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{backend.name}: {exc}") from exc
        return (
            _parse_chat_response(resp.json())
            if backend.dialect == "ollama"
            else _parse_openai_chat_response(resp.json())
        )
    with tracer.start_as_current_span("upstream.chat") as span:
        for key, value in attrs.items():
            span.set_attribute(key, value)
        try:
            resp = await get_client().post(
                f"{backend.url}{_chat_path(backend)}",
                json=body,
                timeout=_timeout(backend),
                **auth_kwargs,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            span.record_exception(exc)
            span.set_attribute("agentproxy.upstream.error", str(exc))
            log.warning(
                "upstream.completed",
                **_correlation_log_fields(span_attrs),
                backend=backend.name,
                backend_dialect=backend.dialect,
                outcome="failed",
            )
            raise UpstreamError(f"{backend.name}: {exc}") from exc
        result = (
            _parse_chat_response(resp.json())
            if backend.dialect == "ollama"
            else _parse_openai_chat_response(resp.json())
        )
        span.set_attribute("gen_ai.usage.input_tokens", result.prompt_eval_count)
        span.set_attribute("gen_ai.usage.output_tokens", result.eval_count)
        span.set_attribute(
            "response.finish_reasons", [result.done_reason] if result.done_reason else []
        )
        log.info(
            "upstream.completed",
            **_correlation_log_fields(span_attrs),
            backend=backend.name,
            backend_dialect=backend.dialect,
            outcome="ok",
        )
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
    body = _chat_body(
        backend,
        num_ctx,
        messages,
        stream=True,
        tools=tools,
        options=options,
        span_attrs=span_attrs,
    )
    auth_kwargs = request_auth_kwargs(backend)
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
            "POST",
            f"{backend.url}{_chat_path(backend)}",
            json=body,
            timeout=_timeout(backend),
            **auth_kwargs,
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
                out: dict[str, Any] = {
                    "message": message,
                    "done": choice.get("finish_reason") is not None,
                }
                if choice.get("finish_reason"):
                    out["done_reason"] = choice.get("finish_reason")
                yield out
        log.info(
            "upstream.completed",
            **_correlation_log_fields(span_attrs),
            backend=backend.name,
            backend_dialect=backend.dialect,
            outcome="ok",
        )
    except httpx.HTTPError as exc:
        if span_cm is not None:
            span.record_exception(exc)
            span.set_attribute("agentproxy.upstream.error", str(exc))
        log.warning(
            "upstream.completed",
            **_correlation_log_fields(span_attrs),
            backend=backend.name,
            backend_dialect=backend.dialect,
            outcome="failed",
        )
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
    if backend.dialect == "ollama":
        if backend.injects_num_ctx:
            body["options"] = _inject_options(options, num_ctx, backend.num_parallel)
        elif options:
            body["options"] = dict(options)
        path = "/api/generate"
    else:
        body.update(_openai_options(options))
        if backend.injects_num_ctx:
            body["num_ctx"] = num_ctx
        path = "/v1/completions"
    try:
        resp = await get_client().post(
            f"{backend.url}{path}",
            json=body,
            timeout=_timeout(backend),
            **request_auth_kwargs(backend),
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise UpstreamError(f"{backend.name}: {exc}") from exc
    data = resp.json()
    if backend.dialect == "ollama":
        return UpstreamResult(
            model=data.get("model", ""),
            content=data.get("response", "") or "",
            prompt_eval_count=int(data.get("prompt_eval_count", 0) or 0),
            eval_count=int(data.get("eval_count", 0) or 0),
            done_reason=data.get("done_reason", "stop") or "stop",
            raw=data,
        )
    choice = (data.get("choices") or [{}])[0]
    usage = data.get("usage") or {}
    return UpstreamResult(
        model=data.get("model", ""),
        content=choice.get("text", "") or "",
        prompt_eval_count=int(usage.get("prompt_tokens", 0) or 0),
        eval_count=int(usage.get("completion_tokens", 0) or 0),
        done_reason=choice.get("finish_reason", "stop") or "stop",
        raw=data,
    )


async def health(backend: Backend) -> bool:
    """Cheap liveness probe used by the circuit breaker's half-open recovery."""
    try:
        resp = await get_client().get(
            f"{backend.url}{_health_path(backend)}",
            timeout=httpx.Timeout(5.0),
            **request_auth_kwargs(backend),
        )
        return resp.status_code == 200
    except (httpx.HTTPError, UpstreamError):
        return False
