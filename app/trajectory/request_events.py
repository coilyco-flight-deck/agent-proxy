"""Metadata-only trajectory events for the latency-sensitive request path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.obs import RequestTraceContext
from app.trajectory.producer import ProducerContext
from app.trajectory.schema import TrajectoryEvent
from app.upstream import UpstreamResult

RequestOutcome = Literal[
    "succeeded",
    "queue_rejected",
    "context_truncated",
    "upstream_failed",
    "stream_failed",
]

_CORRELATION_FIELDS = {
    "ward.run_id": "ward_run_id",
    "agent.session_id": "agent_session_id",
    "ward.target_repo": "repository",
    "ward.issue_ref": "issue_ref",
    "ward.workflow": "workflow",
}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class RequestLifecycle:
    """Stable request identity used to produce non-blocking lifecycle evidence."""

    trace_context: RequestTraceContext
    producer: ProducerContext
    occurred_at: datetime
    action_ref: str
    execution_ref: str

    @classmethod
    def from_trace_context(
        cls,
        trace_context: RequestTraceContext,
        *,
        occurred_at: datetime | None = None,
    ) -> "RequestLifecycle":
        if not trace_context.request_id:
            raise ValueError("request lifecycle events require a request id")
        started_at = occurred_at or datetime.now(timezone.utc)
        correlation = {
            target: value
            for source, target in _CORRELATION_FIELDS.items()
            if (value := _text(trace_context.extra.get(source)))
        }
        correlation["request_id"] = trace_context.request_id
        role = _text(trace_context.extra.get("ward.role"))
        harness = _text(trace_context.extra.get("ward.harness"))
        action_ref = f"agent-proxy:request:{trace_context.request_id}"
        return cls(
            trace_context=trace_context,
            producer=ProducerContext(
                source_name="agent-proxy.request",
                source_version="request-event-v1",
                source_instance_id="request-emitter",
                actor_type="agent",
                actor_id="agent-proxy:caller",
                actor_role=role or harness or "agent",
                correlation=correlation,
            ),
            occurred_at=started_at,
            action_ref=action_ref,
            execution_ref=f"{action_ref}:execution",
        )

    def _attributes(self) -> dict[str, object]:
        return {
            "agentproxy.request_kind": self.trace_context.request_kind,
            "agentproxy.logical_model": self.trace_context.logical_model,
            "gen_ai.request.model": self.trace_context.request_model,
            "ward.harness": _text(self.trace_context.extra.get("ward.harness")),
            "ward.role": _text(self.trace_context.extra.get("ward.role")),
        }

    def action_event(self) -> TrajectoryEvent:
        """Build the metadata-only proposed model-request event."""

        return self.producer.event(
            event_type="action.proposed",
            occurred_at=self.occurred_at,
            idempotency_key=f"{self.action_ref}:proposed",
            attributes=self._attributes(),
            payload={
                "action_kind": "model-request",
                "action_ref": self.action_ref,
                "target_refs": [f"model:{self.trace_context.logical_model}"],
                "intent": f"perform {self.trace_context.request_kind} inference",
                "retention_class": "trajectory",
                "access_tier": "internal",
            },
            input_refs=[self.action_ref],
            transform="agent-proxy.request-emitter",
            transform_version="1",
        )

    def execution_event(
        self,
        outcome: RequestOutcome,
        *,
        result: UpstreamResult | None = None,
        latency_ms: int | None = None,
    ) -> TrajectoryEvent:
        """Build one normalized terminal event without prompt or response bodies."""

        succeeded = outcome == "succeeded"
        request_tokens = result.prompt_eval_count if result is not None else None
        response_tokens = result.eval_count if result is not None else None
        provider_model = result.model if result is not None else self.trace_context.request_model
        model_execution: dict[str, object] = {
            "model": self.trace_context.logical_model,
            "provider": "current-gateway",
            "provider_model": provider_model,
            "request_tokens": request_tokens,
            "response_tokens": response_tokens,
            "total_tokens": (
                request_tokens + response_tokens
                if request_tokens is not None and response_tokens is not None
                else None
            ),
            "latency_ms": latency_ms,
            "retry_count": 0,
            "fallback_count": 0,
            "fallback_from": [],
            "finish_reason": result.done_reason if result is not None else None,
        }
        return self.producer.event(
            event_type="execution.completed" if succeeded else "execution.failed",
            occurred_at=datetime.now(timezone.utc),
            idempotency_key=f"{self.execution_ref}:terminal",
            attributes={**self._attributes(), "agentproxy.request.outcome": outcome},
            payload={
                "execution_id": self.execution_ref,
                "executor_ref": "agent-proxy.current-gateway",
                "action_ref": self.action_ref,
                "outcome": outcome,
                "error_class": None if succeeded else outcome,
                "model_execution": model_execution,
                "retention_class": "trajectory",
                "access_tier": "internal",
            },
            input_refs=[self.action_ref],
            transform="agent-proxy.request-emitter",
            transform_version="1",
        )
