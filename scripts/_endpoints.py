"""Shared endpoint resolution for the proof/harness scripts.

The tower FQDN is never written into a tracked file. It resolves, in order:
``TOWER`` env (bare host), ``PROXY_TOWER_BASE_URL`` env (full URL), or SSM
``/coilysiren/kai-tower-3026/tailnet-fqdn``. The proxy defaults to localhost.
"""

from __future__ import annotations

import os

SSM_TOWER_FQDN = "/coilysiren/kai-tower-3026/tailnet-fqdn"


def tower_base_url() -> str:
    if url := os.environ.get("PROXY_TOWER_BASE_URL"):
        return url.rstrip("/")
    if host := os.environ.get("TOWER"):
        return f"http://{host}:11434"
    try:
        import boto3

        val = boto3.client("ssm").get_parameter(Name=SSM_TOWER_FQDN)["Parameter"]["Value"]
        return f"http://{val}:11434"
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "Cannot resolve the tower. Set TOWER=<host> or PROXY_TOWER_BASE_URL=<url>, "
            f"or provide AWS creds for SSM ({SSM_TOWER_FQDN}). ({exc})"
        )


def proxy_base_url() -> str:
    return os.environ.get("PROXY_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
