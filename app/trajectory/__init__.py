"""Versioned trajectory event contracts shared by producers and consumers."""

from app.trajectory.schema import (
    CURRENT_SCHEMA_NAME,
    CURRENT_SCHEMA_VERSION,
    TrajectoryEvent,
    canonical_event_bytes,
    event_json_schema,
    validate_event,
)
from app.trajectory.materialize import (
    MaterializationStore,
    MaterializedTrajectory,
    TrajectoryMaterializer,
    materialize_retained_events,
)
from app.trajectory.evaluation import (
    EvaluationRecord,
    EvaluationStore,
    EvaluationSummary,
    assemble_evaluation_records,
    summarize_evaluations,
)
from app.trajectory.store import (
    AsyncTrajectoryEmitter,
    IngestResult,
    ReplayResult,
    TrajectoryStore,
)

__all__ = [
    "CURRENT_SCHEMA_NAME",
    "CURRENT_SCHEMA_VERSION",
    "TrajectoryEvent",
    "TrajectoryStore",
    "AsyncTrajectoryEmitter",
    "IngestResult",
    "ReplayResult",
    "MaterializationStore",
    "MaterializedTrajectory",
    "TrajectoryMaterializer",
    "materialize_retained_events",
    "EvaluationRecord",
    "EvaluationStore",
    "EvaluationSummary",
    "assemble_evaluation_records",
    "summarize_evaluations",
    "canonical_event_bytes",
    "event_json_schema",
    "validate_event",
]
