#!/bin/bash

# Setup script to install dependencies and run tests
# This makes the developer workflow reproducible from a fresh checkout.

set -e

echo "Installing development dependencies..."
uv sync --extra dev

echo "Running tests..."
uv run pytest

echo "Setup complete. To start the proxy server:"
echo "  docker build -t agent-proxy ."
echo "  docker run -p 8080:8080 agent-proxy"
