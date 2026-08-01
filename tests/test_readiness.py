"""Non-generating, route-aware readiness checks."""

from __future__ import annotations

import json

import pytest

from app import readiness, upstream
from app.route_registry import DirectTarget, Route, RouteRegistry


class _Response:
    def __init__(self, payload, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class _GetOnlyClient:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    async def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.responses[url]


def _route(*targets: DirectTarget) -> Route:
    return Route(
        key="community/conversation-management",
        upstream_alias="community/conversation-management",
        direct=targets[0] if targets else None,
        readiness_targets=targets,
    )


def _registry(route: Route) -> RouteRegistry:
    return RouteRegistry(routes={route.key: route}, source={})


def _configure_litellm(monkeypatch, tmp_path) -> None:
    key_file = tmp_path / "litellm-key"
    key_file.write_text("service-key\n", encoding="utf-8")
    settings = readiness.get_settings()
    monkeypatch.setattr(settings, "route_upstream_mode", "litellm")
    monkeypatch.setattr(settings, "tower_base_url", "http://ollama:11434")
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
                }
            ]
        ),
    )


async def test_litellm_route_readiness_uses_only_get_catalog_calls(monkeypatch, tmp_path):
    _configure_litellm(monkeypatch, tmp_path)
    route = _route(
        DirectTarget("ornith:35b", "ollama"),
        DirectTarget("ornith:9b", "ollama"),
    )
    monkeypatch.setattr(readiness, "get_route_registry", lambda: _registry(route))
    client = _GetOnlyClient(
        {
            "http://litellm:4000/health/readiness": _Response({"status": "healthy"}),
            "http://litellm:4000/v1/models": _Response(
                {"data": [{"id": "community/conversation-management"}]}
            ),
            "http://ollama:11434/api/version": _Response({"version": "test"}),
            "http://ollama:11434/api/tags": _Response(
                {"models": [{"name": "ornith:35b"}, {"name": "ornith:9b"}]}
            ),
        }
    )
    monkeypatch.setattr(upstream, "get_client", lambda: client)

    result = await readiness.check_route_readiness(route.key)

    assert result.ready is True
    assert result.failed_checks == ()
    assert {url for url, _kwargs in client.requests} == set(client.responses)
    assert all(
        "Authorization" in kwargs.get("headers", {})
        for url, kwargs in client.requests
        if "litellm" in url
    )
    assert all(
        forbidden not in url
        for url, _kwargs in client.requests
        for forbidden in ("chat", "completions", "generate", "embeddings")
    )


async def test_missing_fallback_model_marks_route_not_ready(monkeypatch, tmp_path):
    _configure_litellm(monkeypatch, tmp_path)
    route = _route(
        DirectTarget("ornith:35b", "ollama"),
        DirectTarget("ornith:9b", "ollama"),
    )
    monkeypatch.setattr(readiness, "get_route_registry", lambda: _registry(route))
    client = _GetOnlyClient(
        {
            "http://litellm:4000/health/readiness": _Response({}),
            "http://litellm:4000/v1/models": _Response(
                {"data": [{"id": "community/conversation-management"}]}
            ),
            "http://ollama:11434/api/version": _Response({}),
            "http://ollama:11434/api/tags": _Response({"models": [{"name": "ornith:35b"}]}),
        }
    )
    monkeypatch.setattr(upstream, "get_client", lambda: client)

    result = await readiness.check_route_readiness(route.key)

    assert result.ready is False
    assert result.failed_checks == ("ollama_catalog",)


async def test_missing_litellm_key_fails_closed_without_unauthenticated_calls(monkeypatch):
    settings = readiness.get_settings()
    monkeypatch.setattr(settings, "route_upstream_mode", "litellm")
    monkeypatch.setattr(settings, "tower_base_url", "http://ollama:11434")
    monkeypatch.setattr(
        settings,
        "backends_json",
        json.dumps([{"name": "litellm", "url": "http://litellm:4000", "dialect": "openai"}]),
    )
    route = _route(DirectTarget("ornith:35b", "ollama"))
    monkeypatch.setattr(readiness, "get_route_registry", lambda: _registry(route))
    client = _GetOnlyClient(
        {
            "http://ollama:11434/api/version": _Response({}),
            "http://ollama:11434/api/tags": _Response({"models": [{"name": "ornith:35b"}]}),
        }
    )
    monkeypatch.setattr(upstream, "get_client", lambda: client)

    result = await readiness.check_route_readiness(route.key)

    assert result.ready is False
    assert result.failed_checks == ("litellm_auth",)
    assert all("litellm" not in url for url, _kwargs in client.requests)


async def test_unknown_route_makes_no_dependency_requests(monkeypatch):
    route = _route(DirectTarget("ornith:35b", "ollama"))
    monkeypatch.setattr(readiness, "get_route_registry", lambda: _registry(route))
    client = _GetOnlyClient({})
    monkeypatch.setattr(upstream, "get_client", lambda: client)

    with pytest.raises(readiness.UnknownRoute):
        await readiness.check_route_readiness("community/unknown")

    assert client.requests == []


async def test_missing_registry_fails_without_dependency_requests(monkeypatch):
    monkeypatch.setattr(readiness, "get_route_registry", lambda: None)
    client = _GetOnlyClient({})
    monkeypatch.setattr(upstream, "get_client", lambda: client)

    result = await readiness.check_route_readiness("community/conversation-management")

    assert result.ready is False
    assert result.failed_checks == ("route_registry",)
    assert client.requests == []


async def test_direct_mode_checks_ollama_without_litellm(monkeypatch):
    settings = readiness.get_settings()
    monkeypatch.setattr(settings, "route_upstream_mode", "direct")
    monkeypatch.setattr(
        settings,
        "backends_json",
        json.dumps([{"name": "tower", "url": "http://ollama:11434", "dialect": "ollama"}]),
    )
    route = _route(DirectTarget("ornith:35b", "ollama"))
    monkeypatch.setattr(readiness, "get_route_registry", lambda: _registry(route))
    client = _GetOnlyClient(
        {
            "http://ollama:11434/api/version": _Response({}),
            "http://ollama:11434/api/tags": _Response({"models": [{"name": "ornith:35b"}]}),
        }
    )
    monkeypatch.setattr(upstream, "get_client", lambda: client)

    result = await readiness.check_route_readiness(route.key)

    assert result.ready is True
    assert {url for url, _kwargs in client.requests} == set(client.responses)
