import importlib
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import (
    Mission,
    MissionArtifact,
    MissionArtifactParent,
    MissionStageRun,
    OutboxEvent,
)
from shared.phase_dag import (
    build_stage_run_specs,
    initialize_stage_runs,
    project_status_to_stage_run,
    stage_idempotency_key,
)
from shared.stage_contracts import (
    RESOURCE_CLASSES,
    STAGE_ORDER,
    resource_class_for_stage,
    resource_class_node_selector,
    stage_dag_catalog,
    validate_stage_selection,
)

stage_routes = importlib.import_module("app4-dashboard.api.routers.mission_stages")
stage_schemas = importlib.import_module("app4-dashboard.api.stage_schemas")
PRINCIPAL = SimpleNamespace(subject="dag-operator", role="operator")


def _gcp_bundle():
    return {
        "schema_version": 1,
        "set_id": "00000000-0000-0000-0000-000000000001",
        "source_sha256": "a" * 64,
        "gcp_list": {
            "key": f"blobs/sha256/{'b' * 2}/{'b' * 64}",
            "size": 100,
            "sha256": "b" * 64,
        },
        "accuracy_csv": {
            "key": f"blobs/sha256/{'c' * 2}/{'c' * 64}",
            "size": 80,
            "sha256": "c" * 64,
        },
        "quality": {
            "adjustment_points": 3,
            "checkpoint_points": 0,
            "marked_observations": 6,
            "verification": "adjustment-only-unverified",
        },
    }


def test_gcp_bundle_is_scoped_to_reconstruction_stage_parameters():
    request = stage_schemas.StageRunCreate(parameters={"gcp_bundle": _gcp_bundle()})

    parameters = stage_routes._stage_parameters("reconstruction", request)

    assert parameters["gcp_bundle"]["set_id"] == "00000000-0000-0000-0000-000000000001"
    with pytest.raises(HTTPException) as error:
        stage_routes._stage_parameters("detection", request)
    assert error.value.status_code == 422


def _reconstruction_artifact_request(
    vol_id: str,
    run_id: str,
    artifact_id: str,
    *,
    checksum: str = "b" * 64,
    parent_artifact_ids: list[str] | None = None,
):
    key = (
        f"missions/{vol_id}/stage-runs/{run_id}/"
        "reconstruction-workspace/manifest.json"
    )
    return stage_schemas.ArtifactCreate(
        artifact_id=artifact_id,
        kind="reconstruction_workspace",
        uri=f"s3://{stage_routes.S3_BUCKET}/{key}",
        checksum_sha256=checksum,
        metadata={"manifest_key": key},
        parent_artifact_ids=parent_artifact_ids or [],
    )


@pytest.fixture
def dag_sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    MissionStageRun.__table__.create(engine)
    MissionArtifact.__table__.create(engine)
    MissionArtifactParent.__table__.create(engine)
    OutboxEvent.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return session_scope


def test_default_stage_plan_is_versioned_and_dependency_ordered():
    specs = build_stage_run_specs({"vol_id": "mission-dag"})

    assert [spec["stage"] for spec in specs] == list(STAGE_ORDER)
    assert specs[0]["status"] == "queued"
    assert all(spec["status"] == "blocked" for spec in specs[1:])
    assert len({spec["idempotency_key"] for spec in specs}) == len(STAGE_ORDER)
    assert all(spec["parameters"]["work_drive"] is None for spec in specs)
    assert [spec["resource_class"] for spec in specs] == [
        "gpu-geometry",
        "gpu-high-memory",
        "gpu-high-memory",
        "gpu-standard",
        "gpu-standard",
    ]


def test_detection_stage_persists_sam_prompt_and_tile_size():
    specs = build_stage_run_specs(
        {
            "vol_id": "mission-sam",
            "ai_backend": "sam3",
            "sam_prompt": "construction vehicle",
            "tile_size": 1024,
        }
    )

    detection = next(spec for spec in specs if spec["stage"] == "detection")
    assert detection["parameters"]["ai"]["sam_prompt"] == "construction vehicle"
    assert detection["parameters"]["ai"]["tile_size"] == 1024


def test_stage_plan_and_manual_retry_preserve_the_selected_work_drive():
    specs = build_stage_run_specs(
        {"vol_id": "mission-drive", "work_drive": "drive-j"}
    )
    assert all(
        spec["parameters"]["work_drive"] == "drive-j" for spec in specs
    )

    request = stage_schemas.StageRunCreate(parameters={})
    parameters = stage_routes._stage_parameters(
        "rasterization",
        request,
        {"work_drive": "drive-j"},
    )
    assert parameters["work_drive"] == "drive-j"


