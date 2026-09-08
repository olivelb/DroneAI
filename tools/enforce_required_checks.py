"""Inspect or add required GitHub Actions gates without removing protections."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ACTIONS_APP_ID = 15368
ROOT = Path(__file__).resolve().parents[1]


def _check_identity(check: dict[str, Any]) -> tuple[str, int]:
    return str(check["context"]), int(check.get("app_id") or -1)


def required_checks_update(current: dict[str, Any], required: list[str]) -> dict[str, Any]:
    checks = [{"context": context, "app_id": app_id}
              for context, app_id in map(_check_identity, current.get("checks", []))]
    names = {check["context"] for check in checks}
    for context in current.get("contexts", []):
        if context not in names:
            checks.append({"context": context, "app_id": -1})
            names.add(context)
    for context in required:
        if not any(check["context"] == context and check.get("app_id") == ACTIONS_APP_ID for check in checks):
            checks.append({"context": context, "app_id": ACTIONS_APP_ID})
    return {"strict": current["strict"], "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="olivelb/DroneAI")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    endpoint = f"repos/{args.repo}/branches/main/protection/required_status_checks"
    current = json.loads(subprocess.check_output(["gh", "api", endpoint], text=True))
    required = json.loads((ROOT / ".github/required-checks.json").read_text())
    update = required_checks_update(current, required)
    if args.apply:
        subprocess.run(["gh", "api", "--method", "PATCH", endpoint, "--input", "-"],
                       input=json.dumps(update), text=True, check=True, capture_output=True)
        verified = json.loads(subprocess.check_output(["gh", "api", endpoint], text=True))
        if verified["strict"] != current["strict"] or not (
            set(map(_check_identity, update["checks"]))
            <= set(map(_check_identity, verified["checks"]))
        ):
            raise RuntimeError("Required-check settings did not match the requested update")
        print(json.dumps(verified, indent=2))
    else:
        print(json.dumps(update, indent=2))


if __name__ == "__main__":
    main()
