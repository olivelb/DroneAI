"""Mission execution and artifact models."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from geoalchemy2 import Geometry
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
    event,
)
from sqlalchemy.orm import relationship

from shared.database_schema import (
    AGGREGATION_STATUSES,
    MISSION_RESOURCE_CLASSES,
    MISSION_STAGE_RUN_STATUSES,
    MISSION_STAGE_TYPES,
    MISSION_STATUSES,
    PORTABLE_BIGINT,
    PORTABLE_JSON,
    Base,
    RequiredTimestampMixin,
    _values_check,
)
from shared.tenancy import LEGACY_ORGANIZATION_ID, mission_prefix


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
    workspace_prefix = Column(String(1024), nullable=False)

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


@event.listens_for(Mission, "init", propagate=True)  # type: ignore[untyped-decorator]
def _initialize_mission_workspace(
    _target: Mission,
    _args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    """Keep locally constructed and imported missions on the canonical namespace."""

    if kwargs.get("workspace_prefix") is not None:
        return
    vol_id = kwargs.get("vol_id")
    if vol_id is None:
        return
    kwargs["workspace_prefix"] = mission_prefix(
        str(kwargs.get("organization_id") or LEGACY_ORGANIZATION_ID),
        str(vol_id),
    )


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
