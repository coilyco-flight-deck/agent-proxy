#!/bin/bash

# Simple smoke test for the agent-proxy build. Confirms the runtime deps are a
# proper PEP 621 array (not the old malformed [project.dependencies] table) and
# that every runtime import is both declared and present in the lock.
set -e

cd "$(dirname "$0")"

echo "=== Testing container fixes ==="

echo "Checking pyproject.toml runtime dependencies (PEP 621 array):"
grep -A 10 "^dependencies = \[" pyproject.toml

echo ""
echo "Checking each runtime dep is locked:"
for pkg in fastapi pydantic pydantic-settings hypercorn httpx structlog prometheus-client tiktoken; do
    if grep -q "name = \"$pkg\"" uv.lock; then
        echo "  OK   $pkg"
    else
        echo "  FAIL $pkg missing from uv.lock"
        exit 1
    fi
done

echo ""
echo "Checking the app imports clean under runtime-only deps:"
uv run --no-dev python -c "import app.main; print('  OK   import app.main')"

echo ""
echo "=== Test completed successfully ==="
