"""Cold-path ingestion adapter for immutable agent-compose bundles."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.trajectory.producer import ProducerContext
from app.trajectory.schema import TrajectoryEvent
from app.trajectory.store import IngestResult, TrajectoryStore

ADAPTER_VERSION = "1"
MANIFEST_FORMAT = "agent-compose.bundle"
TRACE_FORMAT = "agent-compose.trace"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _read_object(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value, _canonical_json(value)


def _required_text(value: dict[str, Any], key: str, source: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{source}.{key} must be a non-empty string")
    return item


def _required_string_list(value: dict[str, Any], key: str, source: str) -> list[str]:
    item = value.get(key)
    if not isinstance(item, list) or any(not isinstance(entry, str) for entry in item):
        raise ValueError(f"{source}.{key} must be a string array")
    return item


def _bundle_time(manifest_path: Path, trace_path: Path) -> datetime:
    return datetime.fromtimestamp(
        max(manifest_path.stat().st_mtime, trace_path.stat().st_mtime),
        tz=timezone.utc,
    )


def events_from_agent_compose_bundle(
    bundle_dir: str | Path,
    *,
    correlation: dict[str, str] | None = None,
) -> tuple[TrajectoryEvent, ...]:
    """Translate one verified agent-compose bundle surface into trajectory events.

    The adapter reads only the two public bundle entry points. It does not inspect
    or duplicate the opaque context tree.
    """

    root = Path(bundle_dir)
    manifest_path = root / "manifest.json"
    trace_path = root / "trace.json"
    manifest, manifest_bytes = _read_object(manifest_path)
    trace, trace_bytes = _read_object(trace_path)

    if manifest.get("format") != MANIFEST_FORMAT:
        raise ValueError(f"manifest.json format must be {MANIFEST_FORMAT!r}")
    if trace.get("format") != TRACE_FORMAT:
        raise ValueError(f"trace.json format must be {TRACE_FORMAT!r}")

    role = _required_text(manifest, "role", "manifest")
    model_class = _required_text(manifest, "model_class", "manifest")
    personalities = _required_string_list(manifest, "personalities", "manifest")
    sources = _required_string_list(manifest, "sources", "manifest")
    decisions = trace.get("decisions")
    if not isinstance(decisions, list) or any(not isinstance(item, dict) for item in decisions):
        raise ValueError("trace.decisions must be an object array")

    bundle_bytes = manifest_bytes + b"\n" + trace_bytes
    bundle_digest = hashlib.sha256(bundle_bytes).hexdigest()
    occurred_at = _bundle_time(manifest_path, trace_path)
    bundle_ref = f"agent-compose:bundle:{bundle_digest}"
    context = ProducerContext(
        source_name="agent-compose",
        source_version=MANIFEST_FORMAT,
        source_instance_id="bundle-adapter",
        actor_type="agent",
        actor_id=f"agent-compose:{role}",
        actor_role=role,
        correlation=correlation or {},
    )

    capability_claims = sorted(
        {
            str(decision["subject"])
            for decision in decisions
            if decision.get("kind") == "skill"
            and decision.get("outcome") == "selected"
            and isinstance(decision.get("subject"), str)
        }
    )
    common_attributes = {
        "agentcompose.bundle.digest": bundle_digest,
        "agentcompose.bundle.format": MANIFEST_FORMAT,
        "agentcompose.model_class": model_class,
        "agentcompose.personalities": personalities,
        "agentcompose.sources": sources,
    }
    events = [
        context.event(
            event_type="actor.observed",
            occurred_at=occurred_at,
            idempotency_key=f"{bundle_ref}:actor",
            attributes=common_attributes,
            payload={
                "actor_ref": context.actor_id,
                "identity_source": "agent-compose.manifest",
                "role": role,
                "capability_claims": capability_claims,
                "retention_class": "trajectory",
                "access_tier": "public-safe",
            },
            input_refs=[f"{bundle_ref}:manifest", f"{bundle_ref}:trace"],
            transform="agent-compose.bundle-adapter",
            transform_version=ADAPTER_VERSION,
            content_sha256=bundle_digest,
        ),
        context.event(
            event_type="artifact.observed",
            occurred_at=occurred_at,
            idempotency_key=f"{bundle_ref}:artifact",
            attributes={
                **common_attributes,
                "agentcompose.delivery": manifest.get("delivery", {}),
                "agentcompose.decision_count": len(decisions),
            },
            payload={
                "artifact_ref": bundle_ref,
                "artifact_kind": "agent-compose.bundle",
                "media_type": "application/vnd.agent-compose.bundle+json",
                "content_sha256": bundle_digest,
                "size": len(bundle_bytes),
                "retention_class": "trajectory",
                "access_tier": "public-safe",
            },
            input_refs=[f"{bundle_ref}:manifest", f"{bundle_ref}:trace"],
            transform="agent-compose.bundle-adapter",
            transform_version=ADAPTER_VERSION,
            content_sha256=bundle_digest,
        ),
    ]

    for index, decision in enumerate(decisions):
        subject = _required_text(decision, "subject", f"trace.decisions[{index}]")
        outcome = _required_text(decision, "outcome", f"trace.decisions[{index}]")
        decision_bytes = _canonical_json(decision)
        decision_digest = hashlib.sha256(decision_bytes).hexdigest()
        decision_ref = f"{bundle_ref}:decision:{index}"
        events.append(
            context.event(
                event_type="observation.recorded",
                occurred_at=occurred_at,
                idempotency_key=f"{decision_ref}:{decision_digest}",
                attributes=common_attributes,
                payload={
                    "observation_kind": "agent-compose.selection-decision",
                    "observation_ref": decision_ref,
                    "subject_ref": subject,
                    "measured_facts": {
                        "kind": decision.get("kind", ""),
                        "source": decision.get("source", ""),
                        "outcome": outcome,
                        "reason": decision.get("reason", ""),
                    },
                    "retention_class": "trajectory",
                    "access_tier": "public-safe",
                },
                input_refs=[bundle_ref],
                transform="agent-compose.trace-adapter",
                transform_version=ADAPTER_VERSION,
                content_sha256=decision_digest,
            )
        )

    return tuple(events)


def ingest_agent_compose_bundle(
    bundle_dir: str | Path,
    store: TrajectoryStore,
    *,
    correlation: dict[str, str] | None = None,
) -> tuple[IngestResult, ...]:
    """Durably ingest all trajectory events derived from one bundle."""

    return tuple(
        store.ingest(event.model_dump(mode="json", exclude_none=True))
        for event in events_from_agent_compose_bundle(bundle_dir, correlation=correlation)
    )
