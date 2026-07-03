"""
Model catalog and ``num_ctx`` derivation (issue #32).

This retires the hand-maintained logical-model alias table. Harnesses now pass
the **real ollama tag** (e.g. ``qwen3:4b``) as the request ``model`` - no
logical indirection, no static tag/``num_ctx`` guesses that drift from what is
actually pulled on a backend.

The proxy instead reads each model's **real context window** from the backend's
``/api/tags`` (``details.context_length``, confirmed live: ``qwen3:8b`` = 40960,
``qwen3:4b`` = 262144), caches it, and derives a safe ceiling as
``num_ctx = min(context_length, configured_ceiling) - headroom``. The caller can
still never override ``num_ctx`` (upstream forces it) - that invariant is the
whole wedge and stays.

The larger litellm-as-core re-core that supersedes this routing layer entirely
is tracked in ``coilyco-bridge/agentic-os-hardware#25`` and is compatible with
this phase-1 change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import get_settings


@dataclass(frozen=True)
class Backend:
    """One upstream target and the dialect it speaks."""

    name: str
    url: str
    ollama_tag: str
    dialect: str = "ollama"
    chat_path: str | None = None
    health_path: str | None = None
    injects_num_ctx: bool = True
    timeout: float | None = None  # None -> use the global request_timeout.


@dataclass
class LogicalModel:
    """A resolved model: the requested tag, its derived safe ``num_ctx``, and the
    ordered backend chain that serves it.

    The name is kept for the ``logical_model`` observability label it feeds, but
    there is no logical indirection any more - ``name`` is the real ollama tag
    the harness asked for, flowing straight through to every backend's
    ``ollama_tag``. The chain is the resilience layer's fallback order.
    """

    name: str
    num_ctx: int
    backends: list[Backend] = field(default_factory=list)

    @property
    def primary(self) -> Backend:
        return self.backends[0]


# --------------------------------------------------------------------------- #
# num_ctx derivation
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Backend chain (tag-independent)
# --------------------------------------------------------------------------- #


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


def _backends_for_tag(tag: str) -> list[Backend]:
    """Build the fallback chain for ``tag`` by stamping it onto every spec."""
    out: list[Backend] = []
    for spec in _backend_specs():
        out.append(
            Backend(
                name=spec["name"],
                url=spec["url"].rstrip("/"),
                ollama_tag=spec.get("ollama_tag", tag),
                dialect=spec.get("dialect", "ollama"),
                chat_path=spec.get("chat_path"),
                health_path=spec.get("health_path"),
                injects_num_ctx=spec.get("injects_num_ctx", True),
                timeout=spec.get("timeout"),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Catalog (/api/tags) cache
# --------------------------------------------------------------------------- #
#
# ``/api/tags`` on the primary backend is the single source of truth for which
# tags exist and each tag's real ``context_length``. It is read once per base URL
# and cached; a value of ``None`` means "present but not reporting a context
# length" so :func:`derive_num_ctx` falls back to the ceiling. The second return
# element records whether the fetch succeeded, so an unreachable backend fails
# *open* (a real request is served with a conservative window) rather than
# turning every tag into a 404. Only *successful* reads are cached - a failed
# fetch is retried on the next request, so a tower that comes up after the proxy
# started is still discovered.

_catalog_cache: dict[str, dict[str, int | None]] = {}


async def _catalog(base_url: str) -> tuple[dict[str, int | None], bool]:
    """Return ``(tag -> context_length, fetch_ok)`` for ``base_url``, caching
    only successful reads."""
    cached = _catalog_cache.get(base_url)
    if cached is not None:
        return cached, True

    tags: dict[str, int | None] = {}
    try:
        # Lazy import breaks the models <-> upstream import cycle and reuses the
        # shared, OTel-instrumented httpx client.
        from . import upstream

        resp = await upstream.get_client().get(f"{base_url}/api/tags", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        for entry in data.get("models", []) or []:
            name = entry.get("name") or entry.get("model")
            if not name:
                continue
            details = entry.get("details") or {}
            ctx = details.get("context_length")
            tags[name] = ctx if isinstance(ctx, int) and ctx > 0 else None
    except Exception:
        return {}, False

    _catalog_cache[base_url] = tags
    return tags, True


def reset_catalog() -> None:
    """Test hook / operational reset: force the next access to re-fetch."""
    _catalog_cache.clear()


# --------------------------------------------------------------------------- #
# Resolution surface
# --------------------------------------------------------------------------- #


async def resolve(tag: str) -> LogicalModel | None:
    """Resolve a real ollama ``tag`` to a :class:`LogicalModel`, or ``None``.

    Reads the primary backend's ``/api/tags`` (cached) to derive the tag's safe
    ``num_ctx`` and confirm it exists. Returns ``None`` only for a *genuinely
    unknown* tag - one absent from a catalog we successfully read - which the
    routes turn into a 404. If the catalog could not be read (backend down), the
    tag is served fail-open with a conservative window so a transient outage is
    surfaced as a 502 by the dispatch layer, not misreported as "unknown model".
    """
    if not tag:
        return None
    tags, ok = await _catalog(_primary_base_url())
    if ok and tag not in tags:
        return None
    num_ctx = derive_num_ctx(tags.get(tag))
    return LogicalModel(name=tag, num_ctx=num_ctx, backends=_backends_for_tag(tag))


async def list_tags() -> list[str]:
    """The tags actually present on the primary backend (from ``/api/tags``)."""
    tags, _ok = await _catalog(_primary_base_url())
    return sorted(tags.keys())
