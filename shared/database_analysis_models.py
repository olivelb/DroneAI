"""Analysis, map, GCP, and raster models."""

from datetime import UTC, datetime
from uuid import uuid4

from geoalchemy2 import Geometry
from sqlalchemy import (
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
)
from sqlalchemy.orm import relationship

from shared.database_schema import (
    ANALYSIS_RUN_PHASES,
    ANALYSIS_RUN_STATUSES,
    ANALYSIS_TILE_STATUSES,
    GCP_AUDIT_ACTIONS,
    GCP_OBSERVATION_STATUSES,
    GCP_ROLES,
    MAP_FEATURE_AUDIT_ACTIONS,
    MAP_FEATURE_SOURCES,
    PORTABLE_JSON,
    AppendOnlyAuditMixin,
    Base,
    RequiredTimestampMixin,
    _uuid_identifier_column,
    _values_check,
)


class AIAnalysisRun(RequiredTimestampMixin, Base):
    """Durable, independently rerunnable AI analysis of a mission COG."""

    __tablename__ = "ai_analysis_runs"
    __table_args__ = (
        UniqueConstraint("id", "mission_id", name="uq_ai_analysis_run_mission"),
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
