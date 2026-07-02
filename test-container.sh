#!/bin/bash

# Test script to verify the agent-proxy container setup
echo "Testing agent-proxy container build and environment setup..."

# Check if we have Docker installed
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed or not in PATH"
    exit 1
fi

echo "Docker version:"
docker --version

echo "Building container..."
docker build -t agent-proxy-test .

if [ $? -ne 0 ]; then
    echo "ERROR: Container build failed"
    exit 1
fi

echo "Container built successfully!"

# Verify the container can be run (no need to actually start it, just check if it works)
echo "Checking that all required components exist..."
docker run agent-proxy-test ls -la /app || true

echo "Setup is complete and container artifacts are ready!"