import hashlib
import importlib
import io
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared.model_provenance import build_model_manifest
from shared.tile_results import build_tile_result_artifact


PROCESSING_ROOT = Path(__file__).resolve().parents[1] / "app3-processing"
if str(PROCESSING_ROOT) not in sys.path:
    sys.path.insert(0, str(PROCESSING_ROOT))

dispatcher_module = importlib.import_module("processing_dispatcher")
legacy_module = importlib.import_module("legacy_aggregation")


class FakeCancellationRegistry:
    def __init__(self):
        self.cancelled = False
        self.cleared = []

    def clear(self, *args):
        self.cleared.append(args)

    def is_cancelled(self, *_args, **_kwargs):
        return self.cancelled


class FakeTiler:
    def __init__(self):
        self.calls = []

    def slice(self, ortho_s3_key, vol_id, **kwargs):
        self.calls.append((ortho_s3_key, vol_id, kwargs))


class FakeWorkflow:
    def __init__(self):
        self.detections = []
        self.recoveries = 0

    def process_detection(self, data):
        self.detections.append(data)

    def recover(self):
        self.recoveries += 1


def _dispatcher():
    cancellation = FakeCancellationRegistry()
    tiler = FakeTiler()
    analysis = FakeWorkflow()
    legacy = FakeWorkflow()
    dispatcher = dispatcher_module.ProcessingDispatcher(
        orthomosaic_topic="orthomosaic",
        cancellation_registry=cancellation,
        tiler=tiler,
        analysis_workflow=analysis,
        legacy_workflow=legacy,
    )
    return dispatcher, cancellation, tiler, analysis, legacy


def test_orthomosaic_event_checks_attempt_and_routes_all_options():
    dispatcher, cancellation, tiler, analysis, legacy = _dispatcher()

    dispatcher.process_event(
        {
            "vol_id": "mission-1",
            "ortho_s3_key": "missions/mission-1/orthomosaic.tif",
            "analysis_run_id": "run-1",
            "attempt": 3,
            "tile_size": 512,
            "classes": ["truck"],
            "ai_confidence": 0.42,
            "ai_backend": "sam3",
            "ai_model_variant": "yolo26m",
            "sam_prompt": "lorry",
        },
        "orthomosaic",
    )

    assert cancellation.cleared == []
    assert tiler.calls == [
        (
            "missions/mission-1/orthomosaic.tif",
            "mission-1",
            {
                "tile_size": 512,
                "classes": ["truck"],
                "ai_confidence": 0.42,
                "ai_backend": "sam3",
                "ai_model_variant": "yolo26m",
                "sam_prompt": "lorry",
                "analysis_run_id": "run-1",
                "analysis_attempt": 3,
            },
        )
    ]
    assert analysis.detections == []
    assert legacy.detections == []


def test_detection_routes_to_modern_or_legacy_workflow():
    dispatcher, _cancellation, _tiler, analysis, legacy = _dispatcher()
    modern = {
        "vol_id": "mission-1",
        "tile_index": 1,
        "analysis_run_id": "run-1",
    }
    old = {"vol_id": "mission-1", "tile_index": 2}

    dispatcher.process_event(modern, "tile-detections")
    dispatcher.process_event(old, "tile-detections")

    assert analysis.detections == [modern]
    assert legacy.detections == [old]


def test_cancelled_detection_is_ignored_and_recovery_calls_both_workflows():
    dispatcher, cancellation, _tiler, analysis, legacy = _dispatcher()
    cancellation.cancelled = True

    dispatcher.process_event(
        {"vol_id": "mission-1", "tile_index": 1, "attempt": 2},
        "tile-detections",
    )
    dispatcher.recover()

    assert analysis.detections == []
    assert legacy.detections == []
    assert analysis.recoveries == 1
    assert legacy.recoveries == 1


def test_cancelled_orthomosaic_is_ignored_before_tiling():
    dispatcher, cancellation, tiler, _analysis, _legacy = _dispatcher()
    cancellation.cancelled = True

    dispatcher.process_event(
        {
            "vol_id": "mission-1",
            "ortho_s3_key": "missions/mission-1/orthomosaic.tif",
            "analysis_run_id": "run-1",
            "attempt": 2,
        },
        "orthomosaic",
    )

    assert tiler.calls == []


