"""Ingest cli-guard audit rows and specgen policy evidence."""

from __future__ import annotations

import argparse
import json

from app.trajectory.guard import ingest_guard_data
from app.trajectory.store import TrajectoryStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Agent Proxy trajectory SQLite database")
    parser.add_argument("--audit-jsonl", help="append-only cli-guard audit JSONL path")
    parser.add_argument("--specgen-root", help="specgen project root containing KDL and locks")
    parser.add_argument("--actor-role", default="agent")
    parser.add_argument("--ward-run-id")
    parser.add_argument("--agent-session-id")
    parser.add_argument("--repository")
    parser.add_argument("--issue-ref")
    parser.add_argument("--workflow")
    return parser


def main() -> None:
    args = _parser().parse_args()
    correlation = {
        key: value
        for key, value in {
            "ward_run_id": args.ward_run_id,
            "agent_session_id": args.agent_session_id,
            "repository": args.repository,
            "issue_ref": args.issue_ref,
            "workflow": args.workflow,
        }.items()
        if value
    }
    results = ingest_guard_data(
        TrajectoryStore(args.db),
        audit_path=args.audit_jsonl,
        specgen_root=args.specgen_root,
        actor_role=args.actor_role,
        correlation=correlation,
    )
    print(
        json.dumps(
            {
                "database": args.db,
                "outcomes": [result.as_dict() for result in results],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
