"""Tenant-scoped dataset and object-storage authorization helpers."""

from __future__ import annotations

from collections.abc import Iterable
import logging
from typing import Any, cast

from fastapi import HTTPException, status

from shared.access_audit import append_access_audit_event
from shared.database import Dataset
from shared.tenancy import LEGACY_ORGANIZATION_ID

from .mission_access import get_owned_mission
from .security import Principal

audit_logger = logging.getLogger("droneai.audit.dataset_access")


def resolve_dataset_owner(
    principal: Principal,
    requested_owner: str | None,
    *,
    action: str,
    dataset_name: str | None = None,
    audit_session: Any | None = None,
) -> str:
    """Resolve dataset ownership inside one tenant with audited delegation."""

    owner = (requested_owner or principal.subject).strip()
    if not owner:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner subject cannot be empty",
        )
    if owner != principal.subject:
        if principal.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dataset not found",
            )
        if audit_session is None:
            raise RuntimeError(
                "Cross-member dataset access requires a durable audit session"
            )
        append_access_audit_event(
            audit_session,
            organization_id=getattr(
                principal,
                "organization_id",
                LEGACY_ORGANIZATION_ID,
            ),
            actor_subject=principal.subject,
            actor_role=principal.role,
            actor_realm=getattr(principal, "realm", "tenant"),
            actor_member_id=getattr(principal, "member_id", None),
            actor_credential_id=getattr(principal, "credential_id", None),
            action=action,
            target_owner_subject=owner,
            resource_type="dataset",
            resource_id=dataset_name,
        )
        audit_session.flush()
        audit_logger.warning(
            "admin_cross_member_dataset_access principal=%s owner=%s action=%s dataset=%s",
            principal.subject,
            owner,
            action,
            dataset_name or "-",
        )
    return owner


def dataset_query(
    session: Any,
    principal: Principal,
    *,
    requested_owner: str | None = None,
    action: str,
    dataset_name: str | None = None,
    statuses: Iterable[str] = ("ready",),
) -> Any:
    owner = resolve_dataset_owner(
        principal,
        requested_owner,
        action=action,
        dataset_name=dataset_name,
        audit_session=session,
    )
    organization_id = getattr(
        principal,
        "organization_id",
        LEGACY_ORGANIZATION_ID,
    )
    return session.query(Dataset).filter(
        Dataset.organization_id == organization_id,
        Dataset.owner_subject == owner,
        Dataset.status.in_(tuple(statuses)),
    )


def get_owned_dataset(
    session: Any,
    principal: Principal,
    *,
    name: str | None = None,
    prefix: str | None = None,
    requested_owner: str | None = None,
    action: str = "read",
    statuses: Iterable[str] = ("ready",),
    for_update: bool = False,
) -> Dataset:
    query = dataset_query(
        session,
        principal,
        requested_owner=requested_owner,
        action=action,
        dataset_name=name,
        statuses=statuses,
    )
    if name is not None:
        query = query.filter(Dataset.name == name)
    if prefix is not None:
        query = query.filter(Dataset.prefix == prefix)
    if for_update:
        query = query.with_for_update()
    dataset = cast(Dataset | None, query.first())
    if dataset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dataset not found",
        )
    return dataset


def normalize_storage_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/").rstrip("/")
    if normalized in {"", "/"}:
        return ""
    if normalized.startswith("/") or "//" in normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid object storage path",
        )
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid object storage path",
        )
    return normalized


def authorize_storage_path(
    session: Any,
    path: str,
    principal: Principal,
    *,
    requested_owner: str | None = None,
    action: str,
) -> str:
    """Authorize one S3 path against its owning dataset or mission."""

    normalized = normalize_storage_path(path)
    if not normalized:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Path not found")
    datasets = dataset_query(
        session,
        principal,
        requested_owner=requested_owner,
        action=action,
    ).all()
    matched_dataset = next(
        (
            dataset
            for dataset in datasets
            if normalized == str(dataset.prefix)
            or normalized.startswith(f"{dataset.prefix}/")
        ),
        None,
    )
    if matched_dataset is not None:
        get_owned_dataset(
            session,
            principal,
            prefix=str(matched_dataset.prefix),
            requested_owner=requested_owner,
            action=action,
        )
        return normalized
    parts = normalized.split("/")
    if parts[0] == "missions":
        get_owned_mission(
            session,
            parts[1],
            principal,
            requested_owner=requested_owner,
            action=action,
        )
        return normalized
    if (
        len(parts) >= 4
        and parts[0] == "organizations"
        and parts[1] == getattr(principal, "organization_id", LEGACY_ORGANIZATION_ID)
        and parts[2] == "missions"
    ):
        get_owned_mission(
            session,
            parts[3],
            principal,
            requested_owner=requested_owner,
            action=action,
        )
        return normalized
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Path not found")
