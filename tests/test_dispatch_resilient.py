"""Acceptance for the name-based resilient dispatch entry (leg 04 step 10, #14).

Covers the issue's stated acceptance:

* a backend that fails twice then succeeds -> result returned and
  ``llm_retries_total`` incremented per retry,
* a backend that always fails -> a clean ``AllBackendsFailed`` (never a hang),
* a dead primary in a multi-backend chain -> fall back to the next backend and
  ``llm_fallbacks_total`` incremented.

The engine (``dispatch``) is exercised through ``dispatch_resilient`` so the
name -> registry -> chain resolution is proven end to end, not just the engine.
"""

import pytest

from app import resilience, upstream
from app.models import Backend, LogicalModel, Registry
from app.obs import llm_fallbacks_total, llm_retries_total
from app.resilience import AllBackendsFailed, UnknownLogicalModel
from app.upstream import UpstreamError, UpstreamResult


def _good(content: str = "ok") -> UpstreamResult:
    return UpstreamResult(model="m", content=content, prompt_eval_count=1, eval_count=1)


def _install_registry(monkeypatch, model: LogicalModel) -> None:
    """Point ``dispatch_resilient``'s name lookup at a controlled one-model table."""
    reg = Registry({model.name: model})
    monkeypatch.setattr(resilience, "get_registry", lambda: reg)


def _retries(model: str, backend: str) -> float:
    return llm_retries_total.labels(logical_model=model, backend=backend)._value.get()


def _fallbacks(model: str, backend: str) -> float:
    return llm_fallbacks_total.labels(logical_model=model, backend=backend)._value.get()


@pytest.fixture(autouse=True)
def _fast_and_isolated(monkeypatch):
    # Zero the backoff sleep so the retry tests run instantly, and give each test
    # a fresh breaker registry so an open circuit can't leak across cases.
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(resilience.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(resilience, "breakers", resilience.CircuitBreakerRegistry())


async def test_fails_twice_then_succeeds_counts_retries(monkeypatch):
    backend = Backend(name="b-retry", url="http://x", ollama_tag="t")
    model = LogicalModel("fast-think", 4096, [backend])
    _install_registry(monkeypatch, model)

    calls = {"n": 0}

    async def flaky(be, num_ctx, messages, *, tools=None, options=None, span_attrs=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise UpstreamError("transient")
        return _good("recovered")

    monkeypatch.setattr(upstream, "chat", flaky)

    before = _retries(model.name, backend.name)
    result = await resilience.dispatch_resilient(model.name, [{"role": "user", "content": "hi"}])

    assert result.content == "recovered"
    assert calls["n"] == 3  # two failures rerolled, third attempt won
    assert _retries(model.name, backend.name) - before == 2


async def test_always_fails_surfaces_clean_error(monkeypatch):
    backend = Backend(name="b-dead", url="http://x", ollama_tag="t")
    model = LogicalModel("fast", 4096, [backend])
    _install_registry(monkeypatch, model)

    async def dead(*args, **kwargs):
        raise UpstreamError("backend down")

    monkeypatch.setattr(upstream, "chat", dead)

    with pytest.raises(AllBackendsFailed):
        await resilience.dispatch_resilient(model.name, [{"role": "user", "content": "hi"}])


async def test_falls_back_to_next_backend(monkeypatch):
    primary = Backend(name="b-primary", url="http://x", ollama_tag="t")
    secondary = Backend(name="b-secondary", url="http://y", ollama_tag="t")
    model = LogicalModel("ctx", 4096, [primary, secondary])
    _install_registry(monkeypatch, model)

    async def chat(be, num_ctx, messages, *, tools=None, options=None, span_attrs=None):
        if be.name == primary.name:
            raise UpstreamError("dead primary")
        return _good("from-secondary")

    monkeypatch.setattr(upstream, "chat", chat)

    before = _fallbacks(model.name, primary.name)
    result = await resilience.dispatch_resilient(model.name, [{"role": "user", "content": "hi"}])

    assert result.content == "from-secondary"
    assert _fallbacks(model.name, primary.name) - before == 1


async def test_unknown_logical_name_raises(monkeypatch):
    model = LogicalModel("known", 4096, [Backend(name="b", url="http://x", ollama_tag="t")])
    _install_registry(monkeypatch, model)

    with pytest.raises(UnknownLogicalModel):
        await resilience.dispatch_resilient("does-not-exist", [])
