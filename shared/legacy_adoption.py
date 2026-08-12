"""Fail-closed adoption of historical storage into an explicit organization.

The source namespace is never deleted. Object copies are idempotent and the
database ownership switch happens only after every source identity has been
revalidated. This is an operational migration; it does not change scientific
parameters or results.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from shared.artifact_manifest import (
    ARTIFACT_MANIFEST_VERSION,
    LEGACY_MANIFEST_VERSION,
    TENANT_ARTIFACT_MANIFEST_VERSION,
    ArtifactManifest,
    ManifestBlob,
    ManifestFile,
    ManifestParent,
    canonical_v3_bytes,
    content_addressed_blob_key,
    parse_artifact_manifest,
)
from shared.database import (
    Dataset,
    DatasetUploadSession,
    InboxEvent,
    Mission,
    MissionArtifact,
    Organization,
    OrganizationMember,
    OutboxEvent,
)
from shared.config import S3_BUCKET
from shared.organization_saas import check_storage_reservation
from shared.legacy_adoption_types import (
    AdoptionObjectStore,
    AdoptionPlan,
    ArtifactAdoption,
    ControlWrite,
    CopyIntent,
    ObjectIdentity,
    ResourceAdoption,
)
from shared.tenancy import (
    LEGACY_ORGANIZATION_ID,
    LOCAL_ORGANIZATION_ID,
    dataset_prefix,
    mission_prefix,
    validate_organization_id,
)

TERMINAL_MISSION_STATUSES = {
    "success",
    "completed",
    "error",
    "cancelled",
    "stale",
}
TERMINAL_STAGE_STATUSES = {"succeeded", "failed", "cancelled"}
TERMINAL_ANALYSIS_STATUSES = {"completed", "failed", "cancelled"}
GLOBAL_CAS_PATTERN = re.compile(r"^blobs/sha256/([0-9a-f]{2})/([0-9a-f]{64})$")


def _validated_subject(value: str, field_name: str) -> str:
    subject = value.strip()
    if not subject or len(subject) > 256:
        raise ValueError(f"{field_name} must contain 1 to 256 characters")
    return subject


def _validated_run_id(value: str | None) -> str:
    if value is None:
        return str(uuid4())
    try:
        return str(UUID(value))
    except ValueError as error:
        raise ValueError("run_id must be a UUID") from error


def _identity(store: AdoptionObjectStore, key: str) -> ObjectIdentity:
    info = store.object_info(key)
    if info is None:
        raise FileNotFoundError(f"Missing legacy adoption source object: {key}")
    metadata = info.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    raw_checksum = metadata_map.get("sha256")
    checksum = str(raw_checksum) if raw_checksum else None
    return ObjectIdentity(
        key=key,
        size_bytes=int(cast(int, info["size"])),
        etag=str(info.get("etag") or ""),
        checksum_sha256=checksum,
    )


def _inventory(
    store: AdoptionObjectStore,
    prefix: str,
) -> tuple[ObjectIdentity, ...]:
    boundary = f"{prefix.rstrip('/')}/"
    keys = sorted(set(store.list_objects(boundary)))
    if any(not key.startswith(boundary) for key in keys):
        raise ValueError(f"Storage listed an object outside {boundary}")
    return tuple(_identity(store, key) for key in keys)


def _target_key(source_key: str, source_prefix: str, target_prefix: str) -> str:
    boundary = f"{source_prefix.rstrip('/')}/"
    if not source_key.startswith(boundary):
        raise ValueError(f"Object is outside the adoption source: {source_key}")
    relative = source_key.removeprefix(boundary)
    if not relative or any(
        component in {"", ".", ".."} for component in relative.split("/")
    ):
        raise ValueError(f"Object has an unsafe adoption key: {source_key}")
    return f"{target_prefix.rstrip('/')}/{relative}"


def _manifest_key(artifact: MissionArtifact) -> str:
    metadata = cast(dict[str, object], artifact.artifact_metadata or {})
    key = metadata.get("manifest_key")
    if isinstance(key, str) and key:
        return key
    marker = f"s3://{S3_BUCKET}/"
    uri = str(artifact.uri)
    if uri.startswith(marker):
        return uri.removeprefix(marker)
    raise ValueError(f"Artifact {artifact.artifact_id} has no canonical manifest key")


def _control_write(target_key: str, payload: bytes) -> ControlWrite:
    return ControlWrite(
        target_key=target_key,
        payload=payload,
        checksum_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _dataset_control_write(
    dataset: Dataset,
    *,
    target_organization_id: str,
    source_prefix: str,
    target_prefix: str,
    store: AdoptionObjectStore,
) -> tuple[ControlWrite, dict[str, int]]:
    manifest_key = str(dataset.manifest_s3_key)
    if manifest_key != f"{source_prefix}/dataset-manifest.json":
        raise ValueError(f"Dataset {dataset.name} has a non-canonical legacy manifest")
    try:
        payload = json.loads(store.read_bytes(manifest_key))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Dataset {dataset.name} manifest is invalid") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"Dataset {dataset.name} manifest schema is unsupported")
    if payload.get("dataset") != str(dataset.name):
        raise ValueError(f"Dataset {dataset.name} manifest identity is invalid")
    if payload.get("organization_id") not in {
        None,
        LEGACY_ORGANIZATION_ID,
    }:
        raise ValueError(f"Dataset {dataset.name} manifest is not legacy-bound")
    files = payload.get("files")
    if not isinstance(files, list):
        raise ValueError(f"Dataset {dataset.name} manifest files are invalid")
    if len(files) != int(dataset.file_count):
        raise ValueError(f"Dataset {dataset.name} manifest file count is invalid")
    total_bytes = 0
    manifest_files: dict[str, int] = {}
    for item in files:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("s3_key"), str)
            or isinstance(item.get("size"), bool)
            or not isinstance(item.get("size"), int)
            or int(item["size"]) < 0
        ):
            raise ValueError(f"Dataset {dataset.name} manifest file is invalid")
        source_key = str(item["s3_key"])
        if source_key in manifest_files:
            raise ValueError(f"Dataset {dataset.name} manifest has duplicate files")
        target_key = _target_key(
            source_key,
            source_prefix,
            target_prefix,
        )
        identity = _identity(store, source_key)
        if identity.size_bytes != int(item["size"]):
            raise OSError(f"Dataset {dataset.name} file size is inconsistent")
        total_bytes += identity.size_bytes
        manifest_files[source_key] = identity.size_bytes
        item["s3_key"] = target_key
    if total_bytes != int(dataset.total_bytes):
        raise ValueError(f"Dataset {dataset.name} manifest total size is invalid")
    payload["organization_id"] = target_organization_id
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return (
        _control_write(f"{target_prefix}/dataset-manifest.json", canonical),
        manifest_files,
    )


def _validate_dataset_upload_session(
    session: Session,
    dataset: Dataset,
    manifest_files: Mapping[str, int],
) -> None:
    if dataset.upload_session_id is None:
        return
    upload = session.get(DatasetUploadSession, int(dataset.upload_session_id))
    if (
        upload is None
        or str(upload.organization_id) != LEGACY_ORGANIZATION_ID
        or str(upload.dataset_name) != str(dataset.name)
        or str(upload.status) != "completed"
        or int(upload.file_count) != int(dataset.file_count)
        or int(upload.total_bytes) != int(dataset.total_bytes)
    ):
        raise ValueError(f"Dataset {dataset.name} upload session is inconsistent")
    upload_files = {str(item.s3_key): int(item.size_bytes) for item in upload.files}
    if (
        len(upload_files) != len(upload.files)
        or any(str(item.status) != "completed" for item in upload.files)
        or upload_files != manifest_files
    ):
        raise ValueError(f"Dataset {dataset.name} upload files are inconsistent")


def _artifact_adoptions(
    mission: Mission,
    *,
    target_organization_id: str,
    source_prefix: str,
    target_prefix: str,
    store: AdoptionObjectStore,
) -> tuple[
    tuple[ArtifactAdoption, ...],
    tuple[CopyIntent, ...],
    tuple[ControlWrite, ...],
]:
    artifacts = {
        str(artifact.artifact_id): artifact for artifact in mission.artifacts
    }
    parsed: dict[str, ArtifactManifest] = {}
    source_keys: dict[str, str] = {}
    for artifact_id, artifact in artifacts.items():
        source_key = _manifest_key(artifact)
        target_key = _target_key(source_key, source_prefix, target_prefix)
        content = store.read_bytes(source_key)
        digest = hashlib.sha256(content).hexdigest()
        if digest != str(artifact.checksum_sha256):
            raise OSError(
                f"Artifact {artifact_id} manifest checksum mismatch: "
                f"{digest}/{artifact.checksum_sha256}"
            )
        parsed[artifact_id] = parse_artifact_manifest(
            content,
            manifest_key=source_key,
        )
        source_keys[artifact_id] = source_key
        if target_key == source_key:
            raise ValueError("Artifact adoption did not change its manifest key")

    converted: dict[str, ArtifactAdoption] = {}
    writes: dict[str, ControlWrite] = {}
    copies: dict[tuple[str, str], CopyIntent] = {}
    active: set[str] = set()

    def convert(artifact_id: str) -> ArtifactAdoption:
        existing = converted.get(artifact_id)
        if existing is not None:
            return existing
        if artifact_id in active:
            raise ValueError(f"Artifact parent cycle includes {artifact_id}")
        active.add(artifact_id)
        try:
            artifact = artifacts[artifact_id]
            manifest = parsed[artifact_id]
            target_manifest_key = _target_key(
                source_keys[artifact_id],
                source_prefix,
                target_prefix,
            )
            if manifest.schema_version == LEGACY_MANIFEST_VERSION:
                payload = store.read_bytes(source_keys[artifact_id])
                target_version = LEGACY_MANIFEST_VERSION
            elif manifest.schema_version == ARTIFACT_MANIFEST_VERSION:
                files: list[ManifestFile] = []
                for item in manifest.files:
                    target_blob_key = content_addressed_blob_key(
                        item.blob.checksum_sha256,
                        organization_id=target_organization_id,
                    )
                    identity = _identity(store, item.blob.key)
                    if (
                        identity.size_bytes != item.blob.size_bytes
                        or identity.checksum_sha256
                        != item.blob.checksum_sha256
                    ):
                        raise OSError(
                            f"Artifact blob identity mismatch: {item.blob.key}"
                        )
                    copies[(item.blob.key, target_blob_key)] = CopyIntent(
                        item.blob.key,
                        target_blob_key,
                        item.blob.size_bytes,
                    )
                    files.append(
                        ManifestFile(
                            path=item.path,
                            role=item.role,
                            blob=ManifestBlob(
                                key=target_blob_key,
                                size_bytes=item.blob.size_bytes,
                                checksum_sha256=item.blob.checksum_sha256,
                            ),
                        )
                    )
                parents: list[ManifestParent] = []
                for parent in manifest.parents:
                    if parent.artifact_id not in artifacts:
                        raise ValueError(
                            f"Artifact {artifact_id} has a parent outside its mission"
                        )
                    adopted_parent = convert(parent.artifact_id)
                    if source_keys[parent.artifact_id] != parent.manifest_key:
                        raise ValueError(
                            f"Artifact {artifact_id} parent manifest key is inconsistent"
                        )
                    parents.append(
                        ManifestParent(
                            artifact_id=parent.artifact_id,
                            manifest_key=adopted_parent.target_manifest_key,
                            checksum_sha256=(
                                adopted_parent.target_checksum_sha256
                            ),
                        )
                    )
                payload = canonical_v3_bytes(
                    ArtifactManifest(
                        schema_version=TENANT_ARTIFACT_MANIFEST_VERSION,
                        files=tuple(files),
                        parents=tuple(parents),
                        organization_id=target_organization_id,
                    )
                )
                target_version = TENANT_ARTIFACT_MANIFEST_VERSION
            else:
                raise ValueError(
                    f"Legacy mission artifact {artifact_id} is already tenant-bound"
                )
            write = _control_write(target_manifest_key, payload)
            writes[target_manifest_key] = write
            result = ArtifactAdoption(
                database_id=int(artifact.id),
                artifact_id=artifact_id,
                target_manifest_key=target_manifest_key,
                target_checksum_sha256=write.checksum_sha256,
                target_schema_version=target_version,
            )
            converted[artifact_id] = result
            return result
        finally:
            active.remove(artifact_id)

    for artifact_id in sorted(artifacts):
        convert(artifact_id)
    return (
        tuple(converted[key] for key in sorted(converted)),
        tuple(copies[key] for key in sorted(copies)),
        tuple(writes[key] for key in sorted(writes)),
    )


def _collect_global_cas(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        candidate = value.removeprefix(f"s3://{S3_BUCKET}/")
        if GLOBAL_CAS_PATTERN.fullmatch(candidate):
            found.add(candidate)
    elif isinstance(value, Mapping):
        for item in value.values():
            found.update(_collect_global_cas(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_collect_global_cas(item))
    return found


def _mission_json_values(mission: Mission) -> Iterable[object]:
    yield mission.params
    yield mission.service_states
    yield mission.resume_info
    yield mission.tiling_metadata
    for run in mission.stage_runs:
        yield run.parameters
        yield run.provenance
        yield run.quality_metrics
    for analysis in mission.analysis_runs:
        yield analysis.tiling_metadata
        yield analysis.model_manifest


def _mission_resource(
    mission: Mission,
    *,
    target_organization_id: str,
    store: AdoptionObjectStore,
) -> ResourceAdoption:
    source_prefix = mission_prefix(LEGACY_ORGANIZATION_ID, str(mission.vol_id))
    target_prefix = mission_prefix(target_organization_id, str(mission.vol_id))
    if str(mission.workspace_prefix) != source_prefix:
        raise ValueError(f"Mission {mission.vol_id} has a non-canonical legacy prefix")
    source_objects = _inventory(store, source_prefix)
    artifacts, artifact_copies, writes = _artifact_adoptions(
        mission,
        target_organization_id=target_organization_id,
        source_prefix=source_prefix,
        target_prefix=target_prefix,
        store=store,
    )
    rewritten_keys = {write.target_key for write in writes}
    rewritten_sources = {
        source_keys
        for source_keys in (
            _manifest_key(artifact) for artifact in mission.artifacts
        )
    }
    copies = {
        (item.source_key, item.target_key): item for item in artifact_copies
    }
    for item in source_objects:
        if item.key in rewritten_sources:
            continue
        target_key = _target_key(item.key, source_prefix, target_prefix)
        if target_key in rewritten_keys:
            raise ValueError(f"Mission control-object target collision: {target_key}")
        copies[(item.key, target_key)] = CopyIntent(
            item.key,
            target_key,
            item.size_bytes,
        )
    external_cas = set()
    for value in _mission_json_values(mission):
        external_cas.update(_collect_global_cas(value))
    for receipt in (
        receipt
        for run in mission.stage_runs
        for receipt in run.detection_shard_receipts
    ):
        key = str(receipt.result_key)
        if GLOBAL_CAS_PATTERN.fullmatch(key):
            external_cas.add(key)
    for source_key in sorted(external_cas):
        match = GLOBAL_CAS_PATTERN.fullmatch(source_key)
        assert match is not None
        checksum = match.group(2)
        if match.group(1) != checksum[:2]:
            raise ValueError(f"Global CAS key has an invalid shard: {source_key}")
        identity = _identity(store, source_key)
        if identity.checksum_sha256 != checksum:
            raise OSError(f"Global CAS source identity mismatch: {source_key}")
        target_key = content_addressed_blob_key(
            checksum,
            organization_id=target_organization_id,
        )
        copies[(source_key, target_key)] = CopyIntent(
            source_key,
            target_key,
            identity.size_bytes,
        )
    return ResourceAdoption(
        kind="mission",
        database_id=int(mission.id),
        public_id=str(mission.vol_id),
        source_prefix=source_prefix,
        target_prefix=target_prefix,
        source_objects=source_objects,
        external_objects=tuple(
            _identity(store, source_key)
            for source_key in sorted(
                {
                    intent.source_key
                    for intent in copies.values()
                    if not intent.source_key.startswith(f"{source_prefix}/")
                }
            )
        ),
        copy_intents=tuple(copies[key] for key in sorted(copies)),
        control_writes=writes,
        artifacts=artifacts,
    )


def _dataset_resource(
    dataset: Dataset,
    *,
    session: Session,
    target_organization_id: str,
    store: AdoptionObjectStore,
) -> ResourceAdoption:
    source_prefix = dataset_prefix(LEGACY_ORGANIZATION_ID, str(dataset.name))
    target_prefix = dataset_prefix(target_organization_id, str(dataset.name))
    if str(dataset.prefix) != source_prefix:
        raise ValueError(f"Dataset {dataset.name} has a non-canonical legacy prefix")
    source_objects = _inventory(store, source_prefix)
    for item in source_objects:
        _target_key(item.key, source_prefix, target_prefix)
    write, manifest_files = _dataset_control_write(
        dataset,
        target_organization_id=target_organization_id,
        source_prefix=source_prefix,
        target_prefix=target_prefix,
        store=store,
    )
    _validate_dataset_upload_session(session, dataset, manifest_files)
    expected_source_keys = {
        str(dataset.manifest_s3_key),
        *manifest_files,
    }
    if {item.key for item in source_objects} != expected_source_keys:
        raise ValueError(f"Dataset {dataset.name} S3 inventory is inconsistent")
    copies = tuple(
        CopyIntent(
            item.key,
            _target_key(item.key, source_prefix, target_prefix),
            item.size_bytes,
        )
        for item in source_objects
        if item.key != str(dataset.manifest_s3_key)
    )
    return ResourceAdoption(
        kind="dataset",
        database_id=int(dataset.id),
        public_id=str(dataset.name),
        source_prefix=source_prefix,
        target_prefix=target_prefix,
        source_objects=source_objects,
        external_objects=(),
        copy_intents=copies,
        control_writes=(write,),
    )


def _plan_checksum(
    target_organization_id: str,
    owner_subject: str,
    actor_subject: str,
    resources: tuple[ResourceAdoption, ...],
) -> str:
    payload = {
        "target_organization_id": target_organization_id,
        "owner_subject": owner_subject,
        "actor_subject": actor_subject,
        "resources": [
            {
                "kind": item.kind,
                "database_id": item.database_id,
                "public_id": item.public_id,
                "source_prefix": item.source_prefix,
                "target_prefix": item.target_prefix,
                "source_objects": [asdict(value) for value in item.source_objects],
                "external_objects": [
                    asdict(value) for value in item.external_objects
                ],
                "copy_intents": [asdict(value) for value in item.copy_intents],
                "control_writes": [
                    {
                        "target_key": value.target_key,
                        "checksum_sha256": value.checksum_sha256,
                        "size_bytes": len(value.payload),
                    }
                    for value in item.control_writes
                ],
                "artifacts": [asdict(value) for value in item.artifacts],
            }
            for item in resources
        ],
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def _validate_target(
    session: Session,
    target_organization_id: str,
    owner_subject: str,
) -> None:
    organization = session.get(Organization, target_organization_id)
    if organization is None or str(organization.status) != "active":
        raise ValueError("Target organization must exist and be active")
    member = (
        session.query(OrganizationMember)
        .filter(
            OrganizationMember.organization_id == target_organization_id,
            OrganizationMember.subject == owner_subject,
            OrganizationMember.status == "active",
        )
        .one_or_none()
    )
    if member is None:
        raise ValueError("Target owner must be an active organization member")


def _selected_resources(
    session: Session,
    *,
    mission_ids: tuple[str, ...],
    dataset_names: tuple[str, ...],
    all_legacy: bool,
) -> tuple[list[Mission], list[Dataset]]:
    mission_query = session.query(Mission).filter(
        Mission.organization_id == LEGACY_ORGANIZATION_ID
    )
    dataset_query = session.query(Dataset).filter(
        Dataset.organization_id == LEGACY_ORGANIZATION_ID,
        Dataset.status != "deleted",
    )
    if all_legacy:
        missions = list(mission_query.order_by(Mission.vol_id).all())
        datasets = list(dataset_query.order_by(Dataset.name).all())
    else:
        missions = (
            list(mission_query.filter(Mission.vol_id.in_(mission_ids)).all())
            if mission_ids
            else []
        )
        datasets = (
            list(dataset_query.filter(Dataset.name.in_(dataset_names)).all())
            if dataset_names
            else []
        )
        missing_missions = set(mission_ids) - {
            str(item.vol_id) for item in missions
        }
        missing_datasets = set(dataset_names) - {
            str(item.name) for item in datasets
        }
        if missing_missions or missing_datasets:
            raise ValueError(
                "Unknown or non-legacy resources: "
                f"missions={sorted(missing_missions)}, "
                f"datasets={sorted(missing_datasets)}"
            )
    if not missions and not datasets:
        raise ValueError("Adoption selection is empty")
    return missions, datasets


def _validate_resources(
    session: Session,
    missions: list[Mission],
    datasets: list[Dataset],
    target_organization_id: str,
) -> None:
    selected_mission_ids = {int(item.id) for item in missions}
    selected_dataset_ids = {int(item.id) for item in datasets}
    for mission in missions:
        if str(mission.status) not in TERMINAL_MISSION_STATUSES:
            raise ValueError(f"Mission {mission.vol_id} is not terminal")
        if any(
            str(run.status) not in TERMINAL_STAGE_STATUSES
            for run in mission.stage_runs
        ):
            raise ValueError(f"Mission {mission.vol_id} has an active stage run")
        if any(
            str(run.status) not in TERMINAL_ANALYSIS_STATUSES
            for run in mission.analysis_runs
        ):
            raise ValueError(f"Mission {mission.vol_id} has an active analysis")
        if mission.dataset_id is None and str(mission.input_dataset or "").startswith(
            "datasets/"
        ):
            raise ValueError(
                f"Mission {mission.vol_id} references an unmanaged legacy dataset"
            )
        if mission.dataset_id is not None:
            dataset = session.get(Dataset, int(mission.dataset_id))
            if dataset is None or (
                int(dataset.id) not in selected_dataset_ids
                and str(dataset.organization_id) != target_organization_id
            ):
                raise ValueError(
                    f"Mission {mission.vol_id} dataset must be adopted in the same run"
                )
    for dataset in datasets:
        if str(dataset.status) != "ready":
            raise ValueError(f"Dataset {dataset.name} is not ready")
        references = session.query(Mission).filter(
            Mission.dataset_id == dataset.id
        ).all()
        unselected = [
            str(item.vol_id)
            for item in references
            if int(item.id) not in selected_mission_ids
            and str(item.organization_id) != target_organization_id
        ]
        if unselected:
            raise ValueError(
                f"Dataset {dataset.name} has unselected missions: {sorted(unselected)}"
            )
        target_conflict = session.query(Dataset).filter(
            Dataset.organization_id == target_organization_id,
            Dataset.name == dataset.name,
            Dataset.status != "deleted",
        ).one_or_none()
        if target_conflict is not None:
            raise ValueError(
                f"Target organization already has dataset {dataset.name}"
            )
    active_outbox = session.query(OutboxEvent).filter(
        OutboxEvent.organization_id == LEGACY_ORGANIZATION_ID,
        OutboxEvent.status.in_(("pending", "publishing", "failed")),
    )
    selected_vol_ids = {str(item.vol_id) for item in missions}
    for event in active_outbox:
        if str(cast(dict[str, object], event.payload).get("vol_id") or "") in selected_vol_ids:
            raise ValueError("Selected mission still has an active outbox event")
    active_inbox = session.query(InboxEvent).filter(
        InboxEvent.status.in_(("processing", "failed"))
    )
    for event in active_inbox:
        payload = cast(dict[str, object], event.payload)
        if str(payload.get("vol_id") or "") in selected_vol_ids:
            raise ValueError("Selected mission still has an active inbox event")


def build_adoption_plan(
    session: Session,
    *,
    target_organization_id: str,
    owner_subject: str,
    actor_subject: str,
    store: AdoptionObjectStore,
    mission_ids: Iterable[str] = (),
    dataset_names: Iterable[str] = (),
    all_legacy: bool = False,
    run_id: str | None = None,
) -> AdoptionPlan:
    """Inventory and validate one deterministic, read-only adoption plan."""

    organization_id = validate_organization_id(target_organization_id)
    if organization_id in {LEGACY_ORGANIZATION_ID, LOCAL_ORGANIZATION_ID}:
        raise ValueError("Adoption target must be an explicit customer organization")
    owner = _validated_subject(owner_subject, "owner_subject")
    actor = _validated_subject(actor_subject, "actor_subject")
    selected_missions = tuple(sorted(set(mission_ids)))
    selected_datasets = tuple(sorted(set(dataset_names)))
    if all_legacy and (selected_missions or selected_datasets):
        raise ValueError("all_legacy cannot be combined with explicit resources")
    if not all_legacy and not (selected_missions or selected_datasets):
        raise ValueError("Select resources or use all_legacy")
    _validate_target(session, organization_id, owner)
    missions, datasets = _selected_resources(
        session,
        mission_ids=selected_missions,
        dataset_names=selected_datasets,
        all_legacy=all_legacy,
    )
    _validate_resources(session, missions, datasets, organization_id)
    resources = tuple(
        [
            _dataset_resource(
                dataset,
                session=session,
                target_organization_id=organization_id,
                store=store,
            )
            for dataset in sorted(datasets, key=lambda value: str(value.name))
        ]
        + [
            _mission_resource(
                mission,
                target_organization_id=organization_id,
                store=store,
            )
            for mission in sorted(missions, key=lambda value: str(value.vol_id))
        ]
    )
    logical_usage_bytes = sum(int(dataset.total_bytes) for dataset in datasets) + sum(
        int(artifact.size_bytes or 0)
        for mission in missions
        for artifact in mission.artifacts
    )
    check_storage_reservation(
        session,
        organization_id=organization_id,
        requested_bytes=logical_usage_bytes,
    )
    return AdoptionPlan(
        run_id=_validated_run_id(run_id),
        target_organization_id=organization_id,
        owner_subject=owner,
        actor_subject=actor,
        resources=resources,
        plan_checksum_sha256=_plan_checksum(
            organization_id,
            owner,
            actor,
            resources,
        ),
        logical_usage_bytes=logical_usage_bytes,
    )
