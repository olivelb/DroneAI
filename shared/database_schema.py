"""Shared SQLAlchemy registry, mixins, and schema constants."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

from shared.stage_contracts import RESOURCE_CLASSES


class Base(DeclarativeBase):  # type: ignore[misc]
    pass


PORTABLE_JSON = JSON().with_variant(JSONB(), "postgresql")
PORTABLE_BIGINT = BigInteger().with_variant(Integer(), "sqlite")


class RequiredTimestampMixin:
    """Reusable non-null creation/update timestamps for durable entities."""

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class RevocableCredentialMixin:
    """Shared secret lifecycle fields for tenant and platform credentials."""

    name = Column(String(160), nullable=False)
    secret_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="active")
    expires_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String(256), nullable=True)
    revocation_reason = Column(String(500), nullable=True)
    created_by = Column(String(256), nullable=False)


class AppendOnlyAuditMixin:
    """Shared immutable event identity, actor, snapshots and timestamp."""

    id = Column(PORTABLE_BIGINT, primary_key=True, autoincrement=True)
    event_id = Column(
        String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid4()),
        index=True,
    )
    actor_subject = Column(String(256), nullable=False)
    before_state = Column(PORTABLE_JSON, nullable=True)
    after_state = Column(PORTABLE_JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Mission statuses
# ---------------------------------------------------------------------------

MISSION_STATUSES = (
    "pending",
    "processing",
    "success",
    "completed",
    "error",
    "cancelled",
    "stale",
    "deleting",
    "deletion_failed",
)
AGGREGATION_STATUSES = (
    "pending",
    "collecting",
    "finalizing",
    "completed",
    "failed",
)
ANALYSIS_RUN_STATUSES = (
    "queued",
    "tiling",
    "running",
    "finalizing",
    "completed",
    "failed",
    "cancelled",
)
ANALYSIS_RUN_PHASES = (
    "queued",
    "tiling",
    "detecting",
    "deduplicating",
    "completed",
    "tiling_failed",
    "finalization_failed",
    "recovery_queued",
    "cancelled",
    "recovery_finalizing",
    "recovery_retiling",
    "tile_attempts_exhausted",
    "recovery_detecting",
)
ANALYSIS_TILE_STATUSES = ("queued", "completed", "dead")
MISSION_STAGE_TYPES = (
    "reconstruction",
    "gaussian_training",
    "gaussian_filtering",
    "rasterization",
    "detection",
    "gaussian_viewer",
)
MISSION_STAGE_RUN_STATUSES = (
    "blocked",
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
)
MISSION_RESOURCE_CLASSES = tuple(RESOURCE_CLASSES)
MAP_FEATURE_SOURCES = ("manual", "ai")
GCP_ROLES = ("adjustment", "checkpoint", "disabled")
GCP_OBSERVATION_STATUSES = ("candidate", "marked", "skipped")
GCP_AUDIT_ACTIONS = (
    "imported",
    "point_updated",
    "observation_updated",
    "candidates_refreshed",
    "bundle_materialized",
)
MAP_FEATURE_AUDIT_ACTIONS = (
    "created",
    "updated",
    "reviewed",
    "unreviewed",
    "tombstoned",
    "restored",
)
PIPELINE_LOG_STATUSES = ("processing", "success", "error", "cancelled")
INBOX_EVENT_STATUSES = ("processing", "completed", "failed")
OUTBOX_EVENT_STATUSES = (
    "pending",
    "publishing",
    "published",
    "failed",
    "dead",
)
DATASET_UPLOAD_SESSION_STATUSES = (
    "initializing",
    "uploading",
    "finalizing",
    "completed",
    "aborted",
    "failed",
)
DATASET_UPLOAD_FILE_STATUSES = (
    "initializing",
    "uploading",
    "completing",
    "completed",
    "aborted",
    "failed",
)
DATASET_STATUSES = (
    "ready",
    "deleting",
    "deletion_failed",
    "deleted",
)
ORGANIZATION_STATUSES = ("active", "suspended")
ORGANIZATION_MEMBER_STATUSES = ("active", "suspended")
ORGANIZATION_MEMBER_ROLES = ("viewer", "operator", "admin")
API_CREDENTIAL_STATUSES = ("active", "revoked")
IDENTITY_AUDIT_ACTIONS = (
    "organization_bootstrapped",
    "organization_updated",
    "member_created",
    "member_updated",
    "credential_created",
    "credential_revoked",
    "credential_rotated",
    "invitation_created",
    "invitation_revoked",
    "invitation_accepted",
    "recovery_created",
    "recovery_revoked",
    "recovery_redeemed",
)
IDENTITY_CAPABILITY_PURPOSES = ("invitation", "recovery")
IDENTITY_CAPABILITY_STATUSES = ("pending", "redeemed", "revoked")
PLATFORM_MEMBER_STATUSES = ("active", "suspended")
PLATFORM_MEMBER_ROLES = ("support",)
PLATFORM_CREDENTIAL_STATUSES = ("active", "revoked")
PLATFORM_AUDIT_ACTIONS = (
    "platform_member_provisioned",
    "platform_member_suspended",
    "platform_member_reactivated",
    "platform_credential_created",
    "platform_credential_revoked",
    "platform_credential_rotated",
    "organization_status_updated",
)
ACCESS_AUDIT_ACTOR_REALMS = ("tenant", "platform")
ACCESS_AUDIT_RESOURCE_TYPES = ("mission", "dataset")
ACCESS_AUDIT_OUTCOMES = ("authorized",)
ORGANIZATION_USAGE_ACTIONS = (
    "policy_updated",
    "storage_reserved",
    "storage_released",
    "stage_scheduled",
    "request_throttled",
    "retention_deleted",
    "retention_failed",
    "legacy_adoption_started",
    "legacy_adoption_resource",
    "legacy_adoption_completed",
    "legacy_adoption_failed",
)


def _values_check(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _uuid_identifier_column() -> Column[Any]:
    """Define the common externally visible immutable UUID identifier."""

    return Column(
        String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid4()),
        index=True,
    )
