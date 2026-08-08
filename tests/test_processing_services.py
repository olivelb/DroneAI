import importlib
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import AIAnalysisRun, AIAnalysisTile, Mission
from shared.model_provenance import build_model_manifest

PROCESSING_DIR = Path(__file__).resolve().parents[1] / "app3-processing"
if str(PROCESSING_DIR) not in sys.path:
    sys.path.insert(0, str(PROCESSING_DIR))

analysis_workflow = importlib.import_module("analysis_workflow")
orthomosaic_tiler = importlib.import_module("orthomosaic_tiler")


def _analysis_session_scope():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    AIAnalysisRun.__table__.create(engine)
    AIAnalysisTile.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return scope


def _workflow(**overrides):
    return analysis_workflow.AnalysisWorkflow(
        producer=SimpleNamespace(),
        orthomosaic_topic="orthomosaic",
        tile_topic="tiles",
        dedupe=lambda records: records,
        logger=SimpleNamespace(),
        maximum_tile_attempts=overrides.get("maximum_tile_attempts", 3),
        finalization_lease_seconds=120,
        finalization_owner=overrides.get(
            "finalization_owner",
            "test-worker",
        ),
        maximum_tile_result_bytes=overrides.get(
            "maximum_tile_result_bytes",
            1024,
        ),
        maximum_aggregate_result_bytes=overrides.get(
            "maximum_aggregate_result_bytes",
            2048,
        ),
        maximum_raw_detections=overrides.get(
            "maximum_raw_detections",
            100,
        ),
        maximum_final_detections=overrides.get(
            "maximum_final_detections",
            50,
        ),
    )


def _model_manifest(artifact_sha256="a" * 64):
    return build_model_manifest(
        backend="yolo",
        repository="ultralytics/assets",
        revision="v8.4.0",
        artifact="yolo26l-obb.pt",
        artifact_sha256=artifact_sha256,
        libraries={"ultralytics": "8.4.0"},
        runtime={"device": "cpu"},
        inference={"model_variant": "yolo26l", "confidence": 0.3},
    )


def test_analysis_json_publication_is_atomic_and_verified(
    tmp_path,
    monkeypatch,
):
    uploads = []
    monkeypatch.setattr(
        analysis_workflow.storage,
        "upload_verified_file",
        lambda path, key: uploads.append((Path(path), key)),
    )
    destination = tmp_path / "results" / "detections.geojson"

    analysis_workflow.AnalysisWorkflow._write_verified_json(
        {"type": "FeatureCollection", "features": []},
        "missions/m-1/analyses/a-1/detections.geojson",
        destination,
    )

    assert destination.read_text(encoding="utf-8") == ('{"type":"FeatureCollection","features":[]}')
    assert uploads == [
        (
            destination,
            "missions/m-1/analyses/a-1/detections.geojson",
        )
    ]
    assert not destination.with_suffix(".geojson.tmp").exists()


def test_tiling_plan_has_bounded_overlap_and_private_iteration_state(
    monkeypatch,
):
    monkeypatch.setenv("TILE_OVERLAP", "9999")
    source = SimpleNamespace(
        width=2500,
        height=1700,
        transform=SimpleNamespace(to_gdal=lambda: (0, 1, 0, 0, 0, -1)),
        crs=SimpleNamespace(to_string=lambda: "EPSG:2154"),
    )

    plan = orthomosaic_tiler.OrthomosaicTiler._build_plan(
        source,
        1024,
    )
    public = orthomosaic_tiler.OrthomosaicTiler._public_metadata(plan)

    assert plan["overlap"] == 512
    assert plan["total_tiles"] == (len(plan["x_starts"]) * len(plan["y_starts"]))
    assert public["crs"] == "EPSG:2154"
    assert "x_starts" not in public
    assert "y_starts" not in public


