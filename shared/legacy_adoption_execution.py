"""Transactional execution of one reviewed legacy-adoption plan."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.config import S3_BUCKET
from shared.database import (
    Dataset,
    DatasetUploadSession,
    GcpObservation,
    GcpPoint,
    Mission,
    OrganizationUsageEvent,
    RasterLayerStyle,
    get_session,
)
from shared.legacy_adoption import (
    GLOBAL_CAS_PATTERN,
    _identity,
    _inventory,
    _validate_target,
)
from shared.legacy_adoption_types import (
    AdoptionObjectStore,
    AdoptionPlan,
    ResourceAdoption,
    SessionFactory,
)
from shared.organization_saas import append_usage_event, check_storage_reservation
from shared.tenancy import LEGACY_ORGANIZATION_ID

logger = logging.getLogger(__name__)

LEGACY_GCP_BUNDLE_FIELDS = {
    "schema_version",
    "set_id",
    "source_sha256",
    "gcp_list",
    "accuracy_csv",
    "quality",
}


def _rewrite_string(
    value: str,
    prefix_mapping: Mapping[str, str],
    exact_mapping: Mapping[str, str],
) -> str:
    if value in exact_mapping:
        return exact_mapping[value]
    object_marker = f"s3://{S3_BUCKET}/"
    if value.startswith(object_marker):
        object_key = value.removeprefix(object_marker)
        if object_key in exact_mapping:
            return f"{object_marker}{exact_mapping[object_key]}"
    for source, target in sorted(
        prefix_mapping.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if value == source or value.startswith(f"{source}/"):
            return f"{target}{value[len(source):]}"
        marker = f"s3://{S3_BUCKET}/{source}"
        if value == marker or value.startswith(f"{marker}/"):
            target_marker = f"s3://{S3_BUCKET}/{target}"
            return f"{target_marker}{value[len(marker):]}"
    return value


def _rewrite_value(
    value: object,
    prefix_mapping: Mapping[str, str],
    exact_mapping: Mapping[str, str],
    target_organization_id: str | None = None,
) -> object:
    if isinstance(value, str):
        return _rewrite_string(value, prefix_mapping, exact_mapping)
    if isinstance(value, dict):
        rewritten = {
            key: _rewrite_value(
                item,
                prefix_mapping,
                exact_mapping,
                target_organization_id,
            )
            for key, item in value.items()
        }
        if (
            target_organization_id is not None
            and set(value) == LEGACY_GCP_BUNDLE_FIELDS
            and value.get("schema_version") == 1
        ):
            rewritten["schema_version"] = 2
            rewritten["organization_id"] = target_organization_id
        return rewritten
    if isinstance(value, list):
        return [
            _rewrite_value(
                item,
                prefix_mapping,
                exact_mapping,
                target_organization_id,
            )
            for item in value
        ]
    return value


def _append_run_event(
    session: Session,
    plan: AdoptionPlan,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    idempotency_key: str | None,
    details: dict[str, object],
    quantity: int | None = None,
) -> OrganizationUsageEvent:
    return append_usage_event(
        session,
        organization_id=plan.target_organization_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_subject=plan.actor_subject,
        quantity=quantity,
        unit="bytes" if quantity is not None else None,
        idempotency_key=idempotency_key,
        details=details,
    )


def _record_started(plan: AdoptionPlan, session_factory: SessionFactory) -> None:
    key = f"legacy-adoption:{plan.run_id}:started"
    try:
        with session_factory() as session:
            existing = session.query(OrganizationUsageEvent).filter(
                OrganizationUsageEvent.idempotency_key == key
            ).one_or_none()
            if existing is not None:
                _validate_run_event(existing, plan, "started")
                return
            _append_run_event(
                session,
                plan,
                action="legacy_adoption_started",
                resource_type="legacy_adoption_run",
                resource_id=plan.run_id,
                idempotency_key=key,
                quantity=plan.source_bytes,
                details={
                    "plan_checksum_sha256": plan.plan_checksum_sha256,
                    "source_object_count": plan.source_object_count,
                    "resource_count": len(plan.resources),
                    "source_retained": True,
                },
            )
    except IntegrityError:
        with session_factory() as session:
            winner = session.query(OrganizationUsageEvent).filter(
                OrganizationUsageEvent.idempotency_key == key
            ).one_or_none()
            if winner is None:
                raise
            _validate_run_event(winner, plan, "started")


def _validate_run_event(
    event: OrganizationUsageEvent,
    plan: AdoptionPlan,
    phase: str,
) -> None:
    details = cast(dict[str, object], event.details)
    if details.get("plan_checksum_sha256") != plan.plan_checksum_sha256:
        raise ValueError(f"run_id {phase} belongs to a different adoption plan")


def _already_completed(
    plan: AdoptionPlan,
    session_factory: SessionFactory,
) -> bool:
    key = f"legacy-adoption:{plan.run_id}:completed"
    with session_factory() as session:
        event = session.query(OrganizationUsageEvent).filter(
            OrganizationUsageEvent.idempotency_key == key
        ).one_or_none()
        if event is None:
            return False
        _validate_run_event(event, plan, "completion")
        return True


def _copy_plan(plan: AdoptionPlan, store: AdoptionObjectStore) -> None:
    for resource in plan.resources:
        for intent in resource.copy_intents:
            result = store.copy(intent.source_key, intent.target_key)
            if int(cast(int, result["size"])) != intent.size_bytes:
                raise OSError(f"Adoption copy size mismatch: {intent.target_key}")
        for write in resource.control_writes:
            result = store.put_bytes(write.target_key, write.payload)
            if (
                int(cast(int, result["size"])) != len(write.payload)
                or str(result["sha256"]) != write.checksum_sha256
            ):
                raise OSError(f"Adoption control-object mismatch: {write.target_key}")


def _revalidate_sources(
    plan: AdoptionPlan,
    store: AdoptionObjectStore,
) -> None:
    for resource in plan.resources:
        if _inventory(store, resource.source_prefix) != resource.source_objects:
            raise RuntimeError(
                f"Legacy source changed during adoption: {resource.source_prefix}"
            )
        if tuple(
            _identity(store, item.key) for item in resource.external_objects
        ) != resource.external_objects:
            raise RuntimeError(
                f"Legacy external objects changed during adoption: "
                f"{resource.public_id}"
            )


def _commit_dataset(
    session: Session,
    plan: AdoptionPlan,
    resource: ResourceAdoption,
    prefix_mapping: Mapping[str, str],
) -> None:
    dataset = (
        session.query(Dataset)
        .filter(Dataset.id == resource.database_id)
        .with_for_update()
        .one()
    )
    if (
        str(dataset.organization_id) != LEGACY_ORGANIZATION_ID
        or str(dataset.prefix) != resource.source_prefix
    ):
        raise RuntimeError(f"Dataset changed before commit: {resource.public_id}")
    dataset.organization_id = plan.target_organization_id
    dataset.owner_subject = plan.owner_subject
    dataset.prefix = resource.target_prefix
    dataset.manifest_s3_key = f"{resource.target_prefix}/dataset-manifest.json"
    if dataset.upload_session_id is not None:
        upload = (
            session.query(DatasetUploadSession)
            .filter(DatasetUploadSession.id == dataset.upload_session_id)
            .with_for_update()
            .one()
        )
        upload.organization_id = plan.target_organization_id
        for item in upload.files:
            item.s3_key = _rewrite_string(
                str(item.s3_key),
                prefix_mapping,
                {},
            )


def _commit_mission(
    session: Session,
    plan: AdoptionPlan,
    resource: ResourceAdoption,
    prefix_mapping: Mapping[str, str],
    exact_mapping: Mapping[str, str],
) -> None:
    mission = (
        session.query(Mission)
        .filter(Mission.id == resource.database_id)
        .with_for_update()
        .one()
    )
    if (
        str(mission.organization_id) != LEGACY_ORGANIZATION_ID
        or str(mission.workspace_prefix) != resource.source_prefix
    ):
        raise RuntimeError(f"Mission changed before commit: {resource.public_id}")
    mission.organization_id = plan.target_organization_id
    mission.owner_subject = plan.owner_subject
    mission.workspace_prefix = resource.target_prefix
    _rewrite_mission_fields(
        mission,
        prefix_mapping,
        exact_mapping,
        plan.target_organization_id,
    )
    _rewrite_stage_records(
        mission,
        prefix_mapping,
        exact_mapping,
        plan.target_organization_id,
    )
    _rewrite_artifact_records(
        mission,
        resource,
        prefix_mapping,
        exact_mapping,
        plan.target_organization_id,
    )
    _rewrite_analysis_records(
        mission,
        prefix_mapping,
        exact_mapping,
        plan.target_organization_id,
    )
    _rewrite_gcp_and_style_records(
        session,
        mission,
        prefix_mapping,
        exact_mapping,
    )


def _rewrite_mission_fields(
    mission: Mission,
    prefix_mapping: Mapping[str, str],
    exact_mapping: Mapping[str, str],
    target_organization_id: str,
) -> None:
    for field_name in ("input_dataset", "ortho_s3_key"):
        value = getattr(mission, field_name)
        if value is not None:
            setattr(
                mission,
                field_name,
                _rewrite_string(str(value), prefix_mapping, exact_mapping),
            )
    for field_name in ("params", "service_states", "resume_info", "tiling_metadata"):
        setattr(
            mission,
            field_name,
            _rewrite_value(
                getattr(mission, field_name),
                prefix_mapping,
                exact_mapping,
                target_organization_id,
            ),
        )


def _rewrite_stage_records(
    mission: Mission,
    prefix_mapping: Mapping[str, str],
    exact_mapping: Mapping[str, str],
    target_organization_id: str,
) -> None:
    for run in mission.stage_runs:
        for field_name in ("parameters", "provenance", "quality_metrics"):
            setattr(
                run,
                field_name,
                _rewrite_value(
                    getattr(run, field_name),
                    prefix_mapping,
                    exact_mapping,
                    target_organization_id,
                ),
            )
        for receipt in run.detection_shard_receipts:
            receipt.result_key = _rewrite_string(
                str(receipt.result_key),
                prefix_mapping,
                exact_mapping,
            )


def _rewrite_artifact_records(
    mission: Mission,
    resource: ResourceAdoption,
    prefix_mapping: Mapping[str, str],
    exact_mapping: Mapping[str, str],
    target_organization_id: str,
) -> None:
    artifact_plans = {item.database_id: item for item in resource.artifacts}
    for artifact in mission.artifacts:
        adopted = artifact_plans[int(artifact.id)]
        artifact.uri = f"s3://{S3_BUCKET}/{adopted.target_manifest_key}"
        artifact.checksum_sha256 = adopted.target_checksum_sha256
        metadata = cast(dict[str, object], artifact.artifact_metadata or {})
        rewritten = cast(
            dict[str, object],
            _rewrite_value(
                metadata,
                prefix_mapping,
                exact_mapping,
                target_organization_id,
            ),
        )
        rewritten["manifest_key"] = adopted.target_manifest_key
        rewritten["manifest_schema_version"] = adopted.target_schema_version
        artifact.artifact_metadata = rewritten


def _rewrite_analysis_records(
    mission: Mission,
    prefix_mapping: Mapping[str, str],
    exact_mapping: Mapping[str, str],
    target_organization_id: str,
) -> None:
    for analysis in mission.analysis_runs:
        for field_name in ("ortho_s3_key", "result_s3_key"):
            value = getattr(analysis, field_name)
            if value is not None:
                setattr(
                    analysis,
                    field_name,
                    _rewrite_string(str(value), prefix_mapping, exact_mapping),
                )
        for field_name in ("tiling_metadata", "model_manifest"):
            setattr(
                analysis,
                field_name,
                _rewrite_value(
                    getattr(analysis, field_name),
                    prefix_mapping,
                    exact_mapping,
                    target_organization_id,
                ),
            )
        for tile in analysis.tiles:
            for field_name in ("tile_s3_key", "result_s3_key"):
                value = getattr(tile, field_name)
                if value is not None:
                    setattr(
                        tile,
                        field_name,
                        _rewrite_string(
                            str(value), prefix_mapping, exact_mapping
                        ),
                    )


def _rewrite_gcp_and_style_records(
    session: Session,
    mission: Mission,
    prefix_mapping: Mapping[str, str],
    exact_mapping: Mapping[str, str],
) -> None:
    observations = (
        session.query(GcpObservation)
        .join(GcpPoint, GcpPoint.id == GcpObservation.gcp_point_id)
        .filter(GcpPoint.mission_id == mission.id)
    )
    for observation in observations:
        if observation.image_s3_key is not None:
            observation.image_s3_key = _rewrite_string(
                str(observation.image_s3_key),
                prefix_mapping,
                exact_mapping,
            )
    for style in session.query(RasterLayerStyle).filter(
        RasterLayerStyle.mission_id == mission.id
    ):
        style.layer_key = _rewrite_string(
            str(style.layer_key),
            prefix_mapping,
            exact_mapping,
        )


def _commit_plan(
    plan: AdoptionPlan,
    store: AdoptionObjectStore,
    session_factory: SessionFactory,
) -> None:
    _revalidate_sources(plan, store)
    prefix_mapping = {
        resource.source_prefix: resource.target_prefix
        for resource in plan.resources
    }
    exact_mapping = {
        intent.source_key: intent.target_key
        for resource in plan.resources
        for intent in resource.copy_intents
        if GLOBAL_CAS_PATTERN.fullmatch(intent.source_key)
    }
    with session_factory() as session:
        for resource in sorted(
            plan.resources,
            key=lambda item: (item.kind, item.database_id),
        ):
            model = Dataset if resource.kind == "dataset" else Mission
            (
                session.query(model.id)
                .filter(model.id == resource.database_id)
                .with_for_update()
                .one()
            )
        completed = session.query(OrganizationUsageEvent).filter(
            OrganizationUsageEvent.idempotency_key
            == f"legacy-adoption:{plan.run_id}:completed"
        ).one_or_none()
        if completed is not None:
            _validate_run_event(completed, plan, "completion")
            return
        _validate_target(
            session,
            plan.target_organization_id,
            plan.owner_subject,
        )
        check_storage_reservation(
            session,
            organization_id=plan.target_organization_id,
            requested_bytes=plan.logical_usage_bytes,
        )
        for resource in plan.resources:
            if resource.kind == "dataset":
                _commit_dataset(session, plan, resource, prefix_mapping)
            else:
                _commit_mission(
                    session,
                    plan,
                    resource,
                    prefix_mapping,
                    exact_mapping,
                )
            _append_run_event(
                session,
                plan,
                action="legacy_adoption_resource",
                resource_type=resource.kind,
                resource_id=resource.public_id,
                idempotency_key=(
                    f"legacy-adoption:{plan.run_id}:{resource.kind}:"
                    f"{resource.database_id}"
                ),
                quantity=resource.source_bytes,
                details={
                    "run_id": plan.run_id,
                    "source_prefix": resource.source_prefix,
                    "target_prefix": resource.target_prefix,
                    "object_count": resource.source_object_count,
                    "source_retained": True,
                },
            )
        _append_run_event(
            session,
            plan,
            action="legacy_adoption_completed",
            resource_type="legacy_adoption_run",
            resource_id=plan.run_id,
            idempotency_key=f"legacy-adoption:{plan.run_id}:completed",
            quantity=plan.source_bytes,
            details={
                "plan_checksum_sha256": plan.plan_checksum_sha256,
                "resource_count": len(plan.resources),
                "source_object_count": plan.source_object_count,
                "source_retained": True,
            },
        )


def _record_failure(
    plan: AdoptionPlan,
    error: BaseException,
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        _append_run_event(
            session,
            plan,
            action="legacy_adoption_failed",
            resource_type="legacy_adoption_run",
            resource_id=plan.run_id,
            idempotency_key=None,
            details={
                "plan_checksum_sha256": plan.plan_checksum_sha256,
                "error_type": type(error).__name__,
                "error": str(error)[:4000],
                "source_retained": True,
            },
        )


def apply_adoption_plan(
    plan: AdoptionPlan,
    *,
    store: AdoptionObjectStore,
    session_factory: SessionFactory = get_session,
) -> None:
    """Copy, verify and transactionally commit one resumable adoption plan."""

    if _already_completed(plan, session_factory):
        return
    _record_started(plan, session_factory)
    try:
        _copy_plan(plan, store)
        _commit_plan(plan, store, session_factory)
    except BaseException as error:
        try:
            _record_failure(plan, error, session_factory)
        except Exception:
            logger.exception(
                "Could not persist failure evidence for legacy-adoption run %s",
                plan.run_id,
            )
        raise
