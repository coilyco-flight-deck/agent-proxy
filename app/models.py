"""
Logical-model registry (leg 02 "logical routing", leg 04 step 2).

A logical name (``fast-think``, ``fast``, ``ctx-think``, ``ctx``, ``tune``) maps
to a per-model safe ``num_ctx`` and an ordered fallback chain of backends. The
first backend is the primary (the 3026 tower); later entries are the siblings /
CPU / API fallbacks the resilience layer walks on failure. A deploy can override
the whole table via ``PROXY_MODELS_JSON`` / ``PROXY_MODELS_FILE`` (a ConfigMap
later) without touching code.

Per-model ``num_ctx`` values: use the leg-03 benchmark results when they exist.
Until then ``fast-think`` (Qwen3-A3B) defaults to 49152 (proven safe, leg 01)
and everything else to a conservative 32768.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from .config import get_settings

# Defaults per the leg-04 "Per-model num_ctx source" section.
FAST_THINK_NUM_CTX = 49152  # Qwen3-A3B, proven safe (leg 01).
CONSERVATIVE_NUM_CTX = 32768  # Everything else until leg-03 locks a value.


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
    """A logical name, its safe context ceiling, and its fallback chain."""

    name: str
    num_ctx: int
    backends: list[Backend] = field(default_factory=list)

    @property
    def primary(self) -> Backend:
        return self.backends[0]


def _default_registry() -> dict[str, LogicalModel]:
    """The built-in table. The primary backend for every model is the tower,
    whose base URL resolves from env/SSM at boot (never hardcoded)."""
    tower = get_settings().resolved_tower_base_url()

    def tower_backend(tag: str) -> Backend:
        return Backend(name="tower-3026", url=tower, ollama_tag=tag)

    def llama_backend(tag: str) -> Backend:
        parts = urlsplit(tower)
        llama_url = urlunsplit((parts.scheme, f"{parts.hostname}:8080", "", "", ""))
        return Backend(
            name="tower-llama-8080",
            url=llama_url,
            ollama_tag=tag,
            dialect="openai",
            chat_path="/v1/chat/completions",
            health_path="/health",
            injects_num_ctx=False,
        )

    # Tags chosen to exist on the tower today; a deploy override can repoint them.
    return {
        "fast-think": LogicalModel("fast-think", FAST_THINK_NUM_CTX, [tower_backend("qwen3:30b-a3b")]),
        "fast": LogicalModel("fast", CONSERVATIVE_NUM_CTX, [tower_backend("qwen3-coder:30b")]),
        "ctx-think": LogicalModel("ctx-think", CONSERVATIVE_NUM_CTX, [tower_backend("qwen3:32b")]),
        "ctx": LogicalModel("ctx", CONSERVATIVE_NUM_CTX, [tower_backend("qwen3-coder:30b")]),
        "tune": LogicalModel("tune", CONSERVATIVE_NUM_CTX, [tower_backend("qwen3:30b-a3b")]),
        "gpt-oss-120b": LogicalModel("gpt-oss-120b", CONSERVATIVE_NUM_CTX, [llama_backend("gpt-oss:120b")]),
        "gpt-oss:120b": LogicalModel("gpt-oss:120b", CONSERVATIVE_NUM_CTX, [llama_backend("gpt-oss:120b")]),
    }


def _apply_overrides(base: dict[str, LogicalModel], overrides: dict[str, Any]) -> dict[str, LogicalModel]:
    """Merge a deploy-supplied override dict over the built-in table.

    Shape: ``{name: {"num_ctx": int, "backends": [{"name","url","ollama_tag","timeout"?}, ...]}}``.
    A name present only in the override is added; a name in both is replaced.
    """
    merged = dict(base)
    for name, spec in overrides.items():
        num_ctx = int(spec.get("num_ctx", CONSERVATIVE_NUM_CTX))
        backends_spec = spec.get("backends") or []
        backends = [
            Backend(
                name=b["name"],
                url=b["url"].rstrip("/"),
                ollama_tag=b["ollama_tag"],
                dialect=b.get("dialect", "ollama"),
                chat_path=b.get("chat_path"),
                health_path=b.get("health_path"),
                injects_num_ctx=b.get("injects_num_ctx", True),
                timeout=b.get("timeout"),
            )
            for b in backends_spec
        ]
        if not backends and name in base:
            # Override num_ctx only, keep the existing chain.
            backends = base[name].backends
        merged[name] = LogicalModel(name=name, num_ctx=num_ctx, backends=backends)
    return merged


class Registry:
    """Holds the logical-model table and resolves names to models."""

    def __init__(self, models: dict[str, LogicalModel]):
        self._models = models

    def get(self, name: str) -> LogicalModel | None:
        return self._models.get(name)

    def names(self) -> list[str]:
        return list(self._models.keys())

    def all(self) -> list[LogicalModel]:
        return list(self._models.values())

    def all_backends(self) -> list[Backend]:
        seen: dict[str, Backend] = {}
        for m in self._models.values():
            for b in m.backends:
                seen.setdefault(b.name, b)
        return list(seen.values())


_registry: Registry | None = None


def get_registry() -> Registry:
    """Process-wide registry singleton, built from defaults + optional override."""
    global _registry
    if _registry is None:
        table = _default_registry()
        overrides = get_settings().model_overrides()
        if overrides:
            table = _apply_overrides(table, overrides)
        _registry = Registry(table)
    return _registry


def reset_registry() -> None:
    """Test hook: force a rebuild on next access."""
    global _registry
    _registry = None
