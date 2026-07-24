"""Ward reap skill-use ingestion."""

import json
from pathlib import Path

from app.obs import ward_skill_use_total
from app.skill_use import (
    ingest_skill_use_source,
    parse_skill_use_artifact,
    skill_use_trajectory_events,
)
from app.trajectory.store import TrajectoryStore


def test_parse_skill_use_artifact_normalizes_fixture():
    payload = Path("tests/fixtures/ward_skill_use.json").read_text(encoding="utf-8")
    records = parse_skill_use_artifact(json.loads(payload))

    assert [record.skill for record in records] == [
        "repo-agent-proxy",
        "tooling-ward-framing",
    ]
    assert [record.count for record in records] == [2, 1]
    first = records[0]
    assert first.run_id == "engineer-codex-ward-873"
    assert first.request_id == "req-ward-873"
    assert first.correlation_id == "req-ward-873"
    assert first.container_name == "engineer-codex-ward-873"
    assert first.role == "codex"
    assert first.harness == "codex"
    assert first.repo == "coilyco-flight-deck/ward"
    assert first.issue_ref == "coilyco-flight-deck/ward#873"
    assert first.workflow == "direct-to-main"
    assert first.ward_version == "v0.522.0"


def test_parse_skill_use_artifact_supports_nested_metadata():
    records = parse_skill_use_artifact(
        {
            "metadata": {
                "run_id": "run-from-artifact",
                "request_id": "request-from-artifact",
                "repo": "coilyco-flight-deck/agent-proxy",
                "workflow": "merge-remote-main",
            },
            "items": [
                {
                    "name": "nested-metadata-skill",
                    "run": {
                        "container_name": "worker-48",
                        "role": "engineer",
                        "harness": "codex",
                        "ward_version": "v0.793.0",
                    },
                }
            ],
        }
    )

    assert len(records) == 1
    record = records[0]
    assert record.skill == "nested-metadata-skill"
    assert record.run_id == "run-from-artifact"
    assert record.request_id == "request-from-artifact"
    assert record.repo == "coilyco-flight-deck/agent-proxy"
    assert record.workflow == "merge-remote-main"
    assert record.container_name == "worker-48"
    assert record.role == "engineer"
    assert record.harness == "codex"
    assert record.ward_version == "v0.793.0"


def test_parse_skill_use_artifact_ignores_malformed_values():
    records = parse_skill_use_artifact(
        {
            "run": ["not", "metadata"],
            "skill_use": [
                {"skill": {"unexpected": "object"}, "count": {"unexpected": 2}},
                {
                    "skill": "safe-skill",
                    "count": [3],
                    "harness": {"unexpected": "object"},
                    "metadata": ["not", "metadata"],
                    "first_seen": None,
                },
            ],
        }
    )

    assert len(records) == 1
    assert records[0].skill == "safe-skill"
    assert records[0].count == 1
    assert records[0].harness == ""
    assert records[0].first_seen == ""


def test_ingest_skill_use_source_updates_metric(tmp_path):
    fixture = Path("tests/fixtures/ward_skill_use.json")
    target = tmp_path / "skill-usage.json"
    target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

    before = ward_skill_use_total.labels(skill="repo-agent-proxy", harness="codex")._value.get()
    ingested = ingest_skill_use_source(target)
    after = ward_skill_use_total.labels(skill="repo-agent-proxy", harness="codex")._value.get()

    assert ingested == 2
    assert after - before == 2


def test_ingest_skill_use_source_handles_missing_and_empty(tmp_path):
    missing = tmp_path / "nope"
    empty = tmp_path / "skill-usage.json"
    empty.write_text("", encoding="utf-8")

    assert ingest_skill_use_source(missing) == 0
    assert ingest_skill_use_source(empty) == 0


def test_ingest_skill_use_source_walks_archive_directories(tmp_path):
    archive = tmp_path / "engineer-codex-ward-873"
    archive.mkdir()
    target = archive / "skill-usage.json"
    fixture = Path("tests/fixtures/ward_skill_use.json").read_text(encoding="utf-8")
    target.write_text(fixture, encoding="utf-8")

    before = ward_skill_use_total.labels(skill="tooling-ward-framing", harness="codex")._value.get()
    ingested = ingest_skill_use_source(tmp_path)
    after = ward_skill_use_total.labels(skill="tooling-ward-framing", harness="codex")._value.get()

    assert ingested == 2
    assert after - before == 1


def test_skill_use_records_become_metadata_only_trajectory_events():
    payload = Path("tests/fixtures/ward_skill_use.json").read_text(encoding="utf-8")
    events = skill_use_trajectory_events(parse_skill_use_artifact(json.loads(payload)))

    assert len(events) == 2
    first = events[0]
    assert first.event_type == "observation.recorded"
    assert first.payload.observation_kind == "ward.skill-use"
    assert first.payload.subject_ref == "skill:repo-agent-proxy"
    assert first.payload.measured_facts == {"count": 2, "harness": "codex"}
    assert first.correlation.ward_run_id == "engineer-codex-ward-873"
    assert first.correlation.repository == "coilyco-flight-deck/ward"
    assert first.attributes["ward.harness"] == "codex"
    assert first.content.capture == "metadata_only"
    assert first.actor.id == "ward:skill-use-producer"


def test_skill_use_source_is_idempotent_in_trajectory_store(tmp_path):
    fixture = Path("tests/fixtures/ward_skill_use.json")
    target = tmp_path / "skill-usage.json"
    target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    store = TrajectoryStore(tmp_path / "trajectory.sqlite3")

    assert ingest_skill_use_source(target, store) == 2
    assert ingest_skill_use_source(target, store) == 2

    assert len(tuple(store.iter_events())) == 2
    assert store.receipt_outcomes() == (
        "accepted",
        "accepted",
        "duplicate",
        "duplicate",
    )
