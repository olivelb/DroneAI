from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


APP2_ROOT = Path(__file__).resolve().parents[1] / "app2-ia"
if str(APP2_ROOT) not in sys.path:
    sys.path.insert(0, str(APP2_ROOT))

import detection_stage  # noqa: E402
from shared.model_provenance import build_model_manifest  # noqa: E402
from shared.detection_sharding import build_detection_shard_plan  # noqa: E402
from shared.stage_execution import (  # noqa: E402
    StageArtifactInput,
    StageExecutionContext,
)
from shared.stage_workspace import (  # noqa: E402
    PublishedWorkspace,
    RestoredWorkspace,
    WorkspaceSelection,
)


class FakeControl:
    def __init__(self) -> None:
        self.checks = 0

    def raise_if_cancelled(self) -> None:
        self.checks += 1


def _manifest() -> dict[str, object]:
    return build_model_manifest(
        backend="yolo",
        repository="ultralytics/assets",
        revision="v8.4.0",
        artifact="model.pt",
        artifact_sha256="a" * 64,
        libraries={"ultralytics": "test"},
        runtime={"device": "cpu"},
        inference={"confidence": 0.3},
    )


def _context() -> StageExecutionContext:
    return StageExecutionContext(
        run_id="d" * 32,
        mission_id=1,
        organization_id="acme-survey",
        vol_id="quarry-001",
        workspace_prefix="organizations/acme-survey/missions/quarry-001",
        owner_subject="operator-a",
        stage="detection",
        attempt=0,
        mission_attempt=0,
        parameters={
            "ai": {
                "backend": "yolo",
                "model_variant": "yolo26l",
                "classes": ["car"],
                "confidence": 0.3,
                "tile_size": 256,
                "tile_overlap": 64,
            }
        },
        mission_parameters={},
        inputs=(
            StageArtifactInput(
                artifact_id="raster-1",
                kind="raster_product_workspace",
                uri="s3://drone-ai/raster/manifest.json",
                checksum_sha256="b" * 64,
                size_bytes=100,
                metadata={
                    "manifest_key": "raster/manifest.json",
                    "ortho_file": "orthomosaic.tif",
                },
            ),
        ),
    )


def _detection(offset: float = 0.0) -> dict[str, object]:
    return {
        "global_pixel_x": 100.0 + offset,
        "global_pixel_y": 100.0,
        "confidence": 0.9 - offset / 100.0,
        "class_id": 0,
        "class_name": "small-vehicle",
        "segment": [
            [95.0 + offset, 97.0],
            [105.0 + offset, 97.0],
            [105.0 + offset, 103.0],
            [95.0 + offset, 103.0],
        ],
        "tile_index": 0,
    }


def test_detection_runner_streams_bounded_tiles_and_cleans_jpegs(
    tmp_path,
    monkeypatch,
):
    raster_path = tmp_path / "orthomosaic.tif"
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=300,
        height=300,
        count=3,
        dtype="uint8",
        crs="EPSG:32631",
        transform=from_origin(500_000, 4_800_000, 0.1, 0.1),
    ) as destination:
        destination.write(np.zeros((3, 300, 300), dtype=np.uint8))
    context = _context()
    control = FakeControl()
    runner = detection_stage.DetectionStageRunner(
        context,
        control,
        tmp_path,
        detection_stage.DetectionStageConfig.from_context(context),
    )
    calls = []

    def infer(tile_path):
        calls.append(Path(tile_path).name)
        return (
            [
                {
                    "center_x": 25.0,
                    "center_y": 20.0,
                    "confidence": 0.9,
                    "class_id": 0,
                    "class_name": "small-vehicle",
                    "polygon": [[20.0, 18.0], [30.0, 18.0], [30.0, 22.0]],
                }
            ],
            {"model_manifest": _manifest()},
        )

    monkeypatch.setattr(runner, "_infer", infer)

    detections, manifest, metadata = runner.run(raster_path)

    assert len(calls) == 4
    assert len(detections) == 4
    assert manifest["backend"] == "yolo"
    assert metadata["tile_count"] == 4
    assert metadata["planned_inference_pixels"] == 262_144
    assert metadata["pixel_amplification_ratio"] == 2.912711
    assert control.checks == 4
    assert not (tmp_path / ".droneai" / "detection-tiles").exists()


