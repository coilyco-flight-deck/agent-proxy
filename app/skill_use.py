"""Ward reap skill-use telemetry ingestion."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TypeAlias, TypeGuard, TypedDict

from .obs import InstrumentedAction, emit_instrumented_action, get_logger, ward_skill_use_total

log = get_logger("agent-proxy.skill-use")

_SOURCE_FILENAMES = ("skill-usage.json", "skill_use.json")

JsonObject: TypeAlias = dict[str, object]


class RunMetadata(TypedDict):
    run_id: str
    request_id: str
    correlation_id: str
    container_name: str
    role: str
    harness: str
    repo: str
    issue_ref: str
    workflow: str
    ward_version: str


@dataclass(frozen=True)
class SkillUseRecord:
    run_id: str = ""
    request_id: str = ""
    correlation_id: str = ""
    container_name: str = ""
    role: str = ""
    harness: str = ""
    repo: str = ""
    issue_ref: str = ""
    workflow: str = ""
    ward_version: str = ""
    skill: str = ""
    count: int = 0
    first_seen: str = ""
    last_seen: str = ""

    def log_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {
            "run_id": self.run_id,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "container_name": self.container_name,
            "role": self.role,
            "harness": self.harness,
            "repo": self.repo,
            "issue_ref": self.issue_ref,
            "workflow": self.workflow,
            "ward_version": self.ward_version,
            "skill": self.skill,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }
        return {key: value for key, value in fields.items() if value not in ("", 0)}


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
            continue
        if isinstance(value, (int, float, bool)):
            text = str(value).strip()
            if text:
                return text
    return ""


def _positive_int(value: object, default: int = 1) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value > 0 else default
    if not isinstance(value, str):
        return default
    try:
        count = int(value)
    except ValueError:
        return default
    return count if count > 0 else default


def _is_json_object(payload: object) -> TypeGuard[JsonObject]:
    """Accept only string-keyed JSON objects before accessing artifact fields."""
    return isinstance(payload, dict) and all(isinstance(key, str) for key in payload)


def _coerce_metadata(payload: object) -> JsonObject:
    if _is_json_object(payload):
        return payload
    return {}


def _empty_run_metadata() -> RunMetadata:
    return {
        "run_id": "",
        "request_id": "",
        "correlation_id": "",
        "container_name": "",
        "role": "",
        "harness": "",
        "repo": "",
        "issue_ref": "",
        "workflow": "",
        "ward_version": "",
    }


def _extract_run_metadata(payload: JsonObject) -> RunMetadata:
    nested = _coerce_metadata(payload.get("run") or payload.get("metadata") or {})
    flat = payload
    return {
        "run_id": _first_text(
            flat.get("run_id"),
            flat.get("ward_run_id"),
            nested.get("run_id"),
            nested.get("ward_run_id"),
        ),
        "request_id": _first_text(flat.get("request_id"), nested.get("request_id")),
        "correlation_id": _first_text(flat.get("correlation_id"), nested.get("correlation_id")),
        "container_name": _first_text(
            flat.get("container_name"),
            flat.get("ward_container_name"),
            nested.get("container_name"),
            nested.get("ward_container_name"),
        ),
        "role": _first_text(flat.get("role"), nested.get("role")),
        "harness": _first_text(flat.get("harness"), nested.get("harness")),
        "repo": _first_text(flat.get("repo"), flat.get("target_repo"), nested.get("repo")),
        "issue_ref": _first_text(
            flat.get("issue_ref"),
            flat.get("ward_issue_ref"),
            nested.get("issue_ref"),
            nested.get("ward_issue_ref"),
        ),
        "workflow": _first_text(flat.get("workflow"), nested.get("workflow")),
        "ward_version": _first_text(
            flat.get("ward_version"),
            flat.get("version"),
            nested.get("ward_version"),
            nested.get("version"),
        ),
    }


def _skill_rows(payload: object) -> tuple[list[JsonObject], RunMetadata]:
    if isinstance(payload, list):
        rows = [row for row in payload if _is_json_object(row)]
        return rows, _empty_run_metadata()
    if not _is_json_object(payload):
        return [], _empty_run_metadata()

    rows_obj = (
        payload.get("skill_use")
        or payload.get("skill_usage")
        or payload.get("skills")
        or payload.get("data")
        or payload.get("items")
        or []
    )
    if isinstance(rows_obj, list):
        rows = [row for row in rows_obj if _is_json_object(row)]
    elif any(key in payload for key in ("skill", "skill_name", "name")):
        rows = [payload]
    else:
        rows = []
    return rows, _extract_run_metadata(payload)


def _merged_run_metadata(row: JsonObject, run_meta: RunMetadata) -> RunMetadata:
    run_info = _coerce_metadata(row.get("run") or row.get("metadata") or {})
    return {
        "run_id": _first_text(
            row.get("run_id"),
            row.get("ward_run_id"),
            run_info.get("run_id"),
            run_info.get("ward_run_id"),
            run_meta["run_id"],
        ),
        "request_id": _first_text(
            row.get("request_id"),
            run_info.get("request_id"),
            run_meta["request_id"],
        ),
        "correlation_id": _first_text(
            row.get("correlation_id"),
            run_info.get("correlation_id"),
            run_meta["correlation_id"],
        ),
        "container_name": _first_text(
            row.get("container_name"),
            row.get("ward_container_name"),
            run_info.get("container_name"),
            run_info.get("ward_container_name"),
            run_meta["container_name"],
        ),
        "role": _first_text(row.get("role"), run_info.get("role"), run_meta["role"]),
        "harness": _first_text(row.get("harness"), run_info.get("harness"), run_meta["harness"]),
        "repo": _first_text(
            row.get("repo"),
            row.get("target_repo"),
            run_info.get("repo"),
            run_meta["repo"],
        ),
        "issue_ref": _first_text(
            row.get("issue_ref"),
            row.get("ward_issue_ref"),
            run_info.get("issue_ref"),
            run_info.get("ward_issue_ref"),
            run_meta["issue_ref"],
        ),
        "workflow": _first_text(
            row.get("workflow"), run_info.get("workflow"), run_meta["workflow"]
        ),
        "ward_version": _first_text(
            row.get("ward_version"),
            row.get("version"),
            run_info.get("ward_version"),
            run_info.get("version"),
            run_meta["ward_version"],
        ),
    }


def parse_skill_use_artifact(payload: object) -> list[SkillUseRecord]:
    """Normalize the ward reap summary artifact into dashboard-friendly records."""
    rows, run_meta = _skill_rows(payload)
    records: list[SkillUseRecord] = []
    for row in rows:
        merged_meta = _merged_run_metadata(row, run_meta)
        skill = _first_text(row.get("skill"), row.get("skill_name"), row.get("name"))
        if not skill:
            continue
        harness = _first_text(row.get("harness"), merged_meta["harness"])
        count = _positive_int(row.get("count"), default=1)
        records.append(
            SkillUseRecord(
                run_id=merged_meta["run_id"],
                request_id=merged_meta["request_id"],
                correlation_id=merged_meta["correlation_id"],
                container_name=merged_meta["container_name"],
                role=merged_meta["role"],
                harness=harness,
                repo=merged_meta["repo"],
                issue_ref=merged_meta["issue_ref"],
                workflow=merged_meta["workflow"],
                ward_version=merged_meta["ward_version"],
                skill=skill,
                count=count,
                first_seen=_first_text(row.get("first_seen")),
                last_seen=_first_text(row.get("last_seen")),
            )
        )
    return records


def _increment_skill_use_metric(record: SkillUseRecord) -> None:
    ward_skill_use_total.labels(skill=record.skill, harness=record.harness or "unknown").inc(
        record.count
    )


def record_skill_use(records: Iterable[SkillUseRecord]) -> int:
    total = 0
    for record in records:
        total += 1
        emit_instrumented_action(
            InstrumentedAction(
                log_event="ward.skill_use.ingested",
                metric=lambda: _increment_skill_use_metric(record),
                span_event="ward.skill_use.ingested",
                fields=record.log_fields(),
            )
        )
    return total


def ingest_skill_use_payload(payload: object) -> int:
    records = parse_skill_use_artifact(payload)
    if not records:
        return 0
    return record_skill_use(records)


def _read_json_file(path: Path) -> object | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def ingest_skill_use_source(source: str | Path | None) -> int:
    """Read a skill-use artifact file or archive directory and record summaries."""
    if not source:
        return 0
    path = Path(source)
    if not path.exists():
        log.info("ward.skill_use.absent", source=str(path))
        return 0

    total = 0
    if path.is_dir():
        files = [
            child
            for child in path.rglob("*")
            if child.is_file() and child.name in _SOURCE_FILENAMES
        ]
        for child in sorted(files):
            payload = _read_json_file(child)
            if payload is None:
                continue
            total += ingest_skill_use_payload(payload)
        return total

    payload = _read_json_file(path)
    if payload is None:
        return 0
    return ingest_skill_use_payload(payload)
