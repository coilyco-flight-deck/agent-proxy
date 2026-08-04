"""OpenAI-compatible surface (leg 04 steps 1 and 6). Upstream is stubbed so
these run without the tower."""

import json

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from app import models, resilience, upstream
from app.readiness import RouteReadiness, UnknownRoute
from app.route_registry import DirectTarget, Route, RouteRegistry
from app.upstream import UpstreamResult

# The tags the fake backend advertises (issue #32: real ollama tags pass through).
CATALOG: dict[str, int | None] = {"qwen3:4b": 262144, "qwen3:8b": 40960}


@pytest.fixture
def client(monkeypatch, app_client):
    async def fake_chat(backend, num_ctx, messages, *, tools=None, options=None, span_attrs=None):
        return UpstreamResult(
            model=backend.ollama_tag,
            content="Paris",
            prompt_eval_count=42,
            eval_count=3,
        )

    async def fake_catalog(_base_url):
        return dict(CATALOG), True

    monkeypatch.setattr(upstream, "chat", fake_chat)
    monkeypatch.setattr(models, "_catalog", fake_catalog)
    models.reset_catalog()
    yield app_client


class _Span:
    def __init__(self, name, sink):
        self.name = name
        self.sink = sink

    def __enter__(self):
        self.sink.append((self.name, {}))
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def set_attribute(self, key, value):
        self.sink[-1][1][key] = value

    def record_exception(self, exc):
        self.sink[-1][1]["exception"] = str(exc)


class _Tracer:
    def __init__(self, sink):
        self.sink = sink

    def start_as_current_span(self, name):
        return _Span(name, self.sink)


def _span_attrs(spans, name):
    matches = [attrs for span_name, attrs in spans if span_name == name]
    assert matches, f"missing span {name}"
    return matches[-1]


def _capture_events(capsys):
    events = []
    for line in capsys.readouterr().out.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("event") in {"model.request.captured", "model.response.captured"}:
            events.append(payload)
    return events


def test_error_response_records_closed_set_exception(monkeypatch):
    recorded = []
    monkeypatch.setattr("app.main.record_error", recorded.append)

    from app.main import _error

    response = _error(502, "dynamic diagnostic", "upstream_error")

    assert response.status_code == 502
    assert recorded == ["upstream_error"]


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_route_readiness_endpoint(client, monkeypatch):
    async def ready(route):
        return RouteReadiness(route=route, ready=True, failed_checks=())

    monkeypatch.setattr("app.main.check_route_readiness", ready)

    response = client.get("/readyz/community/conversation-management")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "route": "community/conversation-management",
    }


def test_route_readiness_failure_is_minimal(client, monkeypatch):
    async def not_ready(route):
        return RouteReadiness(
            route=route,
            ready=False,
            failed_checks=("litellm_catalog", "ollama_catalog"),
        )

    monkeypatch.setattr("app.main.check_route_readiness", not_ready)

    response = client.get("/readyz/community/conversation-management")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "route": "community/conversation-management",
        "failed_checks": ["litellm_catalog", "ollama_catalog"],
    }
    assert "ornith" not in response.text
    assert "http://" not in response.text


def test_unknown_route_is_not_disclosed(client, monkeypatch):
    async def unknown(_route):
        raise UnknownRoute

    monkeypatch.setattr("app.main.check_route_readiness", unknown)

    response = client.get("/readyz/community/unknown")

    assert response.status_code == 404
    assert response.json() == {"status": "unknown_route"}


def test_health_routes_emit_no_server_spans_or_trajectory_events(client, monkeypatch):
    async def ready(route):
        return RouteReadiness(route=route, ready=True, failed_checks=())

    monkeypatch.setattr("app.main.check_route_readiness", ready)
    monkeypatch.setattr(
        "app.main._emit_trajectory_event",
        lambda _event: pytest.fail("health route emitted a trajectory event"),
    )
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    exporter.clear()

    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz/community/conversation-management").status_code == 200
    assert client.get("/metrics").status_code == 200

    health_routes = {"/healthz", "/readyz/{role}/{intent}", "/metrics"}
    assert not any(
        span.kind is SpanKind.SERVER and span.attributes.get("http.route") in health_routes
        for span in exporter.get_finished_spans()
    )