def test_stage_resource_catalog_is_explicit_and_prevents_gpu_downgrades():
    catalog = stage_dag_catalog()

    assert catalog["resource_classes"] == RESOURCE_CLASSES
    assert [stage["artifact_kind"] for stage in catalog["stages"]] == [
        "reconstruction_workspace",
        "gaussian_training_workspace",
        "gaussian_filtering_workspace",
        "raster_product_workspace",
        "detection_workspace",
    ]
    assert resource_class_for_stage(
        "detection",
        {"ai": {"backend": "sam3"}},
    ) == "gpu-geometry"
    with pytest.raises(ValueError, match="below the gpu-geometry"):
        resource_class_for_stage(
            "detection",
            {
                "ai": {"backend": "sam3"},
                "resource_class": "gpu-standard",
            },
        )
    assert resource_class_for_stage(
        "detection",
        {
            "ai": {"backend": "sam3"},
            "resource_class": "gpu-high-memory",
        },
    ) == "gpu-high-memory"
    assert resource_class_for_stage(
        "detection",
        {"resource_class": "gpu-geometry"},
    ) == "gpu-geometry"
    with pytest.raises(ValueError, match="below the gpu-high-memory"):
        resource_class_for_stage(
            "gaussian_training",
            {"resource_class": "gpu-standard"},
        )
    assert resource_class_for_stage(
        "rasterization",
        {
            "quality_profile": "normal-v1",
            "colmap_params": {"gs_cap_max": "3000000"},
        },
    ) == "gpu-standard"
    assert resource_class_for_stage(
        "rasterization",
        {
            "quality_profile": "high-quality-v2",
            "colmap_params": {"gs_cap_max": "12000000"},
        },
    ) == "gpu-high-memory"
    assert resource_class_for_stage(
        "rasterization",
        {
            "quality_profile": "custom",
            "colmap_params": {"gs_cap_max": "3000001"},
        },
    ) == "gpu-high-memory"
    with pytest.raises(ValueError, match="below the gpu-high-memory"):
        resource_class_for_stage(
            "rasterization",
            {
                "quality_profile": "high-quality-v2",
                "resource_class": "gpu-standard",
            },
        )
    assert resource_class_node_selector("cpu-standard") == {}
    assert resource_class_node_selector("gpu-standard") == {
        "nvidia.com/gpu.present": "true",
        "droneai.io/gpu-vram-at-least-8gb": "true",
    }
    assert resource_class_node_selector("gpu-geometry") == {
        "nvidia.com/gpu.present": "true",
        "droneai.io/gpu-vram-at-least-12gb": "true",
    }
    assert resource_class_node_selector("gpu-high-memory") == {
        "nvidia.com/gpu.present": "true",
        "droneai.io/gpu-vram-at-least-24gb": "true",
    }


def test_partial_stage_requires_an_exact_upstream_artifact():
    with pytest.raises(ValueError, match="rasterization"):
        validate_stage_selection(["detection"], {})

    artifact_id = str(uuid4())
    assert validate_stage_selection(
        ["detection"],
        {"rasterization": artifact_id},
    ) == ["detection"]

    with pytest.raises(ValueError, match="duplicates"):
        validate_stage_selection(["detection", "detection"], {})


def test_stage_idempotency_is_canonical():
    first = stage_idempotency_key(
        "mission-dag",
        "detection",
        2,
        {"threshold": 0.4, "classes": ["car"]},
        ["b", "a"],
    )
    second = stage_idempotency_key(
        "mission-dag",
        "detection",
        2,
        {"classes": ["car"], "threshold": 0.4},
        ["a", "b"],
    )

    assert first == second
    assert len(first) == 64


def test_status_events_project_onto_the_matching_stage_run(dag_sessions):
    with dag_sessions() as session:
        mission = Mission(vol_id="mission-dag", owner_subject="dag-operator")
        session.add(mission)
        session.flush()
        initialize_stage_runs(session, mission, {"vol_id": mission.vol_id})
        project_status_to_stage_run(
            session,
            mission,
            service="COLMAP",
            step="GAUSS",
            event_status="processing",
            progress=42,
            error_message=None,
        )

    with dag_sessions() as session:
        training = session.query(MissionStageRun).filter(
            MissionStageRun.stage == "gaussian_training"
        ).one()
        reconstruction = session.query(MissionStageRun).filter(
            MissionStageRun.stage == "reconstruction"
        ).one()
        assert training.status == "running"
        assert training.progress == 42
        assert training.current_step == "GAUSS"
        assert training.heartbeat_at is not None
        assert reconstruction.status == "succeeded"
        assert reconstruction.progress == 100


