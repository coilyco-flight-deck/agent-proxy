"""Bounded queue backpressure (leg 04 step 3)."""

import asyncio

import pytest

from app.models import LogicalModel, Backend
from app.queue import Job, QueueBusy, WorkQueue


def _model():
    return LogicalModel("fast", 32768, [Backend("b", "http://x", "t")])


async def test_full_queue_raises_queue_busy():
    # No workers, maxsize 1: the first put fills the queue, the next submit 429s.
    q = WorkQueue(maxsize=1, worker_count=0)
    q._queue = asyncio.Queue(maxsize=1)  # bind on this test's loop, no workers.
    loop = asyncio.get_running_loop()
    q._queue.put_nowait(Job(_model(), [], None, None, loop.create_future()))
    with pytest.raises(QueueBusy):
        await q.submit(_model(), [], None, None)


async def test_worker_delivers_result(monkeypatch):
    from app import resilience
    from app.upstream import UpstreamResult

    async def fake_dispatch(model, messages, tools=None, options=None, trace_ctx=None):
        return UpstreamResult(model="t", content="hello")

    monkeypatch.setattr(resilience, "dispatch", fake_dispatch)
    q = WorkQueue(maxsize=4, worker_count=1)
    await q.start()
    try:
        result = await asyncio.wait_for(q.submit(_model(), [], None, None), timeout=5)
        assert result.content == "hello"
    finally:
        await q.stop()


async def test_worker_restores_submitter_trace_context(monkeypatch):
    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    from app import resilience
    from app.upstream import UpstreamResult

    observed = []

    async def fake_dispatch(model, messages, tools=None, options=None, trace_ctx=None):
        observed.append(trace.get_current_span().get_span_context())
        return UpstreamResult(model="t", content="hello")

    monkeypatch.setattr(resilience, "dispatch", fake_dispatch)
    monkeypatch.setattr("app.queue.get_tracer", lambda: None)
    parent = NonRecordingSpan(
        SpanContext(
            trace_id=0x123,
            span_id=0x456,
            is_remote=False,
            trace_flags=TraceFlags(0x01),
        )
    )
    q = WorkQueue(maxsize=4, worker_count=1)
    await q.start()
    try:
        with trace.use_span(parent, end_on_exit=False):
            await asyncio.wait_for(q.submit(_model(), [], None, None), timeout=5)
    finally:
        await q.stop()

    assert len(observed) == 1
    assert observed[0].trace_id == 0x123
    assert observed[0].span_id == 0x456