def test_metrics_exposed(client):
    body = client.get("/metrics").text
    assert "llm_requests_total" in body
    assert "agent_proxy_health_endpoint_requests_total" in body
    assert "agent_proxy_readiness_checks_total" in body


def test_list_models(client):
    # /v1/models reflects the tags actually present on the backend (/api/tags),
    # not a static alias list.
    data = client.get("/v1/models").json()
    ids = {m["id"] for m in data["data"]}
    assert ids == {"qwen3:4b", "qwen3:8b"}


def _registry(runtime: str = "ollama") -> RouteRegistry:
    route = Route(
        key="community/knowledge-retrieval",
        upstream_alias="community/knowledge-retrieval",
        direct=DirectTarget("ornith:35b", runtime),
    )
    return RouteRegistry(routes={route.key: route}, source={})


def test_logical_catalog_hides_physical_models(client, monkeypatch):
    monkeypatch.setattr(models, "get_route_registry", _registry)

    data = client.get("/v1/models").json()
    ids = {model["id"] for model in data["data"]}

    assert ids == {"community/knowledge-retrieval"}
    assert "ornith:35b" not in ids


def test_logical_request_forwards_alias_without_mutating_messages(client, monkeypatch):
    settings = models.get_settings()
    monkeypatch.setattr(settings, "route_upstream_mode", "litellm")
    monkeypatch.setattr(
        settings,
        "backends_json",
        json.dumps(
            [
                {
                    "name": "litellm",
                    "url": "http://litellm:4000",
                    "dialect": "openai",
                }
            ]
        ),
    )
    monkeypatch.setattr(models, "get_route_registry", _registry)

    async def fake_tower_catalog(_base_url):
        return {"ornith:35b": 65536}, True

    captured = {}

    async def fake_chat(backend, num_ctx, messages, *, tools=None, options=None, span_attrs=None):
        captured["model"] = backend.ollama_tag
        captured["messages"] = messages
        captured["span_attrs"] = span_attrs
        return UpstreamResult(model=backend.ollama_tag, content="routed")

    monkeypatch.setattr(models, "_ollama_catalog", fake_tower_catalog)
    monkeypatch.setattr(upstream, "chat", fake_chat)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "community/knowledge-retrieval",
            "messages": [{"role": "user", "content": "retrieve this"}],
            "metadata": {"ward.role": "community"},
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == "community/knowledge-retrieval"
    assert captured["model"] == "community/knowledge-retrieval"
    assert captured["messages"] == [{"role": "user", "content": "retrieve this"}]
    assert captured["span_attrs"]["agentproxy.upstream_mode"] == "litellm"


def test_unsupported_direct_route_fails_closed(client, monkeypatch):
    settings = models.get_settings()
    monkeypatch.setattr(settings, "route_upstream_mode", "direct")
    monkeypatch.setattr(settings, "backends_json", "")
    monkeypatch.setattr(models, "get_route_registry", lambda: _registry("llama.cpp"))

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "community/knowledge-retrieval",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "model_unavailable"
    assert "gpt-oss" not in response.text
    assert "llama.cpp" not in response.text


def test_unknown_logical_route_is_not_found(client, monkeypatch):
    monkeypatch.setattr(models, "get_route_registry", _registry)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "community/not-a-lane", "messages": []},
    )

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "model_not_found"


def test_disabled_logical_route_is_unavailable(client, monkeypatch):
    route = Route(
        key="community/knowledge-retrieval",
        upstream_alias="community/knowledge-retrieval",
        direct=DirectTarget("ornith:35b", "ollama"),
        enabled=False,
    )
    registry = RouteRegistry(routes={route.key: route}, source={})
    monkeypatch.setattr(models, "get_route_registry", lambda: registry)

    response = client.post(
        "/v1/chat/completions",
        json={"model": route.key, "messages": []},
    )

    assert response.status_code == 503
    assert response.json()["error"]["message"] == (
        "route 'community/knowledge-retrieval' is disabled"
    )


