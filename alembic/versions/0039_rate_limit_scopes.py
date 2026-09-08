"""Isolate rate-limit scopes without resetting existing quotas.

Revision ID: 0039
Revises: 0038
"""
from alembic import op
import sqlalchemy as sa

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_rate_limit_buckets", sa.Column("scope", sa.String(64), nullable=False, server_default="legacy"))
    op.drop_constraint("api_rate_limit_buckets_pkey", "api_rate_limit_buckets", type_="primary")
    op.create_primary_key("api_rate_limit_buckets_pkey", "api_rate_limit_buckets", ["scope", "key_hash"])
    op.drop_index("ix_api_rate_limit_buckets_updated_at", table_name="api_rate_limit_buckets")
    op.create_index("ix_api_rate_limit_buckets_scope_updated", "api_rate_limit_buckets", ["scope", "updated_at"])


def downgrade() -> None:
    # Fail rather than silently discard quota state if custom scopes reused hashes.
    op.drop_constraint("api_rate_limit_buckets_pkey", "api_rate_limit_buckets", type_="primary")
    op.create_primary_key("api_rate_limit_buckets_pkey", "api_rate_limit_buckets", ["key_hash"])
    op.drop_index("ix_api_rate_limit_buckets_scope_updated", table_name="api_rate_limit_buckets")
    op.drop_column("api_rate_limit_buckets", "scope")
    op.create_index("ix_api_rate_limit_buckets_updated_at", "api_rate_limit_buckets", ["updated_at"])
