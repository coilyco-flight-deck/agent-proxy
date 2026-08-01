"""Metrics-only route readiness checks that never invoke model inference."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from . import models, upstream
from .config import get_settings
from .obs import (
    agent_proxy_readiness_check_duration_seconds,
    agent_proxy_readiness_checks_total,
    agent_proxy_readiness_last_success_timestamp_seconds,
    agent_proxy_route_ready,
    suppress_health_observability,
)
from .route_registry import DirectTarget, Route, get_route_registry


class UnknownRoute(ValueError):
    """The requested logical route is absent or disabled."""


@dataclass(frozen=True)
class RouteReadiness:
    route: str
    ready: bool
    failed_checks: tuple[str, ...]


def _observe(check: str, ok: bool, started: float) -> bool:
    outcome = "ready" if ok else "not_ready"
    agent_proxy_readiness_checks_total.labels(check=check, outcome=outcome).inc()
    agent_proxy_readiness_check_duration_seconds.labels(check=check).observe(
        max(0.0, time.perf_counter() - started)
    )
    if ok:
        agent_proxy_readiness_last_success_timestamp_seconds.labels(check=check).set(time.time())
    return ok


async def _run(check: str, operation: Callable[[], Awaitable[bool]]) -> tuple[str, bool]:
    started = time.perf_counter()
    try:
        ok = await operation()
    except Exception:
        ok = False
    return check, _observe(check, ok, started)


async def _status_ok(url: str, *, auth: upstream.RequestAuthKwargs | None = None) -> bool:
    response = await upstream.get_client().get(
        url,
        timeout=get_settings().readiness_timeout,
        **(auth or {}),
    )
    response.raise_for_status()
    return True


async def _litellm_catalog_contains(url: str, alias: str, auth: upstream.RequestAuthKwargs) -> bool:
    response = await upstream.get_client().get(
        f"{url}/v1/models",
        timeout=get_settings().readiness_timeout,
        **auth,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return False
    entries = payload.get("data")
    if not isinstance(entries, list):
        return False
    return any(isinstance(entry, dict) and entry.get("id") == alias for entry in entries)


async def _ollama_catalog_contains(url: str, targets: tuple[DirectTarget, ...]) -> bool:
    response = await upstream.get_client().get(
        f"{url}/api/tags",
        timeout=get_settings().readiness_timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return False
    installed = set(models._ollama_tags(payload))
    return all(target.model in installed for target in targets)


def _targets(route: Route) -> tuple[DirectTarget, ...]:
    if route.readiness_targets:
        return route.readiness_targets
    return (route.direct,) if route.direct is not None else ()


async def check_route_readiness(logical_route: str) -> RouteReadiness:
    """Check one governed route without chat, completion, generation, or embedding calls."""

    registry_started = time.perf_counter()
    registry = get_route_registry()
    if registry is None:
        _observe("route_registry", False, registry_started)
        return RouteReadiness(logical_route, False, ("route_registry",))
    _observe("route_registry", True, registry_started)

    route = registry.routes.get(logical_route)
    if route is None or not route.enabled:
        _observe("route_mapping", False, time.perf_counter())
        raise UnknownRoute(logical_route)

    settings = get_settings()
    targets = _targets(route)
    try:
        primary = models._backends_for_model(route.upstream_alias)[0]
    except Exception:
        primary = None
    mode_matches_backend = primary is not None and (
        (settings.route_upstream_mode == "litellm" and primary.dialect == "openai")
        or (settings.route_upstream_mode == "direct" and primary.dialect == "ollama")
    )
    mapping_ok = (
        bool(targets)
        and all(target.runtime == "ollama" for target in targets)
        and mode_matches_backend
    )
    _observe("route_mapping", mapping_ok, time.perf_counter())
    if not mapping_ok:
        agent_proxy_route_ready.labels(logical_route=logical_route).set(0)
        return RouteReadiness(logical_route, False, ("route_mapping",))

    assert primary is not None
    checks: list[Awaitable[tuple[str, bool]]] = []

    if settings.route_upstream_mode == "litellm":
        auth: upstream.RequestAuthKwargs = {}
        auth_ok = bool(primary.api_key_file)
        if auth_ok:
            try:
                auth = upstream.request_auth_kwargs(primary)
            except Exception:
                auth_ok = False
        checks.append(_run("litellm_auth", lambda: _constant(auth_ok)))
        if auth_ok:
            checks.extend(
                (
                    _run(
                        "litellm_readiness",
                        lambda: _status_ok(f"{primary.url}/health/readiness", auth=auth),
                    ),
                    _run(
                        "litellm_catalog",
                        lambda: _litellm_catalog_contains(primary.url, route.upstream_alias, auth),
                    ),
                )
            )
        ollama_url = settings.resolved_tower_base_url().rstrip("/")
    else:
        ollama_url = primary.url

    checks.extend(
        (
            _run("ollama_version", lambda: _status_ok(f"{ollama_url}/api/version")),
            _run(
                "ollama_catalog",
                lambda: _ollama_catalog_contains(ollama_url, targets),
            ),
        )
    )

    with suppress_health_observability():
        results = await asyncio.gather(*checks)
    failed = tuple(name for name, ok in results if not ok)
    ready = not failed
    agent_proxy_route_ready.labels(logical_route=logical_route).set(1 if ready else 0)
    return RouteReadiness(logical_route, ready, failed)


async def _constant(value: bool) -> bool:
    return value