def _mcp_post(client, method, params=None, request_id=1):
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json=body,
    )


def test_mcp_streamable_http_lists_tools_and_models(client):
    initialized = _mcp_post(
        client,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        },
    )
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "agent-proxy"

    tools = _mcp_post(client, "tools/list", request_id=2)
    assert tools.status_code == 200
    assert {tool["name"] for tool in tools.json()["result"]["tools"]} == {
        "list_models",
        "send_prompt",
    }

    models_result = _mcp_post(
        client,
        "tools/call",
        {"name": "list_models", "arguments": {}},
        request_id=3,
    )
    assert models_result.status_code == 200
    assert set(models_result.json()["result"]["structuredContent"]["models"]) == set(CATALOG)


def test_mcp_send_prompt_uses_chat_completion_path(client):
    response = _mcp_post(
        client,
        "tools/call",
        {
            "name": "send_prompt",
            "arguments": {
                "prompt": "capital of France?",
                "model": "qwen3:4b",
                "system_prompt": "Answer briefly.",
            },
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"] == {
        "model": "qwen3:4b",
        "content": "Paris",
        "reasoning_content": "",
        "tool_calls": [],
        "finish_reason": "stop",
        "usage": {
            "prompt_tokens": 42,
            "completion_tokens": 3,
            "total_tokens": 45,
        },
    }


def test_mcp_send_prompt_returns_tool_error_for_unknown_model(client):
    response = _mcp_post(
        client,
        "tools/call",
        {
            "name": "send_prompt",
            "arguments": {"prompt": "hello", "model": "nope"},
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert "unknown model" in result["content"][0]["text"]


def test_mcp_rejects_untrusted_origin(client):
    response = client.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "Origin": "https://untrusted.example",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code == 403


def test_chat_completion_openai_shape(client):
    # Faked dispatch (upstream.chat -> fake ollama reply) must come back shaped to
    # the full OpenAI chat.completion schema this step (#10) locks in: an assistant
    # message with content, a stop finish_reason, and a coherent usage block where
    # total_tokens is the sum of the prompt/completion counts.
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "capital of France?"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "qwen3:4b"
    choice = body["choices"][0]
    assert choice["index"] == 0
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == "Paris"
    assert choice["finish_reason"] == "stop"
    usage = body["usage"]
    assert usage["prompt_tokens"] == 42
    assert usage["completion_tokens"] == 3
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_chat_completion_emits_request_lifecycle_logs(client, monkeypatch):
    events = []

    class CapturingLog:
        def info(self, event, **fields):
            events.append((event, fields))

    def capture_span_log(_span, event, **fields):
        events.append((event, fields))

    monkeypatch.setattr("app.main.log", CapturingLog())
    monkeypatch.setattr("app.main.log_on_span", capture_span_log)

    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "capital of France?"}],
        },
    )

    assert resp.status_code == 200
    assert ("request.accepted", "accepted") in [
        (event, fields["outcome"]) for event, fields in events
    ]
    assert ("request.completed", "ok") in [(event, fields["outcome"]) for event, fields in events]


def test_chat_completion_offers_metadata_trajectory_events(client, monkeypatch):
    class CapturingEmitter:
        dropped = 0

        def __init__(self):
            self.payloads = []

        def emit_nowait(self, payload):
            self.payloads.append(payload)
            return True

        async def stop(self):
            return None

    emitter = CapturingEmitter()
    monkeypatch.setattr("app.main._trajectory_emitter", emitter)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "private prompt"}],
        },
        headers={
            "x-request-id": "fixture-request",
            "x-ward-run-id": "fixture-run",
        },
    )

    assert response.status_code == 200
    assert [payload["event_type"] for payload in emitter.payloads] == [
        "action.proposed",
        "execution.completed",
    ]
    retained = json.dumps(emitter.payloads, sort_keys=True)
    assert "private prompt" not in retained
    assert emitter.payloads[-1]["correlation"]["ward_run_id"] == "fixture-run"


