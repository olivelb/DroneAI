"""Add durable legacy-adoption audit actions.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = (
    "policy_updated",
    "storage_reserved",
    "storage_released",
    "stage_scheduled",
    "request_throttled",
    "retention_deleted",
    "retention_failed",
)
ADOPTION_ACTIONS = (
    "legacy_adoption_started",
    "legacy_adoption_resource",
    "legacy_adoption_completed",
    "legacy_adoption_failed",
)


def _constraint(actions: tuple[str, ...]) -> str:
    values = ", ".join(f"'{value}'" for value in actions)
    return f"action IN ({values})"


def upgrade() -> None:
    op.drop_constraint(
        "ck_organization_usage_action",
        "organization_usage_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_organization_usage_action",
        "organization_usage_events",
        _constraint(OLD_ACTIONS + ADOPTION_ACTIONS),
    )


def downgrade() -> None:
    values = ", ".join(f"'{value}'" for value in ADOPTION_ACTIONS)
    adoption_count = int(
        op.get_bind()
        .execute(
            text(
                "SELECT count(*) FROM organization_usage_events "
                f"WHERE action IN ({values})"
            ),
        )
        .scalar_one()
    )
    if adoption_count:
        raise RuntimeError(
            "Cannot downgrade 0033 while legacy-adoption audit events exist"
        )
    op.drop_constraint(
        "ck_organization_usage_action",
        "organization_usage_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_organization_usage_action",
        "organization_usage_events",
        _constraint(OLD_ACTIONS),
    )
