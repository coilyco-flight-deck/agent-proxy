"""Deploy-owned logical route registry validation."""

from __future__ import annotations

import json

import pytest

from app import route_registry


def _payload() -> dict[str, object]:
    return {
        "format": "agent-proxy-route-registry/v1",
        "source": {
            "format": "aosh.agent-proxy-routes",
            "version": 1,
            "revision": "source-revision",
            "sha256": "source-digest",
        },
        "routes": [
            {
                "key": "community/knowledge-retrieval",
                "upstream_alias": "community/knowledge-retrieval",
                "direct": {"model": "ornith:35b", "runtime": "ollama"},
                "readiness_targets": [
                    {"model": "ornith:35b", "runtime": "ollama"},
                    {"model": "ornith:9b", "runtime": "ollama"},
                ],
            },
            {
                "key": "engineer/autonomous-coding",
                "upstream_alias": "engineer/autonomous-coding",
                "direct": {"model": "gpt-oss:120b", "runtime": "llama.cpp"},
                "enabled": False,
            },
        ],
    }


def _write(tmp_path, payload: dict[str, object]):
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _reset_registry():
    route_registry.reset_route_registry()
    yield
    route_registry.reset_route_registry()


def test_valid_registry_loads_logical_routes(tmp_path):
    registry = route_registry.load_route_registry(_write(tmp_path, _payload()))

    assert registry.listed_keys() == ["community/knowledge-retrieval"]
    route = registry.routes["community/knowledge-retrieval"]
    assert route.upstream_alias == route.key
    assert route.direct == route_registry.DirectTarget("ornith:35b", "ollama")
    assert route.readiness_targets == (
        route_registry.DirectTarget("ornith:35b", "ollama"),
        route_registry.DirectTarget("ornith:9b", "ollama"),
    )
    assert registry.source["format"] == "aosh.agent-proxy-routes"


def test_unknown_format_is_rejected(tmp_path):
    payload = _payload()
    payload["format"] = "agent-proxy-route-registry/v2"

    with pytest.raises(route_registry.RouteRegistryError, match="unsupported"):
        route_registry.load_route_registry(_write(tmp_path, payload))


def test_duplicate_route_key_is_rejected(tmp_path):
    payload = _payload()
    payload["routes"].append(dict(payload["routes"][0]))

    with pytest.raises(route_registry.RouteRegistryError, match="duplicate logical"):
        route_registry.load_route_registry(_write(tmp_path, payload))


def test_missing_upstream_alias_is_rejected(tmp_path):
    payload = _payload()
    del payload["routes"][0]["upstream_alias"]

    with pytest.raises(route_registry.RouteRegistryError, match="upstream_alias"):
        route_registry.load_route_registry(_write(tmp_path, payload))


def test_oversized_registry_is_rejected_before_parsing(tmp_path):
    path = tmp_path / "routes.json"
    path.write_text("x" * (route_registry.MAX_REGISTRY_BYTES + 1), encoding="utf-8")

    with pytest.raises(route_registry.RouteRegistryError, match="size limit"):
        route_registry.load_route_registry(path)


def test_malformed_direct_target_is_rejected(tmp_path):
    payload = _payload()
    del payload["routes"][0]["direct"]["runtime"]

    with pytest.raises(route_registry.RouteRegistryError, match="direct runtime"):
        route_registry.load_route_registry(_write(tmp_path, payload))


def test_duplicate_readiness_target_is_rejected(tmp_path):
    payload = _payload()
    payload["routes"][0]["readiness_targets"].append(
        dict(payload["routes"][0]["readiness_targets"][0])
    )

    with pytest.raises(route_registry.RouteRegistryError, match="duplicate readiness target"):
        route_registry.load_route_registry(_write(tmp_path, payload))


def test_configured_invalid_registry_fails_initialization(monkeypatch, tmp_path):
    path = tmp_path / "routes.json"
    path.write_text("not-json", encoding="utf-8")
    settings = route_registry.get_settings()
    monkeypatch.setattr(settings, "route_registry_file", str(path))
    monkeypatch.setattr(settings, "route_registry_compatibility_mode", False)

    with pytest.raises(route_registry.RouteRegistryError, match="invalid JSON"):
        route_registry.initialize_route_registry()


def test_missing_registry_requires_explicit_compatibility(monkeypatch):
    settings = route_registry.get_settings()
    monkeypatch.setattr(settings, "route_registry_file", "")
    monkeypatch.setattr(settings, "route_registry_compatibility_mode", False)

    with pytest.raises(route_registry.RouteRegistryError, match="required"):
        route_registry.initialize_route_registry()