def test_chat_completion_uses_tracing(client, monkeypatch):
    spans = []
    terminal_logs = []
    tracer = _Tracer(spans)
    monkeypatch.setattr("app.main.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.queue.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.resilience.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.upstream.get_tracer", lambda: tracer)
    monkeypatch.setattr(
        "app.main.log_on_span",
        lambda span, event, *args, **fields: terminal_logs.append((span.name, event)),
    )

    async def fake_chat(backend, num_ctx, messages, *, tools=None, options=None, span_attrs=None):
        return UpstreamResult(
            model=backend.ollama_tag, content="Paris", prompt_eval_count=42, eval_count=3
        )

    async def fake_catalog(_base_url):
        return dict(CATALOG), True

    monkeypatch.setattr(upstream, "chat", fake_chat)
    monkeypatch.setattr(models, "_catalog", fake_catalog)
    models.reset_catalog()
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "capital of France?"}],
        },
    )
    assert resp.status_code == 200
    assert any(name == "request.chat" for name, _ in spans)
    assert ("request.chat", "request.completed") in terminal_logs


def test_chat_completion_preserves_remote_trace_context(client):
    exporter = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    remote_trace_id = "1234567890abcdef1234567890abcdef"
    remote_span_id = "1234567890abcdef"

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "capital of France?"}],
        },
        headers={"traceparent": f"00-{remote_trace_id}-{remote_span_id}-01"},
    )

    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    server_span = next(
        span
        for span in spans
        if span.kind is SpanKind.SERVER
        and span.attributes.get("http.route") == "/v1/chat/completions"
    )
    request_span = next(span for span in spans if span.name == "request.chat")
    assert f"{server_span.context.trace_id:032x}" == remote_trace_id
    assert request_span.context.trace_id == server_span.context.trace_id
    assert request_span.parent.span_id == server_span.context.span_id
    assert server_span.parent.span_id == int(remote_span_id, 16)
    assert "agentproxy.messages" not in request_span.attributes
    assert "agentproxy.tools" not in request_span.attributes
    assert "agentproxy.request.body" not in request_span.attributes
    assert "agentproxy.response.body" not in request_span.attributes


def test_chat_completion_ingests_ward_headers(client, monkeypatch):
    spans = []
    tracer = _Tracer(spans)
    monkeypatch.setattr("app.main.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.queue.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.resilience.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.upstream.get_tracer", lambda: tracer)

    async def fake_chat(backend, num_ctx, messages, *, tools=None, options=None, span_attrs=None):
        return UpstreamResult(
            model=backend.ollama_tag, content="Paris", prompt_eval_count=42, eval_count=3
        )

    async def fake_catalog(_base_url):
        return dict(CATALOG), True

    monkeypatch.setattr(upstream, "chat", fake_chat)
    monkeypatch.setattr(models, "_catalog", fake_catalog)
    models.reset_catalog()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "qwen3:4b", "messages": [{"role": "user", "content": "hi"}]},
        headers={
            "x-request-id": "req-123",
            "x-ward-run-id": "run-123",
            "x-ward-container-name": "container-123",
            "x-ward-role": "role-123",
            "x-ward-harness": "harness-123",
            "x-ward-target-repo": "repo-123",
            "x-ward-issue-ref": "issue-123",
            "x-ward-workflow": "workflow-123",
            "x-ward-context-level": "2",
            "x-ward-version": "v1",
            "x-agent-session-id": "session-123",
        },
    )
    assert resp.status_code == 200
    attrs = _span_attrs(spans, "request.chat")
    assert attrs["agentproxy.request_id"] == "req-123"
    assert attrs["ward.run_id"] == "run-123"
    assert attrs["ward.container_name"] == "container-123"
    assert attrs["ward.role"] == "role-123"
    assert attrs["ward.harness"] == "harness-123"
    assert attrs["ward.target_repo"] == "repo-123"
    assert attrs["ward.issue_ref"] == "issue-123"
    assert attrs["ward.workflow"] == "workflow-123"
    assert attrs["ward.context_level"] == "2"
    assert attrs["ward.version"] == "v1"
    assert attrs["agent.session_id"] == "session-123"
    assert _span_attrs(spans, "queue.wait")["ward.run_id"] == "run-123"


