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

from opentelemetry import context as otel_context

from . import resilience
from .config import get_settings
from .models import LogicalModel
from .obs import (
    RequestTraceContext,
    llm_queue_depth,
    llm_queue_rejected_total,
    log,
    record_error,
    request_log_fields,
    get_tracer,
)
from .upstream import UpstreamResult


class QueueBusy(Exception):
    """The bounded queue is full - the route should answer 429."""


@dataclass
class Job:
    model: LogicalModel
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    options: dict[str, Any] | None
    trace_ctx: RequestTraceContext | None = None
    # Defaulted to None only so it can trail trace_ctx in the dataclass; submit()
    # always constructs a Job with a live future, so the awaiting paths guard it.
    future: "asyncio.Future[UpstreamResult] | None" = field(default=None)
    otel_context: Any | None = None


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

    async def submit(
        self,
        model: LogicalModel,
        messages,
        tools,
        options,
        *,
        trace_ctx: RequestTraceContext | None = None,
    ) -> UpstreamResult:
        """Enqueue a job and await its result. Raises ``QueueBusy`` when full."""
        if self._queue is None:
            raise RuntimeError("queue not started")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[UpstreamResult] = loop.create_future()
        job = Job(
            model=model,
            messages=messages,
            tools=tools,
            options=options,
            trace_ctx=trace_ctx,
            future=future,
            otel_context=otel_context.get_current(),
        )
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            llm_queue_rejected_total.inc()
            if trace_ctx is not None:
                log.warning("queue.rejected", **request_log_fields(trace_ctx, outcome="rejected"))
            raise QueueBusy(model.name)
        llm_queue_depth.set(self._queue.qsize())
        tracer = get_tracer()
        if tracer is None:
            return await future
        with tracer.start_as_current_span("queue.wait") as span:
            span.set_attribute("agentproxy.logical_model", model.name)
            if trace_ctx is not None:
                for key, value in trace_ctx.attrs().items():
                    span.set_attribute(key, value)
            result = await future
            span.set_attribute("gen_ai.usage.input_tokens", result.prompt_eval_count)
            span.set_attribute("gen_ai.usage.output_tokens", result.eval_count)
            span.set_attribute(
                "response.finish_reasons", [result.done_reason] if result.done_reason else []
            )
            return result

    async def _worker(self, idx: int) -> None:
        # start() creates the queue before spawning any worker, so it is bound
        # for the worker's whole lifetime; bind it locally so the type narrows.
        queue = self._queue
        assert queue is not None
        while True:
            job = await queue.get()
            llm_queue_depth.set(queue.qsize())
            context_token = (
                otel_context.attach(job.otel_context) if job.otel_context is not None else None
            )
            try:
                result = await resilience.dispatch(
                    job.model,
                    job.messages,
                    tools=job.tools,
                    options=job.options,
                    trace_ctx=job.trace_ctx,
                )
                if job.future is not None and not job.future.done():
                    job.future.set_result(result)
            except Exception as exc:  # deliver the failure to the awaiting route
                record_error("queue_worker_failed")
                if job.future is not None and not job.future.done():
                    job.future.set_exception(exc)
            finally:
                if context_token is not None:
                    otel_context.detach(context_token)
                queue.task_done()


_queue: WorkQueue | None = None


def get_queue() -> WorkQueue:
    """Process-wide queue singleton (constructed lazily on first access)."""
    global _queue
    if _queue is None:
        s = get_settings()
        _queue = WorkQueue(maxsize=s.queue_maxsize, worker_count=s.worker_count)
    return _queue
