"""Plan or apply an audited adoption of legacy resources into one tenant."""

from __future__ import annotations

import argparse
import json

from shared.database import get_session
from shared.legacy_adoption import build_adoption_plan
from shared.legacy_adoption_execution import apply_adoption_plan
from shared.legacy_adoption_types import S3AdoptionObjectStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy legacy dataset/mission storage into one organization and "
            "transactionally rebind its catalogue rows. The default is a "
            "read-only dry run; source objects are never deleted."
        )
    )
    parser.add_argument("--target-organization-id", required=True)
    parser.add_argument("--owner-subject", required=True)
    parser.add_argument("--actor-subject", required=True)
    parser.add_argument("--mission", action="append", default=[])
    parser.add_argument("--dataset", action="append", default=[])
    parser.add_argument("--all-legacy", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm-plan-checksum",
        help="Required with --apply and must match the freshly computed plan.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.apply and not args.confirm_plan_checksum:
        raise ValueError("--apply requires --confirm-plan-checksum")
    if args.apply and not args.run_id:
        raise ValueError("--apply requires the reviewed dry-run --run-id")
    if args.confirm_plan_checksum and not args.apply:
        raise ValueError("--confirm-plan-checksum is valid only with --apply")
    store = S3AdoptionObjectStore()
    with get_session() as session:
        plan = build_adoption_plan(
            session,
            target_organization_id=args.target_organization_id,
            owner_subject=args.owner_subject,
            actor_subject=args.actor_subject,
            store=store,
            mission_ids=args.mission,
            dataset_names=args.dataset,
            all_legacy=bool(args.all_legacy),
            run_id=args.run_id,
        )
    print(json.dumps(plan.public_summary(apply=bool(args.apply)), indent=2, sort_keys=True))
    if not args.apply:
        return 0
    if args.confirm_plan_checksum != plan.plan_checksum_sha256:
        raise ValueError("Fresh adoption plan does not match the confirmed checksum")
    apply_adoption_plan(plan, store=store)
    print(
        json.dumps(
            {
                "run_id": plan.run_id,
                "status": "completed",
                "source_retained": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