def test_legacy_workflow_reads_and_verifies_referenced_tile_result(monkeypatch):
    manifest = build_model_manifest(
        backend="yolo",
        repository="ultralytics/assets",
        revision="v8.4.0",
        artifact="yolo26l-obb.pt",
        artifact_sha256="a" * 64,
        libraries={"ultralytics": "8.4.0"},
        runtime={"device": "cpu"},
        inference={"confidence": 0.3},
    )
    artifact = build_tile_result_artifact(
        vol_id="mission-1",
        analysis_run_id=None,
        tile_index=3,
        attempt=1,
        model_manifest=manifest,
        detections=[{"class_name": "truck", "confidence": 0.9}],
    )
    payload = json.dumps(artifact, separators=(",", ":")).encode("utf-8")
    key = "missions/mission-1/ai-tile-results/pipeline/attempt_1/tile_3.json"
    monkeypatch.setattr(
        legacy_module.storage,
        "get_object_stream",
        lambda _key: (io.BytesIO(payload), len(payload), "application/json"),
    )
    workflow = legacy_module.LegacyAggregationWorkflow(
        report_progress=lambda *_args, **_kwargs: None,
        report_ia_progress=lambda *_args, **_kwargs: None,
        logger=importlib.import_module("logging").getLogger("test"),
    )

    detections = workflow._event_detections(
        {
            "vol_id": "mission-1",
            "tile_index": 3,
            "attempt": 1,
            "model_manifest": manifest,
            "result_s3_key": key,
            "result_sha256": hashlib.sha256(payload).hexdigest(),
            "result_size_bytes": len(payload),
            "detection_count": 1,
            "result_schema_version": 1,
        }
    )

    assert detections == [
        {"class_name": "truck", "confidence": 0.9, "tile_index": 3}
    ]


def test_legacy_publication_failure_always_cleans_workspace(monkeypatch):
    vol_id = f"test-cleanup-{uuid4().hex}"
    workspace = Path("/tmp/processing") / vol_id

    @contextmanager
    def session_scope():
        yield SimpleNamespace()

    monkeypatch.setattr(legacy_module, "get_session", session_scope)
    monkeypatch.setattr(
        legacy_module,
        "get_mission_detections",
        lambda _session, _vol_id: [],
    )
    monkeypatch.setattr(
        legacy_module.storage,
        "upload_verified_file",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("S3 unavailable")),
    )
    workflow = legacy_module.LegacyAggregationWorkflow(
        report_progress=lambda *_args, **_kwargs: None,
        report_ia_progress=lambda *_args, **_kwargs: None,
        logger=importlib.import_module("logging").getLogger("test"),
    )

    with pytest.raises(RuntimeError, match="S3 unavailable"):
        workflow.generate_vector_results(
            vol_id,
            {"ortho_s3_key": None, "tiling_metadata": {}},
        )

    assert not workspace.exists()


def test_legacy_completion_publishes_durable_ia_terminal(monkeypatch):
    vol_id = f"test-terminal-{uuid4().hex}"
    mission = SimpleNamespace(
        aggregation_status="finalizing",
        aggregation_completed_at=None,
    )

    class Query:
        def filter(self, *_args):
            return self

        def with_for_update(self):
            return self

        def one(self):
            return mission

    class Session:
        @staticmethod
        def query(*_args):
            return Query()

    @contextmanager
    def session_scope():
        yield Session()

    monkeypatch.setattr(legacy_module, "get_session", session_scope)
    monkeypatch.setattr(
        legacy_module,
        "get_mission_detections",
        lambda _session, _vol_id: [],
    )
    monkeypatch.setattr(
        legacy_module.storage,
        "upload_verified_file",
        lambda *_args: None,
    )
    tiler_progress = []
    ia_progress = []
    workflow = legacy_module.LegacyAggregationWorkflow(
        report_progress=lambda *args, **kwargs: tiler_progress.append((args, kwargs)),
        report_ia_progress=lambda *args, **kwargs: ia_progress.append((args, kwargs)),
        logger=importlib.import_module("logging").getLogger("test"),
    )

    workflow.generate_vector_results(
        vol_id,
        {"ortho_s3_key": None, "tiling_metadata": {}},
    )

    assert mission.aggregation_status == "completed"
    assert ia_progress == [
        (
            (vol_id, "DETECTING", 100),
                {
                    "status": "success",
                    "log": "IA durably completed all tiles with 0 vector detections (0 raw)",
                    "organization_id": "legacy-unassigned",
                },
        )
    ]
    assert tiler_progress[0][0] == (vol_id, "DONE", 100)
    assert tiler_progress[0][1]["status"] == "success"
