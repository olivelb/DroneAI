"""Exercise current stable models against ``head-1`` and preserve their data.

The CI workflow invokes ``seed`` after downgrading one Alembic revision, then
upgrades to head and invokes ``verify``. A migration that requires the new
schema before deployment, or loses stable organization/mission data during
upgrade, therefore fails the rolling-compatibility gate.
"""

from __future__ import annotations

import argparse

from sqlalchemy import inspect, text

from shared.database import Mission, Organization, get_engine, get_session
from shared.tenancy import mission_prefix

ORGANIZATION_ID = "rolling-compatibility"
MISSION_ID = "rolling-compatibility-mission"


def _revision() -> str:
    with get_engine().connect() as connection:
        return str(
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        )


def seed() -> None:
    revision = _revision()
    with get_session() as session:
        existing_mission = session.query(Mission).filter_by(
            vol_id=MISSION_ID
        ).one_or_none()
        if existing_mission is not None:
            session.delete(existing_mission)
            session.flush()
        existing_organization = session.get(Organization, ORGANIZATION_ID)
        if existing_organization is not None:
            session.delete(existing_organization)
            session.flush()
        session.add(
            Organization(
                id=ORGANIZATION_ID,
                display_name=f"Rolling compatibility from {revision}",
                status="active",
                created_by="migration-qualification",
                updated_by="migration-qualification",
            )
        )
        session.flush()
        session.add(
            Mission(
                vol_id=MISSION_ID,
                organization_id=ORGANIZATION_ID,
                owner_subject="migration-qualification",
                workspace_prefix=mission_prefix(ORGANIZATION_ID, MISSION_ID),
                status="pending",
            )
        )
    print(f"Seeded current stable models on Alembic revision {revision}")


def verify() -> None:
    revision = _revision()
    with get_session(organization_id=ORGANIZATION_ID) as session:
        organization = session.get(Organization, ORGANIZATION_ID)
        mission = session.query(Mission).filter_by(vol_id=MISSION_ID).one()
        if organization is None:
            raise RuntimeError("rolling upgrade lost the organization")
        if mission.workspace_prefix != mission_prefix(ORGANIZATION_ID, MISSION_ID):
            raise RuntimeError("rolling upgrade changed the mission namespace")
    if not inspect(get_engine()).has_table("access_audit_events"):
        raise RuntimeError("head schema is missing access_audit_events")
    print(f"Verified stable data after upgrade to Alembic revision {revision}")


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
