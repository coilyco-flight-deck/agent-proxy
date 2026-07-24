"""Shared helpers for cold-path trajectory event producers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.trajectory.schema import (
    CURRENT_SCHEMA_NAME,
    CURRENT_SCHEMA_VERSION,
    TrajectoryEvent,
    validate_event,
)


def deterministic_uuid7(occurred_at: datetime, idempotency_key: str) -> UUID:
    """Derive a stable UUIDv7 from domain time and a producer idempotency key."""

    milliseconds = int(occurred_at.timestamp() * 1000)
    if milliseconds < 0 or milliseconds >= 1 << 48:
        raise ValueError("trajectory occurrence time is outside the UUIDv7 timestamp range")
    digest = bytearray(hashlib.sha256(idempotency_key.encode("utf-8")).digest()[:16])
    digest[0:6] = milliseconds.to_bytes(6, "big")
    digest[6] = (digest[6] & 0x0F) | 0x70
    digest[8] = (digest[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(digest))


@dataclass(frozen=True)
class ProducerContext:
    """Stable producer, actor, and optional domain-correlation metadata."""

    source_name: str
    source_version: str
    source_instance_id: str
    actor_type: str
    actor_id: str
    actor_role: str
    correlation: dict[str, str] = field(default_factory=dict)

    def event(
        self,
        *,
        event_type: str,
        occurred_at: datetime,
        idempotency_key: str,
        payload: dict[str, Any],
        attributes: dict[str, Any] | None = None,
        input_refs: list[str] | None = None,
        transform: str,
        transform_version: str,
        content_sha256: str = "",
    ) -> TrajectoryEvent:
        """Build and validate one metadata-only trajectory event."""

        return validate_event(
            {
                "event_id": str(deterministic_uuid7(occurred_at, idempotency_key)),
                "schema_name": CURRENT_SCHEMA_NAME,
                "schema_version": CURRENT_SCHEMA_VERSION,
                "event_type": event_type,
                "occurred_at": occurred_at,
                "observed_at": occurred_at,
                "source": {
                    "name": self.source_name,
                    "version": self.source_version,
                    "instance_id": self.source_instance_id,
                },
                "idempotency_key": idempotency_key,
                "correlation": self.correlation,
                "actor": {
                    "type": self.actor_type,
                    "id": self.actor_id,
                    "role": self.actor_role,
                },
                "attributes": attributes or {},
                "payload": payload,
                "content": {
                    "capture": "metadata_only",
                    "body_ref": "",
                    "body_sha256": "",
                    "redaction": {
                        "status": "not_captured",
                        "policy_version": "metadata-only-v1",
                    },
                },
                "provenance": {
                    "producer_event_ids": [],
                    "input_refs": input_refs or [],
                    "transform": transform,
                    "transform_version": transform_version,
                    "content_sha256": content_sha256,
                },
            }
        )
