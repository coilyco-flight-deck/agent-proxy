"""Slowness as a failover trigger (issue #108)."""

import asyncio

import pytest

from app import resilience, upstream
from app.config import get_settings
from app.models import Backend, LogicalModel
from app.obs import llm_backend_saturated_total
from app.upstream import UpstreamResult


@pytest.fixture(autouse=True)
def _fresh_breakers(monkeypatch):
    monkeypatch.setattr(resilience, "breakers", resilience.CircuitBreakerRegistry())
    monkeypatch.setattr(get_settings(), "retry_base_delay", 0.0)


def _chain() -> LogicalModel:
    return LogicalModel(
        name="sirens-echo/default",
        num_ctx=4096,
        backends=[
            Backend(name="tower", url="http://tower", ollama_tag="ornith:35b"),
            Backend(name="hosted", url="http://hosted", ollama_tag="deepseek", dialect="openai"),
        ],
    )


def _saturations(model: str, backend: str) -> float:
    return llm_backend_saturated_total.labels(logical_model=model, backend=backend)._value.get()


async def test_a_quiet_backend_fails_over_instead_of_being_waited_on(monkeypatch):
    """The 2026-08-12 outage: the GPU accepted the request and went quiet."""
    served: list[str] = []

    async def chat(backend, *args, **kwargs):
        served.append(backend.name)
        if backend.name == "tower":
            await asyncio.sleep(5)
            raise AssertionError("the saturated backend should never answer")
        return UpstreamResult(model="deepseek", content="from the hosted tier")

    monkeypatch.setattr(upstream, "chat", chat)
    monkeypatch.setattr(get_settings(), "backend_slow_after", 0.5)
    model = _chain()
    before = _saturations(model.name, "tower")

    result = await resilience.dispatch(model, [{"role": "user", "content": "hi"}])

    # Failover on slowness is what makes the cloud tier reachable at all.
    assert result.content == "from the hosted tier"
    assert served == ["tower", "hosted"]
    assert _saturations(model.name, "tower") == before + 1


async def test_a_saturated_backend_stops_receiving_work(monkeypatch):
    async def chat(backend, *args, **kwargs):
        if backend.name == "tower":
            await asyncio.sleep(5)
        return UpstreamResult(model="deepseek", content="ok")

    monkeypatch.setattr(upstream, "chat", chat)
    monkeypatch.setattr(get_settings(), "backend_slow_after", 0.5)
    monkeypatch.setattr(get_settings(), "saturation_threshold", 1)
    model = _chain()

    await resilience.dispatch(model, [{"role": "user", "content": "hi"}])

    # A backend nobody can get a token out of is unavailable, whatever it says.
    assert not resilience.breakers.allow(model.backends[0])


async def test_slowness_is_not_a_failure_when_the_threshold_is_off(monkeypatch):
    calls: list[str] = []

    async def chat(backend, *args, **kwargs):
        calls.append(backend.name)
        await asyncio.sleep(0.05)
        return UpstreamResult(model="ornith", content="slow but fine")

    monkeypatch.setattr(upstream, "chat", chat)
    monkeypatch.setattr(get_settings(), "backend_slow_after", 0.0)

    result = await resilience.dispatch(_chain(), [{"role": "user", "content": "hi"}])

    assert result.content == "slow but fine"
    assert calls == ["tower"]


