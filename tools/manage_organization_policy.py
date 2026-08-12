"""Review or apply one complete organization SaaS policy through operator DB."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from shared.database import Organization, get_session
from shared.organization_saas import (
    PolicyValues,
    get_policy,
    policy_values,
    set_policy,
)
from shared.tenancy import validate_organization_id

POLICY_KEYS = {
    "storage_limit_bytes",
    "concurrent_stage_runs_limit",
    "request_rate_per_minute",
    "request_burst",
    "retention_days",
}


def load_policy(path: Path) -> PolicyValues:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != POLICY_KEYS:
        raise ValueError(
            "Policy JSON must contain exactly: "
            + ", ".join(sorted(POLICY_KEYS))
        )
    if any(
        value is not None and (isinstance(value, bool) or not isinstance(value, int))
        for value in payload.values()
    ):
        raise ValueError("Policy values must be positive integers or null")
    return PolicyValues(**payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply a complete commercial policy without granting tenant admins "
            "quota escalation rights. The default is a read-only dry run."
        )
    )
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--actor-subject", required=True)
    parser.add_argument("--policy-file", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    organization_id = validate_organization_id(args.organization_id)
    values = load_policy(args.policy_file)
    with get_session() as session:
        organization = session.get(Organization, organization_id)
        if organization is None:
            raise RuntimeError(f"Organization does not exist: {organization_id}")
        before = asdict(policy_values(get_policy(session, organization_id)))
        preview = {
            "organization_id": organization_id,
            "before": before,
            "after": asdict(values),
            "apply": bool(args.apply),
        }
        print(json.dumps(preview, indent=2, sort_keys=True))
        if args.apply:
            set_policy(
                session,
                organization_id=organization_id,
                values=values,
                actor_subject=args.actor_subject,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
