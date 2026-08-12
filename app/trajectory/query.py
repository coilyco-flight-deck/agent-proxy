"""Read-only client and CLI for governed trajectory evidence views."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import quote

import httpx

ViewName = Literal[
    "reliability", "cost_latency", "policy", "evaluation", "harness_fit", "skill_fit"
]
INVESTIGATION_VIEWS: tuple[ViewName, ...] = (
    "reliability",
    "cost_latency",
    "policy",
    "evaluation",
)


class QueryError(RuntimeError):
    """A safe query failure that does not include response bodies."""


@dataclass(frozen=True)
class InvestigationFilters:
    repository: str | None = None
    issue: str | None = None
    workflow: str | None = None
    trajectory: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "repository": self.repository,
                "issue": self.issue,
                "workflow": self.workflow,
                "trajectory": self.trajectory,
            }.items()
            if value is not None
        }


def _contains(row: dict[str, Any], key: str, expected: str | None) -> bool:
    if expected is None:
        return True
    values = row.get(key, ())
    return isinstance(values, (list, tuple)) and expected in values


def row_matches(row: dict[str, Any], filters: InvestigationFilters) -> bool:
    if filters.trajectory is not None and row.get("trajectory_id") != filters.trajectory:
        return False
    return (
        _contains(row, "repository", filters.repository)
        and _contains(row, "issue_refs", filters.issue)
        and _contains(row, "workflows", filters.workflow)
    )


class TrajectoryQueryClient:
    """Fetch governed metadata-only evidence without mutating Agent Proxy."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> TrajectoryQueryClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self._client.close()

    def _get_object(self, path: str, *, allow_missing: bool = False) -> dict[str, Any] | None:
        try:
            response = self._client.get(path)
            if allow_missing and response.status_code == 404:
                return None
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise QueryError(f"GET {path} returned HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise QueryError(f"GET {path} failed: {exc.__class__.__name__}") from exc
        if not isinstance(payload, dict):
            raise QueryError(f"GET {path} returned a non-object JSON response")
        return cast(dict[str, Any], payload)

    def view(self, name: ViewName) -> dict[str, Any]:
        payload = self._get_object(f"/v1/trajectory/views/{name}")
        assert payload is not None
        rows = payload.get("rows")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise QueryError(f"trajectory view {name!r} returned invalid rows")
        return payload

    def dossier(self, trajectory_id: str) -> dict[str, Any] | None:
        encoded = quote(trajectory_id, safe="")
        return self._get_object(
            f"/v1/trajectory/dossiers/{encoded}",
            allow_missing=True,
        )


def _view_summary(view: dict[str, Any], row_count: int) -> dict[str, Any]:
    return {
        "contract": view.get("contract"),
        "freshness": view.get("freshness"),
        "access_tier": view.get("access_tier"),
        "matched_row_count": row_count,
    }


def investigate(
    client: TrajectoryQueryClient,
    filters: InvestigationFilters,
) -> dict[str, Any]:
    filtered: dict[ViewName, list[dict[str, Any]]] = {}
    summaries: dict[ViewName, dict[str, Any]] = {}
    trajectory_ids: set[str] = set()

    for name in INVESTIGATION_VIEWS:
        view = client.view(name)
        rows = [cast(dict[str, Any], row) for row in view["rows"] if row_matches(row, filters)]
        filtered[name] = rows
        summaries[name] = _view_summary(view, len(rows))
        trajectory_ids.update(
            str(row["trajectory_id"]) for row in rows if isinstance(row.get("trajectory_id"), str)
        )

    indexes = {
        name: {str(row["trajectory_id"]): row for row in rows} for name, rows in filtered.items()
    }
    trajectories = []
    for trajectory_id in sorted(trajectory_ids):
        trajectories.append(
            {
                "trajectory_id": trajectory_id,
                "reliability": indexes["reliability"].get(trajectory_id),
                "cost_latency": indexes["cost_latency"].get(trajectory_id),
                "policy": indexes["policy"].get(trajectory_id),
                "evaluation": indexes["evaluation"].get(trajectory_id),
                "dossier": client.dossier(trajectory_id),
            }
        )

    return {
        "schema_name": "agentproxy.query.investigation",
        "schema_version": "1.0",
        "filters": filters.as_dict(),
        "views": summaries,
        "trajectory_count": len(trajectories),
        "trajectories": trajectories,
        "interpretation_limits": [
            "Only retained contract-v1 events in the HTTP surface access tier are visible.",
            "This evidence cannot authorize an action and does not include raw bodies.",
        ],
    }


def compare_harness_fit(
    client: TrajectoryQueryClient,
    *,
    harness: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    view = client.view("harness_fit")
    rows = [
        cast(dict[str, Any], row)
        for row in view["rows"]
        if (harness is None or row.get("harness") == harness)
        and (model is None or row.get("model") == model)
    ]
    rows.sort(key=lambda row: (str(row.get("harness", "")), str(row.get("model", ""))))
    return {
        "schema_name": "agentproxy.query.harness-fit",
        "schema_version": "1.0",
        "filters": {
            key: value
            for key, value in {"harness": harness, "model": model}.items()
            if value is not None
        },
        "view": _view_summary(view, len(rows)),
        "rows": rows,
        "interpretation_limits": [
            "Harness-fit rows are observational aggregates, not causal proof or routing authority.",
            "Interpret completion rates with trajectory count and freshness.",
            "The current view has no time-window or repository filter.",
        ],
    }


def evaluate_skill_use(
    client: TrajectoryQueryClient,
    *,
    skill: str | None = None,
    role: str | None = None,
    harness: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Filter the governed skill-fit view for adoption and observed outcomes."""
    view = client.view("skill_fit")
    rows = [
        cast(dict[str, Any], row)
        for row in view["rows"]
        if (skill is None or row.get("skill") == skill)
        and (role is None or row.get("role") == role)
        and (harness is None or row.get("harness") == harness)
        and (model is None or row.get("model") == model)
    ]
    rows.sort(
        key=lambda row: (
            str(row.get("skill", "")),
            str(row.get("role", "")),
            str(row.get("harness", "")),
            str(row.get("model", "")),
        )
    )
    selected_only = [row for row in rows if row.get("selected_without_observed_use")]
    return {
        "schema_name": "agentproxy.query.skill-fit",
        "schema_version": "1.0",
        "filters": {
            key: value
            for key, value in {
                "skill": skill,
                "role": role,
                "harness": harness,
                "model": model,
            }.items()
            if value is not None
        },
        "view": _view_summary(view, len(rows)),
        "rows": rows,
        "selected_without_observed_use": [
            {key: row[key] for key in ("skill", "role", "harness", "model")}
            for row in selected_only
        ],
        "interpretation_limits": [
            "Selection and observed use are separate facts. A selected skill with no "
            "observation is missing evidence, not proof the skill went unused.",
            "Skill-fit rows are observational aggregates, not causal proof that a skill "
            "caused an outcome, and never authorize a Ward action.",
            "Interpret completion rates with trajectory count and freshness.",
            "Unresolved evaluation disagreement is reported, not resolved.",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-proxy-query",
        description="Read and filter Agent Proxy governed trajectory evidence.",
        epilog=(
            "All commands are read-only. Results contain metadata-only internal-tier evidence "
            "and never authorize Ward actions."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("PROXY_BASE_URL", "http://127.0.0.1:8080"),
        help="Agent Proxy base URL. Defaults to PROXY_BASE_URL or localhost:8080.",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds.")
    commands = parser.add_subparsers(dest="command", required=True)

    investigate_parser = commands.add_parser(
        "investigate",
        help="Join trajectory evidence for a repository, issue, workflow, or trajectory.",
    )
    investigate_parser.add_argument("--repository", help="Exact owner/repository correlation.")
    investigate_parser.add_argument("--issue", help="Exact owner/repository#number correlation.")
    investigate_parser.add_argument("--workflow", help="Exact workflow correlation.")
    investigate_parser.add_argument("--trajectory", help="Exact trajectory id.")

    fit_parser = commands.add_parser(
        "harness-fit",
        help="Compare observed harness and model aggregates.",
    )
    fit_parser.add_argument("--harness", help="Exact harness name.")
    fit_parser.add_argument("--model", help="Exact model name.")

    skill_parser = commands.add_parser(
        "skill-use",
        help="Evaluate observed skill selection, adoption, and outcomes.",
    )
    skill_parser.add_argument("--skill", help="Exact skill name.")
    skill_parser.add_argument("--role", help="Exact actor role.")
    skill_parser.add_argument("--harness", help="Exact harness name.")
    skill_parser.add_argument("--model", help="Exact model name.")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "investigate" and not any(
        (args.repository, args.issue, args.workflow, args.trajectory)
    ):
        parser.error("investigate requires --repository, --issue, --workflow, or --trajectory")

    try:
        with TrajectoryQueryClient(args.base_url, timeout=args.timeout) as client:
            if args.command == "investigate":
                result = investigate(
                    client,
                    InvestigationFilters(
                        repository=args.repository,
                        issue=args.issue,
                        workflow=args.workflow,
                        trajectory=args.trajectory,
                    ),
                )
            elif args.command == "skill-use":
                result = evaluate_skill_use(
                    client,
                    skill=args.skill,
                    role=args.role,
                    harness=args.harness,
                    model=args.model,
                )
            else:
                result = compare_harness_fit(
                    client,
                    harness=args.harness,
                    model=args.model,
                )
    except QueryError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def main() -> None:
    raise SystemExit(run())