def test_terminal_worker_error_targets_the_active_stage(dag_sessions):
    with dag_sessions() as session:
        mission = Mission(vol_id="mission-error", owner_subject="dag-operator")
        session.add(mission)
        session.flush()
        initialize_stage_runs(session, mission, {"vol_id": mission.vol_id})
        project_status_to_stage_run(
            session,
            mission,
            service="COLMAP",
            step="GAUSS",
            event_status="processing",
            progress=30,
            error_message=None,
        )
        project_status_to_stage_run(
            session,
            mission,
            service="COLMAP",
            step="ERROR",
            event_status="error",
            progress=0,
            error_message="trainer failed",
        )

    with dag_sessions() as session:
        reconstruction = session.query(MissionStageRun).filter(
            MissionStageRun.stage == "reconstruction"
        ).one()
        training = session.query(MissionStageRun).filter(
            MissionStageRun.stage == "gaussian_training"
        ).one()
        assert reconstruction.status == "succeeded"
        assert training.status == "failed"
        assert training.error_message == "trainer failed"


def test_identified_status_event_never_mutates_a_newer_attempt(dag_sessions):
    with dag_sessions() as session:
        mission = Mission(vol_id="mission-attempts", owner_subject="dag-operator")
        session.add(mission)
        session.flush()
        older = MissionStageRun(
            mission_id=mission.id,
            stage="detection",
            attempt=0,
            status="running",
            idempotency_key="4" * 64,
        )
        newer = MissionStageRun(
            mission_id=mission.id,
            stage="detection",
            attempt=1,
            status="running",
            idempotency_key="5" * 64,
        )
        session.add_all([older, newer])
        session.flush()
        older_id = older.run_id
        project_status_to_stage_run(
            session,
            mission,
            service="IA",
            step="DONE",
            event_status="success",
            progress=100,
            error_message=None,
            stage_run_id=older_id,
        )

    with dag_sessions() as session:
        attempts = session.query(MissionStageRun).order_by(
            MissionStageRun.attempt
        ).all()
        assert attempts[0].status == "succeeded"
        assert attempts[1].status == "running"


def test_stage_retry_is_idempotent_and_uses_exact_mission_artifacts(
    dag_sessions,
    monkeypatch,
):
    raster_id = str(uuid4())
    with dag_sessions() as session:
        mission = Mission(
            vol_id="mission-dag",
            owner_subject="dag-operator",
            params={"vol_id": "mission-dag"},
        )
        session.add(mission)
        session.flush()
        source_run = MissionStageRun(
            mission_id=mission.id,
            stage="rasterization",
            attempt=0,
            status="succeeded",
            idempotency_key="1" * 64,
        )
        session.add(source_run)
        session.flush()
        session.add(
            MissionArtifact(
                artifact_id=raster_id,
                mission_id=mission.id,
                stage_run_id=source_run.id,
                kind="orthomosaic",
                uri="s3://droneai/mission-dag/orthomosaic.tif",
                checksum_sha256="a" * 64,
            )
        )
    monkeypatch.setattr(stage_routes, "get_session", dag_sessions)
    request = stage_schemas.StageRunCreate(
        parameters={"confidence": 0.45},
        upstream_artifact_ids={"rasterization": raster_id},
    )

    first = stage_routes.create_stage_run(
        "mission-dag",
        "detection",
        request,
        PRINCIPAL,
        "retry-request-001",
        None,
    )
    second = stage_routes.create_stage_run(
        "mission-dag",
        "detection",
        request,
        PRINCIPAL,
        "retry-request-001",
        None,
    )

    assert first == second
    assert first["attempt"] == 0
    assert first["resource_class"] == "gpu-standard"
    with dag_sessions() as session:
        assert session.query(MissionStageRun).filter(
            MissionStageRun.stage == "detection"
        ).count() == 1
        assert session.query(OutboxEvent).count() == 1

    monkeypatch.setattr(stage_routes, "stage_jobs_enabled", lambda: True)
    stage_routes.create_stage_run(
        "mission-dag",
        "detection",
        request,
        PRINCIPAL,
        "retry-request-job-002",
        None,
    )
    with dag_sessions() as session:
        assert session.query(MissionStageRun).filter(
            MissionStageRun.stage == "detection"
        ).count() == 2
        assert session.query(OutboxEvent).count() == 1

    with pytest.raises(HTTPException) as error:
        stage_routes.create_stage_run(
            "mission-dag",
            "detection",
            request.model_copy(update={"parameters": {"confidence": 0.8}}),
            PRINCIPAL,
            "retry-request-001",
            None,
        )

    assert error.value.status_code == 409


