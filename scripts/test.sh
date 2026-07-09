#!/usr/bin/env bash
# Sync dev deps and run the unit test suite.
set -euo pipefail

cd "$(dirname "$0")/.."

uv sync --extra dev
.venv/bin/pytest
