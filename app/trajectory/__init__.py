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
from app.trajectory.datasets import (
    DatasetArtifact,
    DatasetArtifactStore,
    DatasetExporter,
    DatasetManifest,
    RedactionPolicy,
    SplitPolicy,
)
from app.trajectory.views import (
    AccessPolicy,
    OperationalView,
    OperationalViewBuilder,
    QueryContract,
    WardDossierInput,
    query_contracts,
)
from app.trajectory.store import (
    AsyncTrajectoryEmitter,
    IngestResult,
    ReplayResult,
    TrajectoryStore,
)
from app.trajectory.agent_compose import (
    events_from_agent_compose_bundle,
    ingest_agent_compose_bundle,
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
    "DatasetArtifact",
    "DatasetArtifactStore",
    "DatasetExporter",
    "DatasetManifest",
    "RedactionPolicy",
    "SplitPolicy",
    "AccessPolicy",
    "OperationalView",
    "OperationalViewBuilder",
    "QueryContract",
    "WardDossierInput",
    "query_contracts",
    "events_from_agent_compose_bundle",
    "ingest_agent_compose_bundle",
    "canonical_event_bytes",
    "event_json_schema",
    "validate_event",
]
