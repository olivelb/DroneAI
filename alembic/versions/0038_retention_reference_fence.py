"""Fence artifact parent references against concurrent mission deletion.

Revision ID: 0038
Revises: 0037
"""
from collections.abc import Sequence
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION droneai_guard_artifact_reference() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        DECLARE mission_row record;
        BEGIN
          FOR mission_row IN
            SELECT m.id, m.status FROM public.missions m
            JOIN public.mission_artifacts a ON a.mission_id = m.id
            WHERE a.id IN (NEW.artifact_id, NEW.parent_artifact_id)
            ORDER BY m.id FOR SHARE OF m
          LOOP
            IF mission_row.status IN ('deleting', 'deletion_failed') THEN
              RAISE EXCEPTION 'Cannot reference artifacts of a deleting mission'
                USING ERRCODE = '23514';
            END IF;
          END LOOP;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER droneai_artifact_reference_fence
        BEFORE INSERT OR UPDATE ON mission_artifact_parents
        FOR EACH ROW EXECUTE FUNCTION droneai_guard_artifact_reference()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER droneai_artifact_reference_fence ON mission_artifact_parents")
    op.execute("DROP FUNCTION droneai_guard_artifact_reference()")
