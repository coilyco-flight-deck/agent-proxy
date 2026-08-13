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
        ("resilience.attempt", "dispatch.cancelled"),
        ("upstream.chat", "upstream.cancelled"),
    ):
        span = next(candidate for candidate in spans if candidate.name == name)
        assert (
            span.attributes.get("agentproxy.outcome") == "cancelled"
            or span.attributes.get("agentproxy.upstream.outcome") == "cancelled"
        )
        assert event in {record.name for record in span.events}

    # queue.wait closed at admission, long before the cancellation, so it
    # reports what it saw rather than an outcome it never observed (issue #105).
    wait_span = next(candidate for candidate in spans if candidate.name == "queue.wait")
    assert wait_span.attributes.get("agentproxy.queue.admitted") is True
    assert wait_span.attributes.get("agentproxy.outcome") is None


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


# Total request deadline (issue #112). The timeout ladder measured there was
# inverted at every layer: caller 180s, agent-proxy 240s, litellm 600s, ~1004s.


async def test_deadline_cuts_the_attempt_and_stops_upstream_work(monkeypatch):
    upstream_cancelled = asyncio.Event()
    never_answers = asyncio.Event()

    async def hangs(request: httpx.Request) -> httpx.Response:
        try:
            await never_answers.wait()
        except asyncio.CancelledError:
            upstream_cancelled.set()
            raise
        raise AssertionError("the transport should never answer")

    client = httpx.AsyncClient(transport=httpx.MockTransport(hangs))
    monkeypatch.setattr(upstream, "get_client", lambda: client)
    monkeypatch.setattr(resilience, "breakers", resilience.CircuitBreakerRegistry())
    model = LogicalModel(
        name="sirens-echo/deepseek",
        num_ctx=4096,
        backends=[
            Backend(
                name="litellm",
                url="http://litellm.invalid",
                ollama_tag="deepseek",
                dialect="openai",
                injects_num_ctx=False,
            )
        ],
        upstream_mode="litellm",
    )

    with pytest.raises(resilience.RequestDeadlineExceeded):
        await resilience.dispatch(
            model,
            [{"role": "user", "content": "hi"}],
            deadline=resilience._now() + 0.05,
        )

    # The attempt was cut, so the connection closed instead of being abandoned
    # while the upstream kept generating for nobody.
    await asyncio.wait_for(upstream_cancelled.wait(), timeout=1)


async def test_expired_deadline_starts_no_attempt(monkeypatch):
    calls: list[str] = []

    async def never_called(*args, **kwargs):
        calls.append("called")
        raise AssertionError("an expired budget must not reach the upstream")

    monkeypatch.setattr(upstream, "chat", never_called)
    monkeypatch.setattr(resilience, "breakers", resilience.CircuitBreakerRegistry())
    model = LogicalModel(
        name="expired",
        num_ctx=4096,
        backends=[Backend(name="b", url="http://x", ollama_tag="t")],
    )

    with pytest.raises(resilience.RequestDeadlineExceeded):
        await resilience.dispatch(
            model, [{"role": "user", "content": "hi"}], deadline=resilience._now() - 1
        )

    assert calls == []


def test_caller_deadline_only_shortens_the_configured_one(monkeypatch):
    settings = resilience.get_settings()
    monkeypatch.setattr(settings, "request_deadline", 100.0)
    base = resilience._now()

    # A caller asking for less gets less.
    assert resilience.request_deadline(10.0) - base == pytest.approx(10.0, abs=0.5)
    # A caller asking for more is held to the operator's ceiling.
    assert resilience.request_deadline(1000.0) - base == pytest.approx(100.0, abs=0.5)


def test_no_deadline_configured_means_unbounded(monkeypatch):
    monkeypatch.setattr(resilience.get_settings(), "request_deadline", 0.0)
    assert resilience.request_deadline(None) is None


def test_caller_header_alone_bounds_an_unconfigured_deadline(monkeypatch):
    monkeypatch.setattr(resilience.get_settings(), "request_deadline", 0.0)
    base = resilience._now()
    assert resilience.request_deadline(30.0) - base == pytest.approx(30.0, abs=0.5)


def test_caller_deadline_header_is_read_from_either_spelling():
    assert main._caller_deadline_ms({"x-request-deadline-ms": "1500"}) == 1.5
    assert main._caller_deadline_ms({"x-request-timeout-ms": "2000"}) == 2.0
    assert main._caller_deadline_ms({"x-request-deadline-ms": "not-a-number"}) is None
    assert main._caller_deadline_ms({"x-request-deadline-ms": "-5"}) is None
    assert main._caller_deadline_ms({}) is None


async def test_completions_disconnect_cancels_upstream(monkeypatch):
    """Issue #112: the legacy surface abandoned work the chat surface cancels."""
    upstream_started = asyncio.Event()
    upstream_cancelled = asyncio.Event()
    blocked = asyncio.Event()

    async def blocking_upstream(request: httpx.Request) -> httpx.Response:
        upstream_started.set()
        try:
            await blocked.wait()
        except asyncio.CancelledError:
            upstream_cancelled.set()
            raise
        raise AssertionError("the transport should never answer")

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
            )
        ],
        upstream_mode="litellm",
    )

    async def resolve_model(name: str) -> LogicalModel | None:
        return model if name == model.name else None

    queue = WorkQueue(maxsize=1, worker_count=1)
    await queue.start()
    monkeypatch.setattr(main, "resolve", resolve_model)
    monkeypatch.setattr(main, "get_queue", lambda: queue)

    request, receive = _request({"model": model.name, "prompt": "block"})
    request.scope["path"] = "/v1/completions"
    try:
        handler = asyncio.create_task(main.completions(request))
        await asyncio.wait_for(upstream_started.wait(), timeout=1)
        receive.put_nowait({"type": "http.disconnect"})

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(handler, timeout=1)
        await asyncio.wait_for(upstream_cancelled.wait(), timeout=1)
    finally:
        await queue.stop()
        await client.aclose()
