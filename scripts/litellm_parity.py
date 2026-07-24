"""Compare the current Agent Proxy surface with a LiteLLM candidate."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.litellm_parity import compare_endpoints


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-url", required=True)
    parser.add_argument("--candidate-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--baseline-api-key", default="parity-fixture")
    parser.add_argument("--candidate-api-key", default="parity-fixture")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    report = asyncio.run(
        compare_endpoints(
            args.baseline_url,
            args.candidate_url,
            args.model,
            baseline_api_key=args.baseline_api_key,
            candidate_api_key=args.candidate_api_key,
        )
    )
    rendered = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
    print(rendered)
    if args.json_path:
        Path(args.json_path).write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if report.surface_parity_passed else 1)


if __name__ == "__main__":
    main()
