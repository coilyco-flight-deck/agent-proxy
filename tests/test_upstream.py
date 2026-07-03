"""num_ctx injection and OpenAI<->ollama translation (leg 04 step 2)."""

import pytest

from app import upstream
from app.models import Backend


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _CapturingClient:
    """Records the last posted body so tests can assert on injected options."""

    def __init__(self, payload):
        self.payload = payload
        self.last_body = None
        self.last_url = None

    async def post(self, url, json=None, timeout=None):
        self.last_url = url
        self.last_body = json
        return _FakeResponse(self.payload)


@pytest.fixture
def backend():
    return Backend(name="tower", url="http://tower:11434", ollama_tag="qwen3:30b-a3b")


@pytest.fixture
def openai_backend():
    return Backend(
        name="tower-llama-8080",
        url="http://tower:8080",
        ollama_tag="gpt-oss:120b",
        dialect="openai",
        chat_path="/v1/chat/completions",
        health_path="/health",
        injects_num_ctx=False,
    )


async def test_num_ctx_is_injected(monkeypatch, backend):
    fake = _CapturingClient(
        {"model": "m", "message": {"content": "hi"}, "prompt_eval_count": 49151}
    )
    monkeypatch.setattr(upstream, "get_client", lambda: fake)

    result = await upstream.chat(
        backend, num_ctx=49152, messages=[{"role": "user", "content": "x"}]
    )

    assert fake.last_url == "http://tower:11434/api/chat"
    assert fake.last_body["options"]["num_ctx"] == 49152
    assert fake.last_body["model"] == "qwen3:30b-a3b"
    assert result.prompt_eval_count == 49151
    assert result.content == "hi"


async def test_caller_cannot_override_num_ctx(monkeypatch, backend):
    fake = _CapturingClient({"message": {"content": "ok"}})
    monkeypatch.setattr(upstream, "get_client", lambda: fake)
    # A caller passing their own num_ctx must not defeat the safe injected value.
    await upstream.chat(backend, num_ctx=49152, messages=[], options={"num_ctx": 999})
    assert fake.last_body["options"]["num_ctx"] == 49152


async def test_num_ctx_scaled_by_num_parallel(monkeypatch):
    # issue #33: ollama divides an injected num_ctx across OLLAMA_NUM_PARALLEL
    # slots, so the client injects target*num_parallel to keep the per-request
    # window equal to the derived num_ctx. A NUM_PARALLEL=2 backend gets double.
    parallel_backend = Backend(
        name="tower", url="http://tower:11434", ollama_tag="qwen3:4b", num_parallel=2
    )
    fake = _CapturingClient({"message": {"content": "ok"}})
    monkeypatch.setattr(upstream, "get_client", lambda: fake)
    await upstream.chat(parallel_backend, num_ctx=49152, messages=[])
    assert fake.last_body["options"]["num_ctx"] == 98304


async def test_thinking_is_parsed(monkeypatch, backend):
    fake = _CapturingClient({"message": {"content": "", "thinking": "reasoning..."}})
    monkeypatch.setattr(upstream, "get_client", lambda: fake)
    result = await upstream.chat(backend, num_ctx=32768, messages=[])
    assert result.thinking == "reasoning..."


async def test_openai_backend_uses_chat_completions(monkeypatch, openai_backend):
    fake = _CapturingClient(
        {
            "model": "gpt-oss:120b",
            "choices": [
                {
                    "message": {"content": "hi", "reasoning_content": "why"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        }
    )
    monkeypatch.setattr(upstream, "get_client", lambda: fake)

    result = await upstream.chat(
        openai_backend, num_ctx=32768, messages=[{"role": "user", "content": "x"}]
    )

    assert fake.last_url == "http://tower:8080/v1/chat/completions"
    assert "options" not in fake.last_body
    assert fake.last_body["model"] == "gpt-oss:120b"
    assert result.content == "hi"
    assert result.thinking == "why"
    assert result.prompt_eval_count == 12
    assert result.eval_count == 7
