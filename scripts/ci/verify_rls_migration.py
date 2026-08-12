"""Seed revision 0025 and verify the current RLS migration chain."""

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
    "identity_capabilities",
    "organization_saas_policies",
    "organization_request_buckets",
    "organization_usage_events",
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
PLATFORM_TABLE_POLICIES = {
    "platform_members": "platform_identity",
    "platform_credentials": "platform_identity",
    "platform_audit_events": "platform_identity",
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
        platform_policy_rows = connection.execute(
            text(
                "SELECT tablename, policyname FROM pg_policies "
                "WHERE schemaname = 'public' "
                "AND tablename = ANY(:tables)"
            ),
            {"tables": list(PLATFORM_TABLE_POLICIES)},
        )
        platform_policies = {
            row.tablename: row.policyname for row in platform_policy_rows
        }
        missing_platform = {
            table
            for table, policy in PLATFORM_TABLE_POLICIES.items()
            if platform_policies.get(table) != policy
        }
        if missing_platform:
            raise RuntimeError(
                "platform identity policies are missing for: "
                + ", ".join(sorted(missing_platform))
            )
        row_security_tables = set(
            connection.execute(
                text(
                    "SELECT relname FROM pg_class "
                    "WHERE relname = ANY(:tables) AND relrowsecurity"
                ),
                {
                    "tables": list(
                        PROTECTED_TABLES | set(PLATFORM_TABLE_POLICIES)
                    )
                },
            ).scalars()
        )
        missing_row_security = (
            PROTECTED_TABLES | set(PLATFORM_TABLE_POLICIES)
        ) - row_security_tables
        if missing_row_security:
            raise RuntimeError(
                "row security is disabled for: "
                + ", ".join(sorted(missing_row_security))
            )
        required_triggers = {
            "trg_identity_audit_append_only",
            "trg_organization_usage_append_only",
            "trg_platform_audit_append_only",
            "trg_platform_organization_update_scope",
        }
        triggers = set(
            connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgname = ANY(:triggers) AND NOT tgisinternal"
                ),
                {"triggers": list(required_triggers)},
            ).scalars()
        )
        if triggers != required_triggers:
            raise RuntimeError(
                "database protection triggers are missing: "
                + ", ".join(sorted(required_triggers - triggers))
            )
        required_functions = {
            "droneai_identity_capability",
            "droneai_identity_capability_member",
            "droneai_platform_identity",
        }
        functions = set(
            connection.execute(
                text(
                    "SELECT proname FROM pg_proc "
                    "WHERE proname = ANY(:functions)"
                ),
                {"functions": list(required_functions)},
            ).scalars()
        )
        if functions != required_functions:
            raise RuntimeError(
                "identity security functions are missing: "
                + ", ".join(sorted(required_functions - functions))
            )
        audience_function = connection.execute(
            text(
                "SELECT to_regprocedure("
                "'droneai_mission_audience(text)')"
            )
        ).scalar_one()
        if audience_function is not None:
            raise RuntimeError("mission audience SECURITY DEFINER still exists")


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
