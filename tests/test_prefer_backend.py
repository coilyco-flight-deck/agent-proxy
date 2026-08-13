"""The caller naming which tier it would rather use (issue #111)."""

import pytest

from app import main, models, upstream
from app.models import Backend, LogicalModel
from app.upstream import UpstreamResult

CATALOG: dict[str, int | None] = {"qwen3:4b": 262144}


def _chain() -> LogicalModel:
    return LogicalModel(
        name="sirens-echo/default",
        num_ctx=4096,
        backends=[
            Backend(name="tower", url="http://tower", ollama_tag="ornith:35b"),
            Backend(name="hosted", url="http://hosted", ollama_tag="deepseek", dialect="openai"),
        ],
    )


# --- the reordering rule ---------------------------------------------------- #


def test_a_named_backend_is_tried_first():
    model = _chain().preferring("hosted")
    assert [b.name for b in model.backends] == ["hosted", "tower"]


def test_the_other_tiers_stay_behind_it():
    """Reorder, never filter: a hint must not be able to empty the chain."""
    model = _chain().preferring("hosted")
    assert len(model.backends) == 2
    assert model.backends[-1].name == "tower"


def test_an_unknown_name_changes_nothing():
    model = _chain().preferring("no-such-backend")
    assert [b.name for b in model.backends] == ["tower", "hosted"]


def test_an_empty_preference_changes_nothing():
    model = _chain()
    assert model.preferring("") is model


def test_preferring_does_not_mutate_the_original():
    model = _chain()
    model.preferring("hosted")
    assert model.primary.name == "tower"


# --- the header ------------------------------------------------------------- #


def test_the_header_is_read_and_bounded():
    assert main._preferred_backend({"x-prefer-backend": "  hosted  "}) == "hosted"
    assert main._preferred_backend({}) == ""
    # An unbounded header value has no business reaching a routing decision.
    assert len(main._preferred_backend({"x-prefer-backend": "x" * 500})) == 64


@pytest.fixture
def client(monkeypatch, app_client):
    async def fake_catalog(_base_url):
        return dict(CATALOG), True

    monkeypatch.setattr(models, "_catalog", fake_catalog)
    models.reset_catalog()
    yield app_client


def test_the_caller_can_stick_to_the_fallback(client, monkeypatch):
    """Kai's ask: Echo tells the proxy to prefer the hosted tier."""
    served: list[str] = []

    async def chat(backend, *args, **kwargs):
        served.append(backend.name)
        return UpstreamResult(model=backend.ollama_tag, content="ok")

    model = _chain()

    async def resolve_model(name):
        return model if name == "qwen3:4b" else None

    monkeypatch.setattr(upstream, "chat", chat)
    monkeypatch.setattr(main, "resolve", resolve_model)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "qwen3:4b", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Prefer-Backend": "hosted"},
    )

    assert response.status_code == 200
    assert served == ["hosted"]


def test_without_the_header_the_configured_order_stands(client, monkeypatch):
    served: list[str] = []

    async def chat(backend, *args, **kwargs):
        served.append(backend.name)
        return UpstreamResult(model=backend.ollama_tag, content="ok")

    model = _chain()

    async def resolve_model(name):
        return model if name == "qwen3:4b" else None

    monkeypatch.setattr(upstream, "chat", chat)
    monkeypatch.setattr(main, "resolve", resolve_model)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "qwen3:4b", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert served == ["tower"]


def test_a_preferred_backend_that_fails_still_falls_back(client, monkeypatch):
    """The hint reorders the chain; it does not disarm it."""
    served: list[str] = []

    async def chat(backend, *args, **kwargs):
        served.append(backend.name)
        if backend.name == "hosted":
            raise upstream.UpstreamError("hosted down")
        return UpstreamResult(model=backend.ollama_tag, content="from the tower")

    model = _chain()

    async def resolve_model(name):
        return model if name == "qwen3:4b" else None

    monkeypatch.setattr(upstream, "chat", chat)
    monkeypatch.setattr(main, "resolve", resolve_model)
    monkeypatch.setattr(main.get_settings(), "retry_base_delay", 0.0)
    monkeypatch.setattr(main.get_settings(), "max_retries", 0)

    response = client.post(
        "/v1/chat/completions",
        json={"model": "qwen3:4b", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-Prefer-Backend": "hosted"},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "from the tower"
    assert served == ["hosted", "tower"]
