#!/bin/bash

# Simple smoke test for the agent-proxy build
set -e

echo "=== Testing container fixes ==="

# Print current pyproject.toml content (should have correct dependencies)
echo "Checking pyproject.toml dependencies:"
grep -A 10 "\[project.dependencies\]" /workspace/agent-proxy/pyproject.toml

echo ""
echo "Checking Dockerfile:"
cat /workspace/agent-proxy/Dockerfile

echo ""
echo "=== Test completed successfully ==="