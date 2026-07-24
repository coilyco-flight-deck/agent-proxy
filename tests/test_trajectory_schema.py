"""Producer and consumer checks for trajectory contract v1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.trajectory import (
    CURRENT_SCHEMA_NAME,
    TrajectoryEvent,
    canonical_event_bytes,
    event_json_schema,
    validate_event,
)

FIXTURES = Path("tests/fixtures/trajectory")


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    [
        "valid.json",
        "duplicate-original.json",
        "duplicate-redelivery.json",
        "late-event.json",
        "partial-trajectory.json",
        "replay.json",
        "redacted-body.json",
        "restricted-body.json",
    ],
)
def test_contract_v1_accepts_interoperability_fixtures(name: str):
    event = validate_event(_fixture(name))

    assert event.schema_name == CURRENT_SCHEMA_NAME
    assert event.event_id.version == 7


@pytest.mark.parametrize(
    "name",
    [
        "invalid-missing-event-id.json",
        "invalid-major-version.json",
        "invalid-body-capture.json",
    ],
)
def test_contract_v1_rejects_invalid_fixtures(name: str):
    with pytest.raises(ValidationError):
        validate_event(_fixture(name))


def test_duplicate_fixture_preserves_the_same_logical_identity():
    original = validate_event(_fixture("duplicate-original.json"))
    redelivery = validate_event(_fixture("duplicate-redelivery.json"))

    assert redelivery.event_id == original.event_id
    assert redelivery.source.name == original.source.name
    assert redelivery.idempotency_key == original.idempotency_key


def test_late_event_keeps_domain_time_distinct_from_observation_time():
    event = validate_event(_fixture("late-event.json"))

    assert event.occurred_at < event.observed_at


def test_partial_fixture_does_not_fabricate_terminal_correlation():
    event = validate_event(_fixture("partial-trajectory.json"))

    assert event.correlation.episode_id is None
    assert event.correlation.ward_run_id is None


def test_replay_preserves_original_identity_and_adds_provenance():
    event = validate_event(_fixture("replay.json"))

    assert event.provenance.producer_event_ids == ["018f6d1d-5e54-7c20-bf7e-5bd1ca1e8198"]
    assert event.provenance.transform == "raw-replay"


def test_unknown_optional_fields_survive_validation_and_serialization():
    payload = _fixture("valid.json")
    payload["producer_extension"] = {"future": True}
    payload["payload"]["producer_payload_extension"] = "kept"

    event = validate_event(payload)
    encoded = json.loads(canonical_event_bytes(event))

    assert encoded["producer_extension"] == {"future": True}
    assert encoded["payload"]["producer_payload_extension"] == "kept"


def test_canonical_bytes_are_stable_across_key_order():
    payload = _fixture("valid.json")
    reversed_payload = dict(reversed(list(payload.items())))

    assert canonical_event_bytes(validate_event(payload)) == canonical_event_bytes(
        validate_event(reversed_payload)
    )


def test_json_schema_exposes_the_event_and_payload_contracts():
    schema = event_json_schema()

    assert schema["title"] == TrajectoryEvent.__name__
    assert set(schema["required"]) >= {
        "event_id",
        "schema_name",
        "schema_version",
        "event_type",
        "payload",
    }
    assert "ArtifactPayload" in schema["$defs"]
