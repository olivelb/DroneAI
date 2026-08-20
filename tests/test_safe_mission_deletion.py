from __future__ import annotations

import importlib
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import (
    AIAnalysisRun,
    AIAnalysisTile,
    Mission,
    MissionStageRun,
    OutboxEvent,
)
from shared.tenancy import mission_prefix

missions = importlib.import_module("app4-dashboard.api.routers.missions")
security = importlib.import_module("app4-dashboard.api.security")


def test_manual_delete_cancels_compute_without_deleting_storage(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    MissionStageRun.__table__.create(engine)
    AIAnalysisRun.__table__.create(engine)
    AIAnalysisTile.__table__.create(engine)
    OutboxEvent.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with scope() as session:
        mission = Mission(
            vol_id="mission-delete",
            organization_id="tenant-a",
            owner_subject="admin-a",
            workspace_prefix=mission_prefix("tenant-a", "mission-delete"),
            status="processing",
            retry_count=2,
        )
        session.add(mission)
        session.flush()
        session.add(
            MissionStageRun(
                run_id="a" * 32,
                mission_id=mission.id,
                stage="rasterization",
                attempt=0,
                status="running",
                idempotency_key="a" * 64,
                executor="kubernetes-job",
                job_name="droneai-raster-delete",
            )
        )
        analysis = AIAnalysisRun(
            run_id="analysis-delete",
            mission_id=mission.id,
            vol_id=mission.vol_id,
            name="Delete me",
            status="running",
            phase="detecting",
            retry_count=1,
            ortho_s3_key="organizations/tenant-a/missions/mission-delete/ortho.tif",
            finalization_owner="worker-a",
        )
        session.add(analysis)
        session.flush()
        session.add(
            AIAnalysisTile(
                analysis_run_id=analysis.id,
                tile_index=0,
                status="queued",
                tile_s3_key="organizations/tenant-a/missions/mission-delete/tile-0.png",
                offset_x=0,
                offset_y=0,
                width=1024,
                height=1024,
            )
        )

    monkeypatch.setattr(missions, "get_session", scope)
    principal = security.Principal(
        subject="admin-a",
        role="admin",
        organization_id="tenant-a",
    )

    response = missions._delete_mission("mission-delete", principal)
    repeated = missions._delete_mission("mission-delete", principal)

    assert response["deletion_pending"] is True
    assert response["s3_objects_deleted"] == 0
    assert response["db_deleted"] is False
    assert repeated["deletion_pending"] is True
    with scope() as session:
        mission = session.query(Mission).one()
        run = session.query(MissionStageRun).one()
        analysis = session.query(AIAnalysisRun).one()
        tile = session.query(AIAnalysisTile).one()
        assert mission.status == "cancelled"
        assert mission.current_step == "DELETION_REQUESTED"
        assert run.status == "cancelled"
        assert analysis.status == "cancelled"
        assert analysis.finalization_owner is None
        assert tile.status == "dead"
        assert session.query(OutboxEvent).count() == 2
