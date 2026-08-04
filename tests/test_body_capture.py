"""Contract tests for strict opt-in model body capture."""

from __future__ import annotations

import json

import pytest
from opentelemetry.sdk.trace import TracerProvider

from app.body_capture import BodyCaptureError, ModelBodyCapture


def _span():
    provider = TracerProvider()
    return provider.get_tracer("body-capture-test").start_as_current_span("request.chat")


def test_disabled_capture_emits_nothing_and_does_not_serialize() -> None:
    capture = ModelBodyCapture(
        enabled=False,
        request_id="disabled",
        request_body={"not_json": object()},
    )

    capture.emit_request(None)
    capture.emit_response(None, {"not_json": object()})


def test_capture_uses_canonical_json_and_exactly_two_correlated_events(capsys) -> None:
    request_body = {"z": [3, 2, 1], "a": {"prompt": "hello"}}
    response_body = {"choices": [{"message": {"content": "world"}}]}
    capture = ModelBodyCapture(
        enabled=True,
        request_id="request-123",
        request_body=request_body,
    )

    with _span() as span:
        capture.emit_request(span)
        capture.emit_response(span, response_body)
        request_attr = span.attributes["agentproxy.request.body"]
        response_attr = span.attributes["agentproxy.response.body"]

    events = []
    for line in capsys.readouterr().out.splitlines():
        payload = json.loads(line)
        if payload.get("event", "").startswith("model."):
            events.append(payload)

    assert [event["event"] for event in events] == [
        "model.request.captured",
        "model.response.captured",
    ]
    assert events[0]["request.body"] == request_body
    assert events[1]["response.body"] == response_body
    assert events[0]["trace_id"] == events[1]["trace_id"]
    assert events[0]["span_id"] == events[1]["span_id"]
    assert events[0]["agentproxy.request_id"] == "request-123"
    assert events[1]["agentproxy.capture.status"] == "complete"
    assert request_attr == json.dumps(
        request_body,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert response_attr == json.dumps(
        response_body,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def test_incomplete_capture_requires_a_closed_set_reason() -> None:
    capture = ModelBodyCapture(enabled=True, request_id="request-123", request_body={})

    with _span() as span:
        capture.emit_request(span)
        with pytest.raises(BodyCaptureError, match="closed-set reason"):
            capture.emit_response(span, {}, status="incomplete")


def test_enabled_capture_fails_hard_on_field_loss() -> None:
    capture = ModelBodyCapture(enabled=True, request_id="request-123", request_body={})

    with _span() as span:
        capture.emit_request(span)
        with pytest.raises(BodyCaptureError, match="losslessly JSON serializable"):
            capture.emit_response(span, {"invalid": float("nan")})


def test_enabled_capture_fails_hard_when_log_delivery_fails(monkeypatch) -> None:
    capture = ModelBodyCapture(enabled=True, request_id="request-123", request_body={})

    def fail(*_args, **_kwargs):
        raise OSError("stdout unavailable")

    monkeypatch.setattr("app.body_capture.log.info", fail)
    with _span() as span:
        with pytest.raises(BodyCaptureError, match="model.request.captured"):
            capture.emit_request(span)
