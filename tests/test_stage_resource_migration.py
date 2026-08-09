import importlib.util
from pathlib import Path
from types import SimpleNamespace


def test_0017_repairs_only_pending_unreserved_rasterization_runs(monkeypatch):
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0017_correct_rasterization_resource_class.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0017", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    statements: list[str] = []
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(execute=lambda statement: statements.append(str(statement))),
    )

    migration.upgrade()

    assert len(statements) == 1
    statement = " ".join(statements[0].split())
    assert "SET resource_class = 'gpu-standard'" in statement
    assert "stage = 'rasterization'" in statement
    assert "resource_class = 'cpu-standard'" in statement
    assert "status IN ('blocked', 'queued')" in statement
    assert "executor IS NULL" in statement