def test_detection_runner_executes_only_the_selected_shard(tmp_path, monkeypatch):
    raster_path = tmp_path / "orthomosaic.tif"
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=600,
        height=500,
        count=3,
        dtype="uint8",
        crs="EPSG:32631",
        transform=from_origin(500_000, 4_800_000, 0.1, 0.1),
    ) as destination:
        destination.write(np.zeros((3, 500, 600), dtype=np.uint8))
    context = _context()
    runner = detection_stage.DetectionStageRunner(
        context,
        FakeControl(),
        tmp_path,
        detection_stage.DetectionStageConfig.from_context(context),
    )
    plan = build_detection_shard_plan(
        600,
        500,
        256,
        64,
        tiles_per_shard=3,
    )
    calls = []

    def infer(tile_path):
        calls.append(Path(tile_path).stem)
        return [], {"model_manifest": _manifest()}

    monkeypatch.setattr(runner, "_infer", infer)

    detections, _manifest_result, metadata = runner.run_shard(
        raster_path,
        plan,
        1,
    )

    assert detections == []
    assert calls == ["tile-000003", "tile-000004", "tile-000005"]
    assert metadata["shard_index"] == 1
    assert metadata["shard_tile_count"] == 3
    assert metadata["shard_count"] == 2
    assert metadata["plan_checksum_sha256"] == plan.checksum_sha256


def test_detection_runner_rejects_model_provenance_change(tmp_path, monkeypatch):
    raster_path = tmp_path / "orthomosaic.tif"
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=300,
        height=300,
        count=3,
        dtype="uint8",
        crs="EPSG:32631",
        transform=from_origin(500_000, 4_800_000, 0.1, 0.1),
    ) as destination:
        destination.write(np.zeros((3, 300, 300), dtype=np.uint8))
    context = _context()
    runner = detection_stage.DetectionStageRunner(
        context,
        FakeControl(),
        tmp_path,
        detection_stage.DetectionStageConfig.from_context(context),
    )
    calls = 0

    def infer(_tile_path):
        nonlocal calls
        calls += 1
        manifest = _manifest()
        if calls > 1:
            manifest = {**manifest, "artifact_sha256": "f" * 64}
        return [], {"model_manifest": manifest}

    monkeypatch.setattr(runner, "_infer", infer)

    with pytest.raises(RuntimeError, match="provenance changed"):
        runner.run(raster_path)


def test_detection_config_rejects_unbounded_prompt_and_confidence():
    context = _context()

    with pytest.raises(ValueError, match="SAM prompt"):
        detection_stage.DetectionStageConfig.from_context(
            replace(
                context,
                parameters={"ai": {"sam_prompt": "x" * 129}},
            )
        )
    with pytest.raises(ValueError, match="confidence"):
        detection_stage.DetectionStageConfig.from_context(
            replace(context, parameters={"ai": {"confidence": 1.1}})
        )


def test_detection_config_enforces_sam3_effective_input_limit():
    context = _context()

    defaulted = detection_stage.DetectionStageConfig.from_context(
        replace(context, parameters={"ai": {"backend": "sam3"}})
    )
    assert defaulted.confidence == 0.75

    with pytest.raises(ValueError, match="must not exceed 1024 pixels"):
        detection_stage.DetectionStageConfig.from_context(
            replace(
                context,
                parameters={
                    "ai": {
                        "backend": "sam3",
                        "tile_size": 1025,
                    }
                },
            )
        )

    yolo = detection_stage.DetectionStageConfig.from_context(
        replace(
            context,
            parameters={
                "ai": {
                    "backend": "yolo",
                    "tile_size": 2048,
                }
            },
        )
    )
    assert yolo.tile_size == 2048


