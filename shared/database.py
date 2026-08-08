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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    relationship,
    sessionmaker,
)

from shared.config import DATABASE_URL

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
MAP_FEATURE_SOURCES = ("manual", "ai")
PIPELINE_LOG_STATUSES = ("processing", "success", "error", "cancelled")
INBOX_EVENT_STATUSES = ("processing", "completed")
OUTBOX_EVENT_STATUSES = (
    "pending",
    "publishing",
    "published",
    "failed",
    "dead",
)


def _values_check(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


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
    status = Column(String(32), nullable=False, default="pending")
    pipeline = Column(String(32), nullable=False, default="modern")

    # S3 references (replace filesystem paths)
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
    logs = relationship("MissionLog", back_populates="mission", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Mission(vol_id={self.vol_id!r}, status={self.status!r}, step={self.current_step!r})>"


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
    run_id = Column(
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

    mission = relationship("Mission", back_populates="map_features")
    analysis_run = relationship("AIAnalysisRun", back_populates="features")


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
