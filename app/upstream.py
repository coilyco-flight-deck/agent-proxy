"""
Upstream client (leg 02 "upstream client", leg 04 step 2).

Forwards a chat/completion to a backend's **native ollama** API
(``/api/chat`` or ``/api/generate``) with ``options.num_ctx`` injected. Injecting
the correct per-model ``num_ctx`` is the highest-value fix: it removes the silent
32k left-truncation that every ``/v1`` OpenAI-compatible harness rides into
(leg 01). The client speaks ollama natively so it controls ``options`` the way
the OpenAI-compatible passthrough cannot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from .config import get_settings
from .models import Backend


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


async def chat(
    backend: Backend,
    num_ctx: int,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
) -> UpstreamResult:
    """Non-streaming native ``/api/chat`` call with ``num_ctx`` injected."""
    body: dict[str, Any] = {
        "model": backend.ollama_tag,
        "messages": messages,
        "stream": False,
        "options": _inject_options(options, num_ctx),
    }
    if tools:
        body["tools"] = tools
    try:
        resp = await get_client().post(f"{backend.url}/api/chat", json=body, timeout=_timeout(backend))
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise UpstreamError(f"{backend.name}: {exc}") from exc
    return _parse_chat_response(resp.json())


async def chat_stream(
    backend: Backend,
    num_ctx: int,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    options: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Streaming native ``/api/chat`` - yields raw ollama NDJSON chunks."""
    body: dict[str, Any] = {
        "model": backend.ollama_tag,
        "messages": messages,
        "stream": True,
        "options": _inject_options(options, num_ctx),
    }
    if tools:
        body["tools"] = tools
    try:
        async with get_client().stream(
            "POST", f"{backend.url}/api/chat", json=body, timeout=_timeout(backend)
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except httpx.HTTPError as exc:
        raise UpstreamError(f"{backend.name}: {exc}") from exc


async def generate(
    backend: Backend,
    num_ctx: int,
    prompt: str,
    *,
    options: dict[str, Any] | None = None,
) -> UpstreamResult:
    """Non-streaming native ``/api/generate`` for the ``/v1/completions`` surface."""
    body: dict[str, Any] = {
        "model": backend.ollama_tag,
        "prompt": prompt,
        "stream": False,
        "options": _inject_options(options, num_ctx),
    }
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
        resp = await get_client().get(f"{backend.url}/api/version", timeout=httpx.Timeout(5.0))
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
