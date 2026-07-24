"""Compare the current Agent Proxy surface with a LiteLLM candidate."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.litellm_parity import compare_endpoints


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-url", required=True)
    parser.add_argument("--candidate-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    report = asyncio.run(
        compare_endpoints(
            args.baseline_url,
            args.candidate_url,
            args.model,
            baseline_api_key=os.environ.get(
                "AGENT_PROXY_PARITY_BASELINE_API_KEY", "parity-fixture"
            ),
            candidate_api_key=os.environ.get(
                "AGENT_PROXY_PARITY_CANDIDATE_API_KEY", "parity-fixture"
            ),
        )
    )
    rendered = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if report.surface_parity_passed else 1)


if __name__ == "__main__":
    main()
