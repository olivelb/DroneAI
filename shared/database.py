"""PostGIS-backed database models for mission state, detections, and logs.

Replaces:
- In-memory ``mission_states`` dict in the dashboard API
- ``mission_state.json`` / ``mission_state_history.jsonl`` on NFS
- In-memory ``MissionRegistry`` aggregation in the processing worker

Uses SQLAlchemy 2.0 + GeoAlchemy2 for PostGIS geometry support.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import (
    create_engine,
    text,
)
from sqlalchemy.orm import (
    Session,
    sessionmaker,
)

from shared.config import DATABASE_URL
from shared.tenancy import (
    LEGACY_ORGANIZATION_ID,
    current_organization_id,
    validate_organization_id,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Engine & session factory (lazy init)
# ---------------------------------------------------------------------------

_engine: Any = None
_SessionFactory: Callable[[], Session] | None = None


def get_engine() -> Any:
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
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory


def _set_postgres_session_context(
    session: Session,
    *,
    organization_id: str | None,
    authentication_credential_id: str | None,
    platform_credential_id: str | None,
    identity_capability_id: str | None,
) -> None:
    """Set transaction-local RLS inputs without leaking them through the pool."""

    if session.get_bind().dialect.name != "postgresql":
        return
    if organization_id is not None:
        session.execute(
            text("SELECT set_config('droneai.organization_id', :organization_id, true)"),
            {"organization_id": validate_organization_id(organization_id)},
        )
    if authentication_credential_id is not None:
        session.execute(
            text("SELECT set_config('droneai.authentication_credential_id', :credential_id, true)"),
            {"credential_id": authentication_credential_id},
        )
    if platform_credential_id is not None:
        session.execute(
            text("SELECT set_config('droneai.platform_credential_id', :credential_id, true)"),
            {"credential_id": platform_credential_id},
        )
    if identity_capability_id is not None:
        session.execute(
            text("SELECT set_config('droneai.identity_capability_id', :capability_id, true)"),
            {"capability_id": identity_capability_id},
        )


@contextmanager
def get_session(
    *,
    organization_id: str | None = None,
    authentication_credential_id: str | None = None,
    platform_credential_id: str | None = None,
    identity_capability_id: str | None = None,
) -> Iterator[Session]:
    """Yield an atomic session with transaction-local PostgreSQL RLS context."""

    factory = get_session_factory()
    session: Session = factory()
    try:
        contextual_organization = current_organization_id()
        if (
            organization_id is not None
            and contextual_organization is not None
            and validate_organization_id(organization_id) != contextual_organization
        ):
            raise ValueError("Explicit organization_id conflicts with the request context")
        _set_postgres_session_context(
            session,
            organization_id=organization_id or contextual_organization,
            authentication_credential_id=authentication_credential_id,
            platform_credential_id=platform_credential_id,
            identity_capability_id=identity_capability_id,
        )
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

from shared.database_analysis_models import (
    AIAnalysisRun as AIAnalysisRun,
)
from shared.database_analysis_models import (
    AIAnalysisTile as AIAnalysisTile,
)
from shared.database_analysis_models import (
    GcpAuditEvent as GcpAuditEvent,
)
from shared.database_analysis_models import (
    GcpObservation as GcpObservation,
)
from shared.database_analysis_models import (
    GcpPoint as GcpPoint,
)
from shared.database_analysis_models import (
    GcpSet as GcpSet,
)
from shared.database_analysis_models import (
    MapFeature as MapFeature,
)
from shared.database_analysis_models import (
    MapFeatureAuditEvent as MapFeatureAuditEvent,
)
from shared.database_analysis_models import (
    RasterLayerStyle as RasterLayerStyle,
)
from shared.database_delivery_models import (
    APIRateLimitBucket as APIRateLimitBucket,
)
from shared.database_delivery_models import (
    InboxEvent as InboxEvent,
)
from shared.database_delivery_models import (
    MissionLog as MissionLog,
)
from shared.database_delivery_models import (
    OutboxEvent as OutboxEvent,
)
from shared.database_identity_models import (
    AccessAuditEvent as AccessAuditEvent,
)
from shared.database_identity_models import (
    ApiCredential as ApiCredential,
)
from shared.database_identity_models import (
    IdentityAuditEvent as IdentityAuditEvent,
)
from shared.database_identity_models import (
    IdentityCapability as IdentityCapability,
)
from shared.database_identity_models import (
    Organization as Organization,
)
from shared.database_identity_models import (
    OrganizationMember as OrganizationMember,
)
from shared.database_identity_models import (
    PlatformAuditEvent as PlatformAuditEvent,
)
from shared.database_identity_models import (
    PlatformCredential as PlatformCredential,
)
from shared.database_identity_models import (
    PlatformMember as PlatformMember,
)
from shared.database_mission_models import (
    Detection as Detection,
)
from shared.database_mission_models import (
    DetectionShardReceipt as DetectionShardReceipt,
)
from shared.database_mission_models import (
    Mission as Mission,
)
from shared.database_mission_models import (
    MissionArtifact as MissionArtifact,
)
from shared.database_mission_models import (
    MissionArtifactParent as MissionArtifactParent,
)
from shared.database_mission_models import (
    MissionStageRun as MissionStageRun,
)
from shared.database_mission_models import (
    ProcessedTile as ProcessedTile,
)
from shared.database_saas_models import (
    Dataset as Dataset,
)
from shared.database_saas_models import (
    DatasetUploadFile as DatasetUploadFile,
)
from shared.database_saas_models import (
    DatasetUploadSession as DatasetUploadSession,
)
from shared.database_saas_models import (
    OrganizationRequestBucket as OrganizationRequestBucket,
)
from shared.database_saas_models import (
    OrganizationSaasPolicy as OrganizationSaasPolicy,
)
from shared.database_saas_models import (
    OrganizationUsageEvent as OrganizationUsageEvent,
)
from shared.database_schema import (
    ACCESS_AUDIT_ACTOR_REALMS as ACCESS_AUDIT_ACTOR_REALMS,
)
from shared.database_schema import (
    ACCESS_AUDIT_OUTCOMES as ACCESS_AUDIT_OUTCOMES,
)
from shared.database_schema import (
    ACCESS_AUDIT_RESOURCE_TYPES as ACCESS_AUDIT_RESOURCE_TYPES,
)
from shared.database_schema import (
    AGGREGATION_STATUSES as AGGREGATION_STATUSES,
)
from shared.database_schema import (
    ANALYSIS_RUN_PHASES as ANALYSIS_RUN_PHASES,
)
from shared.database_schema import (
    ANALYSIS_RUN_STATUSES as ANALYSIS_RUN_STATUSES,
)
from shared.database_schema import (
    ANALYSIS_TILE_STATUSES as ANALYSIS_TILE_STATUSES,
)
from shared.database_schema import (
    API_CREDENTIAL_STATUSES as API_CREDENTIAL_STATUSES,
)
from shared.database_schema import (
    DATASET_STATUSES as DATASET_STATUSES,
)
from shared.database_schema import (
    DATASET_UPLOAD_FILE_STATUSES as DATASET_UPLOAD_FILE_STATUSES,
)
from shared.database_schema import (
    DATASET_UPLOAD_SESSION_STATUSES as DATASET_UPLOAD_SESSION_STATUSES,
)
from shared.database_schema import (
    GCP_AUDIT_ACTIONS as GCP_AUDIT_ACTIONS,
)
from shared.database_schema import (
    GCP_OBSERVATION_STATUSES as GCP_OBSERVATION_STATUSES,
)
from shared.database_schema import (
    GCP_ROLES as GCP_ROLES,
)
from shared.database_schema import (
    IDENTITY_AUDIT_ACTIONS as IDENTITY_AUDIT_ACTIONS,
)
from shared.database_schema import (
    IDENTITY_CAPABILITY_PURPOSES as IDENTITY_CAPABILITY_PURPOSES,
)
from shared.database_schema import (
    IDENTITY_CAPABILITY_STATUSES as IDENTITY_CAPABILITY_STATUSES,
)
from shared.database_schema import (
    INBOX_EVENT_STATUSES as INBOX_EVENT_STATUSES,
)
from shared.database_schema import (
    MAP_FEATURE_AUDIT_ACTIONS as MAP_FEATURE_AUDIT_ACTIONS,
)
from shared.database_schema import (
    MAP_FEATURE_SOURCES as MAP_FEATURE_SOURCES,
)
from shared.database_schema import (
    MISSION_RESOURCE_CLASSES as MISSION_RESOURCE_CLASSES,
)
from shared.database_schema import (
    MISSION_STAGE_RUN_STATUSES as MISSION_STAGE_RUN_STATUSES,
)
from shared.database_schema import (
    MISSION_STAGE_TYPES as MISSION_STAGE_TYPES,
)
from shared.database_schema import (
    MISSION_STATUSES as MISSION_STATUSES,
)
from shared.database_schema import (
    ORGANIZATION_MEMBER_ROLES as ORGANIZATION_MEMBER_ROLES,
)
from shared.database_schema import (
    ORGANIZATION_MEMBER_STATUSES as ORGANIZATION_MEMBER_STATUSES,
)
from shared.database_schema import (
    ORGANIZATION_STATUSES as ORGANIZATION_STATUSES,
)
from shared.database_schema import (
    ORGANIZATION_USAGE_ACTIONS as ORGANIZATION_USAGE_ACTIONS,
)
from shared.database_schema import (
    OUTBOX_EVENT_STATUSES as OUTBOX_EVENT_STATUSES,
)
from shared.database_schema import (
    PIPELINE_LOG_STATUSES as PIPELINE_LOG_STATUSES,
)
from shared.database_schema import (
    PLATFORM_AUDIT_ACTIONS as PLATFORM_AUDIT_ACTIONS,
)
from shared.database_schema import (
    PLATFORM_CREDENTIAL_STATUSES as PLATFORM_CREDENTIAL_STATUSES,
)
from shared.database_schema import (
    PLATFORM_MEMBER_ROLES as PLATFORM_MEMBER_ROLES,
)
from shared.database_schema import (
    PLATFORM_MEMBER_STATUSES as PLATFORM_MEMBER_STATUSES,
)
from shared.database_schema import (
    PORTABLE_BIGINT as PORTABLE_BIGINT,
)
from shared.database_schema import (
    PORTABLE_JSON as PORTABLE_JSON,
)
from shared.database_schema import (
    AppendOnlyAuditMixin as AppendOnlyAuditMixin,
)
from shared.database_schema import (
    Base as Base,
)
from shared.database_schema import (
    RequiredTimestampMixin as RequiredTimestampMixin,
)
from shared.database_schema import (
    RevocableCredentialMixin as RevocableCredentialMixin,
)


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


def get_mission_audience(
    vol_id: str,
    organization_id: str | None = None,
) -> tuple[str, str] | None:
    """Resolve a realtime audience inside one explicit RLS tenant.

    Version-one status events did not carry an organization. Only those
    historical events may fall back to the isolated legacy organization; a
    mission identifier alone must never reveal its current tenant.
    """

    target_organization_id = (
        LEGACY_ORGANIZATION_ID if organization_id is None else validate_organization_id(organization_id)
    )
    with get_session(organization_id=target_organization_id) as session:
        mission = (
            session.query(Mission)
            .filter(
                Mission.organization_id == target_organization_id,
                Mission.vol_id == vol_id,
            )
            .first()
        )
        if mission is None:
            return None
        return str(mission.organization_id), str(mission.owner_subject)
