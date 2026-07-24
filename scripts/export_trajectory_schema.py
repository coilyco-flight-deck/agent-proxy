"""Export the trajectory contract JSON Schema for non-Python consumers."""

from __future__ import annotations

import json
from pathlib import Path

from app.trajectory import event_json_schema


def main() -> None:
    target = Path("schemas/trajectory-event-v1.schema.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(event_json_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(target)


if __name__ == "__main__":
    main()
