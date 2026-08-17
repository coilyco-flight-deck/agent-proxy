# Per-repo task manifest. Run `just` (or `just --list`) to see every verb.
#
# Recipes take trailing arguments directly: `just <verb> a b`, where the
# retired form was `ward exec <verb> -- a b`.
#
# One line of comment per recipe on purpose: just reads only the LAST comment
# line above a recipe, so a wrapped description silently truncates to its tail.
#
# `ward exec` is retired. `.ward/ward.yaml` survives carrying catalog metadata
# only, because the catalog hooks upstream in agentic-os pin that exact path.

set positional-arguments

# Default target: list every available recipe.
default:
    @just --list --unsorted

# Sync dev deps and run the unit test suite (tower not required).
test *ARGS:
    @uv run --extra dev pytest "$@"

# Check Python formatting.
format-check *ARGS:
    @uv run --extra dev black --check . "$@"

# Format Python sources.
format *ARGS:
    @uv run --extra dev black . "$@"

# Run the Python linter.
lint *ARGS:
    @uv run --extra dev ruff check . "$@"

# Run the Python type checker.
typecheck *ARGS:
    @uv run --extra dev mypy app "$@"

# Run the repository validation hooks over all files.
pre-commit *ARGS:
    @pre-commit run --all-files "$@"

# Install the pre-commit and pre-push hooks into a fresh clone.
pre-commit-install *ARGS:
    @pre-commit install --hook-type pre-commit --hook-type pre-push "$@"

# Serve the proxy on 127.0.0.1:8080.
serve *ARGS:
    @uv run uvicorn app.main:app --port 8080 "$@"

# Sync the app + dev dependencies.
sync *ARGS:
    @uv run uv sync "$@"

# Regenerate the committed trajectory contract JSON Schema.
schema *ARGS:
    @uv run python scripts/export_trajectory_schema.py "$@"

# Compare the current Agent Proxy surface with a standalone LiteLLM candidate.
litellm-parity *ARGS:
    @uv run python scripts/litellm_parity.py "$@"

# Prove a durable trajectory ledger through online backup and replay.
trajectory-evidence *ARGS:
    @uv run python scripts/trajectory_evidence.py "$@"

# Read and filter governed trajectory evidence through Agent Proxy.
trajectory-query *ARGS:
    @uv run agent-proxy-query "$@"

# Ingest one verified agent-compose bundle into the durable trajectory store.
ingest-agent-compose *ARGS:
    @uv run python scripts/ingest_agent_compose.py "$@"

# Ingest cli-guard audit rows and specgen policy evidence.
ingest-guard-data *ARGS:
    @uv run python scripts/ingest_guard_data.py "$@"

# Build the image and assert it boots + serves the three endpoints (needs a Docker daemon).
test-container *ARGS:
    @bash ./test-container.sh "$@"

# Daemonless boot probe against the frozen runtime deps.
boot-probe *ARGS:
    @bash ./boot_probe.sh "$@"

# Daemonless import/build smoke (runtime deps locked, app imports clean).
smoke *ARGS:
    @bash ./test-fixes.sh "$@"

# Prove the 32k truncation cliff is gone through the proxy (needs the tower + a running proxy).
proof *ARGS:
    @uv run python scripts/truncation_proof.py "$@"

# Concurrency burst against a running proxy - queue or shed (needs --yes, spends tokens).
burst-probe *ARGS:
    @uv run python scripts/burst_probe.py "$@"

# Reliability harness - score a context-growing, tool-using loop and report a reliability percentage.
reliability *ARGS:
    @uv run python scripts/reliability_loop.py "$@"