@pytest.mark.parametrize(
    ("v2_enabled", "selective_restore"),
    [(False, False), (True, False), (True, True)],
)
def test_detection_stage_publishes_deduplicated_geojson_and_provenance(
    tmp_path,
    monkeypatch,
    v2_enabled,
    selective_restore,
):
    monkeypatch.setenv("DRONEAI_STAGE_WORK_ROOT", str(tmp_path / "work"))

    def restore(
        _manifest,
        destination,
        _checksum,
        cancellation_check,
        **kwargs,
    ):
        cancellation_check()
        if selective_restore:
            assert kwargs["selection"] == WorkspaceSelection(
                paths=frozenset({"orthomosaic.tif"})
            )
        else:
            assert "selection" not in kwargs
        path = Path(destination, "orthomosaic.tif")
        path.write_bytes(b"raster")
        return RestoredWorkspace(
            size_bytes=100,
            file_count=1,
            downloaded_bytes=140,
            reused_bytes=0,
            download_seconds=0.5,
            manifest_size_bytes=40,
        )

    monkeypatch.setattr(detection_stage, "restore_workspace_measured", restore)
    monkeypatch.setattr(
        detection_stage.DetectionStageRunner,
        "run",
        lambda *_args: (
            [_detection(), _detection(1.0)],
            _manifest(),
            {
                "width": 1000,
                "height": 800,
                "crs": "EPSG:32631",
                "transform": [500_000.0, 0.1, 0.0, 4_800_000.0, 0.0, -0.1],
                "tile_size": 256,
                "tile_overlap": 64,
                "tile_count": 4,
                "plan_checksum_sha256": "e" * 64,
                "shard_count": 1,
                "planned_inference_pixels": 262_144,
                "pixel_amplification_ratio": 2.912711,
            },
        ),
    )
    inspected = {}

    def publish(workspace, prefix, cancellation_check, **kwargs):
        cancellation_check()
        geojson = Path(workspace, ".droneai", "detection", "detections.geojson")
        raw = Path(workspace, ".droneai", "detection", "detections.json")
        inspected["geojson"] = geojson.read_text(encoding="utf-8")
        inspected["raw"] = raw.read_text(encoding="utf-8")
        if v2_enabled:
            assert kwargs["default_role"] == "detection-workspace"
            assert kwargs["role_overrides"] == {
                ".droneai/detection/detections.json": "detection-records",
                ".droneai/detection/detections.geojson": "detection-features",
            }
            assert kwargs["parents"][0].artifact_id == "raster-1"
            assert kwargs["allow_partial_workspace"] is selective_restore
        return PublishedWorkspace(
            manifest_key=f"{prefix}/manifest.json",
            uri=f"s3://drone-ai/{prefix}/manifest.json",
            checksum_sha256="c" * 64,
            size_bytes=123,
            file_count=4,
        )

    monkeypatch.setattr(
        detection_stage,
        "artifact_manifest_v2_write_enabled",
        lambda: v2_enabled,
    )
    monkeypatch.setattr(
        detection_stage,
        "artifact_selective_restore_enabled",
        lambda: selective_restore,
    )
    monkeypatch.setattr(detection_stage, "publish_workspace", publish)
    monkeypatch.setattr(detection_stage, "publish_workspace_v2", publish)

    result = detection_stage.run_detection_stage(_context(), FakeControl())

    assert result.kind == "detection_workspace"
    assert result.metadata["feature_count"] == 1
    assert result.quality_metrics == {
        "tile_count": 4,
        "raw_detection_count": 2,
        "deduplicated_detection_count": 1,
        "geolocated_feature_count": 1,
        "planned_inference_pixels": 262_144,
        "pixel_amplification_ratio": 2.912711,
    }
    assert result.provenance["model_manifest"]["backend"] == "yolo"
    assert result.provenance["tile_plan"]["tile_count"] == 4
    assert result.provenance["tile_plan"]["shard_count"] == 1
    assert result.provenance["workspace_transfer"]["restore"]["transferred_bytes"] == 140
    assert result.provenance["workspace_materialization"] == {
        "mode": "selective" if selective_restore else "full",
        "selected_paths": ["orthomosaic.tif"] if selective_restore else [],
    }
    assert '"feature_count": 1' in inspected["geojson"]
    assert '"detections"' in inspected["raw"]
    assert not (tmp_path / "work" / ("d" * 32)).exists()
