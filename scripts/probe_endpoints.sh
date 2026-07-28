#!/usr/bin/env bash
# Shared live-endpoint assertions for the container / boot tests. Given a base
# URL, wait for /healthz to come up, then assert /healthz, /v1/models, and
# /metrics each respond correctly. Exits non-zero on the first failed check.
# Depends only on curl + grep so it runs identically inside a container probe
# and against a locally-booted process.
#
# Note the real routes are /healthz and /metrics (unprefixed) and /v1/models -
# agent-proxy#24 phrased healthz as /v1/healthz, but app.main registers it at
# /healthz, so that is what we assert.
set -uo pipefail

BASE_URL="${1:-http://127.0.0.1:8080}"
TIMEOUT="${2:-30}"

fail() { echo "  FAIL $1" >&2; exit 1; }

echo "Probing ${BASE_URL} (readiness timeout ${TIMEOUT}s)"

# 1. Wait for /healthz to answer 200 (server may still be binding).
ready=0
for _ in $(seq 1 "$((TIMEOUT * 2))"); do
  if curl -fsS --max-time 2 "${BASE_URL}/healthz" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done
[ "$ready" = 1 ] || fail "/healthz never became ready within ${TIMEOUT}s"

# 2. /healthz body is {"status":"ok"}.
body=$(curl -fsS --max-time "$TIMEOUT" "${BASE_URL}/healthz") || fail "/healthz request failed"
{ echo "$body" | grep -q '"status"' && echo "$body" | grep -q 'ok'; } \
  || fail "/healthz body unexpected: $body"
echo "  OK   /healthz -> $body"

# 3. /v1/models is an OpenAI-shaped list. Its entries are now the tags the
# backend actually serves (issue #32: live /api/tags, not a static alias list),
# so a daemonless / tower-less boot correctly returns an empty data array. We
# assert the list *shape* here, not a non-empty catalog, since this probe runs
# with no backend reachable.
models=$(curl -fsS --max-time "$TIMEOUT" "${BASE_URL}/v1/models") \
  || fail "/v1/models request failed"
{ echo "$models" | grep -q '"object"' && echo "$models" | grep -q '"data"'; } \
  || fail "/v1/models shape unexpected: $models"
echo "  OK   /v1/models -> OpenAI-shaped model list"

# 4. /metrics is Prometheus exposition text.
metrics=$(curl -fsS --max-time "$TIMEOUT" "${BASE_URL}/metrics") \
  || fail "/metrics request failed"
echo "$metrics" | grep -q '# HELP' || fail "/metrics not in Prometheus format"
echo "  OK   /metrics -> Prometheus exposition"

echo "  PASS all endpoints responded"
