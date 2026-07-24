"""Pydantic implementation of the Agent Proxy trajectory contract v1."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CURRENT_SCHEMA_NAME = "agentproxy.trajectory.event"
CURRENT_SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_MAJOR = 1

EventType: TypeAlias = Literal[
    "actor.observed",
    "action.proposed",
    "policy.decided",
    "execution.started",
    "execution.completed",
    "execution.failed",
    "observation.recorded",
    "state.changed",
    "evaluation.recorded",
    "human.intervened",
    "artifact.created",
    "artifact.observed",
]
CaptureMode: TypeAlias = Literal["metadata_only", "redacted_body", "restricted_body"]
RedactionStatus: TypeAlias = Literal["not_captured", "redacted", "restricted", "withheld"]
PolicyDecision: TypeAlias = Literal["allow", "deny", "require_review", "defer"]

_SCHEMA_VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)"
    r"(?:\.(?P<patch>0|[1-9]\d*))?"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ContractModel(BaseModel):
    """Contract model that preserves compatible producer extensions."""

    model_config = ConfigDict(extra="allow")


class Source(ContractModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)


class Correlation(ContractModel):
    trace_id: str | None = None
    span_id: str | None = None
    ward_run_id: str | None = None
    episode_id: str | None = None
    agent_session_id: str | None = None
    request_id: str | None = None
    repository: str | None = None
    issue_ref: str | None = None
    workflow: str | None = None
    parent_event_id: str | None = None
    causation_event_id: str | None = None
    correlation_id: str | None = None


class Actor(ContractModel):
    type: str = Field(min_length=1)
    id: str = Field(min_length=1)
    role: str = Field(min_length=1)


class Redaction(ContractModel):
    status: RedactionStatus
    policy_version: str = Field(min_length=1)


class Content(ContractModel):
    capture: CaptureMode
    body_ref: str = ""
    body_sha256: str = ""
    redaction: Redaction

    @model_validator(mode="after")
    def validate_capture(self) -> "Content":
        if self.body_sha256 and not _SHA256_RE.fullmatch(self.body_sha256):
            raise ValueError("content.body_sha256 must be a lowercase SHA-256 digest")
        if self.capture == "metadata_only":
            if self.body_ref or self.body_sha256:
                raise ValueError("metadata-only events cannot retain a body reference or digest")
            if self.redaction.status not in {"not_captured", "withheld"}:
                raise ValueError("metadata-only events must mark content not captured or withheld")
        else:
            if not self.body_ref or not self.body_sha256:
                raise ValueError("captured bodies require body_ref and body_sha256")
            expected = "redacted" if self.capture == "redacted_body" else "restricted"
            if self.redaction.status != expected:
                raise ValueError(f"{self.capture} requires redaction status {expected}")
        return self


class Provenance(ContractModel):
    producer_event_ids: list[str] = Field(default_factory=list)
    input_refs: list[str] = Field(default_factory=list)
    transform: str = ""
    transform_version: str = ""
    content_sha256: str = ""

    @field_validator("content_sha256")
    @classmethod
    def validate_content_sha256(cls, value: str) -> str:
        if value and not _SHA256_RE.fullmatch(value):
            raise ValueError("provenance.content_sha256 must be a lowercase SHA-256 digest")
        return value


class Cost(ContractModel):
    amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    calculation_version: str = Field(min_length=1)

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        return value.upper()


class ModelExecution(ContractModel):
    model: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    provider_model: str = Field(min_length=1)
    request_tokens: int | None = Field(default=None, ge=0)
    response_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    cost: Cost | None = None
    retry_count: int = Field(default=0, ge=0)
    fallback_count: int = Field(default=0, ge=0)
    fallback_from: list[str] = Field(default_factory=list)
    finish_reason: str | None = None


class Payload(ContractModel):
    model_execution: ModelExecution | None = None
    retention_class: str | None = None
    access_tier: str | None = None


class ActorObservedPayload(Payload):
    actor_ref: str = Field(min_length=1)
    identity_source: str = Field(min_length=1)
    role: str = Field(min_length=1)
    capability_claims: list[str] = Field(default_factory=list)


class ActionProposedPayload(Payload):
    action_kind: str = Field(min_length=1)
    action_ref: str = Field(min_length=1)
    target_refs: list[str] = Field(min_length=1)
    intent: str = Field(min_length=1)
    before_state_ref: str | None = None


class PolicyDecidedPayload(Payload):
    decision: PolicyDecision
    policy_name: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    action_ref: str = Field(min_length=1)


class ExecutionPayload(Payload):
    execution_id: str = Field(min_length=1)
    executor_ref: str = Field(min_length=1)
    action_ref: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    error_class: str | None = None
    after_state_ref: str | None = None


class ObservationRecordedPayload(Payload):
    observation_kind: str = Field(min_length=1)
    observation_ref: str = Field(min_length=1)
    subject_ref: str = Field(min_length=1)
    measured_facts: dict[str, Any]


class StateChangedPayload(Payload):
    before_state_ref: str = Field(min_length=1)
    after_state_ref: str = Field(min_length=1)
    change_kind: str = Field(min_length=1)
    action_ref: str | None = None
    execution_ref: str | None = None

    @model_validator(mode="after")
    def validate_cause(self) -> "StateChangedPayload":
        if not self.action_ref and not self.execution_ref:
            raise ValueError("state changes require action_ref or execution_ref")
        return self


class EvaluationRecordedPayload(Payload):
    evaluation_id: str = Field(min_length=1)
    evaluator_version: str = Field(min_length=1)
    evaluator: str | None = None
    rubric_version: str | None = None
    verifier_version: str | None = None
    evaluation_kind: str = "evaluation"
    annotator_ref: str | None = None
    annotator_role: str | None = None
    chosen_ref: str | None = None
    rejected_ref: str | None = None
    reward: float | None = None
    input_refs: list[str] = Field(min_length=1)
    output_label: str | None = None
    score: float | None = None
    confidence: float = Field(ge=0, le=1)
    supersedes_ref: str | None = None

    @model_validator(mode="after")
    def validate_output(self) -> "EvaluationRecordedPayload":
        if self.output_label is None and self.score is None:
            raise ValueError("evaluations require output_label or score")
        return self


class HumanIntervenedPayload(Payload):
    intervention_kind: str = Field(min_length=1)
    human_role: str | None = None
    actor_ref: str | None = None
    rationale_ref: str = Field(min_length=1)
    affected_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_human(self) -> "HumanIntervenedPayload":
        if not self.human_role and not self.actor_ref:
            raise ValueError("human interventions require human_role or actor_ref")
        return self


class ArtifactPayload(Payload):
    artifact_ref: str = Field(min_length=1)
    artifact_kind: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    retention_class: str = Field(min_length=1)
    access_tier: str = Field(min_length=1)


EventPayload: TypeAlias = (
    ActorObservedPayload
    | ActionProposedPayload
    | PolicyDecidedPayload
    | ExecutionPayload
    | ObservationRecordedPayload
    | StateChangedPayload
    | EvaluationRecordedPayload
    | HumanIntervenedPayload
    | ArtifactPayload
)

_PAYLOAD_TYPES: dict[str, type[Payload]] = {
    "actor.observed": ActorObservedPayload,
    "action.proposed": ActionProposedPayload,
    "policy.decided": PolicyDecidedPayload,
    "execution.started": ExecutionPayload,
    "execution.completed": ExecutionPayload,
    "execution.failed": ExecutionPayload,
    "observation.recorded": ObservationRecordedPayload,
    "state.changed": StateChangedPayload,
    "evaluation.recorded": EvaluationRecordedPayload,
    "human.intervened": HumanIntervenedPayload,
    "artifact.created": ArtifactPayload,
    "artifact.observed": ArtifactPayload,
}


class TrajectoryEvent(ContractModel):
    """The canonical ``agentproxy.trajectory.event`` v1 envelope."""

    event_id: UUID
    schema_name: Literal["agentproxy.trajectory.event"]
    schema_version: str
    event_type: EventType
    occurred_at: datetime
    observed_at: datetime
    source: Source
    idempotency_key: str = Field(min_length=1)
    correlation: Correlation
    actor: Actor
    attributes: dict[str, Any]
    payload: EventPayload
    content: Content
    provenance: Provenance

    @field_validator("event_id")
    @classmethod
    def validate_uuid7(cls, value: UUID) -> UUID:
        if value.version != 7:
            raise ValueError("event_id must be UUIDv7")
        return value

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        match = _SCHEMA_VERSION_RE.fullmatch(value)
        if not match:
            raise ValueError("schema_version must be a semantic major.minor version")
        if int(match.group("major")) != SUPPORTED_SCHEMA_MAJOR:
            raise ValueError(f"unsupported trajectory schema major version: {value}")
        return value

    @field_validator("occurred_at", "observed_at")
    @classmethod
    def validate_utc_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trajectory timestamps must include a UTC offset")
        return value.astimezone(timezone.utc)

    @model_validator(mode="before")
    @classmethod
    def select_payload_model(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        event_type = value.get("event_type")
        payload = value.get("payload")
        payload_type = _PAYLOAD_TYPES.get(event_type) if isinstance(event_type, str) else None
        if payload_type is not None and isinstance(payload, dict):
            value = dict(value)
            value["payload"] = payload_type.model_validate(payload)
        return value

    @model_validator(mode="after")
    def validate_event_payload(self) -> "TrajectoryEvent":
        expected = _PAYLOAD_TYPES[self.event_type]
        if not isinstance(self.payload, expected):
            raise ValueError(f"{self.event_type} requires {expected.__name__}")
        if self.event_type == "execution.failed":
            if not isinstance(self.payload, ExecutionPayload) or not self.payload.error_class:
                raise ValueError("execution.failed requires payload.error_class")
        return self


def validate_event(payload: Any) -> TrajectoryEvent:
    """Validate one producer envelope against the supported v1 contract."""

    return TrajectoryEvent.model_validate(payload)


def event_json_schema() -> dict[str, Any]:
    """Return the interoperable JSON Schema for non-Python consumers."""

    return TrajectoryEvent.model_json_schema(mode="validation")


def canonical_event_bytes(event: TrajectoryEvent) -> bytes:
    """Serialize a validated envelope deterministically for hashing and retention."""

    payload = event.model_dump(mode="json", exclude_none=True)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
