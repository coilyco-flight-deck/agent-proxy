"""Downstream cancellation propagates through queue, resilience, and httpx."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from starlette.requests import Request

from app import main, resilience, upstream
from app.models import Backend, LogicalModel
from app.queue import WorkQueue


def _request(body: dict[str, Any]) -> tuple[Request, asyncio.Queue[dict[str, Any]]]:
    receive: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    receive.put_nowait(
        {
            "type": "http.request",
            "body": json.dumps(body).encode(),
            "more_body": False,
        }
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }
    return Request(scope, receive=receive.get), receive


def _request_id(record: dict[str, Any]) -> str:
    return str(record.get("request_id") or record.get("agentproxy.request_id") or "")


async def test_disconnect_cancels_upstream_and_releases_capacity(monkeypatch, capsys):
    upstream_started = asyncio.Event()
    upstream_cancelled = asyncio.Event()
    upstream_finished = asyncio.Event()
    blocked = asyncio.Event()
    calls: list[str] = []

    async def blocking_upstream(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.host))
        if len(calls) == 1:
            upstream_started.set()
            try:
                await blocked.wait()
            except asyncio.CancelledError:
                upstream_cancelled.set()
                raise
            finally:
                upstream_finished.set()
        return httpx.Response(
            200,
            json={
                "model": "fixture-provider-model",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ordinary success"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(blocking_upstream))
    monkeypatch.setattr(upstream, "get_client", lambda: client)
    monkeypatch.setattr(resilience, "breakers", resilience.CircuitBreakerRegistry())

    model = LogicalModel(
        name="sirens-echo/default",
        num_ctx=4096,
        backends=[
            Backend(
                name="primary",
                url="http://primary.invalid",
                ollama_tag="fixture-provider-model",
                dialect="openai",
                injects_num_ctx=False,
            ),
            Backend(
                name="fallback",
                url="http://fallback.invalid",
                ollama_tag="fixture-provider-model",
                dialect="openai",
                injects_num_ctx=False,
            ),
        ],
        upstream_mode="litellm",
    )

    async def resolve_model(name: str) -> LogicalModel | None:
        return model if name == model.name else None

    queue = WorkQueue(maxsize=1, worker_count=1)
    await queue.start()
    monkeypatch.setattr(main, "resolve", resolve_model)
    monkeypatch.setattr(main, "get_queue", lambda: queue)

    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    exporter.clear()

    cancelled_request, receive = _request(
        {
            "model": model.name,
            "messages": [{"role": "user", "content": "block"}],
            "metadata": {"request_id": "cancelled-request"},
        }
    )

    try:
        handler = asyncio.create_task(main.chat_completions(cancelled_request))
        await asyncio.wait_for(upstream_started.wait(), timeout=1)
        receive.put_nowait({"type": "http.disconnect"})

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(handler, timeout=1)
        await asyncio.wait_for(upstream_cancelled.wait(), timeout=1)
        await asyncio.wait_for(upstream_finished.wait(), timeout=1)

        # Cancellation is terminal. It does not spend a retry or enter fallback.
        assert calls == ["primary.invalid"]

        successful_request, _ = _request(
            {
                "model": model.name,
                "messages": [{"role": "user", "content": "continue"}],
                "metadata": {"request_id": "successful-request"},
            }
        )
        response = await asyncio.wait_for(main.chat_completions(successful_request), timeout=1)
        assert response.status_code == 200
        assert json.loads(bytes(response.body))["choices"][0]["message"]["content"] == (
            "ordinary success"
        )
        assert calls == ["primary.invalid", "primary.invalid"]
    finally:
        await queue.stop()
        await client.aclose()

    records = []
    for line in capsys.readouterr().out.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _request_id(record) == "cancelled-request":
            records.append(record)

    assert any(
        record.get("event") == "upstream.completed" and record.get("outcome") == "cancelled"
        for record in records
    )
    assert any(record.get("event") == "dispatch.cancelled" for record in records)
    assert any(record.get("event") == "queue.cancelled" for record in records)
    assert any(
        record.get("event") == "request.completed" and record.get("outcome") == "cancelled"
        for record in records
    )
    assert not any(record.get("event") == "dispatch.ok" for record in records)
    assert not any(
        record.get("event") == "request.completed" and record.get("outcome") == "ok"
        for record in records
    )

    spans = [
        span
        for span in exporter.get_finished_spans()
        if span.attributes.get("agentproxy.request_id") == "cancelled-request"
    ]
    for name, event in (
        ("request.chat", "request.cancelled"),
        ("queue.wait", "queue.cancelled"),
        ("resilience.attempt", "dispatch.cancelled"),
        ("upstream.chat", "upstream.cancelled"),
    ):
        span = next(candidate for candidate in spans if candidate.name == name)
        assert (
            span.attributes.get("agentproxy.outcome") == "cancelled"
            or span.attributes.get("agentproxy.upstream.outcome") == "cancelled"
        )
        assert event in {record.name for record in span.events}


async def test_enabled_capture_records_cancelled_response_as_incomplete(monkeypatch, capsys):
    model = LogicalModel(
        name="sirens-echo/default",
        num_ctx=4096,
        backends=[],
        upstream_mode="litellm",
    )

    async def resolve_model(name: str) -> LogicalModel | None:
        return model if name == model.name else None

    class CancellingQueue:
        async def submit(self, *_args, **_kwargs):
            raise asyncio.CancelledError

    monkeypatch.setattr(main, "resolve", resolve_model)
    monkeypatch.setattr(main, "get_queue", lambda: CancellingQueue())
    monkeypatch.setattr(main, "is_trace_bodies_enabled", lambda: True)
    capsys.readouterr()
    request, _ = _request(
        {
            "model": model.name,
            "messages": [{"role": "user", "content": "cancel me"}],
            "metadata": {"request_id": "capture-cancelled"},
        }
    )

    with pytest.raises(asyncio.CancelledError):
        await main.chat_completions(request)

    events = []
    for line in capsys.readouterr().out.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") in {"model.request.captured", "model.response.captured"}:
            events.append(record)
    assert len(events) == 2
    assert events[1]["agentproxy.capture.status"] == "incomplete"
    assert events[1]["agentproxy.capture.reason"] == "cancelled"
    assert events[1]["response.body"] == {}
