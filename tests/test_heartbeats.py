"""SSE heartbeats carrying attempt state (issue #104)."""

import asyncio
import json

import pytest

from app import main, models, resilience, upstream
from app.config import get_settings
from app.models import Backend, LogicalModel

CATALOG: dict[str, int | None] = {"qwen3:4b": 262144}


@pytest.fixture
def streaming(monkeypatch, app_client):
    async def fake_catalog(_base_url):
        return dict(CATALOG), True

    monkeypatch.setattr(models, "_catalog", fake_catalog)
    models.reset_catalog()
    yield app_client


def _stream(client, **body):
    return client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen3:4b",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
            **body,
        },
    )


def _comments(text: str) -> list[dict]:
    return [
        json.loads(line[1:].strip())
        for line in text.splitlines()
        if line.startswith(":") and line[1:].strip()
    ]


def _data_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("data:")]


def _one_backend_stream(monkeypatch, chunks):
    """Drive the real dispatch_stream over a stubbed upstream."""

    async def fake_chat_stream(backend, num_ctx, messages, **_kwargs):
        for chunk in chunks:
            yield chunk

    monkeypatch.setattr(upstream, "chat_stream", fake_chat_stream)
    monkeypatch.setattr(resilience, "breakers", resilience.CircuitBreakerRegistry())


def test_attempt_state_precedes_the_first_content_delta(streaming, monkeypatch):
    _one_backend_stream(
        monkeypatch,
        [{"message": {"content": "Paris"}, "done": True, "done_reason": "stop"}],
    )

    body = _stream(streaming).text
    states = _comments(body)

    assert [state["state"] for state in states][:2] == ["attempt", "upstream_started"]
    assert states[0]["n"] == 1 and states[0]["of"] == 1
    # The heartbeat arrives before any content reaches the caller.
    assert body.index(": {") < body.index('"content": "Paris"')


def test_attempt_number_surfaces_a_silent_fallback(streaming, monkeypatch):
    """The retry visibility issue #104 said it would not skip."""
    calls: list[str] = []

    async def fake_chat_stream(backend, num_ctx, messages, **_kwargs):
        calls.append(backend.name)
        if backend.name == "primary":
            raise upstream.UpstreamError("primary down")
            yield  # pragma: no cover - generator marker
        yield {"message": {"content": "ok"}, "done": True, "done_reason": "stop"}

    monkeypatch.setattr(upstream, "chat_stream", fake_chat_stream)
    monkeypatch.setattr(resilience, "breakers", resilience.CircuitBreakerRegistry())

    model = LogicalModel(
        name="qwen3:4b",
        num_ctx=4096,
        backends=[
            Backend(name="primary", url="http://a", ollama_tag="t"),
            Backend(name="secondary", url="http://b", ollama_tag="t"),
        ],
    )

    async def resolve_model(name):
        return model if name == model.name else None

    monkeypatch.setattr(main, "resolve", resolve_model)

    states = _comments(_stream(streaming).text)

    assert calls == ["primary", "secondary"]
    attempts = [state for state in states if state["state"] == "attempt"]
    # From outside, two attempts used to be indistinguishable from one slow one.
    assert [(state["n"], state["of"], state["backend"]) for state in attempts] == [
        (1, 2, "primary"),
        (2, 2, "secondary"),
    ]


def test_a_consumer_ignoring_comments_sees_unchanged_output(streaming, monkeypatch):
    chunks = [{"message": {"content": "Paris"}, "done": True, "done_reason": "stop"}]
    _one_backend_stream(monkeypatch, chunks)

    with_beats = _stream(streaming).text
    monkeypatch.setattr(get_settings(), "heartbeat_interval", 0.0)
    _one_backend_stream(monkeypatch, chunks)
    baseline = _stream(streaming).text

    # Chunk ids differ per request, so compare the shape rather than the bytes.
    assert len(_data_lines(with_beats)) == len(_data_lines(baseline))
    assert _comments(baseline) != []  # state markers are not the keepalive
    assert all(line.startswith(("data:", ":")) or not line for line in with_beats.splitlines())


def test_heartbeats_are_counted(streaming, monkeypatch):
    from app.obs import llm_stream_heartbeats_total

    metric = llm_stream_heartbeats_total.labels(logical_model="qwen3:4b", state="attempt")
    before = metric._value.get()
    _one_backend_stream(
        monkeypatch, [{"message": {"content": "x"}, "done": True, "done_reason": "stop"}]
    )

    _stream(streaming)

    assert metric._value.get() == before + 1


# --- the keepalive interleaver, on its own ---------------------------------- #


async def test_keepalives_fire_while_a_state_persists():
    released = asyncio.Event()

    async def slow():
        await released.wait()
        yield {"message": {"content": "late"}}

    seen: list[object] = []

    async def drain():
        async for item in main._with_keepalives(slow(), 0.02):
            seen.append(item)

    task = asyncio.create_task(drain())
    # Wait for the behaviour rather than for the clock, so a loaded runner
    # cannot turn this into a flake.
    for _ in range(200):
        if seen:
            break
        await asyncio.sleep(0.01)
    assert seen and all(item is main._KEEPALIVE for item in seen)
    keepalives = len(seen)

    released.set()
    await asyncio.wait_for(task, timeout=1)
    # The real chunk arrives after the keepalives, and the read was never lost.
    assert seen[-1] == {"message": {"content": "late"}}
    assert len(seen) == keepalives + 1


async def test_zero_interval_disables_keepalives():
    async def quick():
        yield {"message": {"content": "a"}}

    seen = [item async for item in main._with_keepalives(quick(), 0.0)]
    assert seen == [{"message": {"content": "a"}}]


async def test_keepalives_do_not_swallow_an_upstream_error():
    async def failing():
        raise upstream.UpstreamError("boom")
        yield  # pragma: no cover - generator marker

    with pytest.raises(upstream.UpstreamError):
        async for _item in main._with_keepalives(failing(), 0.02):
            pass
