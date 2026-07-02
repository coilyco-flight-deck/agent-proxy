"""num_ctx injection and OpenAI<->ollama translation (leg 04 step 2)."""

import httpx
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


async def test_num_ctx_is_injected(monkeypatch, backend):
    fake = _CapturingClient({"model": "m", "message": {"content": "hi"}, "prompt_eval_count": 49151})
    monkeypatch.setattr(upstream, "get_client", lambda: fake)

    result = await upstream.chat(backend, num_ctx=49152, messages=[{"role": "user", "content": "x"}])

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


async def test_thinking_is_parsed(monkeypatch, backend):
    fake = _CapturingClient({"message": {"content": "", "thinking": "reasoning..."}})
    monkeypatch.setattr(upstream, "get_client", lambda: fake)
    result = await upstream.chat(backend, num_ctx=32768, messages=[])
    assert result.thinking == "reasoning..."
