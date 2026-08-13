from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_identity_security_definer_functions_revoke_public_execution() -> None:
    migration = (
        ROOT / "alembic" / "versions" / "0034_security_definer_acls.py"
    ).read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "ci" / "verify_rls_migration.py").read_text(
        encoding="utf-8"
    )

    for function in (
        "droneai_platform_identity()",
        "droneai_identity_capability()",
        "droneai_identity_capability_member(text)",
    ):
        assert function in migration
    assert "REVOKE ALL ON FUNCTION" in migration
    assert "information_schema.routine_privileges" in verifier
    assert "grantee = 'PUBLIC'" in verifier
