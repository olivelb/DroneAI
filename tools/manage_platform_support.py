"""Provision an isolated platform-support identity through the operator DB."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any, cast

from shared.database import PlatformCredential, PlatformMember, get_session
from shared.platform_identity import (
    append_platform_audit_event,
    issue_platform_credential,
    revoke_platform_credential,
)

SUBJECT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,255}$")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Provision, suspend, or reactivate one durable platform-support "
            "member. The default is a read-only preview."
        )
    )
    parser.add_argument(
        "--action",
        choices=("provision", "suspend", "reactivate"),
        default="provision",
    )
    parser.add_argument("--subject", required=True)
    parser.add_argument("--credential-name")
    parser.add_argument("--actor-subject", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser


def _validated(value: str, *, label: str, maximum: int = 256) -> str:
    result = value.strip()
    if not result or len(result) > maximum:
        raise ValueError(f"{label} must contain 1 to {maximum} characters")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    subject = _validated(args.subject, label="subject")
    if SUBJECT_PATTERN.fullmatch(subject) is None:
        raise ValueError("subject contains unsupported characters")
    credential_name: str | None = None
    if args.action in {"provision", "reactivate"}:
        if args.credential_name is None:
            parser.error("--credential-name is required for this action")
        credential_name = _validated(
            args.credential_name,
            label="credential name",
            maximum=160,
        )
    elif args.credential_name is not None:
        parser.error("--credential-name is not accepted for suspension")
    actor_subject = _validated(args.actor_subject, label="actor subject")
    with get_session() as session:
        member = session.query(PlatformMember).filter(
            PlatformMember.subject == subject,
        ).one_or_none()
        if args.action == "suspend":
            if member is None:
                raise RuntimeError("Platform member does not exist")
            active_credentials = session.query(PlatformCredential).filter(
                PlatformCredential.member_id == member.id,
                PlatformCredential.status == "active",
            ).with_for_update().all()
            preview = {
                "action": "suspend",
                "subject": subject,
                "current_status": member.status,
                "active_credentials_to_revoke": len(active_credentials),
                "apply": bool(args.apply),
            }
            print(json.dumps(preview, indent=2, sort_keys=True))
            if not args.apply:
                return 0
            previous_status = cast(str, member.status)
            was_active = member.status == "active"
            if was_active:
                mutable_member = cast(Any, member)
                mutable_member.status = "suspended"
                mutable_member.auth_version += 1
                mutable_member.updated_by = actor_subject
            for credential in active_credentials:
                revoke_platform_credential(
                    credential,
                    actor_subject=actor_subject,
                    reason="platform member suspended",
                )
                append_platform_audit_event(
                    session,
                    actor_subject=actor_subject,
                    action="platform_credential_revoked",
                    target_type="platform_credential",
                    target_id=cast(str, credential.id),
                    after_state={
                        "member_id": member.id,
                        "status": "revoked",
                        "reason": "platform member suspended",
                    },
                )
            if was_active or active_credentials:
                append_platform_audit_event(
                    session,
                    actor_subject=actor_subject,
                    action="platform_member_suspended",
                    target_type="platform_member",
                    target_id=cast(str, member.id),
                    before_state={"status": previous_status},
                    after_state={
                        "status": "suspended",
                        "revoked_credentials": len(active_credentials),
                    },
                )
            return 0

        if args.action == "reactivate":
            if member is None:
                raise RuntimeError("Platform member does not exist")
            if member.status != "suspended":
                raise RuntimeError(
                    "Platform member is already active; use provision to "
                    "issue another credential"
                )
        elif member is not None and member.status != "active":
            raise RuntimeError(
                "Platform member is suspended; use --action reactivate"
            )
        preview = {
            "action": args.action,
            "subject": subject,
            "role": "support",
            "member_exists": member is not None,
            "credential_name": credential_name,
            "apply": bool(args.apply),
        }
        print(json.dumps(preview, indent=2, sort_keys=True))
        if not args.apply:
            return 0
        if member is None:
            member = PlatformMember(
                subject=subject,
                role="support",
                status="active",
                created_by=actor_subject,
                updated_by=actor_subject,
            )
            session.add(member)
            session.flush()
            append_platform_audit_event(
                session,
                actor_subject=actor_subject,
                action="platform_member_provisioned",
                target_type="platform_member",
                target_id=cast(str, member.id),
                after_state={
                    "subject": subject,
                    "role": "support",
                    "status": "active",
                },
            )
        elif args.action == "reactivate":
            mutable_member = cast(Any, member)
            mutable_member.status = "active"
            mutable_member.auth_version += 1
            mutable_member.updated_by = actor_subject
            append_platform_audit_event(
                session,
                actor_subject=actor_subject,
                action="platform_member_reactivated",
                target_type="platform_member",
                target_id=cast(str, member.id),
                before_state={"status": "suspended"},
                after_state={"status": "active"},
            )
        assert credential_name is not None
        issued = issue_platform_credential(
            session,
            member=member,
            name=credential_name,
            actor_subject=actor_subject,
        )
        append_platform_audit_event(
            session,
            actor_subject=actor_subject,
            action="platform_credential_created",
            target_type="platform_credential",
            target_id=cast(str, issued.record.id),
            after_state={
                "member_id": member.id,
                "name": credential_name,
                "status": "active",
            },
        )
        print(
            json.dumps(
                {
                    "credential_id": issued.record.id,
                    "token": issued.token,
                    "warning": "Store this token now; it is not recoverable.",
                },
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
