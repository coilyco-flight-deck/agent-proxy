"""
Configuration for the resilience proxy.

Settings come from environment variables (prefix ``PROXY_``) with a best-effort
AWS SSM fallback for the tower FQDN and secrets. Nothing is hardcoded that a
deploy cannot override, and no secret is ever committed - the Sentry DSN and any
API-fallback keys resolve from SSM (or env) at boot. See leg 04 "Secrets and
config" and leg 02 for the design this implements.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# SSM parameter names (never the resolved values) live in the source. The tower
# FQDN is deliberately not written into any tracked file - it resolves here.
SSM_TOWER_FQDN = "/coilysiren/kai-tower-3026/tailnet-fqdn"
SSM_SENTRY_DSN = "/coilysiren/agent-proxy/sentry-dsn"
SSM_API_FALLBACK_KEY = "/coilysiren/agent-proxy/api-fallback-key"


def _ssm_get(name: str) -> str | None:
    """Best-effort SSM SecureString read. Returns None if unavailable.

    boto3 and credentials are optional at dev time; the proxy runs fully against
    a local/stub backend without them. In the cluster the pod's role provides
    both and this resolves the real values at boot.
    """
    try:
        import boto3  # imported lazily so dev without AWS still works
    except ImportError:
        return None
    try:
        client = boto3.client("ssm")
        resp = client.get_parameter(Name=name, WithDecryption=True)
        return resp["Parameter"]["Value"]
    except Exception:
        # No creds, no network, missing param - all degrade to None, never fatal.
        return None


class Settings(BaseSettings):
    """Proxy settings. Env wins over SSM wins over the built-in default."""

    model_config = SettingsConfigDict(env_prefix="PROXY_", extra="ignore")

    proxy_host: str = Field(default="127.0.0.1")
    proxy_port: int = Field(default=8080)
    log_level: str = Field(default="INFO")

    # Queue / worker sizing (leg 04 step 3).
    queue_maxsize: int = Field(default=100)
    worker_count: int = Field(default=4)

    # Resilience knobs (leg 04 step 4).
    max_retries: int = Field(default=2, description="Retries per backend before falling back")
    retry_base_delay: float = Field(default=0.5, description="Backoff base seconds")
    circuit_fail_threshold: int = Field(
        default=5, description="Consecutive fails before opening a breaker"
    )
    circuit_cooldown: float = Field(default=30.0, description="Seconds a breaker stays open")
    request_timeout: float = Field(
        default=600.0, description="Per-backend upstream timeout seconds"
    )

    # Context-budget headroom reserved for the completion (leg 04 step 5).
    num_ctx_headroom: int = Field(default=1024)

    # VRAM-safe upper bound on the injected num_ctx (issue #32). The proxy reads
    # a model's real context_length from /api/tags and injects
    # min(context_length, num_ctx_ceiling) - num_ctx_headroom, so a model that
    # advertises a huge window (qwen3:4b = 262144) never allocates more KV cache
    # than the tower can carry. Default is the leg-01 proven-safe ceiling.
    num_ctx_ceiling: int = Field(default=49152)

    # OLLAMA_NUM_PARALLEL coupling (issue #33). ollama loads num_ctx as the model's
    # *total* context and divides it across OLLAMA_NUM_PARALLEL slots, so the usable
    # per-request window is num_ctx / NUM_PARALLEL. A backend serving >1 slot would
    # silently halve (or worse) the window the proxy asked for. The proxy
    # compensates by injecting `derived_num_ctx * ollama_num_parallel` so each slot
    # still delivers the intended per-request window. Set this to match the
    # backend's real OLLAMA_NUM_PARALLEL. The deploy should pin the backend to 1
    # (ansible ollama role); this is the proxy-side defense in depth. A per-backend
    # override rides in PROXY_BACKENDS_JSON as `"num_parallel"`.
    ollama_num_parallel: int = Field(default=1)

    # Fail-loud verification of the delivered context (issue #33). After a call the
    # proxy compares the backend's prompt_eval_count against the window it asked
    # for; when the backend processed materially fewer prompt tokens than both what
    # was sent and the per-request target, it truncated below the asked-for context
    # (the NUM_PARALLEL division, or a misconfigured num_parallel). The tolerance is
    # the slack that absorbs tokenizer drift between the proxy's tiktoken estimate
    # and ollama's real count. When fail_on_context_truncation is set the request
    # 502s loud instead of returning the short read; the default marks it
    # (metric + finish_reason=length + structured log) but still returns content.
    context_truncation_tolerance: float = Field(default=0.15)
    fail_on_context_truncation: bool = Field(default=False)

    # Observability wiring (leg 04 step 1).
    sentry_dsn: str = Field(default="")
    otel_exporter_otlp_endpoint: str = Field(default="")
    service_name: str = Field(default="agent-proxy")
    trace_bodies: bool = Field(default=False)

    # Ward reap telemetry ingest. The path may point at a single
    # ``skill-usage.json`` artifact or a directory of reaped run archives.
    ward_skill_use_input: str = Field(default="")

    # Cold-path trajectory retention. The default is a file-backed SQLite WAL
    # under the service working directory. Deployments mount this path on durable
    # storage instead of treating the container layer as retention.
    trajectory_db_path: str = Field(default="./data/trajectory.sqlite3")
    trajectory_ingest_queue_size: int = Field(default=256, ge=1)

    # Tower resolution. If PROXY_TOWER_BASE_URL is set it wins outright; else the
    # FQDN resolves from SSM at boot and the base URL is built from it.
    tower_base_url: str = Field(default="")
    tower_port: int = Field(default=11434)

    # Backend chain override (issue #32). A JSON array of backend specs
    # (``{"name","url","dialect"?,"chat_path"?,...}``, no tag - the tag comes
    # from the request) lets a deploy (a ConfigMap later) supply siblings / CPU /
    # an OpenAI-dialect fallback beyond the single built-in tower backend.
    backends_json: str = Field(default="")
    backends_file: str = Field(default="")

    def resolved_tower_base_url(self) -> str:
        """The primary ollama base URL, from env, SSM, or a safe local default."""
        if self.tower_base_url:
            return self.tower_base_url.rstrip("/")
        fqdn = _ssm_get(SSM_TOWER_FQDN)
        if fqdn:
            return f"http://{fqdn}:{self.tower_port}"
        # Dev default: a local ollama (or stub) on the standard port.
        return f"http://127.0.0.1:{self.tower_port}"

    def resolved_sentry_dsn(self) -> str:
        return self.sentry_dsn or (_ssm_get(SSM_SENTRY_DSN) or "")

    def resolved_api_fallback_key(self) -> str:
        return _ssm_get(SSM_API_FALLBACK_KEY) or os.environ.get("PROXY_API_FALLBACK_KEY", "")

    def backend_overrides(self) -> list[dict[str, Any]] | None:
        """Parsed backend-chain override, or None to use the built-in tower."""
        raw = self.backends_json
        if not raw and self.backends_file:
            try:
                with open(self.backends_file, "r", encoding="utf-8") as fh:
                    raw = fh.read()
            except OSError:
                return None
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, list) and parsed else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (SSM reads happen once at first access)."""
    return Settings()


# Convenience module-level handle.
settings = get_settings()
