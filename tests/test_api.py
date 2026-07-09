"""OpenAI-compatible surface (leg 04 steps 1 and 6). Upstream is stubbed so
these run without the tower."""

import pytest
from fastapi.testclient import TestClient

from app import models, resilience, upstream
from app.main import app
from app.upstream import UpstreamResult

# The tags the fake backend advertises (issue #32: real ollama tags pass through).
CATALOG: dict[str, int | None] = {"qwen3:4b": 262144, "qwen3:8b": 40960}


@pytest.fixture
def client(monkeypatch):
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
    with TestClient(app) as c:
        yield c


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


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_metrics_exposed(client):
    body = client.get("/metrics").text
    assert "llm_requests_total" in body


def test_list_models(client):
    # /v1/models reflects the tags actually present on the backend (/api/tags),
    # not a static alias list.
    data = client.get("/v1/models").json()
    ids = {m["id"] for m in data["data"]}
    assert ids == {"qwen3:4b", "qwen3:8b"}


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


def test_chat_completion_uses_tracing(monkeypatch):
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
    with TestClient(app) as c:
        resp = c.post(
            "/v1/chat/completions",
            json={
                "model": "qwen3:4b",
                "messages": [{"role": "user", "content": "capital of France?"}],
            },
        )
    assert resp.status_code == 200
    assert any(name == "request.chat" for name, _ in spans)


def test_chat_completion_ingests_ward_headers(monkeypatch):
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
    with TestClient(app) as c:
        resp = c.post(
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


def test_chat_completion_body_metadata_fallback(monkeypatch):
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
    with TestClient(app) as c:
        resp = c.post(
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


def test_chat_completion_headers_override_body_metadata(monkeypatch):
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
    with TestClient(app) as c:
        resp = c.post(
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


def test_completions_body_metadata_fallback(monkeypatch):
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
    with TestClient(app) as c:
        resp = c.post(
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


def test_stream_chat_completion_ingests_metadata(monkeypatch):
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
    with TestClient(app) as c:
        resp = c.post(
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
