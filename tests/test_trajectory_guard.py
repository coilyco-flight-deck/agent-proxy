"""Cli-guard audit and specgen evidence ingestion."""

from __future__ import annotations

import json

import pytest

from app.trajectory.guard import (
    events_from_cli_guard_audit,
    ingest_guard_data,
    specgen_policy_snapshot,
)
from app.trajectory.schema import canonical_event_bytes
from app.trajectory.store import TrajectoryStore


def _audit_row(*, audit_id: str, decision: str, exit_code: int) -> dict:
    return {
        "id": audit_id,
        "ts": 1710000000,
        "version": "v0.test",
        "decision": decision,
        "verb": "repo.test",
        "argv": ["runner", "--token", "private-test-value"],
        "exit_code": exit_code,
        "stderr_tail": "private failure detail" if exit_code else "",
        "duration_ms": 42,
        "session_id": "fixture-session",
        "egress": [
            {
                "host": "private.test.invalid",
                "decision": "allow",
                "bytes_up": 10,
                "bytes_down": 20,
                "duration_ms": 2,
            }
        ],
        "profile_decision": {
            "allowed": decision == "accept",
            "profile": "engineer",
            "source": "fixture",
            "coordinate": {
                "data_security": "public",
                "blast_radius": "workspace",
                "network_egress": "allowlisted",
                "filesystem_reach": "repository",
            },
            "reason": "private policy detail",
        },
    }


def test_audit_adapter_emits_policy_and_execution_without_sensitive_bodies(tmp_path):
    path = tmp_path / "audit.jsonl"
    accepted = _audit_row(
        audit_id="018f6d1d-5e54-7c20-bf7e-5bd1ca1e8101",
        decision="accept",
        exit_code=1,
    )
    rejected = _audit_row(
        audit_id="018f6d1d-5e54-7c20-bf7e-5bd1ca1e8102",
        decision="reject",
        exit_code=126,
    )
    path.write_text(
        "\n".join(json.dumps(row) for row in (accepted, rejected)) + "\n",
        encoding="utf-8",
    )

    events = events_from_cli_guard_audit(path, policy_snapshot_ref="specgen:snapshot:test")

    assert [event.event_type for event in events] == [
        "action.proposed",
        "policy.decided",
        "execution.failed",
        "action.proposed",
        "policy.decided",
    ]
    assert events[1].payload.decision == "allow"
    assert events[-1].payload.decision == "deny"
    assert events[2].payload.error_class == "process_exit"
    retained = b"\n".join(canonical_event_bytes(event) for event in events)
    assert b"private-test-value" not in retained
    assert b"private failure detail" not in retained
    assert b"private.test.invalid" not in retained
    assert b"private policy detail" not in retained
    assert events[0].attributes["cli_guard.egress"]["allow_count"] == 1


def test_specgen_snapshot_hashes_artifacts_without_copying_content(tmp_path):
    project = tmp_path / ".specgen"
    project.mkdir()
    (project / "ops.kdl").write_text(
        'wrap aguard ops forgejo { grant "GET /repos/{owner}/{repo}" }\n',
        encoding="utf-8",
    )
    (project / "ops.lock.json").write_text('{"openapi":"3.0.0"}\n', encoding="utf-8")
    (project / "specverb.lock").write_text('{"version":2}\n', encoding="utf-8")

    snapshot = specgen_policy_snapshot(project)

    assert len(snapshot.events) == 4
    assert [event.event_type for event in snapshot.events] == [
        "artifact.observed",
        "artifact.observed",
        "artifact.observed",
        "observation.recorded",
    ]
    assert all(event.content.capture == "metadata_only" for event in snapshot.events)
    retained = b"\n".join(canonical_event_bytes(event) for event in snapshot.events)
    assert b"GET /repos" not in retained
    assert snapshot.ref.startswith("specgen:policy-snapshot:")


def test_guard_batch_is_idempotent_and_links_policy_snapshot(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    audit_path.write_text(
        json.dumps(
            _audit_row(
                audit_id="018f6d1d-5e54-7c20-bf7e-5bd1ca1e8103",
                decision="accept",
                exit_code=0,
            )
        )
        + "\n",
        encoding="utf-8",
    )
    project = tmp_path / ".specgen"
    project.mkdir()
    (project / "ops.kdl").write_text("wrap aguard ops forgejo {}\n", encoding="utf-8")
    store = TrajectoryStore(tmp_path / "trajectory.sqlite3")

    first = ingest_guard_data(store, audit_path=audit_path, specgen_root=project)
    second = ingest_guard_data(store, audit_path=audit_path, specgen_root=project)

    assert {result.outcome for result in first} == {"accepted"}
    assert {result.outcome for result in second} == {"duplicate"}
    audit_events = [
        event for event in store.iter_events() if event.source.name == "cli-guard.audit"
    ]
    assert audit_events
    assert all("cli_guard.policy_snapshot_ref" in event.attributes for event in audit_events)


def test_audit_adapter_rejects_malformed_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text("{bad\n", encoding="utf-8")

    with pytest.raises(ValueError, match="audit line 1"):
        events_from_cli_guard_audit(path)
