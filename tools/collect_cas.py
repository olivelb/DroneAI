#!/usr/bin/env python3
"""Inventory or collect orphan CAS objects during an organization maintenance window.

Dry-run by default. Stop every publisher (including independent local runners)
before --execute. SQL locks prevent new missions while the maintenance runs;
Kubernetes cleanup evidence is required for every existing stage execution.
"""
from __future__ import annotations

import argparse
import importlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from shared import storage
from shared.artifact_manifest import parse_artifact_manifest, validate_cas_organization
from shared.cas_gc import CasObject, plan_cas_collection
from shared.database import Mission, MissionStageRun, MissionArtifact, DetectionShardReceipt, Organization, get_session


def _keys(value: Any, prefix: str) -> set[str]:
    if isinstance(value, str):
        value = value.removeprefix(f"s3://{storage.S3_BUCKET}/")
        return {value} if value.startswith(prefix) else set()
    if isinstance(value, dict):
        return set().union(*(_keys(item, prefix) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(item, prefix) for item in value))
    return set()


def collect(organization_id: str, *, execute: bool, grace_days: int, held_manifests: list[str]) -> dict[str, Any]:
    organization = validate_cas_organization(organization_id)
    prefix = f"organizations/{organization}/"
    blob_prefix = prefix + "blobs/sha256/"
    quiescent = importlib.import_module("app4-dashboard.api.retention")._compute_is_quiescent
    with get_session() as session:
        session.query(Organization).filter_by(id=organization).with_for_update().one()
        missions = session.query(Mission).filter_by(organization_id=organization).with_for_update().all()
        if any(not quiescent(session, mission.id) for mission in missions):
            raise RuntimeError("CAS maintenance requires every stage writer to be confirmed stopped")
        protected = set()
        for mission in missions:
            protected.update(_keys(mission.resume_info, blob_prefix))
        for model, field in ((MissionStageRun, "provenance"), (MissionArtifact, "artifact_metadata")):
            for record in session.query(model).filter(model.mission_id.in_([mission.id for mission in missions])):
                protected.update(_keys(getattr(record, field), blob_prefix))
                if model is MissionArtifact:
                    protected.update(_keys(record.uri, blob_prefix))
        for receipt in session.query(DetectionShardReceipt).join(MissionStageRun, MissionStageRun.id == DetectionShardReceipt.stage_run_id).join(Mission, Mission.id == MissionStageRun.mission_id).filter(Mission.organization_id == organization):
            protected.update(_keys(receipt.result_key, blob_prefix))
        # Keep every surviving mission manifest, including failed-publication and
        # resume snapshots; only mission retention removes those roots.
        manifest_keys = {key for key in storage.list_objects(prefix + "missions/") if key.endswith("/manifest.json")}
        manifest_keys.update(held_manifests)
        manifests = {}
        pending = list(manifest_keys)
        while pending:
            key = pending.pop()
            if key in manifests:
                continue
            if not key.startswith(prefix):
                raise ValueError("CAS manifest crosses the organization boundary")
            manifest = parse_artifact_manifest(storage.get_object_bytes(key))
            if manifest.organization_id != organization:
                raise ValueError("CAS manifest organization mismatch")
            manifests[key] = manifest
            pending.extend(parent.manifest_key for parent in manifest.parents)
        client = storage._get_client()
        blobs = [CasObject(item["Key"], int(item["Size"]), item["LastModified"]) for page in client.get_paginator("list_objects_v2").paginate(Bucket=storage.S3_BUCKET, Prefix=blob_prefix) for item in page.get("Contents", [])]
        plan = plan_cas_collection(manifests, manifest_keys, blobs, now=datetime.now(UTC), grace=timedelta(days=grace_days), protected_keys=protected)
        if execute:
            for blob in plan:
                storage.delete_object(blob.key)
        return {"organization_id": organization, "executed": execute, "current_objects": len(blobs), "candidates": len(plan), "candidate_bytes": sum(blob.size for blob in plan), "keys": [blob.key for blob in plan], "version_policy": "current objects only; old versions require a separate lifecycle/erasure policy"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("organization_id")
    parser.add_argument("--grace-days", type=int, default=7)
    parser.add_argument("--hold-manifest", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    if args.execute and not args.writers_stopped:
        parser.error("--execute requires --writers-stopped after stopping every publisher")
    print(json.dumps(collect(args.organization_id, execute=args.execute, grace_days=args.grace_days, held_manifests=args.hold_manifest), indent=2))


if __name__ == "__main__":
    main()
