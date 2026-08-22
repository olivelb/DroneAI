import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _migration():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0036_gaussian_viewer_stage.py"
    spec = importlib.util.spec_from_file_location("migration_0036", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_0036_expands_both_stage_constraints(monkeypatch):
    migration = _migration()
    created: list[tuple[str, str]] = []
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            drop_constraint=lambda *_args, **_kwargs: None,
            create_check_constraint=lambda name, _table, condition: created.append((name, condition)),
        ),
    )

    migration.upgrade()

    assert ("ck_mission_stage_runs_stage", "gaussian_viewer") in [
        (name, "gaussian_viewer" if "gaussian_viewer" in condition else "") for name, condition in created
    ]
    assert any(
        name == "ck_mission_stage_runs_resource_class" and "cpu-high-memory" in condition for name, condition in created
    )


def test_0036_refuses_destructive_downgrade(monkeypatch):
    migration = _migration()
    connection = SimpleNamespace(execute=lambda _statement: SimpleNamespace(first=lambda: ("run",)))
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(get_bind=lambda: connection),
    )

    with pytest.raises(RuntimeError, match="Cannot downgrade 0036"):
        migration.downgrade()
