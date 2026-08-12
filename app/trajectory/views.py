"""Governed operational views and Ward dossier evidence inputs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.trajectory.evaluation import EvaluationRecord, summarize_evaluations
from app.trajectory.materialize import MaterializedTrajectory

ViewName = Literal[
    "reliability", "cost_latency", "policy", "evaluation", "harness_fit", "skill_fit"
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class QueryContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ViewName
    schema_name: str
    schema_version: str
    purpose: str
    required_sources: tuple[str, ...]


class AccessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_tiers: tuple[str, ...] = ("internal",)

    def permits(self, tier: str) -> bool:
        return tier in self.allowed_tiers


class ViewFreshness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: str
    source_materialized_at: str
    complete_through: str
    age_seconds: float
    backfill_source: str
    reconstruction_limit: str


class TraceJoin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_ids: tuple[str, ...]
    span_ids: tuple[str, ...]


class OperationalView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: QueryContract
    freshness: ViewFreshness
    access_tier: str
    source_trajectory_ids: tuple[str, ...]
    source_content_sha256: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]


class WardDossierInput(BaseModel):
    """Evidence-only input. This record can never authorize a Ward action."""

    model_config = ConfigDict(extra="forbid")

    schema_name: str = "agentproxy.ward.dossier-input"
    schema_version: str = "1.0"
    trajectory_id: str
    repository: tuple[str, ...]
    issue_refs: tuple[str, ...]
    workflows: tuple[str, ...]
    ward_run_ids: tuple[str, ...]
    status: str
    reliability: dict[str, Any]
    evaluation: dict[str, Any]
    trace_join: TraceJoin
    evidence_content_sha256: str
    access_tier: str
    may_authorize: bool = False


_CONTRACTS: dict[ViewName, QueryContract] = {
    "reliability": QueryContract(
        name="reliability",
        schema_name="agentproxy.view.reliability",
        schema_version="1.0",
        purpose="Completion, partial state, retry, fallback, and intervention evidence.",
        required_sources=("materialized trajectories",),
    ),
    "cost_latency": QueryContract(
        name="cost_latency",
        schema_name="agentproxy.view.cost-latency",
        schema_version="1.0",
        purpose="Model, provider, token, latency, and cost evidence.",
        required_sources=("materialized trajectories",),
    ),
    "policy": QueryContract(
        name="policy",
        schema_name="agentproxy.view.policy",
        schema_version="1.0",
        purpose="Observed policy decisions without authorization semantics.",
        required_sources=("materialized trajectories",),
    ),
    "evaluation": QueryContract(
        name="evaluation",
        schema_name="agentproxy.view.evaluation",
        schema_version="1.0",
        purpose="Active labels, disagreement, supersession, and late evidence.",
        required_sources=("materialized trajectories", "evaluation records"),
    ),
    "harness_fit": QueryContract(
        name="harness_fit",
        schema_name="agentproxy.view.harness-fit",
        schema_version="1.0",
        purpose="Comparative reliability, retry, fallback, latency, and evaluation evidence.",
        required_sources=("materialized trajectories", "evaluation records"),
    ),
    "skill_fit": QueryContract(
        name="skill_fit",
        schema_name="agentproxy.view.skill-fit",
        schema_version="1.0",
        purpose="Observed skill selection and use against reliability and evaluation evidence.",
        required_sources=("materialized trajectories", "evaluation records"),
    ),
}


def query_contracts() -> tuple[QueryContract, ...]:
    return tuple(_CONTRACTS[name] for name in _CONTRACTS)


class OperationalViewBuilder:
    def __init__(
        self,
        trajectories: tuple[MaterializedTrajectory, ...] | list[MaterializedTrajectory],
        evaluations: tuple[EvaluationRecord, ...] | list[EvaluationRecord],
        *,
        access_policy: AccessPolicy | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.trajectories = tuple(
            sorted(trajectories, key=lambda trajectory: trajectory.trajectory_id)
        )
        self.evaluations = tuple(evaluations)
        self.access_policy = access_policy or AccessPolicy()
        self._clock = clock

    def build(self, name: ViewName) -> OperationalView:
        visible = tuple(
            trajectory
            for trajectory in self.trajectories
            if self.access_policy.permits(trajectory.access_tier)
        )
        builders: dict[
            ViewName,
            Callable[[tuple[MaterializedTrajectory, ...]], tuple[dict[str, Any], ...]],
        ] = {
            "reliability": self._reliability,
            "cost_latency": self._cost_latency,
            "policy": self._policy,
            "evaluation": self._evaluation,
            "harness_fit": self._harness_fit,
            "skill_fit": self._skill_fit,
        }
        rows = builders[name](visible)
        return OperationalView(
            contract=_CONTRACTS[name],
            freshness=self._freshness(visible),
            access_tier=(
                "restricted"
                if any(trajectory.access_tier == "restricted" for trajectory in visible)
                else "internal"
            ),
            source_trajectory_ids=tuple(trajectory.trajectory_id for trajectory in visible),
            source_content_sha256=tuple(trajectory.content_sha256 for trajectory in visible),
            rows=rows,
        )

    def dossier(self, trajectory_id: str) -> WardDossierInput | None:
        trajectory = next(
            (
                candidate
                for candidate in self.trajectories
                if candidate.trajectory_id == trajectory_id
                and self.access_policy.permits(candidate.access_tier)
            ),
            None,
        )
        if trajectory is None:
            return None
        summary = summarize_evaluations(trajectory_id, self.evaluations)
        correlations = trajectory.correlations
        return WardDossierInput(
            trajectory_id=trajectory_id,
            repository=correlations.get("repository", ()),
            issue_refs=correlations.get("issue_ref", ()),
            workflows=correlations.get("workflow", ()),
            ward_run_ids=correlations.get("ward_run_id", ()),
            status=trajectory.status,
            reliability={
                "partial_reasons": trajectory.partial_reasons,
                "retry_count": trajectory.retry_count,
                "fallback_count": trajectory.fallback_count,
                "human_intervention_count": trajectory.human_intervention_count,
            },
            evaluation={
                "active_evaluation_ids": summary.active_evaluation_ids,
                "labels": summary.labels,
                "disagreement": summary.disagreement,
                "has_late_records": summary.has_late_records,
            },
            trace_join=TraceJoin(
                trace_ids=correlations.get("trace_id", ()),
                span_ids=correlations.get("span_id", ()),
            ),
            evidence_content_sha256=trajectory.content_sha256,
            access_tier=trajectory.access_tier,
            may_authorize=False,
        )

    def _base_row(self, trajectory: MaterializedTrajectory) -> dict[str, Any]:
        return {
            "trajectory_id": trajectory.trajectory_id,
            "repository": trajectory.correlations.get("repository", ()),
            "issue_refs": trajectory.correlations.get("issue_ref", ()),
            "workflows": trajectory.correlations.get("workflow", ()),
            "trace_join": TraceJoin(
                trace_ids=trajectory.correlations.get("trace_id", ()),
                span_ids=trajectory.correlations.get("span_id", ()),
            ).model_dump(mode="json"),
            "access_tier": trajectory.access_tier,
        }

    def _reliability(
        self, trajectories: tuple[MaterializedTrajectory, ...]
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                **self._base_row(trajectory),
                "status": trajectory.status,
                "partial_reasons": trajectory.partial_reasons,
                "retry_count": trajectory.retry_count,
                "fallback_count": trajectory.fallback_count,
                "human_intervention_count": trajectory.human_intervention_count,
                "late_event_count": len(trajectory.late_event_ids),
            }
            for trajectory in trajectories
        )

    def _cost_latency(
        self, trajectories: tuple[MaterializedTrajectory, ...]
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                **self._base_row(trajectory),
                "models": trajectory.models,
                "providers": trajectory.providers,
                "model_request_count": trajectory.model_request_count,
                "request_tokens": trajectory.request_tokens,
                "response_tokens": trajectory.response_tokens,
                "total_tokens": trajectory.total_tokens,
                "latency_ms": trajectory.latency_ms,
                "cost_by_currency": trajectory.cost_by_currency,
            }
            for trajectory in trajectories
        )

    def _policy(
        self, trajectories: tuple[MaterializedTrajectory, ...]
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                **self._base_row(trajectory),
                "policy_decisions": trajectory.policy_decisions,
                "observed_only": True,
                "may_authorize": False,
            }
            for trajectory in trajectories
        )

    def _evaluation(
        self, trajectories: tuple[MaterializedTrajectory, ...]
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                **self._base_row(trajectory),
                **summarize_evaluations(trajectory.trajectory_id, self.evaluations).model_dump(
                    mode="json"
                ),
            }
            for trajectory in trajectories
        )

    def _harness_fit(
        self, trajectories: tuple[MaterializedTrajectory, ...]
    ) -> tuple[dict[str, Any], ...]:
        groups: dict[tuple[str, str], list[MaterializedTrajectory]] = defaultdict(list)
        for trajectory in trajectories:
            harnesses = trajectory.harnesses or ("unknown",)
            models = trajectory.models or ("unknown",)
            for harness in harnesses:
                for model in models:
                    groups[(harness, model)].append(trajectory)
        rows: list[dict[str, Any]] = []
        for (harness, model), records in sorted(groups.items()):
            count = len(records)
            completed = sum(record.status == "complete" for record in records)
            costs: dict[str, Decimal] = {}
            for record in records:
                for currency, amount in record.cost_by_currency.items():
                    costs[currency] = costs.get(currency, Decimal(0)) + Decimal(amount)
            rows.append(
                {
                    "harness": harness,
                    "model": model,
                    "trajectory_count": count,
                    "completion_rate": completed / count,
                    "retry_count": sum(record.retry_count for record in records),
                    "fallback_count": sum(record.fallback_count for record in records),
                    "latency_ms": sum(record.latency_ms for record in records),
                    "cost_by_currency": {
                        currency: format(amount, "f") for currency, amount in sorted(costs.items())
                    },
                    "source_trajectory_ids": tuple(record.trajectory_id for record in records),
                    "access_tier": (
                        "restricted"
                        if any(record.access_tier == "restricted" for record in records)
                        else "internal"
                    ),
                }
            )
        return tuple(rows)

    def _skill_fit(
        self, trajectories: tuple[MaterializedTrajectory, ...]
    ) -> tuple[dict[str, Any], ...]:
        """Group observed skill evidence by skill, role, harness, and model.

        Selection and use are separate facts and are never collapsed. A skill
        the manifest selected but that no observation records is the
        missing-evidence case the issue asks to keep explicit, so it still
        produces a row with `observed_use` false and a zero use count.
        """
        groups: dict[tuple[str, str, str, str], list[MaterializedTrajectory]] = defaultdict(list)
        selected_only: set[tuple[str, str, str, str]] = set()
        for trajectory in trajectories:
            roles = trajectory.actor_roles or ("unknown",)
            harnesses = trajectory.harnesses or ("unknown",)
            models = trajectory.models or ("unknown",)
            skills = set(trajectory.skills_selected) | set(trajectory.skills_used)
            for skill in skills:
                for role in roles:
                    for harness in harnesses:
                        for model in models:
                            key = (skill, role, harness, model)
                            groups[key].append(trajectory)
                            if skill not in trajectory.skills_used:
                                selected_only.add(key)
        rows: list[dict[str, Any]] = []
        for key, records in sorted(groups.items()):
            skill, role, harness, model = key
            count = len(records)
            completed = sum(record.status == "complete" for record in records)
            use_count = sum(record.skill_use_counts.get(skill, 0) for record in records)
            evaluated = tuple(
                summarize_evaluations(record.trajectory_id, self.evaluations) for record in records
            )
            rows.append(
                {
                    "skill": skill,
                    "role": role,
                    "harness": harness,
                    "model": model,
                    "observed_use": any(skill in record.skills_used for record in records),
                    "observed_use_count": use_count,
                    "selected_without_observed_use": key in selected_only and use_count == 0,
                    "trajectory_count": count,
                    "completion_rate": completed / count,
                    "retry_count": sum(record.retry_count for record in records),
                    "fallback_count": sum(record.fallback_count for record in records),
                    "human_intervention_count": sum(
                        record.human_intervention_count for record in records
                    ),
                    "evaluation": {
                        "labels": sorted(
                            {label for summary in evaluated for label in summary.labels}
                        ),
                        "disagreement": any(summary.disagreement for summary in evaluated),
                        "has_late_records": any(summary.has_late_records for summary in evaluated),
                    },
                    "source_trajectory_ids": tuple(record.trajectory_id for record in records),
                    "access_tier": (
                        "restricted"
                        if any(record.access_tier == "restricted" for record in records)
                        else "internal"
                    ),
                }
            )
        return tuple(rows)

    def _freshness(self, trajectories: tuple[MaterializedTrajectory, ...]) -> ViewFreshness:
        now = self._clock().astimezone(timezone.utc)
        materialized_at = max(
            (trajectory.materialized_at for trajectory in trajectories),
            default=datetime.fromtimestamp(0, timezone.utc),
        )
        complete_through = min(
            (trajectory.watermark for trajectory in trajectories),
            default=datetime.fromtimestamp(0, timezone.utc),
        )
        return ViewFreshness(
            generated_at=now.isoformat(),
            source_materialized_at=materialized_at.isoformat(),
            complete_through=complete_through.isoformat(),
            age_seconds=max(0.0, (now - materialized_at).total_seconds()),
            backfill_source="immutable raw trajectory ledger",
            reconstruction_limit=(
                "Only retained contract-v1 events inside the selected access tier "
                "can be reconstructed. Missing or expired source bodies remain missing."
            ),
        )
