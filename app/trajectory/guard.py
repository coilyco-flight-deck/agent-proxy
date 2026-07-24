"""Cold-path adapters for cli-guard audit rows and specgen policy artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TypedDict

from app.trajectory.producer import ProducerContext
from app.trajectory.schema import TrajectoryEvent
from app.trajectory.store import IngestResult, TrajectoryStore

ADAPTER_VERSION = "1"
AUDIT_SOURCE_NAME = "cli-guard.audit"
SPECGEN_SOURCE_NAME = "cli-guard.specgen"


class _SpecgenFact(TypedDict):
    path: str
    sha256: str
    size: int


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _required_text(value: dict[str, Any], key: str, source: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{source}.{key} must be a non-empty string")
    return item


def _audit_time(row: dict[str, Any], line_number: int) -> datetime:
    value = row.get("ts")
    if not isinstance(value, (int, float)):
        raise ValueError(f"audit line {line_number}.ts must be a unix timestamp")
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _policy_outcome(row: dict[str, Any]) -> tuple[str, str, str]:
    profile = row.get("profile_decision")
    if isinstance(profile, dict) and isinstance(profile.get("allowed"), bool):
        allowed = bool(profile["allowed"])
        return (
            "allow" if allowed else "deny",
            "cli-guard.profile",
            "profile-allow" if allowed else "profile-deny",
        )
    decision = row.get("decision")
    if decision == "accept":
        return "allow", "cli-guard.argv", "cli-guard-accept"
    if decision == "reject":
        return "deny", "cli-guard.argv", "cli-guard-reject"
    return "defer", "cli-guard.argv", "unrecognized-audit-decision"


def _egress_summary(row: dict[str, Any]) -> dict[str, int]:
    egress = row.get("egress")
    if not isinstance(egress, list):
        return {"allow_count": 0, "deny_count": 0, "bytes_up": 0, "bytes_down": 0}
    valid = [item for item in egress if isinstance(item, dict)]
    return {
        "allow_count": sum(item.get("decision") == "allow" for item in valid),
        "deny_count": sum(item.get("decision") == "deny" for item in valid),
        "bytes_up": sum(
            value
            for item in valid
            if isinstance((value := item.get("bytes_up")), int) and value >= 0
        ),
        "bytes_down": sum(
            value
            for item in valid
            if isinstance((value := item.get("bytes_down")), int) and value >= 0
        ),
    }


def _audit_attributes(
    row: dict[str, Any],
    *,
    row_digest: str,
    policy_snapshot_ref: str | None,
) -> dict[str, Any]:
    raw_argv = row.get("argv")
    argv: list[Any] = raw_argv if isinstance(raw_argv, list) else []
    attributes: dict[str, Any] = {
        "cli_guard.audit.id": row["id"],
        "cli_guard.audit.digest": row_digest,
        "cli_guard.verb": row["verb"],
        "cli_guard.argv.count": len(argv),
        "cli_guard.argv.sha256": _digest(argv),
        "cli_guard.decision": row.get("decision", ""),
        "cli_guard.audit_override": bool(row.get("audit_override", False)),
        "cli_guard.policy_skipped": bool(row.get("policy_skipped", False)),
        "cli_guard.working_tree_dirty": bool(row.get("working_tree_status")),
        "cli_guard.cache": row.get("cache", ""),
        "cli_guard.egress": _egress_summary(row),
    }
    profile = row.get("profile_decision")
    if isinstance(profile, dict):
        coordinate = profile.get("coordinate")
        attributes["cli_guard.profile"] = {
            "name": profile.get("profile", ""),
            "allowed": profile.get("allowed"),
            "coordinate": coordinate if isinstance(coordinate, dict) else {},
        }
    if policy_snapshot_ref:
        attributes["cli_guard.policy_snapshot_ref"] = policy_snapshot_ref
    return attributes


def _audit_rows(path: str | Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"audit line {line_number} is not valid JSON") from exc
            if not isinstance(value, dict):
                raise ValueError(f"audit line {line_number} must contain a JSON object")
            yield line_number, value


def events_from_cli_guard_audit(
    audit_path: str | Path,
    *,
    actor_role: str = "agent",
    correlation: dict[str, str] | None = None,
    policy_snapshot_ref: str | None = None,
) -> tuple[TrajectoryEvent, ...]:
    """Translate append-only cli-guard audit rows into governed trajectory events."""

    events: list[TrajectoryEvent] = []
    base_correlation = correlation or {}
    for line_number, row in _audit_rows(audit_path):
        audit_id = _required_text(row, "id", f"audit line {line_number}")
        verb = _required_text(row, "verb", f"audit line {line_number}")
        occurred_at = _audit_time(row, line_number)
        source_version = row.get("version")
        if not isinstance(source_version, str) or not source_version:
            source_version = "unknown"
        row_digest = _digest(row)
        action_ref = f"cli-guard:audit:{audit_id}:action"
        execution_ref = f"cli-guard:audit:{audit_id}:execution"
        row_correlation = dict(base_correlation)
        session_id = row.get("session_id")
        if isinstance(session_id, str) and session_id:
            row_correlation.setdefault("agent_session_id", session_id)
        context = ProducerContext(
            source_name=AUDIT_SOURCE_NAME,
            source_version=source_version,
            source_instance_id="audit-adapter",
            actor_type="agent",
            actor_id="cli-guard:caller",
            actor_role=actor_role,
            correlation=row_correlation,
        )
        input_refs = [f"cli-guard:audit:{audit_id}"]
        if policy_snapshot_ref:
            input_refs.append(policy_snapshot_ref)
        attributes = _audit_attributes(
            row,
            row_digest=row_digest,
            policy_snapshot_ref=policy_snapshot_ref,
        )
        events.append(
            context.event(
                event_type="action.proposed",
                occurred_at=occurred_at,
                idempotency_key=f"{audit_id}:action",
                attributes=attributes,
                payload={
                    "action_kind": "guarded-command",
                    "action_ref": action_ref,
                    "target_refs": [f"cli-guard:verb:{verb}"],
                    "intent": "execute a cli-guard-governed command",
                    "retention_class": "trajectory",
                    "access_tier": "governed",
                },
                input_refs=input_refs,
                transform="cli-guard.audit-adapter",
                transform_version=ADAPTER_VERSION,
                content_sha256=row_digest,
            )
        )

        policy_decision, policy_name, reason_code = _policy_outcome(row)
        events.append(
            context.event(
                event_type="policy.decided",
                occurred_at=occurred_at,
                idempotency_key=f"{audit_id}:policy",
                attributes=attributes,
                payload={
                    "decision": policy_decision,
                    "policy_name": policy_name,
                    "policy_version": source_version,
                    "reason_code": reason_code,
                    "action_ref": action_ref,
                    "retention_class": "trajectory",
                    "access_tier": "governed",
                },
                input_refs=input_refs,
                transform="cli-guard.audit-adapter",
                transform_version=ADAPTER_VERSION,
                content_sha256=row_digest,
            )
        )

        if policy_decision != "allow":
            continue
        exit_code = row.get("exit_code")
        if not isinstance(exit_code, int):
            raise ValueError(f"audit line {line_number}.exit_code must be an integer")
        failed = exit_code != 0
        execution_attributes = {
            **attributes,
            "process.exit_code": exit_code,
            "process.duration_ms": row.get("duration_ms", 0),
            "process.stderr_tail.sha256": (
                _digest(row["stderr_tail"]) if isinstance(row.get("stderr_tail"), str) else ""
            ),
        }
        events.append(
            context.event(
                event_type="execution.failed" if failed else "execution.completed",
                occurred_at=occurred_at,
                idempotency_key=f"{audit_id}:execution",
                attributes=execution_attributes,
                payload={
                    "execution_id": execution_ref,
                    "executor_ref": "cli-guard",
                    "action_ref": action_ref,
                    "outcome": "failed" if failed else "succeeded",
                    "error_class": "process_exit" if failed else None,
                    "retention_class": "trajectory",
                    "access_tier": "governed",
                },
                input_refs=input_refs,
                transform="cli-guard.audit-adapter",
                transform_version=ADAPTER_VERSION,
                content_sha256=row_digest,
            )
        )
    return tuple(events)


@dataclass(frozen=True)
class SpecgenSnapshot:
    """One content-addressed specgen policy surface and its trajectory events."""

    ref: str
    events: tuple[TrajectoryEvent, ...]


def _specgen_artifacts(root: Path) -> tuple[Path, ...]:
    artifacts = {
        path
        for pattern in ("*.kdl", "*.lock.json", "specverb.lock")
        for path in root.rglob(pattern)
        if path.is_file() and ".git" not in path.parts
    }
    return tuple(sorted(artifacts, key=lambda path: path.relative_to(root).as_posix()))


def specgen_policy_snapshot(
    project_root: str | Path,
    *,
    correlation: dict[str, str] | None = None,
) -> SpecgenSnapshot:
    """Hash a specgen source surface and emit metadata-only artifact evidence."""

    root = Path(project_root)
    paths = _specgen_artifacts(root)
    if not paths:
        raise ValueError("specgen project contains no KDL or committed lock artifacts")
    facts: list[_SpecgenFact] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        facts.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
        )
    snapshot_digest = _digest(facts)
    snapshot_ref = f"specgen:policy-snapshot:{snapshot_digest}"
    occurred_at = datetime.fromtimestamp(
        max(path.stat().st_mtime for path in paths),
        tz=timezone.utc,
    )
    context = ProducerContext(
        source_name=SPECGEN_SOURCE_NAME,
        source_version="specgen-policy-v1",
        source_instance_id="policy-snapshot-adapter",
        actor_type="service",
        actor_id="specgen",
        actor_role="policy-compiler",
        correlation=correlation or {},
    )
    events = []
    for fact in facts:
        artifact_kind = "specgen.guardfile" if fact["path"].endswith(".kdl") else "specgen.lock"
        artifact_ref = f"{snapshot_ref}:{fact['path']}"
        events.append(
            context.event(
                event_type="artifact.observed",
                occurred_at=occurred_at,
                idempotency_key=artifact_ref,
                attributes={
                    "specgen.policy_snapshot_ref": snapshot_ref,
                    "specgen.artifact.path": fact["path"],
                },
                payload={
                    "artifact_ref": artifact_ref,
                    "artifact_kind": artifact_kind,
                    "media_type": (
                        "text/vnd.kdl"
                        if artifact_kind == "specgen.guardfile"
                        else "application/json"
                    ),
                    "content_sha256": fact["sha256"],
                    "size": fact["size"],
                    "retention_class": "policy-evidence",
                    "access_tier": "public-safe",
                },
                input_refs=[artifact_ref],
                transform="specgen.policy-snapshot-adapter",
                transform_version=ADAPTER_VERSION,
                content_sha256=fact["sha256"],
            )
        )
    events.append(
        context.event(
            event_type="observation.recorded",
            occurred_at=occurred_at,
            idempotency_key=f"{snapshot_ref}:summary",
            attributes={"specgen.policy_snapshot_ref": snapshot_ref},
            payload={
                "observation_kind": "specgen.policy-snapshot",
                "observation_ref": snapshot_ref,
                "subject_ref": "cli-guard:generated-policy-surface",
                "measured_facts": {
                    "artifact_count": len(facts),
                    "artifacts": facts,
                },
                "retention_class": "policy-evidence",
                "access_tier": "public-safe",
            },
            input_refs=[f"{snapshot_ref}:{fact['path']}" for fact in facts],
            transform="specgen.policy-snapshot-adapter",
            transform_version=ADAPTER_VERSION,
            content_sha256=snapshot_digest,
        )
    )
    return SpecgenSnapshot(ref=snapshot_ref, events=tuple(events))


def ingest_guard_data(
    store: TrajectoryStore,
    *,
    audit_path: str | Path | None = None,
    specgen_root: str | Path | None = None,
    actor_role: str = "agent",
    correlation: dict[str, str] | None = None,
) -> tuple[IngestResult, ...]:
    """Ingest a specgen snapshot and cli-guard audit stream as one cold-path batch."""

    if audit_path is None and specgen_root is None:
        raise ValueError("audit_path or specgen_root is required")
    events: list[TrajectoryEvent] = []
    snapshot_ref = None
    if specgen_root is not None:
        snapshot = specgen_policy_snapshot(specgen_root, correlation=correlation)
        snapshot_ref = snapshot.ref
        events.extend(snapshot.events)
    if audit_path is not None:
        events.extend(
            events_from_cli_guard_audit(
                audit_path,
                actor_role=actor_role,
                correlation=correlation,
                policy_snapshot_ref=snapshot_ref,
            )
        )
    return tuple(store.ingest(event.model_dump(mode="json", exclude_none=True)) for event in events)