def test_chat_completion_body_metadata_fallback(client, monkeypatch):
    spans = []
    tracer = _Tracer(spans)
    monkeypatch.setattr("app.main.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.queue.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.resilience.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.upstream.get_tracer", lambda: tracer)

    async def fake_chat(backend, num_ctx, messages, *, tools=None, options=None, span_attrs=None):
        return UpstreamResult(
            model=backend.ollama_tag, content="Paris", prompt_eval_count=42, eval_count=3
        )

    async def fake_catalog(_base_url):
        return dict(CATALOG), True

    monkeypatch.setattr(upstream, "chat", fake_chat)
    monkeypatch.setattr(models, "_catalog", fake_catalog)
    models.reset_catalog()
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {
                "request_id": "req-body",
                "ward.run_id": "run-body",
                "ward.target_repo": "repo-body",
                "ward.issue_ref": "issue-body",
                "ward.harness": "harness-body",
                "agent.session_id": "session-body",
            },
        },
    )
    assert resp.status_code == 200
    attrs = _span_attrs(spans, "request.chat")
    assert attrs["agentproxy.request_id"] == "req-body"
    assert attrs["ward.run_id"] == "run-body"
    assert attrs["ward.target_repo"] == "repo-body"
    assert attrs["ward.issue_ref"] == "issue-body"
    assert attrs["ward.harness"] == "harness-body"
    assert attrs["agent.session_id"] == "session-body"


def test_chat_completion_headers_override_body_metadata(client, monkeypatch):
    spans = []
    tracer = _Tracer(spans)
    monkeypatch.setattr("app.main.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.queue.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.resilience.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.upstream.get_tracer", lambda: tracer)

    async def fake_chat(backend, num_ctx, messages, *, tools=None, options=None, span_attrs=None):
        return UpstreamResult(
            model=backend.ollama_tag, content="Paris", prompt_eval_count=42, eval_count=3
        )

    async def fake_catalog(_base_url):
        return dict(CATALOG), True

    monkeypatch.setattr(upstream, "chat", fake_chat)
    monkeypatch.setattr(models, "_catalog", fake_catalog)
    models.reset_catalog()
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3:4b",
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {
                "request_id": "req-body",
                "ward.run_id": "run-body",
                "ward.target_repo": "repo-body",
                "ward.issue_ref": "issue-body",
                "agent.session_id": "session-body",
            },
        },
        headers={
            "x-request-id": "req-header",
            "x-ward-run-id": "run-header",
            "x-ward-target-repo": "repo-header",
            "x-ward-issue-ref": "issue-header",
            "x-agent-session-id": "session-header",
        },
    )
    assert resp.status_code == 200
    attrs = _span_attrs(spans, "request.chat")
    assert attrs["agentproxy.request_id"] == "req-header"
    assert attrs["ward.run_id"] == "run-header"
    assert attrs["ward.target_repo"] == "repo-header"
    assert attrs["ward.issue_ref"] == "issue-header"
    assert attrs["agent.session_id"] == "session-header"


