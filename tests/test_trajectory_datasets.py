"""Versioned training and held-out evaluation dataset exports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.trajectory import (
    DatasetArtifactStore,
    DatasetExporter,
    RedactionPolicy,
    SplitPolicy,
    TrajectoryMaterializer,
    assemble_evaluation_records,
    validate_event,
)

FIXTURES = Path("tests/fixtures/trajectory")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _evidence():
    events = [
        validate_event(_fixture("valid.json")),
        validate_event(_fixture("evaluation-automatic.json")),
        validate_event(_fixture("evaluation-human.json")),
    ]
    trajectories = list(TrajectoryMaterializer().materialize(events))
    evaluations = list(assemble_evaluation_records(events, trajectories))

    base_trajectory = trajectories[0]
    base_evaluation = evaluations[0]
    for index in range(1, 12):
        trajectory_id = f"traj-fixture-{index}"
        trajectories.append(
            base_trajectory.model_copy(
                update={
                    "trajectory_id": trajectory_id,
                    "source_event_ids": (f"source-{index}",),
                }
            )
        )
        evaluations.append(
            base_evaluation.model_copy(
                update={
                    "evaluation_id": f"evaluation-{index}",
                    "trajectory_id": trajectory_id,
                    "source_event_id": f"source-{index}",
                    "supersedes_evaluation_id": None,
                }
            )
        )
    return trajectories, evaluations


def test_all_five_export_schemas_have_immutable_provenance_manifests():
    trajectories, evaluations = _evidence()
    exporter = DatasetExporter(split_policy=SplitPolicy(heldout_modulus=3, heldout_buckets=(0,)))

    artifacts = exporter.export_all(trajectories, evaluations)

    assert [artifact.manifest.kind for artifact in artifacts] == [
        "sft",
        "preference",
        "verifier",
        "reward",
        "held_out_evaluation",
    ]
    for artifact in artifacts:
        assert artifact.manifest.schema_version == "1.0"
        assert artifact.manifest.transform_version == "dataset-export-v1"
        assert artifact.manifest.manifest_sha256
        assert artifact.manifest.immutable_query_boundary
        assert (
            tuple(example.content_sha256 for example in artifact.examples)
            == artifact.manifest.example_content_sha256
        )


def test_trajectory_level_split_prevents_train_and_held_out_leakage():
    trajectories, evaluations = _evidence()
    exporter = DatasetExporter(split_policy=SplitPolicy(heldout_modulus=3, heldout_buckets=(0,)))

    train = exporter.export("sft", trajectories, evaluations)
    held_out = exporter.export("held_out_evaluation", trajectories, evaluations)

    assert train.examples
    assert held_out.examples
    assert set(train.manifest.trajectory_ids).isdisjoint(held_out.manifest.trajectory_ids)
    for trajectory in trajectories:
        assigned = exporter.split_policy.split(trajectory.trajectory_id)
        assert (trajectory.trajectory_id in held_out.manifest.trajectory_ids) == (
            assigned == "held_out"
        )


def test_export_is_reproducible_independent_of_input_order():
    trajectories, evaluations = _evidence()
    exporter = DatasetExporter()

    forward = exporter.export("sft", trajectories, evaluations)
    reverse = exporter.export(
        "sft",
        list(reversed(trajectories)),
        list(reversed(evaluations)),
    )

    assert reverse == forward


def test_restricted_body_refs_require_explicit_opt_in():
    events = [
        validate_event(_fixture("valid.json")),
        validate_event(_fixture("evaluation-human.json")),
    ]
    trajectories = TrajectoryMaterializer().materialize(events)
    evaluations = assemble_evaluation_records(events, trajectories)
    split = SplitPolicy(
        heldout_modulus=2,
        heldout_buckets=(
            1
            - int.from_bytes(
                hashlib.sha256(f"agent-proxy-v1:{trajectories[0].trajectory_id}".encode()).digest()[
                    :8
                ],
                "big",
            )
            % 2,
        ),
    )

    default = DatasetExporter(split_policy=split).export("sft", trajectories, evaluations)
    opted_in = DatasetExporter(
        split_policy=split,
        redaction_policy=RedactionPolicy(include_restricted_body_refs=True),
    ).export("sft", trajectories, evaluations)

    assert default.examples[0].body_ref is None
    assert opted_in.examples[0].body_ref == "content:fixture-annotation"
    assert opted_in.manifest.access_tier == "restricted"


def test_artifact_store_is_write_once_and_idempotent(tmp_path):
    trajectories, evaluations = _evidence()
    artifact = DatasetExporter().export("sft", trajectories, evaluations)
    store = DatasetArtifactStore(tmp_path / "datasets")

    first = store.write(artifact)
    second = store.write(artifact)

    assert second == first
    assert (first / "manifest.json").is_file()
    assert (first / "data.jsonl").is_file()