def test_tiler_removes_workspace_after_success(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    progress = []
    tiler = orthomosaic_tiler.OrthomosaicTiler(
        producer=SimpleNamespace(flush=lambda: 0),
        tile_topic="tiles",
        is_cancelled=lambda *_args: False,
        report_progress=lambda *args, **kwargs: progress.append((args, kwargs)),
        logger=SimpleNamespace(
            info=lambda *_args: None,
            warning=lambda *_args: None,
        ),
    )
    monkeypatch.setattr(
        tiler,
        "_workspace",
        lambda *_args: workspace,
    )

    def fake_download(_key, destination, _vol_id):
        destination.write_bytes(b"orthomosaic")

    monkeypatch.setattr(tiler, "_download", fake_download)
    monkeypatch.setattr(
        tiler,
        "_publish_tiles",
        lambda **_kwargs: 1,
    )
    monkeypatch.setattr(
        tiler,
        "_complete_database_state",
        lambda *_args: True,
    )

    tiler.slice("orthomosaic.tif", "mission-1")

    assert progress[-1][0][1:] == ("TILING_DONE", 100)
    assert not workspace.exists()


def test_processing_temporary_storage_is_bounded():
    chart = (
        Path(__file__).resolve().parents[1] / "charts" / "drone-ai" / "templates" / "processing-worker.yaml"
    ).read_text(encoding="utf-8")
    values = (Path(__file__).resolve().parents[1] / "charts" / "drone-ai" / "values.yaml").read_text(encoding="utf-8")

    assert "sizeLimit: {{ .Values.processingWorker.temporaryStorage.sizeLimit }}" in chart
    assert 'sizeLimit: "50Gi"' in values


def test_analysis_workflow_has_a_bounded_tile_retry_budget():
    workflow = _workflow()

    assert workflow.maximum_tile_attempts == 3
    assert workflow.finalization_lease_seconds == 120
    assert workflow.finalization_owner == "test-worker"
    assert workflow.maximum_tile_result_bytes == 1024
    assert workflow.maximum_aggregate_result_bytes == 2048
    assert workflow.maximum_raw_detections == 100
    assert workflow.maximum_final_detections == 50


def test_analysis_tile_payload_limits_are_enforced(monkeypatch):
    import io

    payload = json.dumps({"raw_detections": [{"class_name": "tree"}] * 3}).encode("utf-8")
    monkeypatch.setattr(
        analysis_workflow.storage,
        "get_object_stream",
        lambda _key: (io.BytesIO(payload), len(payload), "application/json"),
    )

    workflow = _workflow(maximum_raw_detections=2)
    with pytest.raises(RuntimeError, match="raw detection safety limit"):
        workflow._load_tile_payloads(["tile-result.json"])


def test_analysis_tile_payload_size_is_bounded_before_read(monkeypatch):
    import io

    stream = io.BytesIO(b"{}")
    monkeypatch.setattr(
        analysis_workflow.storage,
        "get_object_stream",
        lambda _key: (stream, 11, "application/json"),
    )

    workflow = _workflow(maximum_tile_result_bytes=10)
    with pytest.raises(RuntimeError, match="tile result exceeds"):
        workflow._load_tile_payloads(["oversized.json"])
    assert stream.closed


def test_analysis_aggregate_payload_size_is_bounded(monkeypatch):
    import io

    payload = b'{"raw_detections": []}'
    monkeypatch.setattr(
        analysis_workflow.storage,
        "get_object_stream",
        lambda _key: (io.BytesIO(payload), len(payload), "application/json"),
    )

    workflow = _workflow(
        maximum_tile_result_bytes=len(payload),
        maximum_aggregate_result_bytes=len(payload) + 1,
    )
    with pytest.raises(RuntimeError, match="aggregate result size limit"):
        workflow._load_tile_payloads(["one.json", "two.json"])


def test_active_finalization_lease_rejects_second_owner(monkeypatch):
    session_scope = _analysis_session_scope()
    monkeypatch.setattr(analysis_workflow, "get_session", session_scope)
    with session_scope() as session:
        mission = Mission(vol_id="mission-1")
        session.add(mission)
        session.flush()
        run = AIAnalysisRun(
            run_id="run-1",
            mission_id=mission.id,
            vol_id=mission.vol_id,
            name="Analysis",
            ortho_s3_key="missions/mission-1/orthomosaic.tif",
            total_tiles=1,
            status="finalizing",
            finalization_owner="worker-a",
            finalization_lease_until=(datetime.now(timezone.utc) + timedelta(minutes=5)),
        )
        session.add(run)
        session.flush()
        session.add(
            AIAnalysisTile(
                analysis_run_id=run.id,
                tile_index=0,
                status="completed",
                tile_s3_key="tile.jpg",
                result_s3_key="result.json",
                offset_x=0,
                offset_y=0,
                width=10,
                height=10,
            )
        )

    assert _workflow(finalization_owner="worker-b")._claim_finalization("run-1") is None


def test_recovery_marks_exhausted_tiles_dead(monkeypatch):
    session_scope = _analysis_session_scope()
    monkeypatch.setattr(analysis_workflow, "get_session", session_scope)
    with session_scope() as session:
        mission = Mission(vol_id="mission-1")
        session.add(mission)
        session.flush()
        run = AIAnalysisRun(
            run_id="run-1",
            mission_id=mission.id,
            vol_id=mission.vol_id,
            name="Analysis",
            ortho_s3_key="missions/mission-1/orthomosaic.tif",
            total_tiles=1,
            status="running",
            heartbeat_at=(datetime.now(timezone.utc) - timedelta(minutes=20)),
        )
        session.add(run)
        session.flush()
        session.add(
            AIAnalysisTile(
                analysis_run_id=run.id,
                tile_index=0,
                status="queued",
                tile_s3_key="tile.jpg",
                offset_x=0,
                offset_y=0,
                width=10,
                height=10,
                attempts=3,
            )
        )

    ready, ortho_events, tile_events = _workflow()._plan_recovery()

    assert ready == []
    assert ortho_events == []
    assert tile_events == []
    with session_scope() as session:
        run = session.query(AIAnalysisRun).one()
        tile = session.query(AIAnalysisTile).one()
        assert run.status == "failed"
        assert run.phase == "tile_attempts_exhausted"
        assert tile.status == "dead"


def test_stale_analysis_attempt_cannot_stage_tile_result(monkeypatch):
    session_scope = _analysis_session_scope()
    monkeypatch.setattr(analysis_workflow, "get_session", session_scope)
    with session_scope() as session:
        mission = Mission(vol_id="mission-1")
        session.add(mission)
        session.flush()
        run = AIAnalysisRun(
            run_id="run-1",
            mission_id=mission.id,
            vol_id=mission.vol_id,
            name="Analysis",
            ortho_s3_key="missions/mission-1/orthomosaic.tif",
            retry_count=2,
            total_tiles=1,
            status="running",
        )
        session.add(run)
        session.flush()
        session.add(
            AIAnalysisTile(
                analysis_run_id=run.id,
                tile_index=0,
                status="queued",
                tile_s3_key="tile.jpg",
                offset_x=0,
                offset_y=0,
                width=10,
                height=10,
            )
        )

    workflow = _workflow()
    monkeypatch.setattr(
        workflow,
        "_stage_tile_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale result was staged")),
    )

    workflow.process_detection(
        {
            "vol_id": "mission-1",
            "analysis_run_id": "run-1",
            "tile_index": 0,
            "attempt": 1,
            "detections": [],
        }
    )

    with session_scope() as session:
        assert session.query(AIAnalysisTile).one().status == "queued"


