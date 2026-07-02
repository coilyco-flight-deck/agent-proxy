# Agent Proxy

This is a reliability proxy for the local agent and LLM fleet, with the reliability proxy as phase 1.

## Getting Started

To run tests:
```bash
uv sync --extra dev  
uv run pytest
```

To build the container:
```bash
docker build -t agent-proxy .
```

To run the proxy server:
```bash
docker run -p 8080:8080 agent-proxy
```

## Developer Workflow

1. Clone the repository
2. Install dependencies: `uv sync --extra dev`
3. Run tests to verify installation: `uv run pytest`
4. Build container image: `docker build -t agent-proxy .`  
5. Run proxy in container mode: `docker run -p 8080:8080 agent-proxy`

## Local Development

The proxy runs on port 8080 by default. You can customize the port by setting environment variables:

```bash
PROXY_PORT=9000 docker run -p 9000:9000 agent-proxy
```

Available environment variables:
- `PROXY_HOST` (default: 127.0.0.1)
- `PROXY_PORT` (default: 8080) 
- `LOG_LEVEL` (default: INFO)

## Container Artifact

The following files are provided to make local containerization reproducible:

- `Dockerfile` - Standard container definition
- `README.md` - Documentation for using the proxy  
- `setup.sh` - Script to initialize developer environment

## Testing

All tests must pass before building or deploying:
- Run: `uv run pytest` 

The testing suite validates:
- API endpoint functionality 
- Configuration handling
- Resilience behavior