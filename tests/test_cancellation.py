from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.cancellation import (
    AttemptCancellationRegistry,
    DurableCancellationRegistry,
)
from shared.database import AIAnalysisRun, Mission


@pytest.fixture
def session_scope():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    AIAnalysisRun.__table__.create(engine)
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

    return scope


def test_cancellation_is_scoped_to_campaign_attempt():
    registry = AttemptCancellationRegistry()
    registry.cancel("mission-1", "run-1", 0)

    assert registry.is_cancelled("mission-1", "run-1", 0)
    assert not registry.is_cancelled("mission-1", "run-1", 1)
    assert not registry.is_cancelled("mission-1", "run-2", 0)


def test_cancellation_can_be_cleared_without_affecting_other_attempts():
    registry = AttemptCancellationRegistry()
    registry.cancel("mission-1", "run-1", 0)
    registry.cancel("mission-1", "run-1", 1)

    registry.clear("mission-1", "run-1", 1)

    assert registry.is_cancelled("mission-1", "run-1", 0)
    assert not registry.is_cancelled("mission-1", "run-1", 1)


def test_mission_cancel_does_not_cancel_independent_analysis():
    registry = AttemptCancellationRegistry()
    registry.cancel("mission-1")

    assert registry.is_cancelled("mission-1")
    assert not registry.is_cancelled("mission-1", "analysis-1")


def test_cancellation_is_isolated_by_organization():
    registry = AttemptCancellationRegistry()
    registry.cancel("mission-1", organization_id="tenant-a")

    assert registry.is_cancelled("mission-1", organization_id="tenant-a")
    assert not registry.is_cancelled("mission-1", organization_id="tenant-b")


def test_durable_mission_cancellation_propagates_between_replicas(session_scope):
    with session_scope() as session:
        session.add(Mission(vol_id="mission-1", status="processing", retry_count=0))

    first_replica = DurableCancellationRegistry(session_scope)
    second_replica = DurableCancellationRegistry(session_scope)
    first_replica.cancel("mission-1", attempt=0)

    assert second_replica.is_cancelled("mission-1", attempt=0)
    with session_scope() as session:
        mission = session.query(Mission).one()
        assert mission.status == "cancelled"
        assert mission.current_step == "CANCELLATION_REQUESTED"


def test_durable_mission_cancellation_is_tenant_scoped(session_scope):
    with session_scope() as session:
        session.add_all(
            [
                Mission(
                    vol_id="shared-mission",
                    organization_id="tenant-a",
                    status="processing",
                ),
                Mission(
                    vol_id="shared-mission",
                    organization_id="tenant-b",
                    status="processing",
                ),
            ]
        )

    registry = DurableCancellationRegistry(session_scope)
    registry.cancel("shared-mission", organization_id="tenant-b")

    with session_scope() as session:
        missions = {
            mission.organization_id: mission.status
            for mission in session.query(Mission).all()
        }
    assert missions == {"tenant-a": "processing", "tenant-b": "cancelled"}


def test_durable_cancellation_rejects_a_stale_attempt(session_scope):
    with session_scope() as session:
        session.add(Mission(vol_id="mission-1", status="processing", retry_count=2))

    registry = DurableCancellationRegistry(session_scope)
    with pytest.raises(LookupError, match="no longer on attempt 1"):
        registry.cancel("mission-1", attempt=1)

    assert not registry.is_cancelled("mission-1", attempt=2)
    with session_scope() as session:
        assert session.query(Mission).one().status == "processing"


def test_durable_analysis_cancellation_is_run_and_attempt_scoped(session_scope):
    with session_scope() as session:
        mission = Mission(vol_id="mission-1", status="processing")
        session.add(mission)
        session.flush()
        session.add(
            AIAnalysisRun(
                run_id="run-1",
                mission_id=mission.id,
                vol_id=mission.vol_id,
                name="Vehicles",
                ortho_s3_key="missions/mission-1/orthomosaic.tif",
                status="running",
                phase="detecting",
                retry_count=3,
            )
        )

    first_replica = DurableCancellationRegistry(session_scope)
    first_replica.cancel("mission-1", "run-1", 3)
    first_replica.clear("mission-1", "run-1", 3)
    second_replica = DurableCancellationRegistry(session_scope)

    assert second_replica.is_cancelled("mission-1", "run-1", 3)
    assert not second_replica.is_cancelled("mission-1", "run-1", 2)
    assert not second_replica.is_cancelled("mission-1", "run-2", 3)
    with session_scope() as session:
        run = session.query(AIAnalysisRun).one()
        assert run.status == "cancelled"
        assert run.phase == "cancelled"


def test_durable_negative_checks_are_rate_limited(session_scope):
    with session_scope() as session:
        session.add(Mission(vol_id="mission-1", status="processing"))

    checks = 0

    @contextmanager
    def counting_scope():
        nonlocal checks
        checks += 1
        with session_scope() as session:
            yield session

    current_time = [10.0]
    registry = DurableCancellationRegistry(
        counting_scope,
        poll_seconds=2,
        clock=lambda: current_time[0],
    )

    assert not registry.is_cancelled("mission-1")
    assert not registry.is_cancelled("mission-1")
    assert checks == 1

    current_time[0] = 12.1
    assert not registry.is_cancelled("mission-1")
    assert checks == 2