def test_stage_retry_rejects_artifact_from_wrong_dependency_stage(
    dag_sessions,
    monkeypatch,
):
    artifact_id = str(uuid4())
    with dag_sessions() as session:
        mission = Mission(
            vol_id="mission-wrong-dependency",
            owner_subject="dag-operator",
            params={"vol_id": "mission-wrong-dependency"},
        )
        session.add(mission)
        session.flush()
        source_run = MissionStageRun(
            mission_id=mission.id,
            stage="gaussian_filtering",
            attempt=0,
            status="succeeded",
            idempotency_key="3" * 64,
        )
        session.add(source_run)
        session.flush()
        session.add(
            MissionArtifact(
                artifact_id=artifact_id,
                mission_id=mission.id,
                stage_run_id=source_run.id,
                kind="filtered_gaussians",
                uri="s3://droneai/mission-wrong-dependency/filtered.ply",
                checksum_sha256="e" * 64,
            )
        )
    monkeypatch.setattr(stage_routes, "get_session", dag_sessions)

    with pytest.raises(HTTPException) as error:
        stage_routes.create_stage_run(
            "mission-wrong-dependency",
            "detection",
            stage_schemas.StageRunCreate(
                upstream_artifact_ids={"rasterization": artifact_id},
            ),
            PRINCIPAL,
            "wrong-dependency-001",
            None,
        )

    assert error.value.status_code == 422


def test_artifact_publication_queues_the_next_ready_stage(
    dag_sessions,
    monkeypatch,
):
    with dag_sessions() as session:
        mission = Mission(
            vol_id="mission-chain",
            owner_subject="dag-operator",
            params={"vol_id": "mission-chain"},
        )
        session.add(mission)
        session.flush()
        runs = initialize_stage_runs(
            session,
            mission,
            {"vol_id": mission.vol_id},
        )
        runs[0].status = "running"
        reconstruction_run_id = runs[0].run_id
    monkeypatch.setattr(stage_routes, "get_session", dag_sessions)
    monkeypatch.setattr(
        stage_routes.storage,
        "verify_object_checksum",
        lambda *_args, **_kwargs: None,
    )
    artifact_id = str(uuid4())

    response = stage_routes.publish_stage_artifact(
        "mission-chain",
        reconstruction_run_id,
        _reconstruction_artifact_request(
            "mission-chain",
            reconstruction_run_id,
            artifact_id,
        ),
        PRINCIPAL,
        None,
    )

    assert len(response["queued_stage_run_ids"]) == 1
    with dag_sessions() as session:
        reconstruction = session.query(MissionStageRun).filter(
            MissionStageRun.stage == "reconstruction"
        ).one()
        training = session.query(MissionStageRun).filter(
            MissionStageRun.stage == "gaussian_training"
        ).one()
        assert reconstruction.status == "succeeded"
        assert reconstruction.completed_at is not None
        assert training.status == "queued"
        assert training.upstream_artifact_ids == [artifact_id]
        assert session.query(MissionArtifactParent).count() == 0
        assert session.query(OutboxEvent).count() == 1


def test_artifact_identity_rejects_changed_immutable_content(
    dag_sessions,
    monkeypatch,
):
    artifact_id = str(uuid4())
    with dag_sessions() as session:
        mission = Mission(vol_id="mission-immutable", owner_subject="dag-operator")
        session.add(mission)
        session.flush()
        run = MissionStageRun(
            mission_id=mission.id,
            stage="reconstruction",
            attempt=0,
            status="running",
            idempotency_key="2" * 64,
        )
        session.add(run)
        session.flush()
        run_id = run.run_id
    monkeypatch.setattr(stage_routes, "get_session", dag_sessions)
    monkeypatch.setattr(
        stage_routes.storage,
        "verify_object_checksum",
        lambda *_args, **_kwargs: None,
    )
    original = _reconstruction_artifact_request(
        "mission-immutable",
        run_id,
        artifact_id,
        checksum="c" * 64,
    )
    stage_routes.publish_stage_artifact(
        "mission-immutable", run_id, original, PRINCIPAL, None
    )
    replay = stage_routes.publish_stage_artifact(
        "mission-immutable", run_id, original, PRINCIPAL, None
    )
    assert replay["status"] == "existing"

    with pytest.raises(HTTPException) as error:
        stage_routes.publish_stage_artifact(
            "mission-immutable",
            run_id,
            original.model_copy(update={"checksum_sha256": "d" * 64}),
            PRINCIPAL,
            None,
        )

    assert error.value.status_code == 409

    with pytest.raises(HTTPException) as error:
        stage_routes.publish_stage_artifact(
            "mission-immutable",
            run_id,
            original.model_copy(
                update={"uri": "s3://droneai/mission-immutable/changed.tar.zst"}
            ),
            PRINCIPAL,
            None,
        )

    assert error.value.status_code == 422


