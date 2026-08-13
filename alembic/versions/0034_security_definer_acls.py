"""Restrict execution of identity SECURITY DEFINER functions.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IDENTITY_FUNCTIONS = (
    "droneai_platform_identity()",
    "droneai_identity_capability()",
    "droneai_identity_capability_member(text)",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for function in IDENTITY_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for function in IDENTITY_FUNCTIONS:
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC")
