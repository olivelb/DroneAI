"""PostGIS-backed database models for mission state, detections, and logs.

Replaces:
- In-memory ``mission_states`` dict in the dashboard API
- ``mission_state.json`` / ``mission_state_history.jsonl`` on NFS
- In-memory ``MissionRegistry`` aggregation in the processing worker

Uses SQLAlchemy 2.0 + GeoAlchemy2 for PostGIS geometry support.
"""

import logging
from contextlib import contextmanager
from datetime import datetime, UTC
from typing import Any, cast
from collections.abc import Callable, Iterator
from uuid import uuid4

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    relationship,
    sessionmaker,
)

from shared.config import DATABASE_URL
from shared.stage_contracts import RESOURCE_CLASSES
from shared.tenancy import LEGACY_ORGANIZATION_ID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Engine & session factory (lazy init)
# ---------------------------------------------------------------------------

_engine: Any = None
_SessionFactory: Callable[[], Session] | None = None


def _get_engine() -> Any:
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False,
        )
    return _engine


def get_session_factory() -> Callable[[], Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=_get_engine(), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def get_session() -> Iterator[Session]:
    """Context manager yielding a SQLAlchemy session with auto-commit/rollback."""
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Dispose of the engine and reset singletons (for testing)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Organization(RequiredTimestampMixin, Base):
    """Durable customer boundary for every SaaS-owned resource."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(
            _values_check("status", ORGANIZATION_STATUSES),
            name="ck_organizations_status",
        ),
    )

    id = Column(String(64), primary_key=True)
    display_name = Column(String(160), nullable=False)
    status = Column(String(32), nullable=False, default="active")
    created_by = Column(String(256), nullable=False)
    updated_by = Column(String(256), nullable=False)


class OrganizationMember(RequiredTimestampMixin, Base):
    """Organization-scoped human or service identity and current role."""

    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "subject",
            name="uq_organization_members_subject",
        ),
        Index(
            "ix_organization_members_org_status_role",
            "organization_id",
            "status",
            "role",
        ),
        CheckConstraint(
            _values_check("status", ORGANIZATION_MEMBER_STATUSES),
            name="ck_organization_members_status",
        ),
        CheckConstraint(
            _values_check("role", ORGANIZATION_MEMBER_ROLES),
            name="ck_organization_members_role",
        ),
        CheckConstraint(
            "auth_version >= 1",
            name="ck_organization_members_auth_version",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    subject = Column(String(256), nullable=False)
    role = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="active")
    auth_version = Column(Integer, nullable=False, default=1)
    created_by = Column(String(256), nullable=False)
    updated_by = Column(String(256), nullable=False)


class ApiCredential(RequiredTimestampMixin, Base):
    """Revocable API credential; only its peppered digest is persisted."""

    __tablename__ = "api_credentials"
    __table_args__ = (
        Index(
            "ix_api_credentials_org_member_status",
            "organization_id",
            "member_id",
            "status",
        ),
        CheckConstraint(
            _values_check("status", API_CREDENTIAL_STATUSES),
            name="ck_api_credentials_status",
        ),
    )

    id = Column(String(36), primary_key=True)
    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    member_id = Column(
        String(36),
        ForeignKey("organization_members.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name = Column(String(160), nullable=False)
    secret_hash = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="active")
    expires_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(String(256), nullable=True)
    revocation_reason = Column(String(500), nullable=True)
    rotated_from_id = Column(
        String(36),
        ForeignKey("api_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by = Column(String(256), nullable=False)


class IdentityAuditEvent(AppendOnlyAuditMixin, Base):
    """Database-protected identity and access lifecycle history."""

    __tablename__ = "identity_audit_events"
    __table_args__ = (
        Index(
            "ix_identity_audit_org_created",
            "organization_id",
            "created_at",
        ),
        Index(
            "ix_identity_audit_target_created",
            "target_type",
            "target_id",
            "created_at",
        ),
        CheckConstraint(
            _values_check("action", IDENTITY_AUDIT_ACTIONS),
            name="ck_identity_audit_action",
        ),
    )

    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    action = Column(String(48), nullable=False)
    target_type = Column(String(32), nullable=False)
    target_id = Column(String(256), nullable=False)


class Dataset(RequiredTimestampMixin, Base):
    """Tenant-owned immutable input catalog entry backed by one S3 prefix."""

    __tablename__ = "datasets"
    __table_args__ = (
        Index(
            "uq_datasets_live_organization_name",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=text("status != 'deleted'"),
            sqlite_where=text("status != 'deleted'"),
        ),
        Index(
            "uq_datasets_live_prefix",
            "prefix",
            unique=True,
            postgresql_where=text("status != 'deleted'"),
            sqlite_where=text("status != 'deleted'"),
        ),
        CheckConstraint(
            _values_check("status", DATASET_STATUSES),
            name="ck_datasets_status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = _uuid_identifier_column()
    upload_session_id = Column(
        Integer,
        ForeignKey("dataset_upload_sessions.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
        index=True,
    )
    name = Column(String(256), nullable=False)
    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        default=LEGACY_ORGANIZATION_ID,
        server_default=LEGACY_ORGANIZATION_ID,
        index=True,
    )
    owner_subject = Column(String(256), nullable=False, index=True)
    prefix = Column(String(1024), nullable=False)
    status = Column(String(32), nullable=False, default="ready")
    manifest_s3_key = Column(String(1024), nullable=False)
    file_count = Column(Integer, nullable=False)
    image_count = Column(Integer, nullable=False)
    total_bytes = Column(PORTABLE_BIGINT, nullable=False)
    ready_at = Column(DateTime(timezone=True), nullable=False)
    deletion_requested_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class DatasetUploadSession(RequiredTimestampMixin, Base):
    """Durable ownership and quota boundary for one direct S3 upload batch."""

    __tablename__ = "dataset_upload_sessions"
    __table_args__ = (
        Index("ix_dataset_upload_sessions_expiry", "status", "expires_at"),
        Index(
            "uq_dataset_upload_sessions_active_org_name",
            "organization_id",
            "dataset_name",
            unique=True,
            postgresql_where=text(
                "status IN ('initializing', 'uploading', 'finalizing', 'failed')"
            ),
            sqlite_where=text(
                "status IN ('initializing', 'uploading', 'finalizing', 'failed')"
            ),
        ),
        CheckConstraint(
            _values_check("status", DATASET_UPLOAD_SESSION_STATUSES),
            name="ck_dataset_upload_sessions_status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid4()),
        index=True,
    )
    dataset_name = Column(String(256), nullable=False, index=True)
    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        default=LEGACY_ORGANIZATION_ID,
        server_default=LEGACY_ORGANIZATION_ID,
        index=True,
    )
    status = Column(String(32), nullable=False, default="initializing")
    total_bytes = Column(PORTABLE_BIGINT, nullable=False)
    file_count = Column(Integer, nullable=False)
    part_size = Column(Integer, nullable=False)
    created_by = Column(String(256), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)

    files = relationship(
        "DatasetUploadFile",
        back_populates="upload_session",
        cascade="all, delete-orphan",
    )


class DatasetUploadFile(RequiredTimestampMixin, Base):
    """S3 multipart upload state for one file in a dataset session."""

    __tablename__ = "dataset_upload_files"
    __table_args__ = (
        UniqueConstraint(
            "upload_session_id",
            "filename",
            name="uq_dataset_upload_file_name",
        ),
        CheckConstraint(
            _values_check("status", DATASET_UPLOAD_FILE_STATUSES),
            name="ck_dataset_upload_files_status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(
        String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid4()),
        index=True,
    )
    upload_session_id = Column(
        Integer,
        ForeignKey("dataset_upload_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename = Column(String(512), nullable=False)
    s3_key = Column(String(1024), nullable=False)
    size_bytes = Column(PORTABLE_BIGINT, nullable=False)
    content_type = Column(String(256), nullable=False)
    multipart_upload_id = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="initializing")
    completed_parts = Column(PORTABLE_JSON, nullable=True)
    etag = Column(String(256), nullable=True)
    last_error = Column(Text, nullable=True)

    upload_session = relationship(
        "DatasetUploadSession",
        back_populates="files",
    )


class Mission(Base):
    """Tracks the lifecycle of a single drone processing mission.

    Replaces the in-memory ``mission_states`` dict and ``mission_state.json`` files.
    """

    __tablename__ = "missions"
    __table_args__ = (
        CheckConstraint(
            _values_check("status", MISSION_STATUSES),
            name="ck_missions_status",
        ),
        CheckConstraint(
            _values_check("aggregation_status", AGGREGATION_STATUSES),
            name="ck_missions_aggregation_status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    vol_id = Column(String(256), unique=True, nullable=False, index=True)
    owner_subject = Column(
        String(256),
        nullable=False,
        default="local-development",
        server_default="legacy-unassigned",
        index=True,
    )
    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        default=LEGACY_ORGANIZATION_ID,
        server_default=LEGACY_ORGANIZATION_ID,
        index=True,
    )
    status = Column(String(32), nullable=False, default="pending")
    pipeline = Column(String(32), nullable=False, default="modern")

    # S3 references (replace filesystem paths)
    dataset_id = Column(
        Integer,
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    input_dataset = Column(String(1024), nullable=True)  # S3 prefix for input images
    workspace_prefix = Column(String(1024), nullable=True)  # S3 prefix for mission workspace

    # Pipeline parameters (full JSON blob from mission launch message)
    params = Column(PORTABLE_JSON, nullable=True)

    # Progress tracking
    current_step = Column(String(64), nullable=True)
    progress = Column(Integer, default=0)
    retry_count = Column(Integer, nullable=False, default=0)

    # Per-service state snapshots (e.g. {"COLMAP": {...}, "TILER": {...}, "IA": {...}})
    service_states = Column(PORTABLE_JSON, nullable=True, default=dict)

    # Resume info (replaces mission_state.json's resume fields)
    resume_info = Column(PORTABLE_JSON, nullable=True)

    # Error tracking
    error_message = Column(Text, nullable=True)

    # Tiling metadata (used by aggregation)
    total_tiles = Column(Integer, nullable=True)
    tiles_received = Column(Integer, default=0)
    ortho_s3_key = Column(String(1024), nullable=True)
    tiling_metadata = Column(PORTABLE_JSON, nullable=True)
    aggregation_status = Column(String(32), nullable=False, default="pending")
    aggregation_completed_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    detections = relationship("Detection", back_populates="mission", cascade="all, delete-orphan")
    processed_tiles = relationship(
        "ProcessedTile",
        back_populates="mission",
        cascade="all, delete-orphan",
    )
    analysis_runs = relationship(
        "AIAnalysisRun",
        back_populates="mission",
        cascade="all, delete-orphan",
    )
    map_features = relationship(
        "MapFeature",
        back_populates="mission",
        cascade="all, delete-orphan",
    )
    gcp_sets = relationship(
        "GcpSet",
        back_populates="mission",
        cascade="all, delete-orphan",
    )
    gcp_audit_events = relationship(
        "GcpAuditEvent",
        back_populates="mission",
        passive_deletes=True,
    )
    feature_audit_events = relationship(
        "MapFeatureAuditEvent",
        back_populates="mission",
        cascade="all, delete-orphan",
    )
    layer_styles = relationship(
        "RasterLayerStyle",
        back_populates="mission",
        cascade="all, delete-orphan",
    )
    logs = relationship("MissionLog", back_populates="mission", cascade="all, delete-orphan")
    stage_runs = relationship(
        "MissionStageRun",
        back_populates="mission",
        cascade="all, delete-orphan",
    )
    artifacts = relationship(
        "MissionArtifact",
        back_populates="mission",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Mission(vol_id={self.vol_id!r}, status={self.status!r}, step={self.current_step!r})>"


class MissionStageRun(RequiredTimestampMixin, Base):
    """One idempotent execution attempt for a declared mission stage."""

    __tablename__ = "mission_stage_runs"
    __table_args__ = (
        Index(
            "ix_mission_stage_runs_mission_stage",
            "mission_id",
            "stage",
            "attempt",
        ),
        Index(
            "ix_mission_stage_runs_recovery",
            "status",
            "heartbeat_at",
        ),
        Index(
            "ix_mission_stage_runs_dispatch",
            "status",
            "executor",
            "scheduled_at",
        ),
        Index(
            "ix_mission_stage_runs_job_name",
            "job_name",
            unique=True,
        ),
        UniqueConstraint(
            "mission_id",
            "stage",
            "attempt",
            name="uq_mission_stage_run_attempt",
        ),
        UniqueConstraint("idempotency_key", name="uq_mission_stage_run_idempotency"),
        CheckConstraint(
            _values_check("stage", MISSION_STAGE_TYPES),
            name="ck_mission_stage_runs_stage",
        ),
        CheckConstraint(
            _values_check("status", MISSION_STAGE_RUN_STATUSES),
            name="ck_mission_stage_runs_status",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_mission_stage_runs_progress",
        ),
        CheckConstraint(
            "attempt >= 0",
            name="ck_mission_stage_runs_attempt",
        ),
        CheckConstraint(
            _values_check("resource_class", MISSION_RESOURCE_CLASSES),
            name="ck_mission_stage_runs_resource_class",
        ),
        CheckConstraint(
            "dispatch_attempts >= 0",
            name="ck_mission_stage_runs_dispatch_attempts",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_mission_stage_runs_idempotency_length",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid4()), index=True)
    mission_id = Column(Integer, ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True)
    stage = Column(String(32), nullable=False)
    attempt = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="blocked")
    progress = Column(Integer, nullable=False, default=0)
    current_step = Column(String(64), nullable=True)
    idempotency_key = Column(String(64), nullable=False)
    executor = Column(String(256), nullable=True)
    resource_class = Column(String(64), nullable=False, default="cpu-standard")
    job_name = Column(String(253), nullable=True)
    dispatch_attempts = Column(Integer, nullable=False, default=0)
    dispatch_error = Column(Text, nullable=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    parameters = Column(PORTABLE_JSON, nullable=False, default=dict)
    upstream_artifact_ids = Column(PORTABLE_JSON, nullable=False, default=list)
    provenance = Column(PORTABLE_JSON, nullable=False, default=dict)
    quality_metrics = Column(PORTABLE_JSON, nullable=False, default=dict)
    error_message = Column(Text, nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    mission = relationship("Mission", back_populates="stage_runs")
    artifacts = relationship(
        "MissionArtifact",
        back_populates="stage_run",
        cascade="all, delete-orphan",
    )
    detection_shard_receipts = relationship(
        "DetectionShardReceipt",
        back_populates="stage_run",
        cascade="all, delete-orphan",
    )


class DetectionShardReceipt(Base):
    """Immutable proof that one indexed detection shard published its result."""

    __tablename__ = "detection_shard_receipts"
    __table_args__ = (
        UniqueConstraint(
            "stage_run_id",
            "plan_checksum_sha256",
            "shard_index",
            name="uq_detection_shard_receipt_identity",
        ),
        CheckConstraint(
            "length(plan_checksum_sha256) = 64",
            name="ck_detection_shard_receipts_plan_checksum_length",
        ),
        CheckConstraint(
            "length(result_checksum_sha256) = 64",
            name="ck_detection_shard_receipts_result_checksum_length",
        ),
        CheckConstraint(
            "shard_count >= 2 AND shard_count <= 256",
            name="ck_detection_shard_receipts_shard_count",
        ),
        CheckConstraint(
            "shard_index >= 0 AND shard_index < shard_count",
            name="ck_detection_shard_receipts_shard_index",
        ),
        CheckConstraint(
            "tile_count > 0",
            name="ck_detection_shard_receipts_tile_count",
        ),
        CheckConstraint(
            "result_size_bytes > 0",
            name="ck_detection_shard_receipts_result_size",
        ),
        Index(
            "ix_detection_shard_receipts_run_plan",
            "stage_run_id",
            "plan_checksum_sha256",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stage_run_id = Column(
        Integer,
        ForeignKey("mission_stage_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_checksum_sha256 = Column(String(64), nullable=False)
    shard_index = Column(Integer, nullable=False)
    shard_count = Column(Integer, nullable=False)
    tile_count = Column(Integer, nullable=False)
    result_key = Column(String(1024), nullable=False)
    result_checksum_sha256 = Column(String(64), nullable=False)
    result_size_bytes = Column(PORTABLE_BIGINT, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    stage_run = relationship(
        "MissionStageRun",
        back_populates="detection_shard_receipts",
    )


class MissionArtifact(Base):
    """Immutable output identity produced by exactly one stage run."""

    __tablename__ = "mission_artifacts"
    __table_args__ = (
        CheckConstraint(
            "length(checksum_sha256) = 64",
            name="ck_mission_artifacts_sha256_length",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid4()), index=True)
    mission_id = Column(Integer, ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_run_id = Column(
        Integer,
        ForeignKey("mission_stage_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String(64), nullable=False, index=True)
    uri = Column(String(2048), nullable=False)
    checksum_sha256 = Column(String(64), nullable=False)
    size_bytes = Column(PORTABLE_BIGINT, nullable=True)
    artifact_metadata = Column(PORTABLE_JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    mission = relationship("Mission", back_populates="artifacts")
    stage_run = relationship("MissionStageRun", back_populates="artifacts")
    parent_edges = relationship(
        "MissionArtifactParent",
        foreign_keys="MissionArtifactParent.artifact_id",
        cascade="all, delete-orphan",
    )


class MissionArtifactParent(Base):
    """Exact immutable parent edge between two mission artifacts."""

    __tablename__ = "mission_artifact_parents"
    __table_args__ = (
        Index(
            "ix_mission_artifact_parents_parent",
            "parent_artifact_id",
        ),
        CheckConstraint(
            "artifact_id <> parent_artifact_id",
            name="ck_mission_artifact_parent_not_self",
        ),
    )

    artifact_id = Column(
        Integer,
        ForeignKey("mission_artifacts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    parent_artifact_id = Column(
        Integer,
        ForeignKey("mission_artifacts.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    relation = Column(String(64), nullable=False, default="derived_from")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    parent = relationship(
        "MissionArtifact",
        foreign_keys=[parent_artifact_id],
    )


class Detection(Base):
    """A single object detection from a tile, with PostGIS geometry.

    Replaces in-memory detection aggregation in the processing worker.
    Supports spatial queries (ST_Within, ST_Intersects) and spatial-index
    based deduplication.
    """

    __tablename__ = "detections"
    __table_args__ = (
        Index("ix_detections_vol_tile", "vol_id", "tile_index"),
        Index("ix_detections_geometry", "geometry", postgresql_using="gist"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    mission_id = Column(Integer, ForeignKey("missions.id", ondelete="CASCADE"), nullable=False)
    vol_id = Column(String(256), nullable=False, index=True)
    tile_index = Column(Integer, nullable=False)

    # Classification
    class_name = Column(String(128), nullable=False, index=True)
    class_id = Column(Integer, nullable=True)
    confidence = Column(Float, nullable=False)

    # PostGIS polygon in WGS84 (EPSG:4326)
    geometry = Column(Geometry("POLYGON", srid=4326), nullable=True)

    # Pixel coordinates (in global ortho space)
    pixel_x = Column(Float, nullable=True)
    pixel_y = Column(Float, nullable=True)

    # GPS coordinates
    geo_lon = Column(Float, nullable=True)
    geo_lat = Column(Float, nullable=True)

    # Raw polygon vertices (OBB corners as JSON array of [x,y] pairs)
    segment = Column(PORTABLE_JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Relationships
    mission = relationship("Mission", back_populates="detections")

    def __repr__(self) -> str:
        return (
            f"<Detection(vol_id={self.vol_id!r}, tile={self.tile_index}, "
            f"class={self.class_name!r}, conf={self.confidence:.2f})>"
        )


class ProcessedTile(Base):
    """Durable receipt for every AI tile, including zero-detection tiles."""

    __tablename__ = "processed_tiles"
    __table_args__ = (
        UniqueConstraint(
            "vol_id",
            "tile_index",
            name="uq_processed_tile_vol_index",
        ),
        Index("ix_processed_tiles_mission", "mission_id", "tile_index"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    mission_id = Column(
        Integer,
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
    )
    vol_id = Column(String(256), nullable=False, index=True)
    tile_index = Column(Integer, nullable=False)
    detection_count = Column(Integer, nullable=False, default=0)
    received_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    mission = relationship("Mission", back_populates="processed_tiles")


class AIAnalysisRun(RequiredTimestampMixin, Base):
    """Durable, independently rerunnable AI analysis of a mission COG."""

    __tablename__ = "ai_analysis_runs"
    __table_args__ = (
        Index("ix_ai_runs_mission_created", "mission_id", "created_at"),
        Index("ix_ai_runs_recovery", "status", "heartbeat_at"),
        CheckConstraint(
            _values_check("status", ANALYSIS_RUN_STATUSES),
            name="ck_ai_analysis_runs_status",
        ),
        CheckConstraint(
            _values_check("phase", ANALYSIS_RUN_PHASES),
            name="ck_ai_analysis_runs_phase",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = _uuid_identifier_column()
    mission_id = Column(
        Integer,
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
    )
    vol_id = Column(String(256), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)
    color = Column(String(9), nullable=False, default="#f43f5e")
    tags = Column(PORTABLE_JSON, nullable=False, default=list)

    backend = Column(String(32), nullable=False, default="yolo")
    model_variant = Column(String(128), nullable=True)
    prompt = Column(String(256), nullable=True)
    classes = Column(PORTABLE_JSON, nullable=False, default=list)
    confidence = Column(Float, nullable=False, default=0.3)
    tile_size = Column(Integer, nullable=False, default=1024)
    persist_results = Column(Boolean, nullable=False, default=True)

    status = Column(String(32), nullable=False, default="queued")
    phase = Column(String(64), nullable=False, default="queued")
    total_tiles = Column(Integer, nullable=False, default=0)
    tiles_completed = Column(Integer, nullable=False, default=0)
    detection_count = Column(Integer, nullable=False, default=0)
    progress = Column(Integer, nullable=False, default=0)
    retry_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    finalization_owner = Column(String(256), nullable=True)
    finalization_lease_until = Column(DateTime(timezone=True), nullable=True)

    ortho_s3_key = Column(String(1024), nullable=False)
    result_s3_key = Column(String(1024), nullable=True)
    tiling_metadata = Column(PORTABLE_JSON, nullable=True)
    model_manifest = Column(PORTABLE_JSON, nullable=True)
    heartbeat_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(256), nullable=True)

    mission = relationship("Mission", back_populates="analysis_runs")
    tiles = relationship(
        "AIAnalysisTile",
        back_populates="analysis_run",
        cascade="all, delete-orphan",
    )
    features = relationship(
        "MapFeature",
        back_populates="analysis_run",
        cascade="all, delete-orphan",
    )


class AIAnalysisTile(Base):
    """Recovery journal for one tile of an AI analysis campaign."""

    __tablename__ = "ai_analysis_tiles"
    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "tile_index",
            name="uq_ai_analysis_tile_run_index",
        ),
        Index("ix_ai_analysis_tiles_status", "analysis_run_id", "status"),
        CheckConstraint(
            _values_check("status", ANALYSIS_TILE_STATUSES),
            name="ck_ai_analysis_tiles_status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_run_id = Column(
        Integer,
        ForeignKey("ai_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    tile_index = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="queued")
    tile_s3_key = Column(String(1024), nullable=False)
    result_s3_key = Column(String(1024), nullable=True)
    result_sha256 = Column(String(64), nullable=True)
    result_size_bytes = Column(BigInteger, nullable=True)
    result_attempt = Column(Integer, nullable=True)
    offset_x = Column(Integer, nullable=False)
    offset_y = Column(Integer, nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    bounds_wgs84 = Column(PORTABLE_JSON, nullable=True)
    detection_count = Column(Integer, nullable=False, default=0)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    queued_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    analysis_run = relationship("AIAnalysisRun", back_populates="tiles")


class MapFeature(RequiredTimestampMixin, Base):
    """Searchable PostGIS feature created manually or by a persisted AI run."""

    __tablename__ = "map_features"
    __table_args__ = (
        Index("ix_map_features_geometry", "geometry", postgresql_using="gist"),
        Index("ix_map_features_mission_source", "mission_id", "source"),
        Index("ix_map_features_run_class", "analysis_run_id", "class_name"),
        Index("ix_map_features_name", "name"),
        Index("ix_map_features_visibility", "mission_id", "deleted_at"),
        Index("ix_map_features_review", "mission_id", "reviewed_at"),
        CheckConstraint(
            _values_check("source", MAP_FEATURE_SOURCES),
            name="ck_map_features_source",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    feature_id = Column(
        String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid4()),
        index=True,
    )
    mission_id = Column(
        Integer,
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
    )
    analysis_run_id = Column(
        Integer,
        ForeignKey("ai_analysis_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    vol_id = Column(String(256), nullable=False, index=True)
    source = Column(String(32), nullable=False, default="manual")
    geometry = Column(
        Geometry("GEOMETRY", srid=4326, spatial_index=False),
        nullable=False,
    )
    name = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)
    color = Column(String(9), nullable=False, default="#10b981")
    tags = Column(PORTABLE_JSON, nullable=False, default=list)
    properties = Column(PORTABLE_JSON, nullable=False, default=dict)
    class_name = Column(String(128), nullable=True, index=True)
    confidence = Column(Float, nullable=True)
    tile_index = Column(Integer, nullable=True)
    created_by = Column(String(256), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by = Column(String(256), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(256), nullable=True)
    deletion_reason = Column(Text, nullable=True)

    mission = relationship("Mission", back_populates="map_features")
    analysis_run = relationship("AIAnalysisRun", back_populates="features")
    audit_events = relationship(
        "MapFeatureAuditEvent",
        back_populates="feature",
        cascade="all, delete-orphan",
    )


class MapFeatureAuditEvent(AppendOnlyAuditMixin, Base):
    """Append-only operator audit trail for feature corrections."""

    __tablename__ = "map_feature_audit_events"
    __table_args__ = (
        Index("ix_feature_audit_mission_created", "mission_id", "created_at"),
        Index("ix_feature_audit_feature_created", "feature_id", "created_at"),
        CheckConstraint(
            _values_check("action", MAP_FEATURE_AUDIT_ACTIONS),
            name="ck_map_feature_audit_action",
        ),
    )

    mission_id = Column(
        Integer,
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
    )
    feature_id = Column(
        Integer,
        ForeignKey("map_features.id", ondelete="CASCADE"),
        nullable=False,
    )
    action = Column(String(32), nullable=False)
    reason = Column(Text, nullable=True)

    mission = relationship("Mission", back_populates="feature_audit_events")
    feature = relationship("MapFeature", back_populates="audit_events")


class GcpSet(RequiredTimestampMixin, Base):
    """One imported, provenance-preserving ground-control collection."""

    __tablename__ = "gcp_sets"
    __table_args__ = (
        Index("ix_gcp_sets_mission_created", "mission_id", "created_at"),
        UniqueConstraint("mission_id", "name", name="uq_gcp_set_mission_name"),
        CheckConstraint("version >= 1", name="ck_gcp_sets_version"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    set_id = _uuid_identifier_column()
    mission_id = Column(
        Integer,
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
    )
    vol_id = Column(String(256), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    source_filename = Column(String(512), nullable=False)
    source_format = Column(String(64), nullable=False)
    source_crs = Column(String(128), nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    created_by = Column(String(256), nullable=False)
    version = Column(Integer, nullable=False, default=1)

    mission = relationship("Mission", back_populates="gcp_sets")
    points = relationship(
        "GcpPoint",
        back_populates="gcp_set",
        cascade="all, delete-orphan",
        order_by="GcpPoint.external_id",
    )
    audit_events = relationship(
        "GcpAuditEvent",
        back_populates="gcp_set",
        passive_deletes=True,
        order_by="GcpAuditEvent.created_at",
    )


class GcpPoint(RequiredTimestampMixin, Base):
    """Survey coordinate, role, covariance and map geometry for one GCP."""

    __tablename__ = "gcp_points"
    __table_args__ = (
        UniqueConstraint("gcp_set_id", "external_id", name="uq_gcp_point_external_id"),
        Index("ix_gcp_points_geometry", "geometry", postgresql_using="gist"),
        Index("ix_gcp_points_mission_role", "mission_id", "role"),
        CheckConstraint(
            _values_check("role", GCP_ROLES),
            name="ck_gcp_points_role",
        ),
        CheckConstraint(
            "horizontal_accuracy_m > 0 AND vertical_accuracy_m > 0 AND image_accuracy_px > 0",
            name="ck_gcp_points_accuracy",
        ),
        CheckConstraint("version >= 1", name="ck_gcp_points_version"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    point_id = Column(
        String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid4()),
        index=True,
    )
    gcp_set_id = Column(
        Integer,
        ForeignKey("gcp_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    mission_id = Column(
        Integer,
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_id = Column(String(160), nullable=False)
    geometry = Column(
        Geometry("POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    source_x = Column(Float, nullable=False)
    source_y = Column(Float, nullable=False)
    source_z = Column(Float, nullable=False)
    altitude_m = Column(Float, nullable=False)
    role = Column(String(32), nullable=False, default="adjustment")
    horizontal_accuracy_m = Column(Float, nullable=False)
    vertical_accuracy_m = Column(Float, nullable=False)
    image_accuracy_px = Column(Float, nullable=False)
    properties = Column(PORTABLE_JSON, nullable=False, default=dict)
    version = Column(Integer, nullable=False, default=1)

    gcp_set = relationship("GcpSet", back_populates="points")
    observations = relationship(
        "GcpObservation",
        back_populates="point",
        cascade="all, delete-orphan",
        order_by="GcpObservation.image_name",
    )


class GcpObservation(RequiredTimestampMixin, Base):
    """Candidate, marked or skipped image-space observation of a GCP."""

    __tablename__ = "gcp_observations"
    __table_args__ = (
        UniqueConstraint("gcp_point_id", "image_name", name="uq_gcp_observation_image"),
        Index("ix_gcp_observations_point_status", "gcp_point_id", "status"),
        CheckConstraint(
            _values_check("status", GCP_OBSERVATION_STATUSES),
            name="ck_gcp_observations_status",
        ),
        CheckConstraint(
            "(status != 'marked') OR (pixel_x IS NOT NULL AND pixel_y IS NOT NULL)",
            name="ck_gcp_observations_marked_pixel",
        ),
        CheckConstraint(
            "(image_width_px IS NULL AND image_height_px IS NULL) OR (image_width_px > 0 AND image_height_px > 0)",
            name="ck_gcp_observations_image_dimensions",
        ),
        CheckConstraint("version >= 1", name="ck_gcp_observations_version"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    observation_id = Column(
        String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid4()),
        index=True,
    )
    gcp_point_id = Column(
        Integer,
        ForeignKey("gcp_points.id", ondelete="CASCADE"),
        nullable=False,
    )
    image_name = Column(String(512), nullable=False)
    image_s3_key = Column(String(1024), nullable=True)
    status = Column(String(32), nullable=False, default="candidate")
    pixel_x = Column(Float, nullable=True)
    pixel_y = Column(Float, nullable=True)
    candidate_distance_m = Column(Float, nullable=True)
    candidate_method = Column(String(32), nullable=True)
    projected_pixel_x = Column(Float, nullable=True)
    projected_pixel_y = Column(Float, nullable=True)
    image_width_px = Column(Integer, nullable=True)
    image_height_px = Column(Integer, nullable=True)
    image_longitude = Column(Float, nullable=True)
    image_latitude = Column(Float, nullable=True)
    created_by = Column(String(256), nullable=False)
    updated_by = Column(String(256), nullable=False)
    version = Column(Integer, nullable=False, default=1)

    point = relationship("GcpPoint", back_populates="observations")


class GcpAuditEvent(AppendOnlyAuditMixin, Base):
    """Database-protected append-only history of GCP workspace actions."""

    __tablename__ = "gcp_audit_events"
    __table_args__ = (
        Index("ix_gcp_audit_set_created", "gcp_set_id", "created_at"),
        Index("ix_gcp_audit_mission_created", "mission_id", "created_at"),
        CheckConstraint(
            _values_check("action", GCP_AUDIT_ACTIONS),
            name="ck_gcp_audit_action",
        ),
    )

    mission_id = Column(
        Integer,
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
    )
    gcp_set_id = Column(
        Integer,
        ForeignKey("gcp_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    gcp_point_id = Column(
        Integer,
        ForeignKey("gcp_points.id", ondelete="SET NULL"),
        nullable=True,
    )
    gcp_observation_id = Column(
        Integer,
        ForeignKey("gcp_observations.id", ondelete="SET NULL"),
        nullable=True,
    )
    action = Column(String(48), nullable=False)

    mission = relationship("Mission", back_populates="gcp_audit_events")
    gcp_set = relationship("GcpSet", back_populates="audit_events")
    point = relationship("GcpPoint")
    observation = relationship("GcpObservation")


class RasterLayerStyle(RequiredTimestampMixin, Base):
    """Named mutable display recipe, separate from an immutable raster."""

    __tablename__ = "raster_layer_styles"
    __table_args__ = (
        UniqueConstraint(
            "mission_id",
            "layer_key",
            "name",
            name="uq_raster_layer_style_name",
        ),
        Index("ix_raster_layer_styles_mission_layer", "mission_id", "layer_key"),
        CheckConstraint("version >= 1", name="ck_raster_layer_styles_version"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    style_id = Column(
        String(36),
        unique=True,
        nullable=False,
        default=lambda: str(uuid4()),
        index=True,
    )
    mission_id = Column(
        Integer,
        ForeignKey("missions.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_id = Column(
        Integer,
        ForeignKey("mission_artifacts.id", ondelete="CASCADE"),
        nullable=True,
    )
    layer_key = Column(String(64), nullable=False)
    name = Column(String(160), nullable=False)
    style = Column(PORTABLE_JSON, nullable=False, default=dict)
    is_default = Column(Boolean, nullable=False, default=False)
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(String(256), nullable=False)
    updated_by = Column(String(256), nullable=False)

    mission = relationship("Mission", back_populates="layer_styles")
    artifact = relationship("MissionArtifact")


class MissionLog(Base):
    """Persisted log entry from the pipeline-status Kafka stream.

    Replaces the volatile in-memory ``status_history`` deque in the dashboard API.
    """

    __tablename__ = "mission_logs"
    __table_args__ = (
        Index("ix_logs_mission_created", "mission_id", "created_at"),
        CheckConstraint(
            f"status IS NULL OR {_values_check('status', PIPELINE_LOG_STATUSES)}",
            name="ck_mission_logs_status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    mission_id = Column(Integer, ForeignKey("missions.id", ondelete="CASCADE"), nullable=False)
    vol_id = Column(String(256), nullable=False, index=True)

    service = Column(String(32), nullable=True)  # COLMAP, TILER, IA
    step = Column(String(64), nullable=True)
    status = Column(String(32), nullable=True)  # processing, success, error
    progress = Column(Integer, nullable=True)
    message = Column(Text, nullable=True)
    details = Column(PORTABLE_JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    # Relationships
    mission = relationship("Mission", back_populates="logs")

    def __repr__(self) -> str:
        return f"<MissionLog(vol_id={self.vol_id!r}, service={self.service!r}, step={self.step!r})>"


class InboxEvent(Base):
    """Durable consumer receipt used to suppress event reprocessing."""

    __tablename__ = "inbox_events"
    __table_args__ = (
        UniqueConstraint(
            "consumer_group",
            "event_id",
            name="uq_inbox_consumer_event",
        ),
        Index("ix_inbox_source_offset", "source_topic", "source_partition", "source_offset"),
        Index("ix_inbox_claim", "status", "locked_at"),
        CheckConstraint(
            _values_check("status", INBOX_EVENT_STATUSES),
            name="ck_inbox_events_status",
        ),
    )

    id = Column(PORTABLE_BIGINT, primary_key=True, autoincrement=True)
    consumer_group = Column(String(256), nullable=False)
    event_id = Column(String(128), nullable=False)
    event_type = Column(String(64), nullable=False)
    source_topic = Column(String(256), nullable=True)
    source_partition = Column(Integer, nullable=True)
    source_offset = Column(BigInteger, nullable=True)
    payload = Column(PORTABLE_JSON, nullable=False)
    status = Column(String(32), nullable=False, default="processing")
    attempts = Column(Integer, nullable=False, default=1)
    last_error = Column(Text, nullable=True)
    received_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    processed_at = Column(DateTime(timezone=True), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    locked_by = Column(String(256), nullable=True)


class OutboxEvent(Base):
    """Event persisted in the same transaction as its domain mutation."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_outbox_event_id"),
        Index("ix_outbox_dispatch", "status", "available_at", "created_at"),
        CheckConstraint(
            _values_check("status", OUTBOX_EVENT_STATUSES),
            name="ck_outbox_events_status",
        ),
    )

    id = Column(PORTABLE_BIGINT, primary_key=True, autoincrement=True)
    event_id = Column(String(128), nullable=False)
    event_type = Column(String(64), nullable=False)
    topic = Column(String(256), nullable=False)
    message_key = Column(String(512), nullable=True)
    payload = Column(PORTABLE_JSON, nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    locked_at = Column(DateTime(timezone=True), nullable=True)
    locked_by = Column(String(256), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    published_at = Column(DateTime(timezone=True), nullable=True)
    dead_at = Column(DateTime(timezone=True), nullable=True)


class APIRateLimitBucket(Base):
    """Shared token-bucket state for horizontally scaled API replicas."""

    __tablename__ = "api_rate_limit_buckets"

    key_hash = Column(String(64), primary_key=True)
    tokens = Column(Float, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, index=True)


# ---------------------------------------------------------------------------
# Helper queries
# ---------------------------------------------------------------------------


def get_or_create_mission(
    session: Session,
    vol_id: str,
    **kwargs: Any,
) -> Mission:
    """Get an existing mission by vol_id or create a new one."""
    mission = cast(
        Mission | None,
        session.query(Mission).filter(Mission.vol_id == vol_id).first(),
    )
    if mission is None:
        mission = Mission(vol_id=vol_id, **kwargs)
        session.add(mission)
        session.flush()
        logger.info("Created mission: %s", vol_id)
    return mission


def update_mission_progress(
    session: Session,
    vol_id: str,
    step: str,
    progress: int,
    status: str = "processing",
    service: str | None = None,
    error_message: str | None = None,
) -> Mission | None:
    """Update mission progress and optionally its status."""
    mission = cast(
        Mission | None,
        session.query(Mission).filter(Mission.vol_id == vol_id).first(),
    )
    if mission is None:
        logger.warning("Mission not found for progress update: %s", vol_id)
        return None
    mission.current_step = step
    mission.progress = progress
    mission.status = status
    if error_message:
        mission.error_message = error_message
    if service:
        states = mission.service_states or {}
        states[service] = {"step": step, "progress": progress, "status": status}
        mission.service_states = states
    mission.updated_at = datetime.now(UTC)
    return mission


def count_received_tiles(session: Session, vol_id: str) -> int:
    """Count durable AI responses, including tiles with no detections."""
    return int(session.query(ProcessedTile).filter(ProcessedTile.vol_id == vol_id).count())


def get_mission_detections(session: Session, vol_id: str) -> list[Detection]:
    """Get all detections for a mission, ordered by tile index."""
    return cast(
        list[Detection],
        session.query(Detection).filter(Detection.vol_id == vol_id).order_by(Detection.tile_index, Detection.id).all(),
    )
