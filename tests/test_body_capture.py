"""Contract tests for strict opt-in model body capture."""

from __future__ import annotations

import json

import pytest
from opentelemetry.sdk.trace import TracerProvider

from app.body_capture import (
    BodyCaptureError,
    ModelBodyCapture,
    last_user_message,
    response_projection,
)


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


def test_last_user_message_reads_the_final_user_turn_not_the_first() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "second question"},
        ]
    }

    assert last_user_message(body) == "second question"


def test_last_user_message_survives_trailing_tool_rounds() -> None:
    body = {
        "messages": [
            {"role": "user", "content": "what is the server state"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"co2": 325}'},
        ]
    }

    assert last_user_message(body) == "what is the server state"


def test_last_user_message_joins_text_parts_and_ignores_other_parts() -> None:
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "look at this"},
                    {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
                    {"type": "text", "text": "and this"},
                ],
            }
        ]
    }

    assert last_user_message(body) == "look at this\nand this"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"messages": "not a list"},
        {"messages": []},
        {"messages": [{"role": "system", "content": "no user turn"}]},
        {"messages": [{"role": "user", "content": "   "}]},
        {"messages": [{"role": "user", "content": None}]},
        {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]},
        {"messages": ["not a dict"]},
    ],
)
def test_last_user_message_returns_none_rather_than_raising(body) -> None:
    assert last_user_message(body) is None


def test_request_capture_carries_the_user_message(capsys) -> None:
    request_body = {
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "where should I start"},
        ]
    }
    capture = ModelBodyCapture(
        enabled=True,
        request_id="request-123",
        request_body=request_body,
    )

    with _span() as span:
        capture.emit_request(span)

    event = next(
        payload
        for payload in map(json.loads, capsys.readouterr().out.splitlines())
        if payload.get("event") == "model.request.captured"
    )
    assert event["agentproxy.user_message"] == "where should I start"
    assert event["request.body"] == request_body


def test_request_capture_omits_the_field_when_there_is_no_user_message(capsys) -> None:
    capture = ModelBodyCapture(
        enabled=True,
        request_id="request-123",
        request_body={"messages": [{"role": "system", "content": "be helpful"}]},
    )

    with _span() as span:
        capture.emit_request(span)

    event = next(
        payload
        for payload in map(json.loads, capsys.readouterr().out.splitlines())
        if payload.get("event") == "model.request.captured"
    )
    assert "agentproxy.user_message" not in event


def test_response_projection_reads_finish_reason_message_and_usage() -> None:
    body = {
        "choices": [{"finish_reason": "stop", "message": {"content": "here you go"}}],
        "usage": {"completion_tokens": 72, "prompt_tokens": 6161, "total_tokens": 6233},
    }

    assert response_projection(body) == {
        "agentproxy.finish_reason": "stop",
        "agentproxy.assistant_message": "here you go",
        "agentproxy.completion_tokens": 72,
        "agentproxy.prompt_tokens": 6161,
    }


def test_response_projection_describes_a_budget_exhausted_turn() -> None:
    body = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": "", "reasoning_content": "thinking at length"},
            }
        ],
        "usage": {"completion_tokens": 900, "prompt_tokens": 47215},
    }

    projection = response_projection(body)

    assert projection["agentproxy.finish_reason"] == "length"
    assert projection["agentproxy.completion_tokens"] == 900
    assert "agentproxy.assistant_message" not in projection


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": "not a list"},
        {"choices": ["not a dict"]},
        {"choices": [{}], "usage": "not a dict"},
        {"choices": [{"finish_reason": ""}]},
        {"usage": {"completion_tokens": True}},
        {"usage": {"completion_tokens": "72"}},
    ],
)
def test_response_projection_omits_rather_than_raising(body) -> None:
    assert response_projection(body) == {}


def test_response_capture_carries_the_projection(capsys) -> None:
    capture = ModelBodyCapture(enabled=True, request_id="request-123", request_body={})
    response_body = {
        "choices": [{"finish_reason": "stop", "message": {"content": "world"}}],
        "usage": {"completion_tokens": 5, "prompt_tokens": 11},
    }

    with _span() as span:
        capture.emit_request(span)
        capture.emit_response(span, response_body)

    event = next(
        payload
        for payload in map(json.loads, capsys.readouterr().out.splitlines())
        if payload.get("event") == "model.response.captured"
    )
    assert event["agentproxy.finish_reason"] == "stop"
    assert event["agentproxy.assistant_message"] == "world"
    assert event["agentproxy.completion_tokens"] == 5
    assert event["agentproxy.prompt_tokens"] == 11
    assert event["response.body"] == response_body


def test_incomplete_response_capture_keeps_its_reason_and_projection(capsys) -> None:
    capture = ModelBodyCapture(enabled=True, request_id="request-123", request_body={})

    with _span() as span:
        capture.emit_request(span)
        capture.emit_response(
            span,
            {"choices": [{"finish_reason": "length"}]},
            status="incomplete",
            reason="stream_failed",
        )

    event = next(
        payload
        for payload in map(json.loads, capsys.readouterr().out.splitlines())
        if payload.get("event") == "model.response.captured"
    )
    assert event["agentproxy.capture.reason"] == "stream_failed"
    assert event["agentproxy.capture.status"] == "incomplete"
    assert event["agentproxy.finish_reason"] == "length"


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
