"""Metadata-only request lifecycle trajectory events."""

from __future__ import annotations

from datetime import datetime, timezone

from app.obs import RequestTraceContext
from app.trajectory.request_events import RequestLifecycle
from app.trajectory.schema import canonical_event_bytes
from app.upstream import UpstreamResult


def _lifecycle() -> RequestLifecycle:
    return RequestLifecycle.from_trace_context(
        RequestTraceContext(
            logical_model="fixture-model",
            request_model="fixture-model",
            request_kind="chat",
            request_id="fixture-request",
            extra={
                "ward.run_id": "fixture-run",
                "ward.role": "engineer",
                "ward.harness": "codex",
                "ward.target_repo": "coilyco-flight-deck/agent-proxy",
                "ward.issue_ref": "coilyco-flight-deck/agent-proxy#55",
                "ward.workflow": "direct-to-main",
                "agent.session_id": "fixture-session",
                "agentproxy.messages": [{"role": "user", "content": "private prompt"}],
                "agentproxy.tools": [{"name": "private tool"}],
            },
        ),
        occurred_at=datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc),
    )


def test_request_lifecycle_emits_correlated_metadata_without_bodies():
    lifecycle = _lifecycle()
    action = lifecycle.action_event()
    completed = lifecycle.execution_event(
        "succeeded",
        result=UpstreamResult(
            model="fixture-provider-model",
            content="private response",
            prompt_eval_count=12,
            eval_count=3,
            total_duration=42_000_000,
            eval_duration=30_000_000,
            done_reason="stop",
        ),
        latency_ms=42,
    )

    assert action.event_type == "action.proposed"
    assert completed.event_type == "execution.completed"
    assert completed.payload.model_execution.total_tokens == 15
    assert completed.payload.model_execution.latency_ms == 42
    assert completed.attributes["ollama.total_duration_ms"] == 42.0
    assert completed.attributes["ollama.eval_duration_ms"] == 30.0
    assert completed.attributes["gen_ai.usage.input_tokens"] == 12
    assert completed.correlation.ward_run_id == "fixture-run"
    assert completed.correlation.repository == "coilyco-flight-deck/agent-proxy"
    assert completed.actor.role == "engineer"
    retained = canonical_event_bytes(action) + canonical_event_bytes(completed)
    assert b"private prompt" not in retained
    assert b"private response" not in retained
    assert b"private tool" not in retained


def test_request_failure_uses_normalized_error_class():
    failed = _lifecycle().execution_event("queue_rejected", latency_ms=1)

    assert failed.event_type == "execution.failed"
    assert failed.payload.outcome == "queue_rejected"
    assert failed.payload.error_class == "queue_rejected"
    assert failed.content.capture == "metadata_only"


def test_request_cancellation_is_metadata_only_terminal_evidence():
    cancelled = _lifecycle().execution_event("cancelled", latency_ms=2)

    assert cancelled.event_type == "execution.failed"
    assert cancelled.payload.outcome == "cancelled"
    assert cancelled.payload.error_class == "cancelled"
    assert cancelled.attributes["agentproxy.request.outcome"] == "cancelled"
    assert cancelled.content.capture == "metadata_only"
