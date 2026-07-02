#!/usr/bin/env bash
# Daemonless boot validation of the container runtime (agent-proxy#24).
#
# Reproduces exactly what the Dockerfile installs and runs - `uv sync --frozen
# --no-dev` then `python -m app.main` off the resulting .venv - and asserts the
# process binds, serves /healthz + /v1/models + /metrics, and stays up (no
# crash). This is the repeatable check for a box with NO Docker daemon (a ward
# feature container, a daemonless CI leg). test-container.sh is the full image
# build+run for a box that has a daemon; both share scripts/probe_endpoints.sh
# so the endpoint assertions are identical.
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8080}"
BASE_URL="http://127.0.0.1:${PORT}"
BOOT_LOG="$(mktemp -t agent-proxy-boot.XXXXXX.log)"

echo "=== uv sync --frozen --no-dev (the exact Dockerfile install path) ==="
uv sync --frozen --no-dev

echo "=== boot: .venv/bin/python -m app.main (the exact container CMD) ==="
PROXY_HOST=127.0.0.1 PROXY_PORT="$PORT" .venv/bin/python -m app.main >"$BOOT_LOG" 2>&1 &
APP_PID=$!
cleanup() { kill "$APP_PID" 2>/dev/null || true; }
trap cleanup EXIT

echo "=== probe live endpoints ==="
if ! ./scripts/probe_endpoints.sh "$BASE_URL" 30; then
  echo "ERROR: endpoint probe failed. Boot log:" >&2
  tail -40 "$BOOT_LOG" >&2
  exit 1
fi

echo "=== assert the process is still alive (no crash) ==="
if ! kill -0 "$APP_PID" 2>/dev/null; then
  echo "ERROR: app process died. Boot log:" >&2
  tail -40 "$BOOT_LOG" >&2
  exit 1
fi
echo "  OK   pid ${APP_PID} still serving"

echo ""
echo "=== PASS: frozen runtime boots and serves /healthz + /v1/models + /metrics ==="
