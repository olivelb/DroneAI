"""Owner-scoped catalogue revisions without loading mission object graphs."""
from __future__ import annotations

import hashlib
import time
from typing import Any, TypedDict, cast

from sqlalchemy.orm import Session

from shared.database import Mission, MissionStageRun
from .mission_access import mission_query
from .mission_state import MISSION_PROCESSING_STALE_SECONDS
from .security import Principal


class MissionCatalogIndex(TypedDict):
    versions: dict[str, str]
    observed_at: float
    stale_after_seconds: float


def catalogue_index(
    session: Session, principal: Principal, owner_subject: str | None
) -> MissionCatalogIndex:
    query = cast(Any, mission_query(
        session, principal, requested_owner=owner_subject, action="catalog"
    ))
    # Include every contributing stage revision, not just the maximum timestamp:
    # an older stage can change while another stage retains a newer timestamp.
    rows = query.with_entities(Mission.id, Mission.vol_id, Mission.updated_at)
    hashes = {}
    names = {}
    for mission_id, vol_id, updated_at in rows.order_by(Mission.id).yield_per(1000):
        hashes[mission_id] = hashlib.sha256(f"{mission_id}:{updated_at}".encode())
        names[mission_id] = str(vol_id)
    stages = session.query(
        MissionStageRun.mission_id, MissionStageRun.id, MissionStageRun.updated_at
    ).filter(
        MissionStageRun.mission_id.in_(query.with_entities(Mission.id).statement),
        MissionStageRun.analysis_run_id.is_(None),
    ).order_by(MissionStageRun.mission_id, MissionStageRun.id)
    for mission_id, stage_id, updated_at in stages.yield_per(1000):
        if mission_id in hashes:
            hashes[mission_id].update(f"|{stage_id}:{updated_at}".encode())
    return {
        "versions": {names[key]: value.hexdigest() for key, value in hashes.items()},
        "observed_at": time.time(),
        "stale_after_seconds": MISSION_PROCESSING_STALE_SECONDS,
    }