def test_analysis_run_pins_first_model_manifest_and_rejects_mixed_results(
    monkeypatch,
):
    session_scope = _analysis_session_scope()
    monkeypatch.setattr(analysis_workflow, "get_session", session_scope)
    with session_scope() as session:
        mission = Mission(vol_id="mission-1")
        session.add(mission)
        session.flush()
        run = AIAnalysisRun(
            run_id="run-1",
            mission_id=mission.id,
            vol_id=mission.vol_id,
            name="Analysis",
            backend="yolo",
            ortho_s3_key="missions/mission-1/orthomosaic.tif",
            total_tiles=1,
            status="running",
        )
        session.add(run)
        session.flush()
        session.add(
            AIAnalysisTile(
                analysis_run_id=run.id,
                tile_index=0,
                status="queued",
                tile_s3_key="tile.jpg",
                offset_x=0,
                offset_y=0,
                width=10,
                height=10,
            )
        )

    workflow = _workflow()
    monkeypatch.setattr(
        workflow,
        "_stage_tile_result",
        lambda *_args, **_kwargs: ("result.json", 0),
    )
    monkeypatch.setattr(
        workflow,
        "_mark_tile_complete",
        lambda *_args, **_kwargs: False,
    )
    event = {
        "vol_id": "mission-1",
        "analysis_run_id": "run-1",
        "tile_index": 0,
        "attempt": 0,
        "detections": [],
        "model_manifest": _model_manifest(),
    }

    workflow.process_detection(event)

    with session_scope() as session:
        assert session.query(AIAnalysisRun).one().model_manifest == _model_manifest()

    with pytest.raises(RuntimeError, match="different model provenance"):
        workflow.process_detection(
            {
                **event,
                "model_manifest": _model_manifest("b" * 64),
            }
        )
