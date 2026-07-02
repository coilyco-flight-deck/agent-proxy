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

    async def fake_dispatch(model, messages, tools=None, options=None):
        return UpstreamResult(model="t", content="hello")

    monkeypatch.setattr(resilience, "dispatch", fake_dispatch)
    q = WorkQueue(maxsize=4, worker_count=1)
    await q.start()
    try:
        result = await asyncio.wait_for(q.submit(_model(), [], None, None), timeout=5)
        assert result.content == "hello"
    finally:
        await q.stop()
