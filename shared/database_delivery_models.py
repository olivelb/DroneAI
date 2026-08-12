"""Log, inbox, outbox, and rate-limit models."""

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
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
    INBOX_EVENT_STATUSES,
    OUTBOX_EVENT_STATUSES,
    PIPELINE_LOG_STATUSES,
    PORTABLE_BIGINT,
    PORTABLE_JSON,
    Base,
    _values_check,
)
from shared.tenancy import LEGACY_ORGANIZATION_ID


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
    organization_id = Column(
        String(64),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        default=LEGACY_ORGANIZATION_ID,
        server_default=LEGACY_ORGANIZATION_ID,
        index=True,
    )
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
