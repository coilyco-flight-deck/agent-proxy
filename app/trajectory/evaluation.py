"""Evaluation, verifier, annotation, and human-intervention projections."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from app.trajectory.materialize import MaterializedTrajectory
from app.trajectory.schema import (
    EvaluationRecordedPayload,
    HumanIntervenedPayload,
    TrajectoryEvent,
)

EVALUATION_SCHEMA_NAME = "agentproxy.trajectory.evaluation"
EVALUATION_SCHEMA_VERSION = "1.0"

EvaluationKind = Literal["evaluation", "verifier", "annotation", "human_intervention"]
EvaluationOrigin = Literal["automatic", "human"]


class EvaluatorIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    implementation_version: str
    rubric_version: str | None = None
    verifier_version: str | None = None


class AnnotatorProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_ref: str
    role: str


class EvaluationRecord(BaseModel):
    """Immutable evidence record joined to one materialized trajectory."""

    model_config = ConfigDict(extra="forbid")

    schema_name: str = EVALUATION_SCHEMA_NAME
    schema_version: str = EVALUATION_SCHEMA_VERSION
    evaluation_id: str
    trajectory_id: str
    source_event_id: str
    kind: EvaluationKind
    origin: EvaluationOrigin
    evaluator: EvaluatorIdentity
    annotator: AnnotatorProvenance | None = None
    input_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    output_label: str | None = None
    score: float | None = None
    chosen_ref: str | None = None
    rejected_ref: str | None = None
    reward: float | None = None
    confidence: float = Field(ge=0, le=1)
    supersedes_evaluation_id: str | None = None
    occurred_at: str
    observed_at: str
    late: bool
    capture: str
    body_ref: str
    body_sha256: str
    redaction_status: str
    redaction_policy_version: str
    access_tier: str
    content_sha256: str


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trajectory_id: str
    active_evaluation_ids: tuple[str, ...]
    superseded_evaluation_ids: tuple[str, ...]
    labels: tuple[str, ...]
    disagreement: bool
    has_late_records: bool
    access_tier: str


def _digest(record: EvaluationRecord) -> str:
    payload = record.model_dump(mode="json", exclude={"content_sha256"})
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _finalize(record: EvaluationRecord) -> EvaluationRecord:
    return record.model_copy(update={"content_sha256": _digest(record)})


def _event_to_record(
    event: TrajectoryEvent,
    trajectory: MaterializedTrajectory,
) -> EvaluationRecord | None:
    source_event_id = str(event.event_id)
    late = source_event_id in trajectory.late_event_ids
    payload = event.payload
    if isinstance(payload, EvaluationRecordedPayload):
        if payload.evaluation_kind not in {"evaluation", "verifier", "annotation"}:
            raise ValueError(f"unsupported evaluation kind: {payload.evaluation_kind}")
        kind = cast(EvaluationKind, payload.evaluation_kind)
        origin: EvaluationOrigin = (
            "human"
            if payload.annotator_ref or payload.annotator_role or event.actor.type == "human"
            else "automatic"
        )
        annotator = None
        if origin == "human":
            annotator = AnnotatorProvenance(
                actor_ref=payload.annotator_ref or event.actor.id,
                role=payload.annotator_role or event.actor.role,
            )
        record = EvaluationRecord(
            evaluation_id=payload.evaluation_id,
            trajectory_id=trajectory.trajectory_id,
            source_event_id=source_event_id,
            kind=kind,
            origin=origin,
            evaluator=EvaluatorIdentity(
                name=payload.evaluator or event.actor.id,
                implementation_version=payload.evaluator_version,
                rubric_version=payload.rubric_version,
                verifier_version=payload.verifier_version,
            ),
            annotator=annotator,
            input_refs=tuple(payload.input_refs),
            evidence_refs=tuple(sorted(set(payload.input_refs + event.provenance.input_refs))),
            output_label=payload.output_label,
            score=payload.score,
            chosen_ref=payload.chosen_ref,
            rejected_ref=payload.rejected_ref,
            reward=payload.reward,
            confidence=payload.confidence,
            supersedes_evaluation_id=payload.supersedes_ref,
            occurred_at=event.occurred_at.isoformat(),
            observed_at=event.observed_at.isoformat(),
            late=late,
            capture=event.content.capture,
            body_ref=event.content.body_ref,
            body_sha256=event.content.body_sha256,
            redaction_status=event.content.redaction.status,
            redaction_policy_version=event.content.redaction.policy_version,
            access_tier=payload.access_tier
            or ("restricted" if event.content.capture == "restricted_body" else "internal"),
            content_sha256="",
        )
        return _finalize(record)

    if isinstance(payload, HumanIntervenedPayload):
        record = EvaluationRecord(
            evaluation_id=f"intervention:{source_event_id}",
            trajectory_id=trajectory.trajectory_id,
            source_event_id=source_event_id,
            kind="human_intervention",
            origin="human",
            evaluator=EvaluatorIdentity(
                name=payload.actor_ref or event.actor.id,
                implementation_version="human",
            ),
            annotator=AnnotatorProvenance(
                actor_ref=payload.actor_ref or event.actor.id,
                role=payload.human_role or event.actor.role,
            ),
            input_refs=(payload.affected_ref,),
            evidence_refs=tuple(
                sorted({payload.affected_ref, payload.rationale_ref, *event.provenance.input_refs})
            ),
            output_label=payload.intervention_kind,
            confidence=1.0,
            occurred_at=event.occurred_at.isoformat(),
            observed_at=event.observed_at.isoformat(),
            late=late,
            capture=event.content.capture,
            body_ref=event.content.body_ref,
            body_sha256=event.content.body_sha256,
            redaction_status=event.content.redaction.status,
            redaction_policy_version=event.content.redaction.policy_version,
            access_tier=payload.access_tier
            or ("restricted" if event.content.capture == "restricted_body" else "internal"),
            content_sha256="",
        )
        return _finalize(record)
    return None


def assemble_evaluation_records(
    events: tuple[TrajectoryEvent, ...] | list[TrajectoryEvent],
    trajectories: tuple[MaterializedTrajectory, ...] | list[MaterializedTrajectory],
) -> tuple[EvaluationRecord, ...]:
    """Join evaluation-shaped raw events to stable materialized trajectories."""

    trajectory_by_event = {
        event_id: trajectory
        for trajectory in trajectories
        for event_id in trajectory.source_event_ids
    }
    records: list[EvaluationRecord] = []
    for event in events:
        trajectory = trajectory_by_event.get(str(event.event_id))
        if trajectory is None:
            continue
        record = _event_to_record(event, trajectory)
        if record is not None:
            records.append(record)
    return tuple(
        sorted(
            records,
            key=lambda record: (
                record.trajectory_id,
                record.occurred_at,
                record.evaluation_id,
            ),
        )
    )


def summarize_evaluations(
    trajectory_id: str,
    records: tuple[EvaluationRecord, ...] | list[EvaluationRecord],
) -> EvaluationSummary:
    relevant = [record for record in records if record.trajectory_id == trajectory_id]
    superseded = {
        record.supersedes_evaluation_id
        for record in relevant
        if record.supersedes_evaluation_id is not None
    }
    active = [record for record in relevant if record.evaluation_id not in superseded]
    labels = tuple(
        sorted({record.output_label for record in active if record.output_label is not None})
    )
    access_tier = (
        "restricted"
        if any(record.access_tier == "restricted" for record in relevant)
        else "internal"
    )
    return EvaluationSummary(
        trajectory_id=trajectory_id,
        active_evaluation_ids=tuple(record.evaluation_id for record in active),
        superseded_evaluation_ids=tuple(sorted(superseded)),
        labels=labels,
        disagreement=len(labels) > 1,
        has_late_records=any(record.late for record in relevant),
        access_tier=access_tier,
    )


class EvaluationStore:
    """Append-only evaluator and annotation evidence store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        if self._initialized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS evaluation_records (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_id TEXT NOT NULL UNIQUE,
                    trajectory_id TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    access_tier TEXT NOT NULL,
                    supersedes_evaluation_id TEXT,
                    content_sha256 TEXT NOT NULL,
                    record BLOB NOT NULL
                );

                CREATE INDEX IF NOT EXISTS evaluation_records_trajectory_idx
                    ON evaluation_records(trajectory_id, sequence);

                CREATE TRIGGER IF NOT EXISTS evaluation_records_no_update
                    BEFORE UPDATE ON evaluation_records
                    BEGIN SELECT RAISE(ABORT, 'evaluation records are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS evaluation_records_no_delete
                    BEFORE DELETE ON evaluation_records
                    BEGIN SELECT RAISE(ABORT, 'evaluation records are append-only'); END;
                """)
        self._initialized = True

    def save(self, record: EvaluationRecord) -> EvaluationRecord:
        self.initialize()
        connection = self._connect()
        try:
            return self._save_on(connection, record)
        finally:
            connection.close()

    def _save_on(
        self, connection: sqlite3.Connection, record: EvaluationRecord
    ) -> EvaluationRecord:
        """Append one record on a caller-owned connection, so a batch connects once."""

        raw = json.dumps(
            record.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        with connection:
            existing = connection.execute(
                "SELECT content_sha256, record FROM evaluation_records WHERE evaluation_id = ?",
                (record.evaluation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["content_sha256"]) != record.content_sha256:
                    raise ValueError(
                        f"evaluation id {record.evaluation_id} already has different evidence"
                    )
                return EvaluationRecord.model_validate_json(bytes(existing["record"]))
            connection.execute(
                """
                INSERT INTO evaluation_records (
                    evaluation_id, trajectory_id, source_event_id, kind, origin,
                    occurred_at, observed_at, access_tier,
                    supersedes_evaluation_id, content_sha256, record
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.evaluation_id,
                    record.trajectory_id,
                    record.source_event_id,
                    record.kind,
                    record.origin,
                    record.occurred_at,
                    record.observed_at,
                    record.access_tier,
                    record.supersedes_evaluation_id,
                    record.content_sha256,
                    raw,
                ),
            )
        return record

    def save_all(self, records: tuple[EvaluationRecord, ...]) -> tuple[EvaluationRecord, ...]:
        """Append a batch over one connection, for the same reason as the sibling store."""

        if not records:
            return ()
        self.initialize()
        connection = self._connect()
        try:
            return tuple(self._save_on(connection, record) for record in records)
        finally:
            connection.close()

    def for_trajectory(self, trajectory_id: str) -> tuple[EvaluationRecord, ...]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT record
                FROM evaluation_records
                WHERE trajectory_id = ?
                ORDER BY sequence
                """,
                (trajectory_id,),
            ).fetchall()
        return tuple(EvaluationRecord.model_validate_json(bytes(row["record"])) for row in rows)

    def all(self) -> tuple[EvaluationRecord, ...]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT record FROM evaluation_records ORDER BY trajectory_id, sequence"
            ).fetchall()
        return tuple(EvaluationRecord.model_validate_json(bytes(row["record"])) for row in rows)
