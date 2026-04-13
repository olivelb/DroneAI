"""PostGIS-backed database models for mission state, detections, and logs.

Replaces:
- In-memory ``mission_states`` dict in the dashboard API
- ``mission_state.json`` / ``mission_state_history.jsonl`` on NFS
- In-memory ``MissionRegistry`` aggregation in the processing worker

Uses SQLAlchemy 2.0 + GeoAlchemy2 for PostGIS geometry support.
"""

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    Column,
    DateTime,
    Enum,
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://droneai:droneai-local@postgres.drone-ai.svc:5432/droneai",
)

# ---------------------------------------------------------------------------
# Engine & session factory (lazy init)
# ---------------------------------------------------------------------------

_engine = None
_SessionFactory = None


def _get_engine():
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


def get_session_factory() -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=_get_engine(), expire_on_commit=False)
    return _SessionFactory


@contextmanager
def get_session():
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


def reset_engine():
    """Dispose of the engine and reset singletons (for testing)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Mission statuses
# ---------------------------------------------------------------------------

MISSION_STATUSES = ("pending", "processing", "completed", "error", "cancelled", "stale")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Mission(Base):
    """Tracks the lifecycle of a single drone processing mission.

    Replaces the in-memory ``mission_states`` dict and ``mission_state.json`` files.
    """

    __tablename__ = "missions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vol_id = Column(String(256), unique=True, nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending")
    pipeline = Column(String(32), nullable=False, default="modern")

    # S3 references (replace filesystem paths)
    input_dataset = Column(String(1024), nullable=True)  # S3 prefix for input images
    workspace_prefix = Column(String(1024), nullable=True)  # S3 prefix for mission workspace

    # Pipeline parameters (full JSON blob from mission launch message)
    params = Column(JSONB, nullable=True)

    # Progress tracking
    current_step = Column(String(64), nullable=True)
    progress = Column(Integer, default=0)

    # Per-service state snapshots (e.g. {"COLMAP": {...}, "TILER": {...}, "IA": {...}})
    service_states = Column(JSONB, nullable=True, default=dict)

    # Resume info (replaces mission_state.json's resume fields)
    resume_info = Column(JSONB, nullable=True)

    # Error tracking
    error_message = Column(Text, nullable=True)

    # Tiling metadata (used by aggregation)
    total_tiles = Column(Integer, nullable=True)
    tiles_received = Column(Integer, default=0)
    ortho_s3_key = Column(String(1024), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    detections = relationship("Detection", back_populates="mission", cascade="all, delete-orphan")
    logs = relationship("MissionLog", back_populates="mission", cascade="all, delete-orphan")

    def __repr__(self):
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
    segment = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    mission = relationship("Mission", back_populates="detections")

    def __repr__(self):
        return (
            f"<Detection(vol_id={self.vol_id!r}, tile={self.tile_index}, "
            f"class={self.class_name!r}, conf={self.confidence:.2f})>"
        )


class MissionLog(Base):
    """Persisted log entry from the pipeline-status Kafka stream.

    Replaces the volatile in-memory ``status_history`` deque in the dashboard API.
    """

    __tablename__ = "mission_logs"
    __table_args__ = (Index("ix_logs_mission_created", "mission_id", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    mission_id = Column(Integer, ForeignKey("missions.id", ondelete="CASCADE"), nullable=False)
    vol_id = Column(String(256), nullable=False, index=True)

    service = Column(String(32), nullable=True)  # COLMAP, TILER, IA
    step = Column(String(64), nullable=True)
    status = Column(String(32), nullable=True)  # processing, success, error
    progress = Column(Integer, nullable=True)
    message = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    mission = relationship("Mission", back_populates="logs")

    def __repr__(self):
        return f"<MissionLog(vol_id={self.vol_id!r}, service={self.service!r}, step={self.step!r})>"


# ---------------------------------------------------------------------------
# Helper queries
# ---------------------------------------------------------------------------


def get_or_create_mission(session: Session, vol_id: str, **kwargs) -> Mission:
    """Get an existing mission by vol_id or create a new one."""
    mission = session.query(Mission).filter(Mission.vol_id == vol_id).first()
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
    service: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Optional[Mission]:
    """Update mission progress and optionally its status."""
    mission = session.query(Mission).filter(Mission.vol_id == vol_id).first()
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
    mission.updated_at = datetime.now(timezone.utc)
    return mission


def count_received_tiles(session: Session, vol_id: str) -> int:
    """Count distinct tile indexes received for a mission."""
    result = (
        session.query(Detection.tile_index)
        .filter(Detection.vol_id == vol_id)
        .distinct()
        .count()
    )
    return result


def get_mission_detections(session: Session, vol_id: str) -> list[Detection]:
    """Get all detections for a mission, ordered by tile index."""
    return (
        session.query(Detection)
        .filter(Detection.vol_id == vol_id)
        .order_by(Detection.tile_index, Detection.id)
        .all()
    )
