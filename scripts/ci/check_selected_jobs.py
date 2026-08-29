"""Reject failed selectors, missing decisions and skipped required jobs."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any


def validate_results(
    needs: dict[str, Any], selector: str, jobs: dict[str, str],
) -> None:
    selection = needs.get(selector, {})
    if selection.get("result") != "success":
        raise ValueError(f"Selector {selector} did not succeed")
    outputs = selection.get("outputs", {})
    for job, output in jobs.items():
        required = outputs.get(output)
        if required not in ("true", "false"):
            raise ValueError(f"Selector output {output} is missing or invalid")
        expected = "success" if required == "true" else "skipped"
        result = needs.get(job, {}).get("result")
        if result != expected:
            raise ValueError(f"{job}: selected={required}, expected {expected}, got {result}")
        print(f"{job}: {'qualified' if required == 'true' else 'not-required'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selector", required=True)
    parser.add_argument("--job", action="append", required=True, metavar="JOB=OUTPUT")
    args = parser.parse_args()
    jobs = dict(value.split("=", 1) for value in args.job)
    validate_results(json.loads(os.environ["NEEDS_JSON"]), args.selector, jobs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
