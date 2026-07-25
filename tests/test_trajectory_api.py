"""Cold-path trajectory intake API."""

from __future__ import annotations

import json
from pathlib import Path

from app.trajectory import TrajectoryStore
from app.trajectory import api as trajectory_api

FIXTURES = Path("tests/fixtures/trajectory")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_intake_accepts_then_deduplicates(app_client, monkeypatch, tmp_path):
    store = TrajectoryStore(tmp_path / "trajectory.sqlite3")
    monkeypatch.setattr(trajectory_api, "get_trajectory_store", lambda: store)

    accepted = app_client.post("/v1/trajectory/events", json=_fixture("valid.json"))
    duplicate = app_client.post("/v1/trajectory/events", json=_fixture("valid.json"))

    assert accepted.status_code == 202
    assert accepted.json()["outcome"] == "accepted"
    assert duplicate.status_code == 200
    assert duplicate.json()["outcome"] == "duplicate"


def test_intake_quarantines_invalid_delivery(app_client, monkeypatch, tmp_path):
    store = TrajectoryStore(tmp_path / "trajectory.sqlite3")
    monkeypatch.setattr(trajectory_api, "get_trajectory_store", lambda: store)

    response = app_client.post(
        "/v1/trajectory/events",
        json=_fixture("invalid-missing-event-id.json"),
    )

    assert response.status_code == 422
    assert response.json()["outcome"] == "quarantined"
    assert response.json()["errors"]
