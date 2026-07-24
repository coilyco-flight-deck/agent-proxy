"""Tests for observability wiring helpers."""

import json

import pytest

from app.obs import (
    InstrumentedAction,
    _add_trace_context,
    _otlp_http_traces_url,
    emit_instrumented_action,
    get_logger,
    init_sentry,
    log_on_span,
    metrics_text,
)


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        # Base endpoint (the documented convention) gets the signal path appended.
        ("http://host.docker.internal:4318", "http://host.docker.internal:4318/v1/traces"),
        # Trailing slash is normalized, not doubled.
        ("http://host.docker.internal:4318/", "http://host.docker.internal:4318/v1/traces"),
        ("http://localhost:4318", "http://localhost:4318/v1/traces"),
        # Already-full traces URL is left alone (idempotent), no double-append.
        (
            "http://host.docker.internal:4318/v1/traces",
            "http://host.docker.internal:4318/v1/traces",
        ),
        (
            "http://host.docker.internal:4318/v1/traces/",
            "http://host.docker.internal:4318/v1/traces",
        ),
    ],
)
def test_otlp_http_traces_url(endpoint, expected):
    assert _otlp_http_traces_url(endpoint) == expected


def test_get_logger_emits_json(capsys):
    get_logger("t").info("hello", k=1)
    line = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello"
    assert payload["k"] == 1


def test_get_logger_adds_active_trace_context(monkeypatch, capsys):
    class SpanContext:
        is_valid = True
        trace_id = 0x123
        span_id = 0x456

    class Span:
        def get_span_context(self):
            return SpanContext()

    monkeypatch.setattr("opentelemetry.trace.get_current_span", lambda: Span())

    get_logger("t").info("correlated")

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["trace_id"] == "00000000000000000000000000000123"
    assert payload["span_id"] == "0000000000000456"


def test_trace_context_processor_leaves_invalid_context_unchanged(monkeypatch):
    class SpanContext:
        is_valid = False

    class Span:
        def get_span_context(self):
            return SpanContext()

    monkeypatch.setattr("opentelemetry.trace.get_current_span", lambda: Span())
    event = {"event": "uncorrelated"}

    assert _add_trace_context(None, "info", event) == {"event": "uncorrelated"}


def test_trace_context_processor_is_failure_safe(monkeypatch):
    def fail():
        raise RuntimeError("trace context unavailable")

    monkeypatch.setattr("opentelemetry.trace.get_current_span", fail)
    event = {"event": "still-logged"}

    assert _add_trace_context(None, "info", event) == {"event": "still-logged"}


def test_log_on_span_activates_requested_trace_context(capsys):
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    span = NonRecordingSpan(
        SpanContext(
            trace_id=0x123,
            span_id=0x456,
            is_remote=False,
            trace_flags=TraceFlags(0x01),
        )
    )

    log_on_span(span, "root.completed")

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["event"] == "root.completed"
    assert payload["trace_id"] == "00000000000000000000000000000123"
    assert payload["span_id"] == "0000000000000456"


def test_emit_instrumented_action_hits_log_metric_and_span(monkeypatch, capsys):
    events = []
    attributes = {}
    metric_calls = []

    class Span:
        def is_recording(self):
            return True

        def add_event(self, name, attrs):
            events.append((name, attrs))

        def set_attribute(self, key, value):
            attributes[key] = value

    monkeypatch.setattr("app.obs._current_span", lambda: Span())

    emit_instrumented_action(
        InstrumentedAction(
            log_event="request.prompt_trimmed",
            metric=lambda: metric_calls.append(1),
            span_event="request.prompt_trimmed",
            fields={"logical_model": "m", "dropped_message_count": 2},
        )
    )

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["event"] == "request.prompt_trimmed"
    assert payload["logical_model"] == "m"
    assert metric_calls == [1]
    assert events == [
        ("request.prompt_trimmed", {"logical_model": "m", "dropped_message_count": 2})
    ]
    assert attributes["logical_model"] == "m"
    assert attributes["dropped_message_count"] == 2


def test_metrics_text_exposes_leg04_names():
    text = metrics_text()
    assert isinstance(text, bytes)
    for name in (
        b"llm_queue_depth",
        b"llm_retries_total",
        b"llm_fallbacks_total",
        b"llm_circuit_state",
        b"llm_truncation_avoided_total",
        b"ward_skill_use_total",
    ):
        assert name in text


def test_init_sentry_no_dsn_is_noop(monkeypatch):
    # With no DSN configured, init_sentry must not raise (best-effort, no-op).
    from app import config

    monkeypatch.setattr(config.Settings, "resolved_sentry_dsn", lambda self: "")
    config.get_settings.cache_clear()
    try:
        init_sentry()
    finally:
        config.get_settings.cache_clear()
