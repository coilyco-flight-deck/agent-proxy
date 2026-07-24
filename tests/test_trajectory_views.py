"""Operational evidence views and Ward dossier inputs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.trajectory import (
    AccessPolicy,
    OperationalViewBuilder,
    TrajectoryMaterializer,
    assemble_evaluation_records,
    query_contracts,
    validate_event,
)

FIXTURES = Path("tests/fixtures/trajectory")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _policy_event() -> dict:
    payload = _fixture("valid.json")
    payload["event_id"] = "018f6d1d-5e54-7c20-bf7e-5bd1ca1e81a7"
    payload["event_type"] = "policy.decided"
    payload["idempotency_key"] = "fixture-policy"
    payload["payload"] = {
        "decision": "allow",
        "policy_name": "fixture-policy",
        "policy_version": "v1",
        "reason_code": "fixture",
        "action_ref": "action:fixture",
    }
    return payload


def _builder(include_restricted: bool = False):
    completed = _fixture("valid.json")
    completed["attributes"]["ward.harness"] = "codex"
    events = [
        validate_event(completed),
        validate_event(_policy_event()),
        validate_event(_fixture("evaluation-automatic.json")),
    ]
    trajectories = TrajectoryMaterializer().materialize(events)
    evaluations = assemble_evaluation_records(events, trajectories)
    allowed = ("internal", "restricted") if include_restricted else ("internal",)
    return OperationalViewBuilder(
        trajectories,
        evaluations,
        access_policy=AccessPolicy(allowed_tiers=allowed),
        clock=lambda: datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc),
    )


def test_query_contracts_cover_every_required_operational_dimension():
    assert [contract.name for contract in query_contracts()] == [
        "reliability",
        "cost_latency",
        "policy",
        "evaluation",
        "harness_fit",
    ]


def test_reliability_cost_policy_and_evaluation_views_preserve_trace_joins():
    builder = _builder()

    reliability = builder.build("reliability")
    cost = builder.build("cost_latency")
    policy = builder.build("policy")
    evaluation = builder.build("evaluation")

    assert reliability.rows[0]["retry_count"] == 1
    assert reliability.rows[0]["fallback_count"] == 0
    assert reliability.rows[0]["trace_join"]["trace_ids"] == ["fixture-trace"]
    assert cost.rows[0]["latency_ms"] == 812
    assert cost.rows[0]["total_tokens"] == 1540
    assert cost.rows[0]["cost_by_currency"] == {"USD": "0.000000"}
    assert policy.rows[0]["policy_decisions"] == {"allow": 1}
    assert policy.rows[0]["may_authorize"] is False
    assert evaluation.rows[0]["active_evaluation_ids"] == ["evaluation:auto:1"]


def test_harness_fit_compares_harness_and_model_without_raw_content():
    view = _builder().build("harness_fit")

    assert view.rows[0]["harness"] == "codex"
    assert view.rows[0]["model"] == "fixture-model"
    assert view.rows[0]["completion_rate"] == 1.0
    assert "body_ref" not in view.rows[0]


def test_dossier_is_evidence_only_and_links_to_otel_context():
    builder = _builder()
    trajectory_id = builder.trajectories[0].trajectory_id

    dossier = builder.dossier(trajectory_id)

    assert dossier is not None
    assert dossier.may_authorize is False
    assert dossier.repository == ("example/repository",)
    assert dossier.issue_refs == ("example/repository#42",)
    assert dossier.trace_join.trace_ids == ("fixture-trace",)
    assert dossier.evidence_content_sha256


def test_access_policy_filters_restricted_trajectories_before_rows():
    completed = validate_event(_fixture("valid.json"))
    restricted = validate_event(_fixture("evaluation-human.json"))
    trajectories = TrajectoryMaterializer().materialize([completed, restricted])
    evaluations = assemble_evaluation_records([completed, restricted], trajectories)

    internal = OperationalViewBuilder(trajectories, evaluations).build("evaluation")
    restricted_view = OperationalViewBuilder(
        trajectories,
        evaluations,
        access_policy=AccessPolicy(allowed_tiers=("internal", "restricted")),
    ).build("evaluation")

    assert internal.rows == ()
    assert len(restricted_view.rows) == 1
    assert restricted_view.access_tier == "restricted"


def test_freshness_names_backfill_and_reconstruction_limits():
    freshness = _builder().build("reliability").freshness

    assert freshness.age_seconds > 0
    assert freshness.backfill_source == "immutable raw trajectory ledger"
    assert "retained contract-v1 events" in freshness.reconstruction_limit
