from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.database import Mission, ProcessedTile, get_or_create_mission


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "0035_tenant_mission_identity.py"


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    ProcessedTile.__table__.create(engine)
    return engine


def test_mission_identity_is_unique_inside_an_organization() -> None:
    engine = _engine()

    with Session(engine) as session:
        tenant_a = get_or_create_mission(
            session,
            "shared-flight",
            organization_id="tenant-a",
        )
        tenant_b = get_or_create_mission(
            session,
            "shared-flight",
            organization_id="tenant-b",
        )
        same_tenant = get_or_create_mission(
            session,
            "shared-flight",
            organization_id="tenant-a",
        )

        assert tenant_a.id != tenant_b.id
        assert same_tenant.id == tenant_a.id
        assert session.query(Mission).count() == 2

        session.add(Mission(vol_id="shared-flight", organization_id="tenant-a"))
        with pytest.raises(IntegrityError):
            session.flush()


def test_processed_tile_identity_is_scoped_to_the_mission() -> None:
    engine = _engine()

    with Session(engine) as session:
        tenant_a = Mission(vol_id="shared-flight", organization_id="tenant-a")
        tenant_b = Mission(vol_id="shared-flight", organization_id="tenant-b")
        session.add_all([tenant_a, tenant_b])
        session.flush()
        session.add_all(
            [
                ProcessedTile(
                    mission_id=tenant_a.id,
                    vol_id=tenant_a.vol_id,
                    tile_index=0,
                    detection_count=0,
                ),
                ProcessedTile(
                    mission_id=tenant_b.id,
                    vol_id=tenant_b.vol_id,
                    tile_index=0,
                    detection_count=0,
                ),
            ]
        )
        session.flush()

        session.add(
            ProcessedTile(
                mission_id=tenant_a.id,
                vol_id=tenant_a.vol_id,
                tile_index=0,
                detection_count=0,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_tenant_mission_identity_migration_replaces_global_constraints() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'down_revision: str | None = "0034"' in source
    assert '"missions_vol_id_key"' in source
    assert '"uq_missions_organization_vol_id"' in source
    assert '["organization_id", "vol_id"]' in source
    assert '"uq_processed_tile_mission_index"' in source
    assert '["mission_id", "tile_index"]' in source
    assert "GROUP BY vol_id HAVING COUNT(*) > 1" in source
    assert "GROUP BY vol_id, tile_index" in source
    assert "Cannot downgrade 0035" in source
    assert "application/schema forward" in source
