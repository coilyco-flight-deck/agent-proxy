"""Logical route resolution and backend-derived ``num_ctx`` policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import get_settings
from .route_registry import Route, get_route_registry


@dataclass(frozen=True)
class Backend:
    """One upstream target and the dialect it speaks."""

    name: str
    url: str
    ollama_tag: str
    dialect: str = "ollama"
    chat_path: str | None = None
    health_path: str | None = None
    api_key_file: str | None = None
    injects_num_ctx: bool = True
    timeout: float | None = None  # None -> use the global request_timeout.
    # The backend's OLLAMA_NUM_PARALLEL (issue #33). 1 leaves injection
    # unchanged. Scaling rationale: docs/context-safety-settings.md.
    num_parallel: int = 1
    # Capacity state at dispatch (#109). Docs: docs/backend-regime.md.
    regime: str = "unknown"


@dataclass
class LogicalModel:
    """A logical route, safe context window, and ordered transport chain."""

    name: str
    num_ctx: int
    backends: list[Backend] = field(default_factory=list)
    upstream_mode: str = "direct"

    @property
    def primary(self) -> Backend:
        return self.backends[0]


# num_ctx derivation


def derive_num_ctx(context_length: int | None) -> int:
    """Derive the safe ``num_ctx`` from a model's real context window.

    ``min(context_length, configured_ceiling) - headroom``. The ceiling
    (``PROXY_NUM_CTX_CEILING``) is the VRAM-safe upper bound the tower can carry
    regardless of how large a model advertises; the headroom
    (``PROXY_NUM_CTX_HEADROOM``) leaves room for the completion. A ``None``
    context length (backend unreachable, or a tag present but not reporting one)
    falls back to the ceiling so a real request never gets a degenerate window.
    """
    settings = get_settings()
    ceiling = settings.num_ctx_ceiling
    headroom = settings.num_ctx_headroom
    base = ceiling if context_length is None else min(context_length, ceiling)
    return max(base - headroom, 1)


# Backend chain (tag-independent)


def _backend_specs() -> list[dict[str, Any]]:
    """The ordered backend targets, without a tag (the tag comes per-request).

    Defaults to the single tower ollama backend, whose base URL resolves from
    env/SSM at boot. A deploy supplies a fallback chain (siblings / CPU / an
    OpenAI-dialect target) via ``PROXY_BACKENDS_JSON`` / ``PROXY_BACKENDS_FILE``.
    """
    override = get_settings().backend_overrides()
    if override:
        return override
    tower = get_settings().resolved_tower_base_url()
    return [{"name": "tower-3026", "url": tower, "dialect": "ollama"}]


def _primary_base_url() -> str:
    """Base URL of the primary backend - the one whose ``/api/tags`` is the
    catalog source of truth for listing and context lengths."""
    return _backend_specs()[0]["url"].rstrip("/")


def _backends_for_model(upstream_model: str) -> list[Backend]:
    """Stamp the resolved upstream model onto every transport target."""
    default_parallel = get_settings().ollama_num_parallel
    default_regime = get_settings().backend_regime
    out: list[Backend] = []
    for spec in _backend_specs():
        out.append(
            Backend(
                name=spec["name"],
                url=spec["url"].rstrip("/"),
                ollama_tag=upstream_model,
                dialect=spec.get("dialect", "ollama"),
                chat_path=spec.get("chat_path"),
                health_path=spec.get("health_path"),
                api_key_file=spec.get("api_key_file"),
                injects_num_ctx=spec.get("injects_num_ctx", True),
                timeout=spec.get("timeout"),
                num_parallel=int(spec.get("num_parallel", default_parallel) or 1),
                regime=str(spec.get("regime") or default_regime),
            )
        )
    return out


# Catalog (/api/tags) cache. Read once per base URL, successful reads only.
# Fail-open and re-fetch semantics: docs/backend-catalog.md.

_catalog_cache: dict[str, dict[str, int | None]] = {}


async def _catalog(base_url: str) -> tuple[dict[str, int | None], bool]:
    """Return ``(tag -> context_length, fetch_ok)`` for ``base_url``, caching
    only successful reads.

    Ollama remains the context-metadata authority. When the primary inference
    backend is OpenAI-shaped (the standalone LiteLLM inner gateway), its
    authenticated ``/v1/models`` catalog supplies the allowed model set and the
    tower's ``/api/tags`` supplies context lengths. This keeps Agent Proxy's
    safe-context policy above LiteLLM without exposing models the service key
    cannot use.
    """
    cached = _catalog_cache.get(base_url)
    if cached is not None:
        return cached, True

    primary = _backends_for_model("")[0]
    try:
        # Lazy import breaks the models <-> upstream import cycle and reuses the
        # shared, OTel-instrumented httpx client.
        from . import upstream

        if primary.dialect == "ollama":
            resp = await upstream.get_client().get(f"{base_url}/api/tags", timeout=10.0)
            resp.raise_for_status()
            tags = _ollama_tags(resp.json())
            _catalog_cache[base_url] = tags
            return tags, True

        resp = await upstream.get_client().get(
            f"{base_url}/v1/models",
            timeout=10.0,
            **upstream.request_auth_kwargs(primary),
        )
        resp.raise_for_status()
        allowed = {
            entry.get("id")
            for entry in (resp.json().get("data", []) or [])
            if isinstance(entry, dict) and entry.get("id")
        }

        tower_resp = await upstream.get_client().get(
            f"{get_settings().resolved_tower_base_url()}/api/tags",
            timeout=10.0,
        )
        tower_resp.raise_for_status()
        tower_tags = _ollama_tags(tower_resp.json())
        tags = {name: tower_tags[name] for name in allowed if name in tower_tags}
    except Exception:
        return {}, False

    _catalog_cache[base_url] = tags
    return tags, True


def _ollama_tags(data: dict[str, Any]) -> dict[str, int | None]:
    """Extract model context metadata from an Ollama ``/api/tags`` response."""
    tags: dict[str, int | None] = {}
    for entry in data.get("models", []) or []:
        name = entry.get("name") or entry.get("model")
        if not name:
            continue
        details = entry.get("details") or {}
        ctx = details.get("context_length")
        tags[name] = ctx if isinstance(ctx, int) and ctx > 0 else None
    return tags


def reset_catalog() -> None:
    """Test hook / operational reset: force the next access to re-fetch."""
    _catalog_cache.clear()


# Resolution surface


async def resolve(tag: str) -> LogicalModel | None:
    """Resolve a logical key, or a physical tag in compatibility mode."""
    if not tag:
        return None
    registry = get_route_registry()
    if registry is not None:
        route = registry.routes.get(tag)
        if route is None:
            return None
        if not route.enabled:
            raise RouteUnavailable(f"route '{tag}' is disabled")
        return await _resolve_route(route)

    tags, ok = await _catalog(_primary_base_url())
    if ok and tag not in tags:
        return None
    num_ctx = derive_num_ctx(tags.get(tag))
    return LogicalModel(
        name=tag,
        num_ctx=num_ctx,
        backends=_backends_for_model(tag),
        upstream_mode="direct",
    )


async def list_tags() -> list[str]:
    """List governed logical keys, or live physical tags in compatibility mode."""
    registry = get_route_registry()
    if registry is not None:
        return registry.listed_keys()
    tags, _ok = await _catalog(_primary_base_url())
    return sorted(tags.keys())


class RouteUnavailable(ValueError):
    """A known route cannot run through the selected upstream mode."""


async def _resolve_route(route: Route) -> LogicalModel:
    settings = get_settings()
    specs = _backend_specs()
    dialects = {spec.get("dialect", "ollama") for spec in specs}
    direct_model = route.direct.model if route.direct is not None else None
    context_length: int | None = None

    if settings.route_upstream_mode == "litellm":
        if dialects != {"openai"}:
            raise RouteUnavailable(f"route '{route.key}' requires the LiteLLM upstream")
        upstream_model = route.upstream_alias
        if direct_model:
            tower_tags, ok = await _ollama_catalog(settings.resolved_tower_base_url())
            if ok:
                context_length = tower_tags.get(direct_model)
    else:
        if dialects != {"ollama"}:
            raise RouteUnavailable(f"route '{route.key}' has no supported direct upstream")
        if route.direct is None or route.direct.runtime != "ollama":
            raise RouteUnavailable(f"route '{route.key}' has no supported direct target")
        upstream_model = route.direct.model
        tower_tags, ok = await _ollama_catalog(specs[0]["url"])
        if ok:
            context_length = tower_tags.get(upstream_model)

    return LogicalModel(
        name=route.key,
        num_ctx=derive_num_ctx(context_length),
        backends=_backends_for_model(upstream_model),
        upstream_mode=settings.route_upstream_mode,
    )


async def _ollama_catalog(base_url: str) -> tuple[dict[str, int | None], bool]:
    base_url = base_url.rstrip("/")
    cached = _catalog_cache.get(base_url)
    if cached is not None:
        return cached, True
    try:
        from . import upstream

        response = await upstream.get_client().get(f"{base_url}/api/tags", timeout=10.0)
        response.raise_for_status()
        tags = _ollama_tags(response.json())
    except Exception:
        return {}, False
    _catalog_cache[base_url] = tags
    return tags, True