def test_completions_body_metadata_fallback(client, monkeypatch):
    spans = []
    tracer = _Tracer(spans)
    monkeypatch.setattr("app.main.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.queue.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.resilience.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.upstream.get_tracer", lambda: tracer)

    async def fake_chat(backend, num_ctx, messages, *, tools=None, options=None, span_attrs=None):
        return UpstreamResult(
            model=backend.ollama_tag, content="Paris", prompt_eval_count=42, eval_count=3
        )

    async def fake_catalog(_base_url):
        return dict(CATALOG), True

    monkeypatch.setattr(upstream, "chat", fake_chat)
    monkeypatch.setattr(models, "_catalog", fake_catalog)
    models.reset_catalog()
    resp = client.post(
        "/v1/completions",
        json={
            "model": "qwen3:4b",
            "prompt": "hello",
            "metadata": {
                "request_id": "req-completions-body",
                "ward.run_id": "run-completions-body",
                "ward.target_repo": "repo-completions-body",
                "ward.issue_ref": "issue-completions-body",
            },
        },
    )
    assert resp.status_code == 200
    attrs = _span_attrs(spans, "request.completions")
    assert attrs["agentproxy.request_id"] == "req-completions-body"
    assert attrs["ward.run_id"] == "run-completions-body"
    assert attrs["ward.target_repo"] == "repo-completions-body"
    assert attrs["ward.issue_ref"] == "issue-completions-body"


def test_stream_chat_completion_ingests_metadata(client, monkeypatch):
    spans = []
    tracer = _Tracer(spans)
    monkeypatch.setattr("app.main.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.resilience.get_tracer", lambda: tracer)
    monkeypatch.setattr("app.upstream.get_tracer", lambda: tracer)

    async def fake_dispatch_stream(model, messages, *, tools=None, options=None, trace_ctx=None):
        assert trace_ctx is not None
        attrs = trace_ctx.attrs()
        assert attrs["agentproxy.request_id"] == "req-stream"
        assert attrs["ward.run_id"] == "run-stream"
        assert attrs["ward.target_repo"] == "repo-stream"
        assert attrs["ward.issue_ref"] == "issue-stream"
        assert attrs["ward.harness"] == "harness-stream"
        assert attrs["agent.session_id"] == "session-stream"
        yield {
            "message": {"content": "Paris"},
            "done": True,
            "done_reason": "stop",
        }

    async def fake_catalog(_base_url):
        return dict(CATALOG), True

    monkeypatch.setattr(resilience, "dispatch_stream", fake_dispatch_stream)
    monkeypatch.setattr(models, "_catalog", fake_catalog)
    models.reset_catalog()
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3:4b",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {
                "ward.run_id": "run-stream",
                "ward.target_repo": "repo-stream",
                "ward.issue_ref": "issue-stream",
                "ward.harness": "harness-stream",
                "agent.session_id": "session-stream",
            },
        },
        headers={"x-request-id": "req-stream"},
    )
    assert resp.status_code == 200
    assert "data:" in resp.text
    assert _span_attrs(spans, "request.chat")["ward.run_id"] == "run-stream"


def test_unknown_model_404(client):
    resp = client.post("/v1/chat/completions", json={"model": "nope", "messages": []})
    assert resp.status_code == 404
    assert resp.json()["error"]["type"] == "model_not_found"


def test_completions_surface(client):
    resp = client.post("/v1/completions", json={"model": "qwen3:4b", "prompt": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "text_completion"
    assert body["choices"][0]["text"] == "Paris"


def test_chat_capture_contains_every_request_and_response_field(client, monkeypatch, capsys):
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    exporter.clear()
    monkeypatch.setattr("app.main.is_trace_bodies_enabled", lambda: True)

    async def captured_chat(
        backend, num_ctx, messages, *, tools=None, options=None, span_attrs=None
    ):
        return UpstreamResult(
            model=backend.ollama_tag,
            content="Paris",
            thinking="The capital is well known.",
            tool_calls=[{"function": {"name": "lookup", "arguments": {"city": "Paris"}}}],
            prompt_eval_count=42,
            eval_count=3,
        )

    monkeypatch.setattr(upstream, "chat", captured_chat)
    capsys.readouterr()
    request_body = {
        "model": "qwen3:4b",
        "messages": [{"role": "user", "content": "capital of France?"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "lookup", "parameters": {"type": "object"}},
            }
        ],
        "temperature": 0.25,
        "response_format": {"type": "json_object"},
        "metadata": {"fixture": {"nested": [1, 2, 3]}},
        "vendor_extension": {"preserved": True},
    }
    response = client.post(
        "/v1/chat/completions",
        json=request_body,
        headers={"authorization": "Bearer must-not-be-captured", "x-request-id": "capture-chat"},
    )

    assert response.status_code == 200
    events = _capture_events(capsys)
    assert [event["event"] for event in events] == [
        "model.request.captured",
        "model.response.captured",
    ]
    assert events[0]["request.body"] == request_body
    assert events[1]["response.body"] == response.json()
    assert "authorization" not in json.dumps(events)
    assert events[0]["trace_id"] == events[1]["trace_id"]
    assert events[0]["span_id"] == events[1]["span_id"]
    assert events[0]["agentproxy.request_id"] == "capture-chat"

    request_span = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "request.chat"
        and span.attributes.get("agentproxy.request_id") == "capture-chat"
    )
    assert json.loads(request_span.attributes["agentproxy.request.body"]) == request_body
    assert json.loads(request_span.attributes["agentproxy.response.body"]) == response.json()


