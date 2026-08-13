from __future__ import annotations

import importlib
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import Mission, MissionStageRun, OutboxEvent
from shared.tenancy import mission_prefix

missions = importlib.import_module("app4-dashboard.api.routers.missions")
security = importlib.import_module("app4-dashboard.api.security")


def test_manual_delete_cancels_compute_without_deleting_storage(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    MissionStageRun.__table__.create(engine)
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
        assert mission.status == "cancelled"
        assert mission.current_step == "DELETION_REQUESTED"
        assert run.status == "cancelled"
        assert session.query(OutboxEvent).count() == 1
