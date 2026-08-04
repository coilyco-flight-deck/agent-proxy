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