def test_stream_capture_reconstructs_reasoning_tools_usage_and_finish(client, monkeypatch, capsys):
    monkeypatch.setattr("app.main.is_trace_bodies_enabled", lambda: True)

    async def fake_dispatch_stream(model, messages, *, tools=None, options=None, trace_ctx=None):
        yield {
            "message": {"content": "Par", "thinking": "known "},
            "done": False,
        }
        yield {
            "message": {
                "content": "is",
                "thinking": "answer",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-fixture",
                        "type": "function",
                        "function": {"name": "look_", "arguments": '{"city":"Par'},
                    }
                ],
            },
            "done": False,
        }
        yield {
            "message": {
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"name": "up", "arguments": 'is"}'},
                    }
                ]
            },
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 7,
            "eval_count": 4,
        }

    monkeypatch.setattr(resilience, "dispatch_stream", fake_dispatch_stream)
    capsys.readouterr()
    request_body = {
        "model": "qwen3:4b",
        "stream": True,
        "messages": [{"role": "user", "content": "capital?"}],
        "stream_options": {"include_usage": True},
    }
    response = client.post(
        "/v1/chat/completions",
        json=request_body,
        headers={"x-request-id": "capture-stream"},
    )

    assert response.status_code == 200
    assert "reasoning_content" in response.text
    assert "tool_calls" in response.text
    events = _capture_events(capsys)
    assert len(events) == 2
    assert events[0]["request.body"] == request_body
    captured = events[1]["response.body"]
    message = captured["choices"][0]["message"]
    assert message["content"] == "Paris"
    assert message["reasoning_content"] == "known answer"
    assert message["tool_calls"] == [
        {
            "id": "call-fixture",
            "type": "function",
            "function": {"name": "look_up", "arguments": '{"city":"Paris"}'},
        }
    ]
    assert captured["choices"][0]["finish_reason"] == "tool_calls"
    assert captured["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 4,
        "total_tokens": 11,
    }


def test_text_completion_capture_uses_normalized_prompt_and_full_response(
    client, monkeypatch, capsys
):
    monkeypatch.setattr("app.main.is_trace_bodies_enabled", lambda: True)
    capsys.readouterr()

    response = client.post(
        "/v1/completions",
        json={
            "model": "qwen3:4b",
            "prompt": ["hello", "world"],
            "temperature": 0.1,
            "suffix": "preserved",
        },
        headers={"x-request-id": "capture-completion"},
    )

    assert response.status_code == 200
    events = _capture_events(capsys)
    assert len(events) == 2
    assert events[0]["request.body"] == {
        "model": "qwen3:4b",
        "prompt": "hello\nworld",
        "temperature": 0.1,
        "suffix": "preserved",
    }
    assert events[1]["response.body"] == response.json()


