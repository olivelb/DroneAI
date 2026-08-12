"""Seed revision 0024 and verify the 0025 organization backfill."""

from __future__ import annotations

import argparse
import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

MIGRATED_ORGANIZATION = "migration-existing-customer"


def _engine():
    return create_engine(os.environ["DATABASE_URL"])


def seed() -> None:
    engine = _engine()
    with engine.begin() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if revision != "0024":
            raise RuntimeError(f"seed requires revision 0024, found {revision}")
        connection.execute(
            text(
                "INSERT INTO missions (vol_id, organization_id) "
                "VALUES ('identity-migration-existing', :organization_id)"
            ),
            {"organization_id": MIGRATED_ORGANIZATION},
        )
    engine.dispose()


def verify() -> None:
    engine = _engine()
    with engine.connect() as connection:
        organizations = set(
            connection.execute(
                text(
                    "SELECT id FROM organizations "
                    "WHERE id IN (:migrated, 'legacy-unassigned', 'local-development')"
                ),
                {"migrated": MIGRATED_ORGANIZATION},
            ).scalars()
        )
    expected = {
        MIGRATED_ORGANIZATION,
        "legacy-unassigned",
        "local-development",
    }
    if organizations != expected:
        raise RuntimeError(f"organization backfill mismatch: {organizations!r}")
    mission_foreign_keys = inspect(engine).get_foreign_keys("missions")
    if not any(
        item["constrained_columns"] == ["organization_id"]
        and item["referred_table"] == "organizations"
        for item in mission_foreign_keys
    ):
        raise RuntimeError("missions.organization_id foreign key is missing")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO missions (vol_id, organization_id) "
                    "VALUES ('identity-migration-invalid', 'unknown-customer')"
                )
            )
    except IntegrityError:
        pass
    else:
        raise RuntimeError("unknown organization was accepted")
    engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("seed", "verify"))
    args = parser.parse_args()
    if args.mode == "seed":
        seed()
    else:
        verify()


if __name__ == "__main__":
    main()
