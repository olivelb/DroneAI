"""Real PostgreSQL and S3 qualification for audited legacy adoption."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text

from shared import storage
from shared.artifact_manifest import (
    ARTIFACT_MANIFEST_VERSION,
    ArtifactManifest,
    ManifestBlob,
    ManifestFile,
    canonical_v2_bytes,
    content_addressed_blob_key,
    parse_artifact_manifest,
)
from shared.config import S3_BUCKET
from shared.database import (
    Mission,
    MissionArtifact,
    MissionStageRun,
    Organization,
    OrganizationMember,
    OrganizationUsageEvent,
    get_engine,
    get_session,
)
from shared.gcp_bundle import bundle_blob, validate_gcp_bundle
from shared.legacy_adoption import build_adoption_plan
from shared.legacy_adoption_execution import apply_adoption_plan
from shared.legacy_adoption_types import S3AdoptionObjectStore


def _cleanup_database(organization_id: str) -> None:
    """Remove only this test's synthetic graph and append-only evidence."""

    with get_engine().begin() as connection:
        connection.execute(
            text("DELETE FROM missions WHERE organization_id = :organization_id"),
            {"organization_id": organization_id},
        )
        connection.execute(
            text(
                "DELETE FROM organization_members "
                "WHERE organization_id = :organization_id"
            ),
            {"organization_id": organization_id},
        )
        connection.execute(
            text(
                "ALTER TABLE organization_usage_events DISABLE TRIGGER "
                "trg_organization_usage_append_only"
            )
        )
        try:
            connection.execute(
                text(
                    "DELETE FROM organization_usage_events "
                    "WHERE organization_id = :organization_id "
                    "AND actor_subject = 'integration'"
                ),
                {"organization_id": organization_id},
            )
        finally:
            connection.execute(
                text(
                    "ALTER TABLE organization_usage_events ENABLE TRIGGER "
                    "trg_organization_usage_append_only"
                )
            )
        connection.execute(
            text("DELETE FROM organizations WHERE id = :organization_id"),
            {"organization_id": organization_id},
        )


