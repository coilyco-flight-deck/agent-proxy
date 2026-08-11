"""Strict opt-in capture of complete normalized model request and response bodies."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from opentelemetry import trace

from .config import get_settings
from .obs import log

CaptureStatus = Literal["complete", "incomplete"]
CaptureReason = Literal[
    "cancelled",
    "context_truncated",
    "interrupted",
    "queue_rejected",
    "response_failed",
    "stream_failed",
    "upstream_failed",
]

CAPTURE_SCHEMA_VERSION = 1
_CAPTURE_REASONS = {
    "cancelled",
    "context_truncated",
    "interrupted",
    "queue_rejected",
    "response_failed",
    "stream_failed",
    "upstream_failed",
}


class BodyCaptureError(RuntimeError):
    """The enabled capture contract could not be completed exactly."""


def last_user_message(body: dict[str, Any]) -> str | None:
    """Return the verbatim text of the final user message, or None when there is none.

    The captured request body already holds this text, but only as the last
    matching element of a variable-length messages list. No downstream log
    pipeline field path can address a last element, so the projection happens
    here where the position is known. This is a convenience projection beside
    the complete body, never a substitute for it, so an unreadable or absent
    message omits the field rather than failing the capture.
    """

    messages = body.get("messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content if content.strip() else None
        if isinstance(content, list):
            parts = [
                part["text"]
                for part in content
                if isinstance(part, dict)
                and part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ]
            joined = "\n".join(parts)
            return joined if joined.strip() else None
        return None
    return None


def _canonical_body(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return deterministic JSON and the exact JSON-safe object written to logs."""

    try:
        canonical = json.dumps(
            body,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        normalized = json.loads(canonical)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BodyCaptureError("model body is not losslessly JSON serializable") from exc
    if not isinstance(normalized, dict):
        raise BodyCaptureError("model body must serialize to a JSON object")
    return canonical, normalized


def _span_identity(span: Any | None) -> tuple[str, str]:
    if span is None:
        raise BodyCaptureError("request span is unavailable")
    if not getattr(span, "is_recording", lambda: False)():
        raise BodyCaptureError("request span is not recording")
    try:
        context = span.get_span_context()
    except Exception as exc:
        raise BodyCaptureError("request span context is unavailable") from exc
    if not context.is_valid:
        raise BodyCaptureError("request span context is invalid")
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


def _emit_capture_log(span: Any, event: str, fields: dict[str, object]) -> None:
    """Emit against the request span without best-effort fallback or field loss."""

    try:
        with trace.use_span(span, end_on_exit=False):
            log.info(event, **fields)
    except Exception as exc:
        raise BodyCaptureError(f"failed to emit {event}") from exc


def response_projection(body: dict[str, Any]) -> dict[str, object]:
    """Return the response fields worth reading without parsing the whole body.

    The response counterpart to last_user_message. Every value here sits at a
    fixed path a log pipeline could reach on its own, but deriving it beside the
    request projection keeps one owner for turn-shape fields and serves every
    capture consumer rather than a single ingest path. Values come from the
    first choice, because the complete body still carries the rest. Absent or
    unreadable values are omitted rather than failing the capture.
    """

    projection: dict[str, object] = {}
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        first = choices[0]
        finish_reason = first.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            projection["agentproxy.finish_reason"] = finish_reason
        message = first.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                projection["agentproxy.assistant_message"] = content
    usage = body.get("usage")
    if isinstance(usage, dict):
        for key in ("completion_tokens", "prompt_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                projection[f"agentproxy.{key}"] = value
    return projection


@dataclass
class ModelBodyCapture:
    """Emit exactly one request event and one response event for one model call."""

    enabled: bool
    request_id: str
    request_body: dict[str, Any]
    expected_span_name: str = ""
    _request_emitted: bool = field(default=False, init=False)
    _response_emitted: bool = field(default=False, init=False)

    def emit_request(self, span: Any | None) -> None:
        if not self.enabled:
            return
        if self._request_emitted:
            raise BodyCaptureError("request body capture was emitted more than once")
        if self.expected_span_name and getattr(span, "name", "") != self.expected_span_name:
            raise BodyCaptureError("body capture is not attached to the request span")

        canonical, normalized = _canonical_body(self.request_body)
        trace_id, span_id = _span_identity(span)
        if span is None:
            raise BodyCaptureError("request span is unavailable")
        fields: dict[str, object] = {
            "agentproxy.capture.schema_version": CAPTURE_SCHEMA_VERSION,
            "agentproxy.capture.status": "complete",
            "agentproxy.request_id": self.request_id,
            "request.body": normalized,
            "service.name": get_settings().service_name,
            "trace_id": trace_id,
            "span_id": span_id,
        }
        user_message = last_user_message(normalized)
        if user_message is not None:
            fields["agentproxy.user_message"] = user_message
        try:
            span.set_attribute("agentproxy.request.body", canonical)
            _emit_capture_log(span, "model.request.captured", fields)
        except BodyCaptureError:
            raise
        except Exception as exc:
            raise BodyCaptureError("failed to attach complete request body capture") from exc
        self._request_emitted = True

    def emit_response(
        self,
        span: Any | None,
        body: dict[str, Any],
        *,
        status: CaptureStatus = "complete",
        reason: CaptureReason | None = None,
    ) -> None:
        if not self.enabled:
            return
        if not self._request_emitted:
            raise BodyCaptureError("response capture cannot precede request capture")
        if self._response_emitted:
            raise BodyCaptureError("response body capture was emitted more than once")
        if status == "complete" and reason is not None:
            raise BodyCaptureError("complete capture cannot carry an incomplete reason")
        if status == "incomplete" and reason not in _CAPTURE_REASONS:
            raise BodyCaptureError("incomplete capture requires a closed-set reason")

        canonical, normalized = _canonical_body(body)
        trace_id, span_id = _span_identity(span)
        if span is None:
            raise BodyCaptureError("request span is unavailable")
        fields: dict[str, object] = {
            "agentproxy.capture.schema_version": CAPTURE_SCHEMA_VERSION,
            "agentproxy.capture.status": status,
            "agentproxy.request_id": self.request_id,
            "response.body": normalized,
            "service.name": get_settings().service_name,
            "trace_id": trace_id,
            "span_id": span_id,
        }
        fields.update(response_projection(normalized))
        if reason is not None:
            fields["agentproxy.capture.reason"] = reason
        try:
            span.set_attribute("agentproxy.response.body", canonical)
            _emit_capture_log(span, "model.response.captured", fields)
        except BodyCaptureError:
            raise
        except Exception as exc:
            raise BodyCaptureError("failed to attach complete response body capture") from exc
        self._response_emitted = True
