"""OpenAI-compatible surface (leg 04 steps 1 and 6). Upstream is stubbed so
these run without the tower."""

import pytest
from fastapi.testclient import TestClient

from app import upstream
from app.main import app
from app.upstream import UpstreamResult


@pytest.fixture
def client(monkeypatch):
    async def fake_chat(backend, num_ctx, messages, *, tools=None, options=None):
        return UpstreamResult(
            model=backend.ollama_tag,
            content="Paris",
            prompt_eval_count=42,
            eval_count=3,
        )

    monkeypatch.setattr(upstream, "chat", fake_chat)
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_metrics_exposed(client):
    body = client.get("/metrics").text
    assert "llm_requests_total" in body


def test_list_models(client):
    data = client.get("/v1/models").json()
    ids = {m["id"] for m in data["data"]}
    assert {"fast-think", "fast", "ctx-think", "ctx", "tune"} <= ids


def test_chat_completion_openai_shape(client):
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "fast", "messages": [{"role": "user", "content": "capital of France?"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "Paris"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["prompt_tokens"] == 42


def test_unknown_model_404(client):
    resp = client.post("/v1/chat/completions", json={"model": "nope", "messages": []})
    assert resp.status_code == 404
    assert resp.json()["error"]["type"] == "model_not_found"


def test_completions_surface(client):
    resp = client.post("/v1/completions", json={"model": "fast", "prompt": "hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "text_completion"
    assert body["choices"][0]["text"] == "Paris"
