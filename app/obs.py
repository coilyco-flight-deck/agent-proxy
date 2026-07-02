"""
Observability wiring: structlog JSON logs, prometheus metrics, OpenTelemetry
traces, Sentry errors. This module is imported before any request logic so every
path below is instrumented from line one (leg 04 step 1, leg 02 observability).

All metric objects are defined here once and imported everywhere else, so the
names in leg 04 (``llm_queue_depth``, ``llm_retries_total``,
``llm_fallbacks_total``, ``llm_circuit_state``, ``llm_truncation_avoided_total``)
have a single source of truth.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field

import structlog
from prometheus_client import Counter, Gauge, Histogram

from .config import get_settings

# --------------------------------------------------------------------------- #
# Prometheus metrics - the leg 04 names plus request-level counters.
# --------------------------------------------------------------------------- #

llm_requests_total = Counter(
    "llm_requests_total", "Requests accepted by the proxy", ["logical_model", "outcome"]
)
llm_queue_depth = Gauge("llm_queue_depth", "Jobs currently waiting in the in-memory queue")
llm_queue_rejected_total = Counter(
    "llm_queue_rejected_total", "Requests rejected with 429 because the queue was full"
)
llm_retries_total = Counter(
    "llm_retries_total", "Dispatch retries against a single backend", ["logical_model", "backend"]
)
llm_fallbacks_total = Counter(
    "llm_fallbacks_total", "Falls to the next backend in a logical model's chain", ["logical_model", "backend"]
)
# 0 = closed (healthy), 1 = open (tripped), 2 = half-open (probing).
llm_circuit_state = Gauge("llm_circuit_state", "Per-backend circuit breaker state", ["backend"])
llm_truncation_avoided_total = Counter(
    "llm_truncation_avoided_total", "Requests trimmed to fit the safe context budget", ["logical_model"]
)
llm_validation_failures_total = Counter(
    "llm_validation_failures_total", "Responses rejected by validation", ["logical_model", "reason"]
)
llm_prompt_tokens = Histogram(
    "llm_prompt_tokens",
    "Prompt tokens forwarded upstream (post-guard)",
    ["logical_model"],
    buckets=(1024, 4096, 8192, 16384, 32768, 49152, 65536, 98304, 131072),
)
llm_upstream_latency_seconds = Histogram(
    "llm_upstream_latency_seconds", "Upstream generation latency", ["logical_model", "backend"]
)


def _configure_structlog(log_level: str) -> None:
    """JSON logs to stdout, shared processor chain, level from settings."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


_tracer = None


def _otlp_http_traces_url(endpoint: str) -> str:
    """Resolve the OTLP/HTTP traces URL from a configured base endpoint.

    The config value is the OTLP base (e.g. ``http://host.docker.internal:4318``),
    per OTEL_EXPORTER_OTLP_ENDPOINT convention. But the Python OTLP/HTTP
    ``OTLPSpanExporter(endpoint=...)`` kwarg is taken VERBATIM and does NOT append
    the ``/v1/traces`` signal path the way the env var does, so a base value posts
    to the collector root and gets a 404. Append it here, idempotently."""
    base = endpoint.rstrip("/")
    if base.endswith("/v1/traces"):
        return base
    return base + "/v1/traces"


def _configure_otel(service_name: str, endpoint: str):
    """Best-effort OTel tracer. Degrades to a no-op tracer if the SDK/exporter
    is unavailable, so obs never blocks startup."""
    global _tracer
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=_otlp_http_traces_url(endpoint)))
            )
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
    except Exception:
        _tracer = None
    return _tracer


def get_tracer():
    """The configured tracer, or None if OTel is not wired."""
    return _tracer


def is_trace_bodies_enabled() -> bool:
    return get_settings().trace_bodies


@dataclass(frozen=True)
class RequestTraceContext:
    logical_model: str
    request_model: str
    request_kind: str
    trace_bodies: bool = False
    request_id: str = ""
    extra: dict[str, object] = field(default_factory=dict)

    def attrs(self) -> dict[str, object]:
        data: dict[str, object] = {
            "agentproxy.logical_model": self.logical_model,
            "gen_ai.request.model": self.request_model,
            "agentproxy.request_kind": self.request_kind,
        }
        if self.request_id:
            data["agentproxy.request_id"] = self.request_id
        data.update(self.extra)
        return data


def request_log_fields(ctx: RequestTraceContext, **fields: object) -> dict[str, object]:
    out = {
        "logical_model": ctx.logical_model,
        "request_model": ctx.request_model,
        "request_kind": ctx.request_kind,
    }
    if ctx.request_id:
        out["request_id"] = ctx.request_id
    out.update(ctx.extra)
    out.update(fields)
    return out


def _configure_sentry(dsn: str, service_name: str) -> None:
    if not dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=dsn, traces_sample_rate=0.0, environment=service_name)
    except Exception:
        pass


_initialized = False


def setup_observability() -> structlog.BoundLogger:
    """Idempotent obs bring-up. Call once at import/startup, before any logic."""
    global _initialized
    settings = get_settings()
    if not _initialized:
        _configure_structlog(settings.log_level)
        _configure_otel(settings.service_name, settings.otel_exporter_otlp_endpoint)
        _configure_sentry(settings.resolved_sentry_dsn(), settings.service_name)
        _initialized = True
    return structlog.get_logger(settings.service_name)


# Wire obs at import time so importing any app module gives instrumented logs.
log = setup_observability()
