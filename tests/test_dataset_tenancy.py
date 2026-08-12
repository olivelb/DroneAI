from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import (
    AccessAuditEvent,
    Dataset,
    DatasetUploadSession,
    Mission,
    MissionArtifact,
    OrganizationUsageEvent,
)

dataset_access = importlib.import_module("app4-dashboard.api.dataset_access")
dataset_routes = importlib.import_module("app4-dashboard.api.routers.datasets")
mission_routes = importlib.import_module("app4-dashboard.api.routers.missions")
security = importlib.import_module("app4-dashboard.api.security")


@pytest.fixture
def tenant_sessions(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    DatasetUploadSession.__table__.create(engine)
    Dataset.__table__.create(engine)
    Mission.__table__.create(engine)
    MissionArtifact.__table__.create(engine)
    OrganizationUsageEvent.__table__.create(engine)
    AccessAuditEvent.__table__.create(engine)
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

    monkeypatch.setattr(dataset_routes, "get_session", session_scope)
    monkeypatch.setattr(mission_routes, "get_session", session_scope)
    return session_scope


def _dataset(
    owner: str,
    name: str,
    organization_id: str = "legacy-unassigned",
) -> Dataset:
    prefix = f"datasets/{name}"
    return Dataset(
        name=name,
        organization_id=organization_id,
        owner_subject=owner,
        prefix=prefix,
        status="ready",
        manifest_s3_key=f"{prefix}/dataset-manifest.json",
        file_count=3,
        image_count=2,
        total_bytes=1_024,
        ready_at=datetime.now(UTC),
    )


def test_dataset_listing_is_partitioned_by_owner(tenant_sessions):
    with tenant_sessions() as session:
        session.add_all([_dataset("alice", "alice-flight"), _dataset("bob", "bob-flight")])

    alice = security.Principal("alice", "operator")
    result = dataset_routes.list_datasets(alice)

    assert result == [
        {
            "name": "alice-flight",
            "path": "datasets/alice-flight",
            "image_count": 2,
        }
    ]


def test_dataset_listing_is_partitioned_by_organization_even_for_same_subject(
    tenant_sessions,
):
    with tenant_sessions() as session:
        session.add_all(
            [
                _dataset("alice", "north-flight", "north-survey"),
                _dataset("alice", "south-flight", "south-survey"),
            ]
        )

    result = dataset_routes.list_datasets(
        security.Principal("alice", "operator", "north-survey")
    )

    assert [item["name"] for item in result] == ["north-flight"]


def test_cross_owner_object_download_is_hidden_before_storage_access(
    tenant_sessions,
    monkeypatch,
):
    with tenant_sessions() as session:
        session.add(_dataset("alice", "private-flight"))
    monkeypatch.setattr(
        dataset_routes.storage,
        "file_exists",
        lambda _key: pytest.fail("unauthorized storage must not be queried"),
    )

    with pytest.raises(HTTPException) as error:
        dataset_routes.get_file(
            "datasets/private-flight/DJI_0001.JPG",
            security.Principal("bob", "viewer"),
        )

    assert error.value.status_code == 404


def test_admin_cross_member_dataset_access_must_be_explicit_and_is_audited(
    tenant_sessions,
    caplog,
):
    with tenant_sessions() as session:
        session.add(_dataset("alice", "alice-flight"))

    result = dataset_routes.list_datasets(
        security.Principal("platform-admin", "admin"),
        "alice",
    )

    assert [item["name"] for item in result] == ["alice-flight"]
    assert "admin_cross_member_dataset_access" in caplog.text
    assert "principal=platform-admin" in caplog.text
    with tenant_sessions() as session:
        event = session.query(AccessAuditEvent).one()
        assert event.actor_subject == "platform-admin"
        assert event.target_owner_subject == "alice"
        assert event.action == "list"
        assert event.resource_type == "dataset"
        assert event.resource_id is None


def test_mission_launch_requires_a_ready_owned_dataset(
    tenant_sessions,
    monkeypatch,
):
    with tenant_sessions() as session:
        session.add(_dataset("alice", "private-flight"))
    monkeypatch.setattr(mission_routes, "initialize_stage_runs", lambda *_args: None)
    monkeypatch.setattr(mission_routes, "stage_jobs_enabled", lambda: True)
    params = mission_routes.MissionParams(
        vol_id="bob-mission",
        input_dataset="datasets/private-flight",
    )

    with pytest.raises(HTTPException) as error:
        mission_routes._start_mission(
            params,
            security.Principal("bob", "operator"),
        )

    assert error.value.status_code == 404
    with tenant_sessions() as session:
        assert session.query(Mission).count() == 0


def test_mission_launch_persists_the_catalog_dataset_reference(
    tenant_sessions,
    monkeypatch,
):
    with tenant_sessions() as session:
        session.add(_dataset("alice", "alice-flight"))
    monkeypatch.setattr(mission_routes, "initialize_stage_runs", lambda *_args: None)
    monkeypatch.setattr(mission_routes, "stage_jobs_enabled", lambda: True)
    params = mission_routes.MissionParams(
        vol_id="alice-mission",
        input_dataset="datasets/alice-flight",
    )

    result = mission_routes._start_mission(
        params,
        security.Principal("alice", "operator"),
    )

    assert result == {"status": "success", "vol_id": "alice-mission"}
    with tenant_sessions() as session:
        dataset = session.query(Dataset).one()
        mission = session.query(Mission).one()
        assert mission.dataset_id == dataset.id
        assert mission.owner_subject == dataset.owner_subject


def test_referenced_dataset_cannot_be_deleted(tenant_sessions, monkeypatch):
    with tenant_sessions() as session:
        dataset = _dataset("alice", "alice-flight")
        session.add(dataset)
        session.flush()
        session.add(
            Mission(
                vol_id="alice-mission",
                owner_subject="alice",
                dataset_id=dataset.id,
                input_dataset=dataset.prefix,
            )
        )
    monkeypatch.setattr(
        dataset_routes.storage,
        "delete_prefix",
        lambda _prefix: pytest.fail("referenced dataset must not reach storage deletion"),
    )

    with pytest.raises(HTTPException) as error:
        dataset_routes.delete_dataset(
            "alice-flight",
            security.Principal("alice", "admin"),
        )

    assert error.value.status_code == 409
    with tenant_sessions() as session:
        assert session.query(Dataset).one().status == "ready"


def test_deleting_dataset_can_be_retried_after_a_lost_final_commit(
    tenant_sessions,
    monkeypatch,
):
    with tenant_sessions() as session:
        dataset = _dataset("alice", "retry-delete")
        dataset.status = "deleting"
        session.add(dataset)
    deleted_prefixes: list[str] = []
    monkeypatch.setattr(
        dataset_routes.storage,
        "delete_prefix",
        lambda prefix: deleted_prefixes.append(prefix) or 0,
    )

    result = dataset_routes.delete_dataset(
        "retry-delete",
        security.Principal("alice", "admin"),
    )

    assert result["status"] == "success"
    assert deleted_prefixes == ["datasets/retry-delete/"]
    with tenant_sessions() as session:
        assert session.query(Dataset).one().status == "deleted"


def test_storage_paths_outside_tenant_resources_are_rejected(tenant_sessions):
    with tenant_sessions() as session:
        with pytest.raises(HTTPException) as error:
            dataset_access.authorize_storage_path(
                session,
                "blobs/sha256/secret",
                security.Principal("alice", "viewer"),
                action="download_storage",
            )

    assert error.value.status_code == 404
