"""Tenant-scoped dataset and object-storage authorization helpers."""

from __future__ import annotations

from collections.abc import Iterable
import logging
from typing import Any, cast

from fastapi import HTTPException, status

from shared.database import Dataset

from .mission_access import get_owned_mission
from .security import Principal

audit_logger = logging.getLogger("droneai.audit.dataset_access")


def resolve_dataset_owner(
    principal: Principal,
    requested_owner: str | None,
    *,
    action: str,
    dataset_name: str | None = None,
) -> str:
    """Resolve a dataset tenant boundary with explicit audited admin delegation."""

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
        audit_logger.warning(
            "admin_cross_tenant_dataset_access principal=%s owner=%s action=%s dataset=%s",
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
    )
    return session.query(Dataset).filter(
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
    parts = normalized.split("/") if normalized else []
    if len(parts) < 2:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Path not found")
    resource_prefix = "/".join(parts[:2])
    if parts[0] == "datasets":
        get_owned_dataset(
            session,
            principal,
            prefix=resource_prefix,
            requested_owner=requested_owner,
            action=action,
        )
        return normalized
    if parts[0] == "missions":
        get_owned_mission(
            session,
            parts[1],
            principal,
            requested_owner=requested_owner,
            action=action,
        )
        return normalized
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Path not found")
