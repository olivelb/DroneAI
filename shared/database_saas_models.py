"""SaaS policy and dataset models."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
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
    text,
)
from sqlalchemy.orm import relationship

from shared.database_schema import (
    DATASET_STATUSES,
    DATASET_UPLOAD_FILE_STATUSES,
    DATASET_UPLOAD_SESSION_STATUSES,
    ORGANIZATION_USAGE_ACTIONS,
    PORTABLE_BIGINT,
    PORTABLE_JSON,
    Base,
    RequiredTimestampMixin,
    _uuid_identifier_column,
    _values_check,
)
from shared.tenancy import LEGACY_ORGANIZATION_ID


class OrganizationSaasPolicy(RequiredTimestampMixin, Base):
    """Commercial capacity and lifecycle policy, separate from science."""

    __tablename__ = "organization_saas_policies"
    __table_args__ = (
        CheckConstraint(
            "storage_limit_bytes IS NULL OR storage_limit_bytes >= 1",
            name="ck_organization_saas_policy_storage_limit",
        ),
        CheckConstraint(
            "concurrent_stage_runs_limit IS NULL OR concurrent_stage_runs_limit >= 1",
            name="ck_organization_saas_policy_stage_limit",
        ),
        CheckConstraint(
            "request_rate_per_minute IS NULL OR request_rate_per_minute >= 1",
            name="ck_organization_saas_policy_request_rate",
        ),
        CheckConstraint(
            "request_burst IS NULL OR request_burst >= 1",
            name="ck_organization_saas_policy_request_burst",
        ),
        CheckConstraint(
            "(request_rate_per_minute IS NULL) = (request_burst IS NULL)",
            name="ck_organization_saas_policy_request_pair",
        ),
        CheckConstraint(
            "retention_days IS NULL OR retention_days >= 1",
            name="ck_organization_saas_policy_retention",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_organization_saas_policy_version",
        ),
    )

    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    storage_limit_bytes = Column(PORTABLE_BIGINT, nullable=True)
    concurrent_stage_runs_limit = Column(Integer, nullable=True)
    request_rate_per_minute = Column(Integer, nullable=True)
    request_burst = Column(Integer, nullable=True)
    retention_days = Column(Integer, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(String(256), nullable=False)
    updated_by = Column(String(256), nullable=False)


class OrganizationUsageEvent(Base):
    """Append-only commercial usage and policy-decision ledger."""

    __tablename__ = "organization_usage_events"
    __table_args__ = (
        Index(
            "ix_organization_usage_org_created",
            "organization_id",
            "created_at",
        ),
        Index(
            "ix_organization_usage_resource",
            "organization_id",
            "resource_type",
            "resource_id",
        ),
        CheckConstraint(
            _values_check("action", ORGANIZATION_USAGE_ACTIONS),
            name="ck_organization_usage_action",
        ),
    )

    id = Column(PORTABLE_BIGINT, primary_key=True, autoincrement=True)
    event_id = _uuid_identifier_column()
    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    action = Column(String(48), nullable=False)
    resource_type = Column(String(48), nullable=False)
    resource_id = Column(String(256), nullable=False)
    quantity = Column(PORTABLE_BIGINT, nullable=True)
    unit = Column(String(32), nullable=True)
    actor_subject = Column(String(256), nullable=False)
    idempotency_key = Column(String(256), nullable=True, unique=True)
    details = Column(PORTABLE_JSON, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class OrganizationRequestBucket(Base):
    """Transactional organization-wide API request token bucket."""

    __tablename__ = "organization_request_buckets"

    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tokens = Column(Float, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, index=True)


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
            postgresql_where=text("status IN ('initializing', 'uploading', 'finalizing', 'failed')"),
            sqlite_where=text("status IN ('initializing', 'uploading', 'finalizing', 'failed')"),
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
