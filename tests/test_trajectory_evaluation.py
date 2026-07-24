"""Evaluation, annotation, and human intervention evidence."""

from __future__ import annotations

import json
from pathlib import Path

from app.trajectory import (
    EvaluationStore,
    TrajectoryMaterializer,
    TrajectoryStore,
    assemble_evaluation_records,
    summarize_evaluations,
    validate_event,
)

FIXTURES = Path("tests/fixtures/trajectory")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _records(payloads: list[dict]):
    events = [validate_event(payload) for payload in payloads]
    trajectories = TrajectoryMaterializer().materialize(events)
    return trajectories, assemble_evaluation_records(events, trajectories)


def test_automatic_human_and_intervention_records_join_to_one_trajectory():
    trajectories, records = _records(
        [
            _fixture("valid.json"),
            _fixture("evaluation-automatic.json"),
            _fixture("evaluation-human.json"),
            _fixture("human-intervention.json"),
        ]
    )

    assert len(trajectories) == 1
    assert [record.kind for record in records] == [
        "verifier",
        "annotation",
        "human_intervention",
    ]
    assert records[0].origin == "automatic"
    assert records[1].origin == "human"
    assert records[1].annotator is not None
    assert records[1].access_tier == "restricted"
    assert records[1].evidence_refs == (
        "annotation:fixture",
        "event:018f6d1d-5e54-7c20-bf7e-5bd1ca1e8198",
    )


def test_supersession_preserves_history_but_selects_the_active_record():
    trajectories, records = _records(
        [
            _fixture("valid.json"),
            _fixture("evaluation-automatic.json"),
            _fixture("evaluation-human.json"),
        ]
    )

    summary = summarize_evaluations(trajectories[0].trajectory_id, records)

    assert summary.active_evaluation_ids == ("evaluation:human:1",)
    assert summary.superseded_evaluation_ids == ("evaluation:auto:1",)
    assert summary.disagreement is False
    assert summary.has_late_records is True
    assert summary.access_tier == "restricted"


def test_disagreement_remains_visible_until_superseded():
    human = _fixture("evaluation-human.json")
    human["payload"]["supersedes_ref"] = None
    human["payload"]["output_label"] = "fail"
    trajectories, records = _records(
        [_fixture("valid.json"), _fixture("evaluation-automatic.json"), human]
    )

    summary = summarize_evaluations(trajectories[0].trajectory_id, records)

    assert summary.labels == ("fail", "pass")
    assert summary.disagreement is True


def test_evaluation_store_is_idempotent_and_append_only(tmp_path):
    trajectories, records = _records(
        [_fixture("valid.json"), _fixture("evaluation-automatic.json")]
    )
    store = EvaluationStore(tmp_path / "trajectory.sqlite3")

    first = store.save(records[0])
    second = store.save(records[0])

    assert second == first
    assert store.for_trajectory(trajectories[0].trajectory_id) == (first,)


def test_raw_replay_reconstructs_the_same_evaluation_records(tmp_path):
    source = TrajectoryStore(tmp_path / "source.sqlite3")
    target = TrajectoryStore(tmp_path / "target.sqlite3")
    for name in ["valid.json", "evaluation-automatic.json", "evaluation-human.json"]:
        source.ingest(_fixture(name))

    replay = source.replay_into(target)
    source_events = tuple(source.iter_events())
    target_events = tuple(target.iter_events())
    source_trajectories = TrajectoryMaterializer().materialize(source_events)
    target_trajectories = TrajectoryMaterializer().materialize(target_events)

    assert replay.quarantined == 0
    assert assemble_evaluation_records(
        source_events, source_trajectories
    ) == assemble_evaluation_records(target_events, target_trajectories)
