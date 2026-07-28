"""num_ctx derivation from live /api/tags + pass-through tag resolution (issue #32)."""

import json

import pytest

from app import models, upstream
from app.route_registry import DirectTarget, Route, RouteRegistry

# The live-tower context lengths the issue cites: /api/tags reports
# details.context_length per model.
TAGS: dict[str, int | None] = {"qwen3:8b": 40960, "qwen3:4b": 262144}

# Defaults: ceiling 49152, headroom 1024 -> a tag below the ceiling keeps its
# real window minus headroom; a tag above it is capped to the ceiling.
CEILING = 49152
HEADROOM = 1024


@pytest.fixture(autouse=True)
def _clean_catalog():
    models.reset_catalog()
    yield
    models.reset_catalog()


def _seed_catalog(monkeypatch, tags: dict[str, int | None], ok: bool = True) -> None:
    async def fake_catalog(_base_url):
        return dict(tags), ok

    monkeypatch.setattr(models, "_catalog", fake_catalog)


class _CapturingClient:
    """Records the last posted body so tests can assert on injected options."""

    def __init__(self, payload):
        self.payload = payload
        self.last_body = None

    async def post(self, url, json=None, timeout=None):
        self.last_body = json

        class _Resp:
            def raise_for_status(_self):
                return None

            def json(_self):
                return payload

        payload = self.payload
        return _Resp()


# --- derivation ------------------------------------------------------------- #


def test_derive_caps_a_huge_window_at_the_ceiling():
    # qwen3:4b advertises 262144; the tower cannot carry that, so cap at the
    # configured ceiling before reserving headroom.
    assert models.derive_num_ctx(262144) == CEILING - HEADROOM  # 48128


def test_derive_keeps_a_real_window_below_the_ceiling():
    # qwen3:8b advertises 40960 (< ceiling) -> keep it, minus headroom.
    assert models.derive_num_ctx(40960) == 40960 - HEADROOM  # 39936


def test_derive_none_falls_back_to_the_ceiling():
    # Backend unreachable / tag not reporting a context length: conservative.
    assert models.derive_num_ctx(None) == CEILING - HEADROOM


# --- resolution ------------------------------------------------------------- #


async def test_resolve_reads_context_length_from_tags(monkeypatch):
    _seed_catalog(monkeypatch, TAGS)
    model = await models.resolve("qwen3:4b")
    assert model is not None
    assert model.name == "qwen3:4b"
    assert model.num_ctx == 48128
    # The tag flows straight through to the backend, no logical indirection.
    assert model.primary.ollama_tag == "qwen3:4b"


async def test_resolve_derives_per_model_from_its_own_ctx(monkeypatch):
    _seed_catalog(monkeypatch, TAGS)
    assert (await models.resolve("qwen3:8b")).num_ctx == 39936


async def test_resolve_unknown_tag_is_none_when_catalog_read(monkeypatch):
    # A tag absent from a catalog we successfully read is a genuine 404.
    _seed_catalog(monkeypatch, TAGS, ok=True)
    assert await models.resolve("no-such:tag") is None


async def test_resolve_fails_open_when_backend_unreachable(monkeypatch):
    # Catalog fetch failed: don't turn a real request into a 404, serve it with a
    # conservative window and let dispatch surface backend health as a 502.
    _seed_catalog(monkeypatch, {}, ok=False)
    model = await models.resolve("qwen3:4b")
    assert model is not None
    assert model.num_ctx == CEILING - HEADROOM


async def test_list_tags_reflects_live_backend(monkeypatch):
    _seed_catalog(monkeypatch, TAGS)
    assert set(await models.list_tags()) == {"qwen3:8b", "qwen3:4b"}


