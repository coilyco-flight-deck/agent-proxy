"""num_ctx injection and OpenAI<->ollama translation (leg 04 step 2)."""

import json

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
        self.last_headers = None
        self.last_url = None

    async def post(self, url, json=None, timeout=None, headers=None):
        self.last_url = url
        self.last_body = json
        self.last_headers = headers
        return _FakeResponse(self.payload)


class _StreamingClient:
    def __init__(self, lines):
        self.lines = lines
        self.last_body = None
        self.last_headers = None
        self.last_url = None

    def stream(self, method, url, json=None, timeout=None, headers=None):
        self.last_url = url
        self.last_body = json
        self.last_headers = headers
        lines = self.lines

        class _Response:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def raise_for_status(self):
                return None

            async def aiter_lines(self):
                for line in lines:
                    yield line

        return _Response()


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


class _Tracer:
    def __init__(self, sink):
        self.sink = sink

    def start_as_current_span(self, name):
        return _Span(name, self.sink)


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


async def test_litellm_backend_authenticates_and_preserves_policy(monkeypatch, tmp_path):
    key_file = tmp_path / "litellm-key"
    key_file.write_text("service-key\n", encoding="utf-8")
    backend = Backend(
        name="litellm",
        url="http://litellm:4000",
        ollama_tag="qwen3-coder:30b",
        dialect="openai",
        api_key_file=str(key_file),
        injects_num_ctx=True,
    )
    fake = _CapturingClient(
        {
            "model": "qwen3-coder:30b",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 14, "completion_tokens": 2},
        }
    )
    monkeypatch.setattr(upstream, "get_client", lambda: fake)

    result = await upstream.chat(
        backend,
        num_ctx=48128,
        messages=[{"role": "user", "content": "x"}],
        options={"temperature": 0.2, "num_predict": 32, "num_ctx": 999999},
        span_attrs={
            "agentproxy.request_id": "request-1",
            "ward.run_id": "run-1",
            "agentproxy.messages": "must-not-cross-the-inner-hop",
        },
    )

    assert fake.last_headers == {"Authorization": "Bearer service-key"}
    assert fake.last_body["num_ctx"] == 48128
    assert fake.last_body["max_tokens"] == 32
    assert "num_predict" not in fake.last_body
    assert fake.last_body["metadata"] == {
        "agentproxy.request_id": "request-1",
        "ward.run_id": "run-1",
    }
    assert result.content == "ok"


async def test_logical_route_metadata_never_enters_message_content(monkeypatch):
    backend = Backend(
        name="litellm",
        url="http://litellm:4000",
        ollama_tag="community/knowledge-retrieval",
        dialect="openai",
    )
    fake = _CapturingClient(
        {
            "model": "community/knowledge-retrieval",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        }
    )
    monkeypatch.setattr(upstream, "get_client", lambda: fake)
    messages = [{"role": "user", "content": "member question"}]

    await upstream.chat(
        backend,
        num_ctx=40960,
        messages=messages,
        span_attrs={
            "agentproxy.logical_model": "community/knowledge-retrieval",
            "agentproxy.upstream_mode": "litellm",
            "ward.role": "community",
        },
    )

    assert fake.last_body["messages"] == messages
    assert fake.last_body["metadata"] == {
        "agentproxy.logical_model": "community/knowledge-retrieval",
        "agentproxy.upstream_mode": "litellm",
        "ward.role": "community",
    }


async def test_litellm_stream_authenticates_and_preserves_policy(monkeypatch, tmp_path):
    key_file = tmp_path / "litellm-key"
    key_file.write_text("service-key\n", encoding="utf-8")
    backend = Backend(
        name="litellm",
        url="http://litellm:4000",
        ollama_tag="qwen3-coder:30b",
        dialect="openai",
        api_key_file=str(key_file),
        injects_num_ctx=True,
    )
    fake = _StreamingClient(
        [
            'data: {"choices":[{"delta":{"content":"hi"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
    )
    monkeypatch.setattr(upstream, "get_client", lambda: fake)
    monkeypatch.setattr(upstream, "get_tracer", lambda: None)

    chunks = [
        chunk
        async for chunk in upstream.chat_stream(
            backend,
            num_ctx=48128,
            messages=[{"role": "user", "content": "x"}],
            options={"num_predict": 16},
            span_attrs={"agentproxy.request_id": "request-1"},
        )
    ]

    assert fake.last_headers == {"Authorization": "Bearer service-key"}
    assert fake.last_body["stream"] is True
    assert fake.last_body["num_ctx"] == 48128
    assert fake.last_body["max_tokens"] == 16
    assert fake.last_body["metadata"] == {"agentproxy.request_id": "request-1"}
    assert chunks == [
        {"message": {"content": "hi"}, "done": False},
        {"message": {}, "done": True, "done_reason": "stop"},
    ]


async def test_missing_litellm_key_fails_without_secret_or_path(monkeypatch, tmp_path):
    backend = Backend(
        name="litellm",
        url="http://litellm:4000",
        ollama_tag="qwen3-coder:30b",
        dialect="openai",
        api_key_file=str(tmp_path / "missing-key"),
    )
    monkeypatch.setattr(upstream, "get_client", lambda: _CapturingClient({}))

    with pytest.raises(upstream.UpstreamError) as exc:
        await upstream.chat(backend, num_ctx=48128, messages=[])

    assert str(exc.value) == "litellm: authentication key unavailable"


async def test_span_attrs_flow_to_upstream_span_and_log(monkeypatch, backend, capsys):
    spans = []
    tracer = _Tracer(spans)
    fake = _CapturingClient({"message": {"content": "ok"}})
    monkeypatch.setattr(upstream, "get_client", lambda: fake)
    monkeypatch.setattr(upstream, "get_tracer", lambda: tracer)

    await upstream.chat(
        backend,
        num_ctx=32768,
        messages=[],
        span_attrs={
            "agentproxy.request_id": "req-1",
            "ward.run_id": "run-1",
            "ward.target_repo": "repo-1",
            "ward.issue_ref": "issue-1",
        },
    )

    attrs = dict(spans[-1][1])
    assert spans[-1][0] == "upstream.chat"
    assert attrs["agentproxy.request_id"] == "req-1"
    assert attrs["ward.run_id"] == "run-1"
    assert attrs["ward.target_repo"] == "repo-1"
    assert attrs["ward.issue_ref"] == "issue-1"
    log_payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert log_payload["event"] == "upstream.completed"
    assert log_payload["outcome"] == "ok"
    assert log_payload["backend"] == "tower"
    assert log_payload["ward.run_id"] == "run-1"