async def test_a_spent_budget_is_a_deadline_not_a_saturation(monkeypatch):
    async def chat(backend, *args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(upstream, "chat", chat)
    monkeypatch.setattr(get_settings(), "backend_slow_after", 10.0)

    # The request budget runs out first, so the caller is told that, not that
    # every backend was saturated.
    with pytest.raises(resilience.RequestDeadlineExceeded):
        await resilience.dispatch(
            _chain(),
            [{"role": "user", "content": "hi"}],
            deadline=resilience._now() + 0.2,
        )


async def test_every_tier_saturated_exhausts_the_chain(monkeypatch):
    async def chat(backend, *args, **kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(upstream, "chat", chat)
    monkeypatch.setattr(get_settings(), "backend_slow_after", 0.5)

    with pytest.raises(resilience.AllBackendsFailed, match="did not respond in time"):
        await resilience.dispatch(_chain(), [{"role": "user", "content": "hi"}])


# --- streaming ------------------------------------------------------------- #


async def test_a_stream_that_never_starts_fails_over_and_says_so(monkeypatch):
    async def chat_stream(backend, *args, **kwargs):
        if backend.name == "tower":
            await asyncio.sleep(5)
        yield {"message": {"content": "hosted"}, "done": True, "done_reason": "stop"}

    monkeypatch.setattr(upstream, "chat_stream", chat_stream)
    monkeypatch.setattr(get_settings(), "backend_slow_after", 0.5)

    chunks = [chunk async for chunk in resilience.dispatch_stream(_chain(), [])]
    states = [
        chunk[resilience.STREAM_STATE_KEY]
        for chunk in chunks
        if resilience.STREAM_STATE_KEY in chunk
    ]

    # The caller can tell "backend saturated" from "still working".
    assert any(state["state"] == "backend_saturated" and state["failing_over"] for state in states)
    assert any(chunk.get("message", {}).get("content") == "hosted" for chunk in chunks)


async def test_a_stream_already_generating_is_not_cut_for_being_long(monkeypatch):
    async def chat_stream(backend, *args, **kwargs):
        yield {"message": {"content": "first"}}
        # Longer than the threshold: a stream already generating is not cut.
        await asyncio.sleep(0.7)
        yield {"message": {"content": "second"}, "done": True, "done_reason": "stop"}

    monkeypatch.setattr(upstream, "chat_stream", chat_stream)
    monkeypatch.setattr(get_settings(), "backend_slow_after", 0.5)

    contents = [
        chunk.get("message", {}).get("content")
        async for chunk in resilience.dispatch_stream(_chain(), [])
        if resilience.STREAM_STATE_KEY not in chunk
    ]

    # Only time to the first chunk is bounded; generation is progress.
    assert contents == ["first", "second"]


# --- how long it sticks, and on what count (issue #111) --------------------- #


async def test_one_saturation_does_not_stick_by_default(monkeypatch):
    async def chat(backend, *args, **kwargs):
        if backend.name == "tower":
            await asyncio.sleep(5)
        return UpstreamResult(model="deepseek", content="ok")

    monkeypatch.setattr(upstream, "chat", chat)
    monkeypatch.setattr(get_settings(), "backend_slow_after", 0.5)
    model = _chain()

    await resilience.dispatch(model, [{"role": "user", "content": "hi"}])

    # One slow turn is a slow turn. Two is a condition.
    assert resilience.breakers.allow(model.backends[0])


async def test_saturation_sticks_for_its_own_cooldown(monkeypatch):
    async def chat(backend, *args, **kwargs):
        if backend.name == "tower":
            await asyncio.sleep(5)
        return UpstreamResult(model="deepseek", content="ok")

    monkeypatch.setattr(upstream, "chat", chat)
    monkeypatch.setattr(get_settings(), "backend_slow_after", 0.5)
    monkeypatch.setattr(get_settings(), "saturation_threshold", 2)
    # A game outlasts the 30s failure cooldown by hours.
    monkeypatch.setattr(get_settings(), "saturation_cooldown", 900.0)
    monkeypatch.setattr(get_settings(), "circuit_cooldown", 30.0)
    model = _chain()
    tower = model.backends[0]

    for _ in range(2):
        await resilience.dispatch(model, [{"role": "user", "content": "hi"}])

    assert not resilience.breakers.allow(tower)
    # Past the failure cooldown but well inside the saturation one.
    breaker = resilience.breakers._get(tower)
    breaker.opened_at = resilience._now() - 60
    assert not resilience.breakers.allow(tower)
    # And it does reopen eventually rather than sticking forever.
    breaker.opened_at = resilience._now() - 901
    assert resilience.breakers.allow(tower)


async def test_a_recovered_backend_forgets_its_saturations(monkeypatch):
    slow = {"value": True}

    async def chat(backend, *args, **kwargs):
        if backend.name == "tower" and slow["value"]:
            await asyncio.sleep(5)
        return UpstreamResult(model="ornith", content="ok")

    monkeypatch.setattr(upstream, "chat", chat)
    monkeypatch.setattr(get_settings(), "backend_slow_after", 0.5)
    model = _chain()

    await resilience.dispatch(model, [{"role": "user", "content": "hi"}])
    slow["value"] = False
    await resilience.dispatch(model, [{"role": "user", "content": "hi"}])
    slow["value"] = True
    await resilience.dispatch(model, [{"role": "user", "content": "hi"}])

    # The count is consecutive, so a good turn in between clears it.
    assert resilience.breakers.allow(model.backends[0])
