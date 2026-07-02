"""Tests for observability wiring helpers."""

import pytest

from app.obs import _otlp_http_traces_url


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
