"""Create counts-only backup and replay evidence for a trajectory ledger."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.trajectory.evidence import verify_trajectory_recovery


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=os.environ.get("PROXY_TRAJECTORY_DB_PATH", "./data/trajectory.sqlite3"),
    )
    parser.add_argument("--backup", required=True)
    parser.add_argument("--replay", required=True)
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    evidence = verify_trajectory_recovery(args.source, args.backup, args.replay)
    rendered = json.dumps(evidence.model_dump(mode="json"), indent=2, sort_keys=True)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if evidence.passed else 1)


if __name__ == "__main__":
    main()
