"""Tests for observability wiring helpers."""

import json

import pytest

from app.obs import _otlp_http_traces_url, get_logger, init_sentry, metrics_text


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        # Base endpoint (the documented convention) gets the signal path appended.
        ("http://host.docker.internal:4318", "http://host.docker.internal:4318/v1/traces"),
        # Trailing slash is normalized, not doubled.
        ("http://host.docker.internal:4318/", "http://host.docker.internal:4318/v1/traces"),
        ("http://localhost:4318", "http://localhost:4318/v1/traces"),
        # Already-full traces URL is left alone (idempotent), no double-append.
        ("http://host.docker.internal:4318/v1/traces", "http://host.docker.internal:4318/v1/traces"),
        ("http://host.docker.internal:4318/v1/traces/", "http://host.docker.internal:4318/v1/traces"),
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


def test_metrics_text_exposes_leg04_names():
    text = metrics_text()
    assert isinstance(text, bytes)
    for name in (b"llm_queue_depth", b"llm_retries_total", b"llm_fallbacks_total", b"llm_circuit_state", b"llm_truncation_avoided_total"):
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
