"""Ingest one agent-compose bundle into Agent Proxy's durable trajectory store."""

from __future__ import annotations

import argparse
import json

from app.trajectory.agent_compose import ingest_agent_compose_bundle
from app.trajectory.store import TrajectoryStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, help="verified agent-compose bundle directory")
    parser.add_argument("--db", required=True, help="Agent Proxy trajectory SQLite database")
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
    results = ingest_agent_compose_bundle(
        args.bundle,
        TrajectoryStore(args.db),
        correlation=correlation,
    )
    print(
        json.dumps(
            {
                "bundle": args.bundle,
                "database": args.db,
                "outcomes": [result.as_dict() for result in results],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
