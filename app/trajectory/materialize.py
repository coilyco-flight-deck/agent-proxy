"""Deterministic cold-path episode and trajectory materialization."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.trajectory.schema import TrajectoryEvent
from app.trajectory.store import TrajectoryStore

MATERIALIZATION_SCHEMA_NAME = "agentproxy.trajectory.materialized"
MATERIALIZATION_SCHEMA_VERSION = "1.1"

_STRONG_CORRELATIONS = (
    "episode_id",
    "ward_run_id",
    "agent_session_id",
    "request_id",
    "trace_id",
)
_ALL_CORRELATIONS = (
    "trace_id",
    "span_id",
    "ward_run_id",
    "episode_id",
    "agent_session_id",
    "request_id",
    "repository",
    "issue_ref",
    "workflow",
    "parent_event_id",
    "causation_event_id",
    "correlation_id",
)
_TERMINAL_EVENTS = {"execution.completed", "execution.failed", "human.intervened"}


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MaterializedTrajectory(BaseModel):
    """Versioned, provenance-preserving reconstruction of correlated raw events."""

    model_config = ConfigDict(extra="forbid")

    schema_name: str = MATERIALIZATION_SCHEMA_NAME
    schema_version: str = MATERIALIZATION_SCHEMA_VERSION
    trajectory_id: str
    revision: int = Field(default=1, ge=1)
    status: str
    partial_reasons: tuple[str, ...]
    watermark: datetime
    materialized_at: datetime
    source_event_ids: tuple[str, ...]
    late_event_ids: tuple[str, ...]
    correlations: dict[str, tuple[str, ...]]
    event_type_counts: dict[str, int]
    retry_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)
    model_request_count: int = Field(ge=0)
    request_tokens: int = Field(ge=0)
    response_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    cost_by_currency: dict[str, str]
    policy_decisions: dict[str, int]
    models: tuple[str, ...]
    providers: tuple[str, ...]
    harnesses: tuple[str, ...]
    actor_roles: tuple[str, ...]
    skills_selected: tuple[str, ...] = ()
    skills_used: tuple[str, ...] = ()
    skill_use_counts: dict[str, int] = Field(default_factory=dict)
    human_intervention_count: int = Field(ge=0)
    access_tier: str
    content_sha256: str


def _record_payload(
    record: MaterializedTrajectory,
    *,
    exclude_revision: bool = False,
) -> dict[str, Any]:
    excluded = {"content_sha256"}
    if exclude_revision:
        excluded.add("revision")
    return record.model_dump(mode="json", exclude=excluded)


def _payload_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _finalize(record: MaterializedTrajectory) -> MaterializedTrajectory:
    return record.model_copy(update={"content_sha256": _payload_digest(_record_payload(record))})


def _semantic_digest(record: MaterializedTrajectory) -> str:
    return _payload_digest(_record_payload(record, exclude_revision=True))


class TrajectoryMaterializer:
    """Assemble connected event components without touching the request path."""

    def __init__(self, *, allowed_lateness: timedelta = timedelta(minutes=5)) -> None:
        if allowed_lateness < timedelta(0):
            raise ValueError("allowed_lateness cannot be negative")
        self.allowed_lateness = allowed_lateness

    def materialize(
        self, events: tuple[TrajectoryEvent, ...] | list[TrajectoryEvent]
    ) -> tuple[MaterializedTrajectory, ...]:
        ordered = sorted(events, key=lambda event: str(event.event_id))
        if not ordered:
            return ()

        parents = list(range(len(ordered)))

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root == right_root:
                return
            parents[max(left_root, right_root)] = min(left_root, right_root)

        seen: dict[tuple[str, str], int] = {}
        for index, event in enumerate(ordered):
            for field in _STRONG_CORRELATIONS:
                value = getattr(event.correlation, field)
                if not value:
                    continue
                key = (field, value)
                if key in seen:
                    union(index, seen[key])
                else:
                    seen[key] = index

        groups: dict[int, list[TrajectoryEvent]] = {}
        for index, event in enumerate(ordered):
            groups.setdefault(find(index), []).append(event)

        records = tuple(self._materialize_group(group) for group in groups.values())
        return tuple(sorted(records, key=lambda record: record.trajectory_id))

    def _materialize_group(self, events: list[TrajectoryEvent]) -> MaterializedTrajectory:
        events.sort(
            key=lambda event: (
                event.occurred_at,
                event.observed_at,
                str(event.event_id),
            )
        )
        correlation_values = {
            field: tuple(
                sorted(
                    {
                        value
                        for event in events
                        if (value := getattr(event.correlation, field)) is not None
                    }
                )
            )
            for field in _ALL_CORRELATIONS
        }
        correlation_values = {
            field: values for field, values in correlation_values.items() if values
        }
        identity = [
            f"{field}:{value}"
            for field in _STRONG_CORRELATIONS
            for value in correlation_values.get(field, ())
        ]
        if not identity:
            identity = [f"event:{events[0].event_id}"]
        trajectory_id = (
            "traj-" + hashlib.sha256("|".join(identity).encode("utf-8")).hexdigest()[:32]
        )

        max_observed = max(event.observed_at for event in events)
        watermark = max_observed - self.allowed_lateness
        late_event_ids = tuple(
            str(event.event_id)
            for event in events
            if event.observed_at - event.occurred_at > self.allowed_lateness
        )
        event_types = Counter(event.event_type for event in events)
        partial_reasons: list[str] = []
        if not any(event.event_type in _TERMINAL_EVENTS for event in events):
            partial_reasons.append("missing_terminal_event")
        if not any(correlation_values.get(field) for field in _STRONG_CORRELATIONS):
            partial_reasons.append("missing_primary_correlation")

        retry_count = 0
        fallback_count = 0
        model_request_count = 0
        request_tokens = 0
        response_tokens = 0
        total_tokens = 0
        latency_ms = 0
        costs: dict[str, Decimal] = {}
        policy_decisions: Counter[str] = Counter()
        models: set[str] = set()
        providers: set[str] = set()
        harnesses: set[str] = set()
        actor_roles: set[str] = set()
        skills_selected: set[str] = set()
        skill_use_counts: Counter[str] = Counter()
        access_tier = "internal"
        for event in events:
            actor_roles.add(event.actor.role)
            harness = event.attributes.get("ward.harness") or event.attributes.get(
                "agentproxy.harness"
            )
            if isinstance(harness, str) and harness:
                harnesses.add(harness)
            model_execution = event.payload.model_execution
            if model_execution is not None:
                model_request_count += 1
                retry_count += model_execution.retry_count
                fallback_count += model_execution.fallback_count
                request_tokens += model_execution.request_tokens or 0
                response_tokens += model_execution.response_tokens or 0
                total_tokens += model_execution.total_tokens or 0
                latency_ms += model_execution.latency_ms or 0
                models.add(model_execution.model)
                providers.add(model_execution.provider)
                if model_execution.cost is not None:
                    currency = model_execution.cost.currency
                    costs[currency] = costs.get(currency, Decimal(0)) + model_execution.cost.amount
            skill = event.attributes.get("ward.skill")
            if isinstance(skill, str) and skill:
                count = event.attributes.get("ward.skill.count")
                skill_use_counts[skill] += count if isinstance(count, int) and count > 0 else 1
            payload = event.payload.model_dump(mode="json", exclude_none=True)
            for claim in payload.get("capability_claims") or ():
                if isinstance(claim, str) and claim:
                    skills_selected.add(claim)
            if event.event_type == "policy.decided":
                decision = payload.get("decision")
                if isinstance(decision, str):
                    policy_decisions[decision] += 1
            event_access = str(payload.get("access_tier") or "internal")
            if event.content.capture == "restricted_body" or event_access == "restricted":
                access_tier = "restricted"

        record = MaterializedTrajectory(
            trajectory_id=trajectory_id,
            status="partial" if partial_reasons else "complete",
            partial_reasons=tuple(partial_reasons),
            watermark=watermark,
            materialized_at=max_observed,
            source_event_ids=tuple(str(event.event_id) for event in events),
            late_event_ids=late_event_ids,
            correlations=correlation_values,
            event_type_counts=dict(sorted(event_types.items())),
            retry_count=retry_count,
            fallback_count=fallback_count,
            model_request_count=model_request_count,
            request_tokens=request_tokens,
            response_tokens=response_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            cost_by_currency={
                currency: format(amount, "f") for currency, amount in sorted(costs.items())
            },
            policy_decisions=dict(sorted(policy_decisions.items())),
            models=tuple(sorted(models)),
            providers=tuple(sorted(providers)),
            harnesses=tuple(sorted(harnesses)),
            actor_roles=tuple(sorted(actor_roles)),
            skills_selected=tuple(sorted(skills_selected)),
            skills_used=tuple(sorted(skill_use_counts)),
            skill_use_counts=dict(sorted(skill_use_counts.items())),
            human_intervention_count=event_types["human.intervened"],
            access_tier=access_tier,
            content_sha256="",
        )
        return _finalize(record)


class MaterializationStore:
    """Append-only revision store for deterministic trajectory reconstructions."""

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
                CREATE TABLE IF NOT EXISTS trajectory_materializations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    trajectory_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    materialized_at TEXT NOT NULL,
                    watermark TEXT NOT NULL,
                    semantic_sha256 TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    record BLOB NOT NULL,
                    UNIQUE (trajectory_id, revision)
                );

                CREATE INDEX IF NOT EXISTS trajectory_materializations_latest_idx
                    ON trajectory_materializations(trajectory_id, revision DESC);

                CREATE TRIGGER IF NOT EXISTS trajectory_materializations_no_update
                    BEFORE UPDATE ON trajectory_materializations
                    BEGIN SELECT RAISE(ABORT, 'materializations are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS trajectory_materializations_no_delete
                    BEFORE DELETE ON trajectory_materializations
                    BEGIN SELECT RAISE(ABORT, 'materializations are append-only'); END;
                """)
        self._initialized = True

    def save(self, record: MaterializedTrajectory) -> MaterializedTrajectory:
        self.initialize()
        semantic = _semantic_digest(record)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                """
                SELECT revision, semantic_sha256, record
                FROM trajectory_materializations
                WHERE trajectory_id = ?
                ORDER BY revision DESC
                LIMIT 1
                """,
                (record.trajectory_id,),
            ).fetchone()
            if latest is not None and str(latest["semantic_sha256"]) == semantic:
                return MaterializedTrajectory.model_validate_json(bytes(latest["record"]))
            revision = int(latest["revision"]) + 1 if latest is not None else 1
            versioned = _finalize(record.model_copy(update={"revision": revision}))
            raw = json.dumps(
                versioned.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            connection.execute(
                """
                INSERT INTO trajectory_materializations (
                    trajectory_id, revision, schema_version, status,
                    materialized_at, watermark, semantic_sha256,
                    content_sha256, record
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    versioned.trajectory_id,
                    versioned.revision,
                    versioned.schema_version,
                    versioned.status,
                    _timestamp(versioned.materialized_at),
                    _timestamp(versioned.watermark),
                    semantic,
                    versioned.content_sha256,
                    raw,
                ),
            )
        return versioned

    def save_all(
        self, records: tuple[MaterializedTrajectory, ...]
    ) -> tuple[MaterializedTrajectory, ...]:
        return tuple(self.save(record) for record in records)

    def latest(self, trajectory_id: str) -> MaterializedTrajectory | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT record
                FROM trajectory_materializations
                WHERE trajectory_id = ?
                ORDER BY revision DESC
                LIMIT 1
                """,
                (trajectory_id,),
            ).fetchone()
        if row is None:
            return None
        return MaterializedTrajectory.model_validate_json(bytes(row["record"]))

    def revisions(self, trajectory_id: str) -> tuple[MaterializedTrajectory, ...]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT record
                FROM trajectory_materializations
                WHERE trajectory_id = ?
                ORDER BY revision
                """,
                (trajectory_id,),
            ).fetchall()
        return tuple(
            MaterializedTrajectory.model_validate_json(bytes(row["record"])) for row in rows
        )

    def latest_all(self) -> tuple[MaterializedTrajectory, ...]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT materialized.record
                FROM trajectory_materializations AS materialized
                INNER JOIN (
                    SELECT trajectory_id, MAX(revision) AS revision
                    FROM trajectory_materializations
                    GROUP BY trajectory_id
                ) AS latest
                ON latest.trajectory_id = materialized.trajectory_id
                AND latest.revision = materialized.revision
                ORDER BY materialized.trajectory_id
                """).fetchall()
        return tuple(
            MaterializedTrajectory.model_validate_json(bytes(row["record"])) for row in rows
        )


def materialize_retained_events(
    raw_store: TrajectoryStore,
    derived_store: MaterializationStore,
    *,
    allowed_lateness: timedelta = timedelta(minutes=5),
) -> tuple[MaterializedTrajectory, ...]:
    """Rebuild and append changed derived revisions from immutable raw events."""

    materializer = TrajectoryMaterializer(allowed_lateness=allowed_lateness)
    return derived_store.save_all(materializer.materialize(tuple(raw_store.iter_events())))