def test_mcp_prompt_capture_uses_mcp_boundary_shapes(client, monkeypatch, capsys):
    monkeypatch.setattr("app.main.is_trace_bodies_enabled", lambda: True)
    capsys.readouterr()

    response = _mcp_post(
        client,
        "tools/call",
        {
            "name": "send_prompt",
            "arguments": {
                "prompt": "capital of France?",
                "model": "qwen3:4b",
                "system_prompt": "Answer briefly.",
                "max_tokens": 32,
                "temperature": 0.2,
            },
        },
    )

    assert response.status_code == 200
    events = _capture_events(capsys)
    assert len(events) == 2
    assert events[0]["request.body"] == {
        "prompt": "capital of France?",
        "model": "qwen3:4b",
        "system_prompt": "Answer briefly.",
        "max_tokens": 32,
        "temperature": 0.2,
    }
    assert events[1]["response.body"] == response.json()["result"]["structuredContent"]


def test_mcp_prompt_capture_records_incomplete_upstream_failure(client, monkeypatch, capsys):
    class FailingQueue:
        async def submit(self, *_args, **_kwargs):
            raise resilience.AllBackendsFailed("fixture upstream failure")

    monkeypatch.setattr("app.main.is_trace_bodies_enabled", lambda: True)
    monkeypatch.setattr("app.main.get_queue", lambda: FailingQueue())
    capsys.readouterr()

    response = _mcp_post(
        client,
        "tools/call",
        {
            "name": "send_prompt",
            "arguments": {"prompt": "fail", "model": "qwen3:4b"},
        },
    )

    assert response.status_code == 200
    assert response.json()["result"]["isError"] is True
    events = _capture_events(capsys)
    assert len(events) == 2
    assert events[1]["agentproxy.capture.status"] == "incomplete"
    assert events[1]["agentproxy.capture.reason"] == "upstream_failed"
    assert events[1]["response.body"]["error"]["type"] == "upstream_error"


@pytest.mark.parametrize(
    "path,body,request_id",
    [
        (
            "/v1/chat/completions",
            {"model": "qwen3:4b", "messages": [{"role": "user", "content": "fail"}]},
            "capture-chat-failure",
        ),
        (
            "/v1/completions",
            {"model": "qwen3:4b", "prompt": "fail"},
            "capture-completion-failure",
        ),
    ],
)
def test_http_capture_records_incomplete_upstream_failures(
    client, monkeypatch, capsys, path, body, request_id
):
    class FailingQueue:
        async def submit(self, *_args, **_kwargs):
            raise resilience.AllBackendsFailed("fixture upstream failure")

    monkeypatch.setattr("app.main.is_trace_bodies_enabled", lambda: True)
    monkeypatch.setattr("app.main.get_queue", lambda: FailingQueue())
    capsys.readouterr()

    response = client.post(path, json=body, headers={"x-request-id": request_id})

    assert response.status_code == 502
    events = _capture_events(capsys)
    assert len(events) == 2
    assert events[1]["agentproxy.capture.status"] == "incomplete"
    assert events[1]["agentproxy.capture.reason"] == "upstream_failed"
    assert events[1]["response.body"] == response.json()


def test_stream_capture_records_partial_response_on_failure(client, monkeypatch, capsys):
    monkeypatch.setattr("app.main.is_trace_bodies_enabled", lambda: True)

    async def failing_stream(model, messages, *, tools=None, options=None, trace_ctx=None):
        yield {"message": {"content": "partial"}, "done": False}
        raise resilience.AllBackendsFailed("stream interrupted")

    monkeypatch.setattr(resilience, "dispatch_stream", failing_stream)
    capsys.readouterr()

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3:4b",
            "stream": True,
            "messages": [{"role": "user", "content": "fail"}],
        },
        headers={"x-request-id": "capture-stream-failure"},
    )

    assert response.status_code == 200
    events = _capture_events(capsys)
    assert len(events) == 2
    assert events[1]["agentproxy.capture.status"] == "incomplete"
    assert events[1]["agentproxy.capture.reason"] == "stream_failed"
    assert events[1]["response.body"]["choices"][0]["message"]["content"] == "partial"
