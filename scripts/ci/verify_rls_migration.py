"""Seed revision 0025 and verify the 0026 RLS/outbox migration."""

from __future__ import annotations

import json
import os
import sys

from sqlalchemy import create_engine, inspect, text

EVENT_ID = "rls-migration-existing-outbox"
MISSION_ID = "identity-migration-existing"
ORGANIZATION_ID = "migration-existing-customer"
PROTECTED_TABLES = {
    "organizations",
    "organization_members",
    "api_credentials",
    "identity_audit_events",
    "dataset_upload_sessions",
    "dataset_upload_files",
    "datasets",
    "missions",
    "mission_stage_runs",
    "detection_shard_receipts",
    "mission_artifacts",
    "mission_artifact_parents",
    "detections",
    "processed_tiles",
    "ai_analysis_runs",
    "ai_analysis_tiles",
    "map_features",
    "map_feature_audit_events",
    "gcp_sets",
    "gcp_points",
    "gcp_observations",
    "gcp_audit_events",
    "raster_layer_styles",
    "mission_logs",
    "outbox_events",
}


def seed() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.begin() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        if revision != "0025":
            raise RuntimeError(f"seed requires revision 0025, found {revision}")
        connection.execute(
            text(
                """
                INSERT INTO outbox_events (
                    event_id, event_type, topic, payload, status, attempts,
                    available_at, created_at
                ) VALUES (
                    :event_id, 'mission', 'vols-bruts', CAST(:payload AS json),
                    'pending', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "event_id": EVENT_ID,
                "payload": json.dumps(
                    {"event_type": "mission", "vol_id": MISSION_ID}
                ),
            },
        )


def verify() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    inspector = inspect(engine)
    foreign_keys = inspector.get_foreign_keys("outbox_events")
    if not any(
        item["constrained_columns"] == ["organization_id"]
        and item["referred_table"] == "organizations"
        for item in foreign_keys
    ):
        raise RuntimeError("outbox organization foreign key is missing")

    with engine.connect() as connection:
        organization = connection.execute(
            text(
                "SELECT organization_id FROM outbox_events "
                "WHERE event_id = :event_id"
            ),
            {"event_id": EVENT_ID},
        ).scalar_one()
        if organization != ORGANIZATION_ID:
            raise RuntimeError(
                f"outbox backfill resolved {organization!r}, expected "
                f"{ORGANIZATION_ID!r}"
            )
        policies = set(
            connection.execute(
                text(
                    "SELECT tablename FROM pg_policies "
                    "WHERE schemaname = 'public' AND policyname = 'tenant_isolation'"
                )
            ).scalars()
        )
        missing = PROTECTED_TABLES - policies
        if missing:
            raise RuntimeError(
                "tenant policies are missing for: " + ", ".join(sorted(missing))
            )
        audience = connection.execute(
            text(
                "SELECT audience_organization_id, audience_owner_subject "
                "FROM droneai_mission_audience(:vol_id)"
            ),
            {"vol_id": MISSION_ID},
        ).one()
        if audience[0] != ORGANIZATION_ID:
            raise RuntimeError("mission audience function returned the wrong tenant")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "seed":
        seed()
    elif command == "verify":
        verify()
    else:
        raise SystemExit("usage: verify_rls_migration.py {seed|verify}")


if __name__ == "__main__":
    main()
