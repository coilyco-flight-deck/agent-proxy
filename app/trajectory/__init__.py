"""Versioned trajectory event contracts shared by producers and consumers."""

from app.trajectory.schema import (
    CURRENT_SCHEMA_NAME,
    CURRENT_SCHEMA_VERSION,
    TrajectoryEvent,
    canonical_event_bytes,
    event_json_schema,
    validate_event,
)

__all__ = [
    "CURRENT_SCHEMA_NAME",
    "CURRENT_SCHEMA_VERSION",
    "TrajectoryEvent",
    "canonical_event_bytes",
    "event_json_schema",
    "validate_event",
]
