"""Deterministic episode and trajectory reconstruction."""

from __future__ import annotations

import json
from pathlib import Path

from app.trajectory import (
    MaterializationStore,
    TrajectoryMaterializer,
    TrajectoryStore,
    materialize_retained_events,
    validate_event,
)

FIXTURES = Path("tests/fixtures/trajectory")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _human_intervention() -> dict:
    payload = _fixture("valid.json")
    payload["event_id"] = "018f6d1d-5e54-7c20-bf7e-5bd1ca1e81a2"
    payload["idempotency_key"] = "fixture-human-intervention"
    payload["event_type"] = "human.intervened"
    payload["occurred_at"] = "2026-07-23T05:30:35Z"
    payload["observed_at"] = "2026-07-23T05:40:35Z"
    payload["payload"] = {
        "intervention_kind": "approval",
        "human_role": "operator",
        "rationale_ref": "rationale:fixture",
        "affected_ref": "action:fixture",
    }
    return payload


def test_materialization_is_deterministic_and_preserves_all_joins():
    completed = validate_event(_fixture("valid.json"))
    intervention = validate_event(_human_intervention())
    materializer = TrajectoryMaterializer()

    first = materializer.materialize([completed, intervention])
    second = materializer.materialize([intervention, completed])

    assert first == second
    record = first[0]
    assert record.status == "complete"
    assert record.correlations["ward_run_id"] == ("fixture-run",)
    assert record.correlations["repository"] == ("example/repository",)
    assert record.correlations["issue_ref"] == ("example/repository#42",)
    assert record.correlations["workflow"] == ("direct-to-main",)
    assert record.human_intervention_count == 1
    assert record.retry_count == 1
    assert record.content_sha256


def test_missing_terminal_and_primary_correlation_stay_explicitly_partial():
    event = validate_event(_fixture("partial-trajectory.json"))

    record = TrajectoryMaterializer().materialize([event])[0]

    assert record.status == "partial"
    assert record.partial_reasons == ("missing_terminal_event",)
    assert record.source_event_ids == (str(event.event_id),)


def test_unjoined_event_is_partial_for_both_missing_conditions():
    payload = _fixture("partial-trajectory.json")
    payload["event_id"] = "018f6d1d-5e54-7c20-bf7e-5bd1ca1e81a3"
    payload["idempotency_key"] = "fixture-unjoined"
    payload["correlation"] = {}

    record = TrajectoryMaterializer().materialize([validate_event(payload)])[0]

    assert record.partial_reasons == (
        "missing_terminal_event",
        "missing_primary_correlation",
    )


def test_late_event_appends_a_new_revision_and_reconciles_sources(tmp_path):
    raw = TrajectoryStore(tmp_path / "trajectory.sqlite3")
    derived = MaterializationStore(raw.path)
    raw.ingest(_fixture("valid.json"))

    first = materialize_retained_events(raw, derived)[0]
    raw.ingest(_human_intervention())
    second = materialize_retained_events(raw, derived)[0]

    assert first.revision == 1
    assert second.revision == 2
    assert second.late_event_ids == ("018f6d1d-5e54-7c20-bf7e-5bd1ca1e81a2",)
    assert len(second.source_event_ids) == 2
    assert [record.revision for record in derived.revisions(second.trajectory_id)] == [1, 2]


def test_identical_rematerialization_reuses_the_existing_revision(tmp_path):
    raw = TrajectoryStore(tmp_path / "trajectory.sqlite3")
    derived = MaterializationStore(raw.path)
    raw.ingest(_fixture("valid.json"))

    first = materialize_retained_events(raw, derived)[0]
    second = materialize_retained_events(raw, derived)[0]

    assert second == first
    assert len(derived.revisions(first.trajectory_id)) == 1
