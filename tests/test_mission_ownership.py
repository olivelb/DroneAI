import asyncio
import importlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import Mission

mission_access = importlib.import_module("app4-dashboard.api.mission_access")
mission_state = importlib.import_module("app4-dashboard.api.mission_state")
realtime = importlib.import_module("app4-dashboard.api.realtime")


@pytest.fixture
def mission_sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return session_scope


def test_owner_scoped_lookup_does_not_disclose_another_subject(mission_sessions):
    with mission_sessions() as session:
        session.add(Mission(vol_id="alice-flight", owner_subject="alice"))

    with mission_sessions() as session:
        with pytest.raises(HTTPException) as error:
            mission_access.get_owned_mission(
                session,
                "alice-flight",
                SimpleNamespace(subject="bob", role="operator"),
            )

    assert error.value.status_code == 404


def test_admin_cross_tenant_scope_must_be_explicit_and_is_audited(
    mission_sessions,
    caplog,
):
    with mission_sessions() as session:
        session.add(Mission(vol_id="alice-flight", owner_subject="alice"))

    with mission_sessions() as session:
        mission = mission_access.get_owned_mission(
            session,
            "alice-flight",
            SimpleNamespace(subject="platform-admin", role="admin"),
            requested_owner="alice",
            action="support_detail",
        )

    assert mission.vol_id == "alice-flight"
    assert "admin_cross_tenant_mission_access" in caplog.text
    assert "principal=platform-admin" in caplog.text


def test_status_summary_only_contains_the_requested_owner(
    mission_sessions,
    monkeypatch,
):
    with mission_sessions() as session:
        session.add_all(
            [
                Mission(vol_id="alice-flight", owner_subject="alice"),
                Mission(vol_id="bob-flight", owner_subject="bob"),
            ]
        )
    monkeypatch.setattr(mission_state, "get_session", mission_sessions)

    summary = mission_state.get_status_summary("alice")

    assert [mission["vol_id"] for mission in summary["missions"]] == [
        "alice-flight"
    ]


def test_mission_lookup_is_partitioned_by_organization(mission_sessions):
    with mission_sessions() as session:
        session.add(
            Mission(
                vol_id="north-flight",
                owner_subject="alice",
                organization_id="north-survey",
            )
        )

    with mission_sessions() as session:
        with pytest.raises(HTTPException) as error:
            mission_access.get_owned_mission(
                session,
                "north-flight",
                SimpleNamespace(
                    subject="alice",
                    role="admin",
                    organization_id="south-survey",
                ),
            )

    assert error.value.status_code == 404


def test_realtime_history_and_broadcast_are_partitioned_by_owner():
    class WebSocket:
        def __init__(self):
            self.messages = []

        async def accept(self):
            return None

        async def send_text(self, message):
            self.messages.append(message)

    async def exercise():
        hub = realtime.StatusHub()
        alice = WebSocket()
        bob = WebSocket()
        await hub.connect(alice, "alice")
        await hub.connect(bob, "bob")
        message = hub.remember({"vol_id": "alice-flight"}, "alice")
        await hub.broadcast(message, "alice")
        return alice.messages, bob.messages

    alice_messages, bob_messages = asyncio.run(exercise())

    assert len(alice_messages) == 1
    assert bob_messages == []
