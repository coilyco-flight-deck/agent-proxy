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

### CI quality gate

Every push and pull request is gated by `.forgejo/workflows/ci.yml`, which runs
the declared dev tooling off `pyproject.toml`. Run the same gate locally before
landing so CI is green on arrival:

```bash
ward exec test         # syncs dev deps and runs pytest
uv run ruff check .
uv run black --check .
uv run mypy app
./boot_probe.sh        # ward boot-probe (daemonless boot + endpoint probe)
./test-fixes.sh        # ward smoke (daemonless import/build smoke)
```

`[tool.black]`, `[tool.ruff]`, and `[tool.mypy]` in `pyproject.toml` are the
single source of truth these checks read, so local and CI runs behave
identically. The Docker-daemon acceptance test (`ward test-container`) and the
tower-dependent proof/reliability scripts are intentionally left out of CI - they
need a daemon or the tower and would break a daemonless runner.

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

### Validating the container boots + serves

A build that succeeds is not proof the image runs. Two repeatable checks assert
the image actually boots and serves `/healthz`, `/v1/models`, and `/metrics`
(they share `scripts/probe_endpoints.sh`, so the endpoint assertions are
identical):

- `ward test-container` (`./test-container.sh`) - **with a Docker daemon.**
  Builds the image, runs it detached, waits for `python -m app.main` to bind,
  probes the three endpoints from outside the container, and confirms it stays
  up (no crash / restart loop). Fails loudly with container logs on any error.
- `ward boot-probe` (`./boot_probe.sh`) - **daemonless.** Reproduces the exact
  Dockerfile install path (`uv sync --frozen --no-dev`) and CMD
  (`.venv/bin/python -m app.main`), then probes the same endpoints. This is the
  check that runs in a ward feature container or a daemonless CI leg, where no
  Docker daemon is reachable.

## Testing

All tests must pass before building or deploying:
- Run: `uv run pytest` 

The testing suite validates:
- API endpoint functionality 
- Configuration handling
- Resilience behavior
