from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, insert, update
from sqlalchemy.orm import Session

from shared.database import Mission, MissionStageRun
from shared.tenancy import mission_prefix

routes = importlib.import_module("app4-dashboard.api.routers.mission_catalog")
schemas = importlib.import_module("app4-dashboard.api.schemas")
security = importlib.import_module("app4-dashboard.api.security")
principal = security.Principal("alice", "operator", organization_id="org-a")


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (Mission, MissionStageRun):
        model.__table__.create(engine)
    with Session(engine) as session:
        @contextmanager
        def get_session():
            yield session
        monkeypatch.setattr(routes, "get_session", get_session)
        yield session
    engine.dispose()


def add_mission(session, vol_id="owned", **kwargs):
    values = dict(vol_id=vol_id, organization_id="org-a", owner_subject="alice",
                  status="processing", updated_at=datetime(2026, 1, 1, tzinfo=UTC))
    values.update(kwargs)
    values["workspace_prefix"] = mission_prefix(values["organization_id"], vol_id)
    session.execute(insert(Mission).values(**values))
    session.commit()
    return session.query(Mission.id).filter(Mission.vol_id == vol_id).scalar()


def versions():
    return routes.mission_revisions(principal)["versions"]


def test_index_and_bulk_are_owner_and_tenant_scoped(database):
    add_mission(database)
    add_mission(database, "other-owner", owner_subject="bob")
    add_mission(database, "other-tenant", organization_id="org-b")
    assert set(versions()) == {"owned"}
    selected = schemas.MissionCatalogSelection(vol_ids=["owned", "other-owner", "other-tenant", "missing"])
    assert [item["vol_id"] for item in routes.mission_catalog_items(selected, principal)["items"]] == ["owned"]
    with pytest.raises(HTTPException) as error:
        routes.mission_revisions(principal, "bob")
    assert error.value.status_code == 404


def test_stage_only_and_backdated_changes_are_not_lost(database):
    mission_id = add_mission(database)
    baseline = versions()
    for index, stage in enumerate(("reconstruction", "rasterization")):
        database.execute(insert(MissionStageRun).values(mission_id=mission_id,
            stage=stage, idempotency_key=stage.ljust(64, "0"), updated_at=datetime(2026, 1, 2 + index, tzinfo=UTC)))
    database.commit()
    with_stages = versions()
    assert with_stages != baseline
    database.execute(update(MissionStageRun).where(MissionStageRun.stage == "reconstruction").values(
        progress=10, updated_at=datetime(2025, 1, 1, tzinfo=UTC)))
    database.commit()
    changed = versions()
    assert changed != with_stages
    assert database.query(Mission.updated_at).scalar() == datetime(2026, 1, 1)
    database.execute(delete(MissionStageRun).where(MissionStageRun.stage == "reconstruction"))
    database.commit()
    assert versions() != changed


def test_deletion_new_mission_and_same_name_recreation(database):
    add_mission(database)
    add_mission(database, "surviving")
    original = versions()["owned"]
    database.execute(delete(Mission).where(Mission.vol_id == "owned"))
    database.commit()
    assert "owned" not in versions()
    add_mission(database)
    assert versions()["owned"] != original


def test_index_two_queries_and_eager_catalogue_loading(database):
    database.execute(insert(Mission), [dict(vol_id=f"m-{i}", organization_id="org-a",
        owner_subject="alice", workspace_prefix=mission_prefix("org-a", f"m-{i}")) for i in range(100)])
    database.commit()
    queries = []
    def record(*args):
        queries.append(args[2])
    event.listen(database.bind, "before_cursor_execute", record)
    try:
        assert len(versions()) == 100
        assert len(queries) == 2
        queries.clear()
        assert len(routes.mission_catalog(principal, 100, 0)["items"]) == 100
        assert len(queries) == 3
        queries.clear()
        selected = schemas.MissionCatalogSelection(vol_ids=[f"m-{i}" for i in range(100)])
        assert len(routes.mission_catalog_items(selected, principal)["items"]) == 100
        assert len(queries) == 2
    finally:
        event.remove(database.bind, "before_cursor_execute", record)


@pytest.mark.parametrize("ids", [[], ["x"] * 101, [""], ["x" * 257]])
def test_http_selection_is_bounded_before_database_access(ids, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[security.require_authenticated] = lambda: principal
    monkeypatch.setattr(routes, "get_session", lambda: pytest.fail("invalid request reached database"))
    assert TestClient(app).post("/missions/catalog-items", json={"vol_ids": ids}).status_code == 422