@pytest.mark.integration
def test_legacy_mission_adoption_rewrites_manifest_and_commits_once() -> None:
    suffix = uuid4().hex[:12]
    organization_id = f"adoption-{suffix}"
    vol_id = f"legacy-adoption-{suffix}"
    stage_run_id = str(uuid4())
    adoption_run_id = str(uuid4())
    source_prefix = f"missions/{vol_id}"
    target_prefix = f"organizations/{organization_id}/missions/{vol_id}"
    source_manifest_key = (
        f"{source_prefix}/stage-runs/{stage_run_id}/rasterization/manifest.json"
    )
    target_manifest_key = (
        f"{target_prefix}/stage-runs/{stage_run_id}/rasterization/manifest.json"
    )
    blob = f"synthetic-adoption-{suffix}".encode()
    blob_checksum = hashlib.sha256(blob).hexdigest()
    source_blob_key = content_addressed_blob_key(blob_checksum)
    target_blob_key = content_addressed_blob_key(
        blob_checksum,
        organization_id=organization_id,
    )
    gcp_list = (
        f"EPSG:4326\n1 2 3 4 5 image-{suffix}.jpg point-1\n".encode()
    )
    accuracy_csv = (
        f"point_id,horizontal_accuracy_m\npoint-{suffix},0.02\n".encode()
    )
    gcp_source_keys = (
        str(bundle_blob(gcp_list)["key"]),
        str(bundle_blob(accuracy_csv)["key"]),
    )
    gcp_target_keys = (
        str(bundle_blob(gcp_list, organization_id)["key"]),
        str(bundle_blob(accuracy_csv, organization_id)["key"]),
    )
    gcp_bundle = {
        "schema_version": 1,
        "set_id": str(uuid4()),
        "source_sha256": hashlib.sha256(b"synthetic-gcp-source").hexdigest(),
        "gcp_list": bundle_blob(gcp_list),
        "accuracy_csv": bundle_blob(accuracy_csv),
        "quality": {
            "adjustment_points": 3,
            "checkpoint_points": 0,
            "marked_observations": 6,
            "verification": "adjustment-only-unverified",
        },
    }
    source_manifest = canonical_v2_bytes(
        ArtifactManifest(
            schema_version=ARTIFACT_MANIFEST_VERSION,
            files=(
                ManifestFile(
                    path="orthomosaic.tif",
                    role="raster",
                    blob=ManifestBlob(
                        key=source_blob_key,
                        size_bytes=len(blob),
                        checksum_sha256=blob_checksum,
                    ),
                ),
            ),
        )
    )
    manifest_checksum = hashlib.sha256(source_manifest).hexdigest()
    store = S3AdoptionObjectStore()

    try:
        storage.put_verified_bytes(source_blob_key, blob)
        storage.put_verified_bytes(gcp_source_keys[0], gcp_list)
        storage.put_verified_bytes(gcp_source_keys[1], accuracy_csv)
        storage.put_verified_bytes(source_manifest_key, source_manifest)
        with get_session() as session:
            organization = Organization(
                id=organization_id,
                display_name="Legacy adoption integration",
                status="active",
                created_by="integration",
                updated_by="integration",
            )
            member = OrganizationMember(
                organization_id=organization_id,
                subject=f"admin-{suffix}",
                role="admin",
                status="active",
                created_by="integration",
                updated_by="integration",
            )
            mission = Mission(
                vol_id=vol_id,
                organization_id="legacy-unassigned",
                owner_subject=f"legacy-{suffix}",
                workspace_prefix=source_prefix,
                status="completed",
            )
            session.add_all([organization, member, mission])
            session.flush()
            stage_run = MissionStageRun(
                run_id=stage_run_id,
                mission_id=mission.id,
                stage="rasterization",
                attempt=0,
                status="succeeded",
                progress=100,
                idempotency_key=hashlib.sha256(
                    f"{vol_id}:rasterization".encode()
                ).hexdigest(),
                resource_class="cpu-standard",
                parameters={"gcp_bundle": gcp_bundle},
            )
            session.add(stage_run)
            session.flush()
            session.add(
                MissionArtifact(
                    mission_id=mission.id,
                    stage_run_id=stage_run.id,
                    kind="raster",
                    uri=f"s3://{S3_BUCKET}/{source_manifest_key}",
                    checksum_sha256=manifest_checksum,
                    size_bytes=len(blob),
                    artifact_metadata={
                        "manifest_key": source_manifest_key,
                        "manifest_schema_version": 2,
                    },
                )
            )

        with get_session() as session:
            plan = build_adoption_plan(
                session,
                target_organization_id=organization_id,
                owner_subject=f"admin-{suffix}",
                actor_subject="integration",
                store=store,
                mission_ids=[vol_id],
                run_id=adoption_run_id,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(
                executor.map(
                    lambda _index: apply_adoption_plan(plan, store=store),
                    range(2),
                )
            )
        apply_adoption_plan(plan, store=store)

        with get_session() as session:
            mission = session.query(Mission).filter_by(vol_id=vol_id).one()
            artifact = session.query(MissionArtifact).filter_by(
                mission_id=mission.id
            ).one()
            run = session.query(MissionStageRun).filter_by(
                mission_id=mission.id
            ).one()
            actions = [
                event.action
                for event in session.query(OrganizationUsageEvent)
                .filter(
                    OrganizationUsageEvent.organization_id == organization_id,
                    OrganizationUsageEvent.resource_id.in_((adoption_run_id, vol_id)),
                )
                .order_by(OrganizationUsageEvent.id)
                .all()
            ]
            assert mission.organization_id == organization_id
            assert mission.owner_subject == f"admin-{suffix}"
            assert mission.workspace_prefix == target_prefix
            assert artifact.uri == f"s3://{S3_BUCKET}/{target_manifest_key}"
            assert artifact.artifact_metadata["manifest_schema_version"] == 3
            validate_gcp_bundle(
                run.parameters["gcp_bundle"],
                expected_organization_id=organization_id,
            )
            assert actions == [
                "legacy_adoption_started",
                "legacy_adoption_resource",
                "legacy_adoption_completed",
            ]

        adopted = parse_artifact_manifest(
            storage.get_object_bytes(target_manifest_key),
            manifest_key=target_manifest_key,
        )
        assert adopted.organization_id == organization_id
        assert adopted.files[0].blob.key == target_blob_key
        assert storage.get_object_bytes(source_manifest_key) == source_manifest
        assert storage.get_object_bytes(source_blob_key) == blob
        assert storage.get_object_bytes(target_blob_key) == blob
        assert storage.get_object_bytes(gcp_target_keys[0]) == gcp_list
        assert storage.get_object_bytes(gcp_target_keys[1]) == accuracy_csv
    finally:
        _cleanup_database(organization_id)
        storage.delete_prefix(f"{source_prefix}/")
        storage.delete_prefix(f"{target_prefix}/")
        storage.delete_object(source_blob_key)
        storage.delete_object(target_blob_key)
        for key in (*gcp_source_keys, *gcp_target_keys):
            storage.delete_object(key)
