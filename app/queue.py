"""
In-memory queue and worker pool - the resilience core (leg 02 "in-memory queue",
leg 04 step 3).

The web layer accepts a request, enqueues a job, and awaits its future. A worker
pops the job and dispatches it through the resilience policies. A bounded queue
gives backpressure: when it is full the route returns 429 instead of unbounded
buffering. This decouples client-accept from upstream health and is per-pod and
ephemeral by design (no shared state, leg 02 topology).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from . import resilience
from .config import get_settings
from .models import LogicalModel
from .obs import llm_queue_depth, llm_queue_rejected_total, log
from .upstream import UpstreamResult


class QueueBusy(Exception):
    """The bounded queue is full - the route should answer 429."""


@dataclass
class Job:
    model: LogicalModel
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    options: dict[str, Any] | None
    future: "asyncio.Future[UpstreamResult]" = field(default=None)  # set on submit


class WorkQueue:
    """A bounded ``asyncio.Queue`` fronting a fixed worker pool."""

    def __init__(self, maxsize: int, worker_count: int) -> None:
        # The queue is created in start() so it binds to the loop that actually
        # runs the workers (a module-singleton must not bind at import time).
        self._maxsize = maxsize
        self._queue: asyncio.Queue[Job] | None = None
        self._worker_count = worker_count
        self._workers: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._queue = asyncio.Queue(maxsize=self._maxsize)
        loop = asyncio.get_running_loop()
        self._workers = [loop.create_task(self._worker(i)) for i in range(self._worker_count)]
        log.info("queue.start", workers=self._worker_count, maxsize=self._maxsize)

    async def stop(self) -> None:
        self._running = False
        for w in self._workers:
            w.cancel()
        for w in self._workers:
            try:
                await w
            except asyncio.CancelledError:
                pass
        self._workers = []
        self._queue = None
        log.info("queue.stop")

    async def submit(self, model: LogicalModel, messages, tools, options) -> UpstreamResult:
        """Enqueue a job and await its result. Raises ``QueueBusy`` when full."""
        if self._queue is None:
            raise RuntimeError("queue not started")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[UpstreamResult] = loop.create_future()
        job = Job(model=model, messages=messages, tools=tools, options=options, future=future)
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            llm_queue_rejected_total.inc()
            raise QueueBusy(model.name)
        llm_queue_depth.set(self._queue.qsize())
        return await future

    async def _worker(self, idx: int) -> None:
        while True:
            job = await self._queue.get()
            llm_queue_depth.set(self._queue.qsize())
            try:
                result = await resilience.dispatch(
                    job.model, job.messages, tools=job.tools, options=job.options
                )
                if not job.future.done():
                    job.future.set_result(result)
            except Exception as exc:  # deliver the failure to the awaiting route
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self._queue.task_done()


_queue: WorkQueue | None = None


def get_queue() -> WorkQueue:
    """Process-wide queue singleton (constructed lazily on first access)."""
    global _queue
    if _queue is None:
        s = get_settings()
        _queue = WorkQueue(maxsize=s.queue_maxsize, worker_count=s.worker_count)
    return _queue
