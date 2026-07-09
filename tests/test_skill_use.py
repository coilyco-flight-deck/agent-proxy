"""Ward reap skill-use ingestion."""

import json
from pathlib import Path

from app.obs import ward_skill_use_total
from app.skill_use import ingest_skill_use_source, parse_skill_use_artifact


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
