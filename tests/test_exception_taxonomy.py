"""The closed exception taxonomy and its redaction guarantees (agent-proxy#74).

The taxonomy only bounds SigNoz grouping cardinality if nothing can quietly add
a code to it. `record_error` accepts a plain string, so the guarantee is held
here rather than by the type system: one test walks every literal passed to
`record_error` in `app/` and fails if any is missing from the table, and another
proves an unknown code collapses instead of reaching a span.
"""

import ast
import pathlib

import pytest

from app.obs import (
    ERROR_STAGES,
    ERROR_TAXONOMY,
    UNCLASSIFIED_ERROR,
    classify_error,
    record_error,
)

_APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def _exporter():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider


def _recorded_literals() -> set[str]:
    """Every string literal passed as the first argument to record_error()."""
    found: set[str] = set()
    for path in _APP.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "record_error" or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.add(first.value)
    return found


def test_every_recorded_error_literal_is_in_the_taxonomy():
    """A new record_error("...") code must be added to the table, not invented
    at the call site, or it silently degrades to unclassified in production."""
    literals = _recorded_literals()
    assert literals, "the AST walk found no record_error literals - the scan is broken"
    missing = sorted(literal for literal in literals if literal not in ERROR_TAXONOMY)
    assert not missing, f"codes missing from ERROR_TAXONOMY: {missing}"


def test_error_response_types_are_in_the_taxonomy():
    """`_error()` forwards its err_type straight to record_error, so the client
    facing error types are part of the same closed set."""
    for code in (
        "invalid_request_error",
        "model_not_found",
        "model_unavailable",
        "rate_limit_error",
        "upstream_error",
        "context_truncated",
    ):
        assert code in ERROR_TAXONOMY


def test_grouping_cardinality_is_bounded_and_small():
    """The documented bound. Codes and stages are both finite and enumerable."""
    assert len(ERROR_TAXONOMY) == 14
    assert len(ERROR_STAGES) == 8
    for code, (stage, summary) in ERROR_TAXONOMY.items():
        assert stage in ERROR_STAGES
        assert summary and summary[0].isupper(), f"{code} summary should read as a sentence"
        assert not summary.endswith("."), f"{code} summary should not end in a period"


@pytest.mark.parametrize(
    "hostile",
    [
        "sk-live-DO-NOT-LEAK-9f3a",
        "/v1/chat/completions?token=abc123",
        "user@example.com",
        "http://tower.internal:11434",
        "",
    ],
)
def test_unknown_codes_collapse_and_never_reach_a_span(hostile):
    """An unbounded string must not become a span attribute or a message.

    This is the cardinality and redaction guarantee together: whatever a caller
    passes, only the fixed fallback is recorded.
    """
    code, stage, summary = classify_error(hostile)
    assert code == UNCLASSIFIED_ERROR
    assert stage == "unknown"

    exporter, provider = _exporter()
    with provider.get_tracer("test").start_as_current_span("op") as span:
        record_error(hostile, span)

    ended = exporter.get_finished_spans()
    assert len(ended) == 1
    recorded = ended[0]
    assert recorded.attributes["error.type"] == UNCLASSIFIED_ERROR
    assert recorded.attributes["error.stage"] == "unknown"
    assert recorded.status.description == summary

    for value in recorded.attributes.values():
        assert hostile == "" or hostile not in str(value)
    for event in recorded.events:
        for value in (event.attributes or {}).values():
            assert hostile == "" or hostile not in str(value)


def test_known_codes_keep_their_stable_machine_key():
    """The human summary moved into the message; the code stays the group key."""
    exporter, provider = _exporter()
    with provider.get_tracer("test").start_as_current_span("op") as span:
        record_error("trajectory_event_persist_failed", span)

    recorded = exporter.get_finished_spans()[0]
    assert recorded.attributes["error.type"] == "trajectory_event_persist_failed"
    assert recorded.attributes["error.stage"] == "trajectory"
    assert recorded.status.description == "Trajectory event failed to persist"
    exception = next(event for event in recorded.events if event.name == "exception")
    assert exception.attributes["exception.message"] == "Trajectory event failed to persist"
