"""Read-only Agent Proxy trajectory query helper."""

from __future__ import annotations

import json

import httpx
import pytest

from app.trajectory.query import (
    InvestigationFilters,
    QueryError,
    TrajectoryQueryClient,
    compare_harness_fit,
    investigate,
)


def _view(name: str, rows: list[dict]) -> dict:
    return {
        "contract": {"name": name, "schema_version": "1.0"},
        "freshness": {"age_seconds": 4.0, "complete_through": "2026-08-01T00:00:00Z"},
        "access_tier": "internal",
        "rows": rows,
    }


def _trajectory_row(trajectory_id: str, repository: str, issue: str, workflow: str) -> dict:
    return {
        "trajectory_id": trajectory_id,
        "repository": [repository],
        "issue_refs": [issue],
        "workflows": [workflow],
    }


def test_investigation_composes_filters_and_joins_views_with_one_dossier():
    wanted = _trajectory_row("trajectory-wanted", "example/repo", "example/repo#7", "work")
    unrelated = _trajectory_row("trajectory-other", "other/repo", "other/repo#8", "work")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.startswith("/v1/trajectory/views/"):
            name = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=_view(name, [wanted, unrelated]))
        if request.url.path == "/v1/trajectory/dossiers/trajectory-wanted":
            return httpx.Response(
                200,
                json={"trajectory_id": "trajectory-wanted", "may_authorize": False},
            )
        return httpx.Response(404, json={"error": "not found"})

    with TrajectoryQueryClient(
        "http://proxy.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = investigate(
            client,
            InvestigationFilters(
                repository="example/repo",
                issue="example/repo#7",
                workflow="work",
                trajectory="trajectory-wanted",
            ),
        )

    assert result["trajectory_count"] == 1
    assert result["trajectories"][0]["trajectory_id"] == "trajectory-wanted"
    assert result["trajectories"][0]["dossier"]["may_authorize"] is False
    assert all(summary["matched_row_count"] == 1 for summary in result["views"].values())
    assert calls.count("/v1/trajectory/dossiers/trajectory-wanted") == 1
    assert "/v1/trajectory/dossiers/trajectory-other" not in calls


def test_investigation_preserves_missing_dossier_without_fabricating_evidence():
    row = _trajectory_row("trajectory-partial", "example/repo", "example/repo#7", "work")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/v1/trajectory/views/"):
            name = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=_view(name, [row]))
        return httpx.Response(404, json={"error": "not found"})

    with TrajectoryQueryClient(
        "http://proxy.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = investigate(client, InvestigationFilters(trajectory="trajectory-partial"))

    assert result["trajectories"][0]["dossier"] is None


def test_harness_fit_filters_and_keeps_observational_limits():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_view(
                "harness_fit",
                [
                    {"harness": "codex", "model": "model-a", "trajectory_count": 4},
                    {"harness": "claude", "model": "model-b", "trajectory_count": 9},
                ],
            ),
        )

    with TrajectoryQueryClient(
        "http://proxy.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = compare_harness_fit(client, harness="codex", model="model-a")

    assert result["rows"] == [{"harness": "codex", "model": "model-a", "trajectory_count": 4}]
    assert result["view"]["matched_row_count"] == 1
    assert "observational" in result["interpretation_limits"][0]


def test_http_failures_do_not_copy_response_bodies_into_errors():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"sensitive upstream body")

    with TrajectoryQueryClient(
        "http://proxy.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(QueryError) as error:
            client.view("reliability")

    assert "HTTP 500" in str(error.value)
    assert "sensitive upstream body" not in str(error.value)


def test_non_object_json_is_rejected():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps([]).encode())

    with TrajectoryQueryClient(
        "http://proxy.test",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(QueryError, match="non-object JSON"):
            client.view("reliability")
