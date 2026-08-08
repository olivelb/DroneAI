"""Generate or verify the versioned Kafka event JSON Schema artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.event_schemas import kafka_event_json_schema


DEFAULT_OUTPUT = ROOT / "docs" / "contracts" / "kafka-events-v1.schema.json"


def render_schema() -> str:
    return json.dumps(
        kafka_event_json_schema(),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    expected = render_schema()
    output = args.output.resolve()
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != expected:
            print(
                f"Kafka event schema is stale; run {Path(__file__).name}",
                file=sys.stderr,
            )
            return 1
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
