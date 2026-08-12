"""Skill identity through materialization and the governed skill-fit view (#70).

The prerequisite the issue names: the raw ledger retains `ward.skill`
observations and agent-compose selected-skill claims, but neither survived
materialization, so no view could group by skill. These tests hold both halves -
that the identity survives, and that selection and observed use stay separate
facts rather than collapsing into a single "the skill was involved" signal.
"""

from datetime import datetime, timedelta, timezone

from app.trajectory.materialize import MATERIALIZATION_SCHEMA_VERSION, MaterializedTrajectory
from app.trajectory.views import AccessPolicy, OperationalViewBuilder, query_contracts

_NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


def _trajectory(
    trajectory_id: str,
    *,
    selected: tuple[str, ...] = (),
    used: tuple[str, ...] = (),
    counts: dict[str, int] | None = None,
    status: str = "complete",
    roles: tuple[str, ...] = ("engineer",),
    harnesses: tuple[str, ...] = ("claude",),
    models: tuple[str, ...] = ("qwen3:4b",),
    retry: int = 0,
    fallback: int = 0,
) -> MaterializedTrajectory:
    return MaterializedTrajectory(
        trajectory_id=trajectory_id,
        status=status,
        partial_reasons=(),
        watermark=_NOW,
        materialized_at=_NOW,
        source_event_ids=(f"{trajectory_id}-e1",),
        late_event_ids=(),
        correlations={},
        event_type_counts={},
        retry_count=retry,
        fallback_count=fallback,
        model_request_count=1,
        request_tokens=1,
        response_tokens=1,
        total_tokens=2,
        latency_ms=10,
        cost_by_currency={},
        policy_decisions={},
        models=models,
        providers=("ollama",),
        harnesses=harnesses,
        actor_roles=roles,
        skills_selected=selected,
        skills_used=used,
        skill_use_counts=counts or {},
        human_intervention_count=0,
        access_tier="internal",
        content_sha256="",
    )


def _view(trajectories, evaluations=()):
    builder = OperationalViewBuilder(
        trajectories, evaluations, clock=lambda: _NOW + timedelta(minutes=1)
    )
    return builder.build("skill_fit")


def test_schema_version_records_the_additive_change():
    """Skill fields are additive, so the materialization schema minor bumps."""
    assert MATERIALIZATION_SCHEMA_VERSION == "1.1"


def test_skill_fit_contract_is_published():
    names = {contract.name for contract in query_contracts()}
    assert "skill_fit" in names


def test_defaults_keep_pre_1_1_records_loadable():
    """A record written before the skill fields existed must still validate."""
    record = _trajectory("t-old")
    assert record.skills_selected == ()
    assert record.skills_used == ()
    assert record.skill_use_counts == {}


def test_observed_use_is_grouped_by_skill_role_harness_and_model():
    view = _view(
        [
            _trajectory(
                "t-1",
                selected=("coding-python",),
                used=("coding-python",),
                counts={"coding-python": 3},
            )
        ]
    )
    assert len(view.rows) == 1
    row = view.rows[0]
    assert row["skill"] == "coding-python"
    assert row["role"] == "engineer"
    assert row["harness"] == "claude"
    assert row["model"] == "qwen3:4b"
    assert row["observed_use"] is True
    assert row["observed_use_count"] == 3
    assert row["selected_without_observed_use"] is False
    assert row["completion_rate"] == 1.0


def test_selected_without_use_stays_explicit_rather_than_disappearing():
    """The missing-evidence case the issue asks to keep visible.

    A manifest that selected a skill with no matching observation must still
    produce a row. Dropping it would read as "the skill was never selected",
    which is a different and wrong claim.
    """
    view = _view([_trajectory("t-2", selected=("coding-rust",), used=())])
    assert len(view.rows) == 1
    row = view.rows[0]
    assert row["skill"] == "coding-rust"
    assert row["observed_use"] is False
    assert row["observed_use_count"] == 0
    assert row["selected_without_observed_use"] is True


def test_use_without_selection_is_still_reported():
    """Ward observed a skill the manifest never claimed. Also real evidence."""
    view = _view([_trajectory("t-3", used=("coding-go",), counts={"coding-go": 1})])
    row = view.rows[0]
    assert row["skill"] == "coding-go"
    assert row["observed_use"] is True
    assert row["selected_without_observed_use"] is False


def test_counts_and_outcomes_aggregate_across_trajectories():
    view = _view(
        [
            _trajectory("t-4", used=("coding-python",), counts={"coding-python": 2}, retry=1),
            _trajectory(
                "t-5",
                used=("coding-python",),
                counts={"coding-python": 5},
                status="partial",
                fallback=2,
            ),
        ]
    )
    row = view.rows[0]
    assert row["trajectory_count"] == 2
    assert row["observed_use_count"] == 7
    assert row["completion_rate"] == 0.5
    assert row["retry_count"] == 1
    assert row["fallback_count"] == 2
    assert set(row["source_trajectory_ids"]) == {"t-4", "t-5"}


def test_restricted_evidence_is_withheld_from_the_default_policy():
    """The default policy permits only the internal tier, so a restricted
    trajectory yields no skill row at all rather than a redacted one."""
    restricted = _trajectory(
        "t-6", used=("coding-python",), counts={"coding-python": 1}
    ).model_copy(update={"access_tier": "restricted"})
    assert _view([restricted]).rows == ()


def test_restricted_access_tier_propagates_when_the_policy_allows_it():
    restricted = _trajectory(
        "t-6", used=("coding-python",), counts={"coding-python": 1}
    ).model_copy(update={"access_tier": "restricted"})
    builder = OperationalViewBuilder(
        [restricted],
        (),
        access_policy=AccessPolicy(allowed_tiers=("internal", "restricted")),
        clock=lambda: _NOW + timedelta(minutes=1),
    )
    view = builder.build("skill_fit")
    assert view.rows[0]["access_tier"] == "restricted"
    assert view.access_tier == "restricted"


def test_a_trajectory_with_no_skill_evidence_produces_no_rows():
    assert _view([_trajectory("t-7")]).rows == ()
