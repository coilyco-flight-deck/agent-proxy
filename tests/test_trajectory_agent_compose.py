"""Agent-compose bundle ingestion into the trajectory contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.trajectory.agent_compose import (
    events_from_agent_compose_bundle,
    ingest_agent_compose_bundle,
)
from app.trajectory.store import TrajectoryStore


def _bundle(root: Path) -> Path:
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "format": "agent-compose.bundle",
                "role": "engineer",
                "model_class": "frontier",
                "personalities": ["curious", "grounded", "meticulous"],
                "color": "#90a66a",
                "sources": ["person:kai", "aos-public"],
                "delivery": {
                    "mode": "native-skills",
                    "instructions": "content/instructions.md",
                    "skills_root": "content/skills",
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "trace.json").write_text(
        json.dumps(
            {
                "format": "agent-compose.trace",
                "decisions": [
                    {
                        "subject": "role:engineer",
                        "kind": "profile",
                        "source": "person:kai",
                        "outcome": "selected",
                        "reason": "The requested role activates its full personality set.",
                    },
                    {
                        "subject": "skill:coding-python",
                        "kind": "skill",
                        "source": "aos-public",
                        "outcome": "selected",
                        "reason": "The role selected this capability.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def test_bundle_adapter_emits_actor_artifact_and_decision_events(tmp_path):
    bundle = _bundle(tmp_path / "bundle")

    events = events_from_agent_compose_bundle(
        bundle,
        correlation={"ward_run_id": "run-1", "agent_session_id": "session-1"},
    )

    assert [event.event_type for event in events] == [
        "actor.observed",
        "artifact.observed",
        "observation.recorded",
        "observation.recorded",
    ]
    actor = events[0]
    assert actor.payload.capability_claims == ["skill:coding-python"]
    assert actor.correlation.ward_run_id == "run-1"
    assert actor.content.capture == "metadata_only"
    assert events[1].payload.artifact_kind == "agent-compose.bundle"
    assert events[2].payload.measured_facts["outcome"] == "selected"


def test_bundle_adapter_is_idempotent_in_durable_store(tmp_path):
    bundle = _bundle(tmp_path / "bundle")
    store = TrajectoryStore(tmp_path / "trajectory.sqlite3")

    first = ingest_agent_compose_bundle(bundle, store)
    second = ingest_agent_compose_bundle(bundle, store)

    assert {result.outcome for result in first} == {"accepted"}
    assert {result.outcome for result in second} == {"duplicate"}
    assert len(tuple(store.iter_events())) == len(first)


def test_bundle_adapter_rejects_unknown_contract(tmp_path):
    bundle = _bundle(tmp_path / "bundle")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    manifest["format"] = "agent-compose.bundle.v2"
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest.json format"):
        events_from_agent_compose_bundle(bundle)