def test_manual_artifact_publication_requires_admin_dependency():
    route = next(
        item
        for item in stage_routes.router.routes
        if item.path == "/missions/{vol_id}/stages/runs/{run_id}/artifacts"
    )

    assert any(
        dependency.call is stage_routes.require_admin
        for dependency in route.dependant.dependencies
    )


def test_manual_artifact_publication_validates_contract_before_s3(monkeypatch):
    request = _reconstruction_artifact_request(
        "mission-contract",
        "run-contract",
        str(uuid4()),
    )
    mission = SimpleNamespace(
        vol_id="mission-contract",
        organization_id="legacy-unassigned",
        workspace_prefix="missions/mission-contract",
    )
    run = SimpleNamespace(
        stage="reconstruction",
        run_id="run-contract",
        upstream_artifact_ids=[],
    )
    verifier_calls = []
    monkeypatch.setattr(
        stage_routes.storage,
        "verify_object_checksum",
        lambda *args: verifier_calls.append(args),
    )

    stage_routes._validate_published_artifact(mission, run, request)
    assert verifier_calls == [
        (
            "missions/mission-contract/stage-runs/run-contract/"
            "reconstruction-workspace/manifest.json",
            "b" * 64,
        )
    ]

    invalid_requests = [
        request.model_copy(update={"kind": "detection_workspace"}),
        request.model_copy(update={"uri": "s3://drone-ai/other/manifest.json"}),
        request.model_copy(update={"metadata": {"manifest_key": "other"}}),
        request.model_copy(update={"parent_artifact_ids": [str(uuid4())]}),
    ]
    for invalid in invalid_requests:
        with pytest.raises(HTTPException) as error:
            stage_routes._validate_published_artifact(mission, run, invalid)
        assert error.value.status_code == 422


def test_manual_artifact_publication_rejects_failed_remote_verification(monkeypatch):
    request = _reconstruction_artifact_request(
        "mission-remote",
        "run-remote",
        str(uuid4()),
    )
    monkeypatch.setattr(
        stage_routes.storage,
        "verify_object_checksum",
        lambda *_args: (_ for _ in ()).throw(OSError("missing")),
    )

    with pytest.raises(HTTPException) as error:
        stage_routes._validate_published_artifact(
            SimpleNamespace(
                vol_id="mission-remote",
                organization_id="legacy-unassigned",
                workspace_prefix="missions/mission-remote",
            ),
            SimpleNamespace(
                stage="reconstruction",
                run_id="run-remote",
                upstream_artifact_ids=[],
            ),
            request,
        )

    assert error.value.status_code == 422
    assert error.value.detail == (
        "Artifact manifest is missing or failed remote checksum verification"
    )


def test_new_manual_artifact_requires_a_running_stage(dag_sessions, monkeypatch):
    with dag_sessions() as session:
        mission = Mission(vol_id="mission-queued", owner_subject="dag-operator")
        session.add(mission)
        session.flush()
        run = MissionStageRun(
            mission_id=mission.id,
            stage="reconstruction",
            attempt=0,
            status="queued",
            idempotency_key="7" * 64,
        )
        session.add(run)
        session.flush()
        run_id = run.run_id
    monkeypatch.setattr(stage_routes, "get_session", dag_sessions)
    monkeypatch.setattr(
        stage_routes.storage,
        "verify_object_checksum",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(HTTPException) as error:
        stage_routes.publish_stage_artifact(
            "mission-queued",
            run_id,
            _reconstruction_artifact_request(
                "mission-queued",
                run_id,
                str(uuid4()),
            ),
            PRINCIPAL,
            None,
        )

    assert error.value.status_code == 409