async def test_litellm_catalog_intersects_key_models_with_tower_context(monkeypatch, tmp_path):
    key_file = tmp_path / "litellm-key"
    key_file.write_text("service-key\n", encoding="utf-8")
    settings = models.get_settings()
    monkeypatch.setattr(
        settings,
        "backends_json",
        json.dumps(
            [
                {
                    "name": "litellm",
                    "url": "http://litellm:4000",
                    "dialect": "openai",
                    "api_key_file": str(key_file),
                    "injects_num_ctx": True,
                }
            ]
        ),
    )
    monkeypatch.setattr(settings, "tower_base_url", "http://tower:11434")

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        def __init__(self):
            self.requests = []

        async def get(self, url, timeout=None, headers=None):
            self.requests.append((url, headers))
            if url.endswith("/v1/models"):
                return _Response(
                    {
                        "data": [
                            {"id": "qwen3-coder:30b"},
                            {"id": "qwen3-coder-fallback"},
                        ]
                    }
                )
            return _Response(
                {
                    "models": [
                        {
                            "name": "qwen3-coder:30b",
                            "details": {"context_length": 262144},
                        },
                        {"name": "qwen3:8b", "details": {"context_length": 40960}},
                    ]
                }
            )

    client = _Client()
    monkeypatch.setattr(upstream, "get_client", lambda: client)

    assert await models.list_tags() == ["qwen3-coder:30b"]
    resolved = await models.resolve("qwen3-coder:30b")
    assert resolved is not None
    assert resolved.num_ctx == 48128
    assert resolved.primary.name == "litellm"
    assert client.requests == [
        (
            "http://litellm:4000/v1/models",
            {"Authorization": "Bearer service-key"},
        ),
        ("http://tower:11434/api/tags", None),
    ]


def _logical_registry(runtime: str = "ollama") -> RouteRegistry:
    route = Route(
        key="community/knowledge-retrieval",
        upstream_alias="community/knowledge-retrieval",
        direct=DirectTarget("ornith:35b", runtime),
    )
    return RouteRegistry(routes={route.key: route}, source={})


async def test_logical_route_reaches_litellm_alias(monkeypatch):
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
    monkeypatch.setattr(models, "get_route_registry", _logical_registry)

    async def fake_tower_catalog(_base_url):
        return {"ornith:35b": 65536}, True

    monkeypatch.setattr(models, "_ollama_catalog", fake_tower_catalog)

    model = await models.resolve("community/knowledge-retrieval")

    assert model.name == "community/knowledge-retrieval"
    assert model.primary.ollama_tag == "community/knowledge-retrieval"
    assert model.upstream_mode == "litellm"
    assert model.num_ctx == 48128


async def test_logical_route_resolves_supported_direct_target(monkeypatch):
    settings = models.get_settings()
    monkeypatch.setattr(settings, "route_upstream_mode", "direct")
    monkeypatch.setattr(settings, "backends_json", "")
    monkeypatch.setattr(models, "get_route_registry", _logical_registry)

    async def fake_tower_catalog(_base_url):
        return {"ornith:35b": 40960}, True

    monkeypatch.setattr(models, "_ollama_catalog", fake_tower_catalog)

    model = await models.resolve("community/knowledge-retrieval")

    assert model.name == "community/knowledge-retrieval"
    assert model.primary.ollama_tag == "ornith:35b"
    assert model.upstream_mode == "direct"


async def test_logical_route_rejects_unsupported_direct_runtime(monkeypatch):
    settings = models.get_settings()
    monkeypatch.setattr(settings, "route_upstream_mode", "direct")
    monkeypatch.setattr(settings, "backends_json", "")
    monkeypatch.setattr(
        models,
        "get_route_registry",
        lambda: _logical_registry("llama.cpp"),
    )

    with pytest.raises(models.RouteUnavailable, match="no supported direct target"):
        await models.resolve("community/knowledge-retrieval")


# --- the invariant: the caller can never override num_ctx ------------------- #


async def test_caller_cannot_override_derived_num_ctx(monkeypatch):
    _seed_catalog(monkeypatch, TAGS)
    model = await models.resolve("qwen3:8b")  # derived num_ctx = 39936
    fake = _CapturingClient({"message": {"content": "ok"}})
    monkeypatch.setattr(upstream, "get_client", lambda: fake)

    # Even a caller passing their own num_ctx cannot defeat the derived ceiling.
    await upstream.chat(model.primary, model.num_ctx, messages=[], options={"num_ctx": 999999})
    assert fake.last_body["options"]["num_ctx"] == 39936
