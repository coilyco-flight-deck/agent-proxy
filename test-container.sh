#!/usr/bin/env bash
# Real container acceptance test for agent-proxy (agent-proxy#24).
#
# Unlike a build-only check, this proves the image actually BOOTS and SERVES:
# it builds the image, runs it detached, waits for the CMD (`python -m app.main`)
# to bind, asserts /healthz + /v1/models + /metrics all respond from inside the
# running container, and confirms the container is still up (no crash / restart
# loop) afterwards. Any failure prints the container logs and exits non-zero.
#
# Requires a reachable Docker daemon. On a daemonless box (a ward feature
# container, a daemonless CI leg) run ./boot_probe.sh instead - it validates the
# same boot path against the frozen runtime deps without the image layer.
set -euo pipefail

cd "$(dirname "$0")"

IMAGE="${IMAGE:-agent-proxy-test}"
NAME="${NAME:-agent-proxy-test-run}"
PORT="${PORT:-8080}"
BASE_URL="http://127.0.0.1:${PORT}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker CLI not found on PATH" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: no reachable Docker daemon (docker info failed)." >&2
  echo "       This test needs a real daemon to build and boot the image." >&2
  echo "       On a daemonless box run ./boot_probe.sh instead - it validates" >&2
  echo "       the same boot path against the frozen runtime deps." >&2
  exit 1
fi

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup  # clear any stale container from a prior run

echo "=== docker build -t ${IMAGE} . ==="
docker build -t "$IMAGE" .

echo "=== docker run (detached, PROXY_HOST=0.0.0.0 so the mapped port is reachable) ==="
docker run -d --name "$NAME" -p "${PORT}:8080" \
  -e PROXY_HOST=0.0.0.0 -e PROXY_PORT=8080 "$IMAGE"

echo "=== probe live endpoints ==="
if ! ./scripts/probe_endpoints.sh "$BASE_URL" 40; then
  echo "ERROR: endpoint probe failed. Container logs:" >&2
  docker logs "$NAME" 2>&1 | tail -40 >&2
  exit 1
fi

echo "=== assert the container is still running (no crash / restart loop) ==="
running=$(docker inspect -f '{{.State.Running}}' "$NAME")
restarts=$(docker inspect -f '{{.RestartCount}}' "$NAME")
echo "  State.Running=${running} RestartCount=${restarts}"
if [ "$running" != "true" ]; then
  echo "ERROR: container is not running after the probe window" >&2
  docker logs "$NAME" 2>&1 | tail -40 >&2
  exit 1
fi

echo ""
echo "=== PASS: image builds, boots, and serves /healthz + /v1/models + /metrics ==="
