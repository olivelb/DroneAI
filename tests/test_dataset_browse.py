from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shared.database import Dataset, DatasetUploadFile, DatasetUploadSession

browse = importlib.import_module("app4-dashboard.api.dataset_browse")
routes = importlib.import_module("app4-dashboard.api.routers.datasets")
security = importlib.import_module("app4-dashboard.api.security")


@pytest.fixture
def database(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (DatasetUploadSession, Dataset, DatasetUploadFile):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        @contextmanager
        def get_session():
            yield session
        monkeypatch.setattr(routes, "get_session", get_session)
        yield session


def dataset(session, prefix, *, organization="org-a", owner="alice", catalog=True):
    now = datetime.now(UTC)
    upload = DatasetUploadSession(dataset_name=prefix, organization_id=organization,
        status="completed", total_bytes=48, file_count=3, part_size=16,
        created_by=owner, expires_at=now, completed_at=now)
    session.add(upload)
    session.flush()
    record = Dataset(name=prefix, organization_id=organization, owner_subject=owner,
        upload_session_id=upload.id if catalog else None, prefix=prefix, status="ready",
        manifest_s3_key=f"{prefix}/dataset-manifest.json", file_count=3, image_count=2,
        total_bytes=48, ready_at=now)
    session.add(record)
    for name in ("sub/photo.JPG", "sub/deeper/map.TIFF", "sub/deeper/navigation.txt"):
        session.add(DatasetUploadFile(upload_session_id=upload.id, filename=name,
            s3_key=f"{prefix}/{name}", size_bytes=16, content_type="image/jpeg", status="completed"))
    session.commit()


def test_uploaded_dataset_browse_uses_catalog_without_s3(database, monkeypatch):
    dataset(database, "datasets/owned")
    monkeypatch.setattr(routes.storage, "iter_objects", lambda *args, **kwargs: pytest.fail("unexpected S3 listing"))
    principal = security.Principal("alice", "operator", organization_id="org-a")
    result = routes.browse_path(principal, "datasets/owned", None)
    assert result == [
        {"name": "sub", "path": "datasets/owned/sub", "is_dir": True, "image_count": 2},
        {"name": "dataset-manifest.json", "path": "datasets/owned/dataset-manifest.json", "is_dir": False, "image_count": 0},
    ]
    nested = routes.browse_path(principal, "datasets/owned/sub", None)
    assert nested[0] == {"name": "deeper", "path": "datasets/owned/sub/deeper", "is_dir": True, "image_count": 1}
    assert nested[1]["name"] == "photo.JPG"


@pytest.mark.parametrize("organization,owner", [("org-b", "alice"), ("org-a", "bob")])
def test_catalog_does_not_browse_another_tenant_or_owner(database, monkeypatch, organization, owner):
    dataset(database, "datasets/private", organization=organization, owner=owner)
    monkeypatch.setattr(routes.storage, "iter_objects", lambda *args, **kwargs: pytest.fail("unauthorized S3 access"))
    with pytest.raises(HTTPException) as error:
        routes.browse_path(security.Principal("alice", "operator", organization_id="org-a"), "datasets/private", None)
    assert error.value.status_code == 404


def test_legacy_browse_lists_once_regardless_of_directory_count(database, monkeypatch):
    dataset(database, "datasets/legacy", catalog=False)
    calls = []
    def listing(prefix):
        calls.append(prefix)
        return [f"{prefix}dir-{index}/nested/photo.jpg" for index in range(100)] + [f"{prefix}readme.txt"]
    monkeypatch.setattr(routes.storage, "iter_objects", listing)
    result = routes.browse_path(security.Principal("alice", "operator", organization_id="org-a"), "datasets/legacy", None)
    assert calls == ["datasets/legacy/"]
    assert len(result) == 101
    assert sum(item["image_count"] for item in result) == 100


def test_grouping_rejects_keys_outside_authorized_prefix():
    with pytest.raises(ValueError, match="outside"):
        browse.browse_items_from_keys("datasets/one", ["datasets/other/private.jpg"])


def test_literal_sql_wildcards_do_not_select_sibling_keys(database):
    dataset(database, "datasets/owned")
    upload = database.query(DatasetUploadSession).one()
    for folder in ("sub_dir", "subXdir"):
        database.add(DatasetUploadFile(upload_session_id=upload.id, filename=f"{folder}.jpg",
            s3_key=f"datasets/owned/{folder}/image.jpg", size_bytes=16,
            content_type="image/jpeg", status="completed"))
    database.commit()
    principal = security.Principal("alice", "operator", organization_id="org-a")
    assert browse.catalog_browse_keys(database, "datasets/owned/sub_dir", principal, None) == [
        "datasets/owned/sub_dir/image.jpg",
    ]


def test_storage_pages_are_consumed_lazily(monkeypatch):
    from types import SimpleNamespace
    from shared import storage
    consumed = []
    def paginate(**kwargs):
        assert kwargs == {"Bucket": "test", "Prefix": "root/", "Delimiter": ""}
        consumed.append(1)
        yield {"Contents": [{"Key": "root/first.jpg"}]}
        consumed.append(2)
        yield {"Contents": [{"Key": "root/second.jpg"}]}
    client = SimpleNamespace(get_paginator=lambda name: SimpleNamespace(paginate=paginate))
    monkeypatch.setattr(storage, "_get_client", lambda: client)
    iterator = storage.iter_objects("root/", bucket="test")
    assert consumed == []
    assert next(iterator) == "root/first.jpg"
    assert consumed == [1]
    assert list(iterator) == ["root/second.jpg"]
    assert consumed == [1, 2]
