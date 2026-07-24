"""Reproducible, versioned trajectory dataset exports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.trajectory.evaluation import EvaluationRecord
from app.trajectory.materialize import MaterializedTrajectory

DatasetKind = Literal[
    "sft",
    "preference",
    "verifier",
    "reward",
    "held_out_evaluation",
]
DatasetSplit = Literal["train", "held_out"]

DATASET_SCHEMA_VERSION = "1.0"
DATASET_TRANSFORM_VERSION = "dataset-export-v1"


class SplitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "trajectory-hash-v1"
    seed: str = "agent-proxy-v1"
    heldout_modulus: int = Field(default=10, ge=2)
    heldout_buckets: tuple[int, ...] = (0,)

    def split(self, trajectory_id: str) -> DatasetSplit:
        digest = hashlib.sha256(f"{self.seed}:{trajectory_id}".encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:8], "big") % self.heldout_modulus
        return "held_out" if bucket in self.heldout_buckets else "train"


class RedactionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "metadata-and-refs-v1"
    include_restricted_body_refs: bool = False


class ExportExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    example_id: str
    trajectory_id: str
    evaluation_id: str
    source_event_ids: tuple[str, ...]
    access_tier: str
    content_sha256: str


class SFTExample(ExportExample):
    input_refs: tuple[str, ...]
    target_label: str | None = None
    target_score: float | None = None
    body_ref: str | None = None
    body_sha256: str | None = None


class PreferenceExample(ExportExample):
    chosen_ref: str
    rejected_ref: str


class VerifierExample(ExportExample):
    input_refs: tuple[str, ...]
    expected_label: str | None = None
    expected_score: float | None = None


class RewardExample(ExportExample):
    input_refs: tuple[str, ...]
    reward: float


class HeldOutEvaluationExample(ExportExample):
    input_refs: tuple[str, ...]
    expected_label: str | None = None
    expected_score: float | None = None


DatasetExample = (
    SFTExample | PreferenceExample | VerifierExample | RewardExample | HeldOutEvaluationExample
)


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    kind: DatasetKind
    schema_name: str
    schema_version: str = DATASET_SCHEMA_VERSION
    split: DatasetSplit
    created_at: str
    selection_policy: str
    transform_version: str = DATASET_TRANSFORM_VERSION
    split_policy: SplitPolicy
    redaction_policy: RedactionPolicy
    source_event_ids: tuple[str, ...]
    trajectory_ids: tuple[str, ...]
    evaluation_ids: tuple[str, ...]
    example_content_sha256: tuple[str, ...]
    access_tier: str
    immutable_query_boundary: str
    manifest_sha256: str


class DatasetArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: DatasetManifest
    examples: tuple[DatasetExample, ...]


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _active_evaluations(records: list[EvaluationRecord]) -> list[EvaluationRecord]:
    superseded = {
        record.supersedes_evaluation_id
        for record in records
        if record.supersedes_evaluation_id is not None
    }
    return [record for record in records if record.evaluation_id not in superseded]


def _example_id(kind: DatasetKind, record: EvaluationRecord) -> str:
    seed = f"{kind}:{record.trajectory_id}:{record.evaluation_id}"
    return "example-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _finalize_example(example: DatasetExample) -> DatasetExample:
    digest = _sha256(example.model_dump(mode="json", exclude={"content_sha256"}))
    return example.model_copy(update={"content_sha256": digest})


class DatasetExporter:
    """Build immutable refs-first datasets from materialized evidence."""

    def __init__(
        self,
        *,
        split_policy: SplitPolicy | None = None,
        redaction_policy: RedactionPolicy | None = None,
        selection_policy: str = "active-evaluations-v1",
    ) -> None:
        self.split_policy = split_policy or SplitPolicy()
        self.redaction_policy = redaction_policy or RedactionPolicy()
        self.selection_policy = selection_policy

    def export(
        self,
        kind: DatasetKind,
        trajectories: tuple[MaterializedTrajectory, ...] | list[MaterializedTrajectory],
        evaluations: tuple[EvaluationRecord, ...] | list[EvaluationRecord],
    ) -> DatasetArtifact:
        trajectory_by_id = {record.trajectory_id: record for record in trajectories}
        split: DatasetSplit = "held_out" if kind == "held_out_evaluation" else "train"
        relevant = [
            record
            for record in _active_evaluations(list(evaluations))
            if record.trajectory_id in trajectory_by_id
            and self.split_policy.split(record.trajectory_id) == split
        ]
        relevant.sort(key=lambda record: (record.trajectory_id, record.evaluation_id))
        examples = tuple(
            example
            for record in relevant
            if (example := self._example(kind, record, trajectory_by_id[record.trajectory_id]))
            is not None
        )
        source_event_ids = tuple(
            sorted({event_id for example in examples for event_id in example.source_event_ids})
        )
        trajectory_ids = tuple(sorted({example.trajectory_id for example in examples}))
        evaluation_ids = tuple(sorted({example.evaluation_id for example in examples}))
        access_tier = (
            "restricted"
            if any(example.access_tier == "restricted" for example in examples)
            else "internal"
        )
        created_at = max(
            (
                trajectory_by_id[trajectory_id].materialized_at.isoformat()
                for trajectory_id in trajectory_ids
            ),
            default="1970-01-01T00:00:00+00:00",
        )
        query_boundary = (
            f"materialized_at<={created_at};split={split};" f"selection={self.selection_policy}"
        )
        identity = {
            "kind": kind,
            "split": split,
            "schema_version": DATASET_SCHEMA_VERSION,
            "selection_policy": self.selection_policy,
            "transform_version": DATASET_TRANSFORM_VERSION,
            "split_policy": self.split_policy.model_dump(mode="json"),
            "redaction_policy": self.redaction_policy.model_dump(mode="json"),
            "source_event_ids": source_event_ids,
            "trajectory_ids": trajectory_ids,
            "evaluation_ids": evaluation_ids,
            "example_content_sha256": tuple(example.content_sha256 for example in examples),
            "immutable_query_boundary": query_boundary,
        }
        dataset_id = "dataset-" + _sha256(identity)[:32]
        manifest = DatasetManifest(
            dataset_id=dataset_id,
            kind=kind,
            schema_name=f"agentproxy.dataset.{kind}",
            split=split,
            created_at=created_at,
            selection_policy=self.selection_policy,
            split_policy=self.split_policy,
            redaction_policy=self.redaction_policy,
            source_event_ids=source_event_ids,
            trajectory_ids=trajectory_ids,
            evaluation_ids=evaluation_ids,
            example_content_sha256=tuple(example.content_sha256 for example in examples),
            access_tier=access_tier,
            immutable_query_boundary=query_boundary,
            manifest_sha256="",
        )
        manifest = manifest.model_copy(
            update={
                "manifest_sha256": _sha256(
                    manifest.model_dump(mode="json", exclude={"manifest_sha256"})
                )
            }
        )
        return DatasetArtifact(manifest=manifest, examples=examples)

    def export_all(
        self,
        trajectories: tuple[MaterializedTrajectory, ...] | list[MaterializedTrajectory],
        evaluations: tuple[EvaluationRecord, ...] | list[EvaluationRecord],
    ) -> tuple[DatasetArtifact, ...]:
        kinds: tuple[DatasetKind, ...] = (
            "sft",
            "preference",
            "verifier",
            "reward",
            "held_out_evaluation",
        )
        return tuple(self.export(kind, trajectories, evaluations) for kind in kinds)

    def _example(
        self,
        kind: DatasetKind,
        record: EvaluationRecord,
        trajectory: MaterializedTrajectory,
    ) -> DatasetExample | None:
        common: dict[str, Any] = {
            "example_id": _example_id(kind, record),
            "trajectory_id": record.trajectory_id,
            "evaluation_id": record.evaluation_id,
            "source_event_ids": tuple(
                sorted({record.source_event_id, *trajectory.source_event_ids})
            ),
            "access_tier": (
                "restricted"
                if record.access_tier == "restricted" or trajectory.access_tier == "restricted"
                else "internal"
            ),
            "content_sha256": "",
        }
        if kind == "sft":
            if record.output_label is None and record.score is None:
                return None
            include_body = (
                self.redaction_policy.include_restricted_body_refs
                and record.capture == "restricted_body"
            )
            return _finalize_example(
                SFTExample(
                    **common,
                    input_refs=record.input_refs,
                    target_label=record.output_label,
                    target_score=record.score,
                    body_ref=record.body_ref if include_body else None,
                    body_sha256=record.body_sha256 if include_body else None,
                )
            )
        if kind == "preference":
            if not record.chosen_ref or not record.rejected_ref:
                return None
            return _finalize_example(
                PreferenceExample(
                    **common,
                    chosen_ref=record.chosen_ref,
                    rejected_ref=record.rejected_ref,
                )
            )
        if kind == "verifier":
            if record.kind != "verifier":
                return None
            return _finalize_example(
                VerifierExample(
                    **common,
                    input_refs=record.input_refs,
                    expected_label=record.output_label,
                    expected_score=record.score,
                )
            )
        if kind == "reward":
            reward = record.reward if record.reward is not None else record.score
            if reward is None:
                return None
            return _finalize_example(
                RewardExample(**common, input_refs=record.input_refs, reward=reward)
            )
        if record.output_label is None and record.score is None:
            return None
        return _finalize_example(
            HeldOutEvaluationExample(
                **common,
                input_refs=record.input_refs,
                expected_label=record.output_label,
                expected_score=record.score,
            )
        )


class DatasetArtifactStore:
    """Write-once directory store for manifest plus JSONL examples."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def write(self, artifact: DatasetArtifact) -> Path:
        target = self.root / artifact.manifest.dataset_id
        manifest_bytes = (
            json.dumps(
                artifact.manifest.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        data_bytes = b"".join(
            _canonical(example.model_dump(mode="json")) + b"\n" for example in artifact.examples
        )
        if target.exists():
            existing_manifest = (target / "manifest.json").read_bytes()
            existing_data = (target / "data.jsonl").read_bytes()
            if existing_manifest != manifest_bytes or existing_data != data_bytes:
                raise ValueError(
                    f"dataset id {artifact.manifest.dataset_id} already has different content"
                )
            return target
        target.mkdir(parents=True)
        (target / "manifest.json").write_bytes(manifest_bytes)
        (target / "data.jsonl").write_bytes(data_bytes)
        return target
