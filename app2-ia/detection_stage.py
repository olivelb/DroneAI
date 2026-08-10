"""Bounded full-raster YOLO/SAM3 detection stage executor."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import rasterio
from PIL import Image
from rasterio.windows import Window

from detection_core import DetectionRecord, run_yolo_detection
from sam3_backend import Sam3Backend
from shared.artifact_manifest import ManifestParent
from shared.detection_geometry import dedupe_mission_detections
from shared.detection_shard_results import DetectionAggregate
from shared.detection_sharding import DetectionShardPlan, build_detection_shard_plan
from shared.geospatial_assets import detections_feature_collection
from shared.json_io import atomic_write_json
from shared.model_provenance import validate_model_manifest
from shared.pipeline_params import normalize_ai_backend
from shared.sam3_capabilities import SAM3_DEFAULT_CONFIDENCE, validate_sam3_tile_size
from shared.stage_execution import (
    StageExecutionContext,
    StageExecutionControl,
    StageExecutionResult,
)
from shared.stage_workspace import (
    RestoredWorkspace,
    WorkspaceSelection,
    artifact_manifest_v2_write_enabled,
    artifact_selective_restore_enabled,
    publish_workspace,
    publish_workspace_v2,
    resolve_workspace_path,
    restore_workspace_measured,
    workspace_transfer_provenance,
)
from shared.validation import safe_child_path


logger = logging.getLogger("app2-ia.detection-stage")


@dataclass(frozen=True)
class DetectionStageConfig:
    backend: str
    model_variant: str
    classes: tuple[str, ...]
    confidence: float
    sam_prompt: str
    tile_size: int
    overlap: int
    maximum_tiles: int
    maximum_raw_detections: int

    @classmethod
    def from_context(cls, context: StageExecutionContext) -> DetectionStageConfig:
        raw_ai = context.parameters.get("ai") or {}
        if not isinstance(raw_ai, dict):
            raise ValueError("Detection stage AI parameters must be an object")
        ai = cast(dict[str, Any], raw_ai)
        backend = normalize_ai_backend(cast(str | None, ai.get("backend")))
        raw_confidence = ai.get("confidence")
        confidence = float(
            raw_confidence
            if raw_confidence is not None
            else (SAM3_DEFAULT_CONFIDENCE if backend == "sam3" else 0.3)
        )
        if not 0.0 < confidence <= 1.0:
            raise ValueError("Detection confidence must be in (0, 1]")
        raw_classes = ai.get("classes") or ["car"]
        if not isinstance(raw_classes, list) or not all(
            isinstance(item, str) and item.strip() for item in raw_classes
        ):
            raise ValueError("Detection classes must be non-empty strings")
        tile_size = int(
            str(ai.get("tile_size") or os.getenv("DETECTION_TILE_SIZE", "1024"))
        )
        overlap = int(
            str(
                ai.get("tile_overlap")
                or os.getenv("DETECTION_TILE_OVERLAP", str(tile_size // 4))
            )
        )
        if not 256 <= tile_size <= 4096:
            raise ValueError("Detection tile size must be between 256 and 4096")
        if backend == "sam3":
            validate_sam3_tile_size(tile_size)
        if not 0 <= overlap < tile_size:
            raise ValueError("Detection tile overlap must be smaller than tile size")
        model_variant = str(ai.get("model_variant") or "yolo26l").strip()
        if not model_variant or len(model_variant) > 128:
            raise ValueError("Detection model variant must contain 1 to 128 characters")
        sam_prompt = str(ai.get("sam_prompt") or "car").strip() or "car"
        if len(sam_prompt) > 128:
            raise ValueError("SAM prompt must contain at most 128 characters")
        return cls(
            backend=backend,
            model_variant=model_variant,
            classes=tuple(str(item).strip() for item in raw_classes),
            confidence=confidence,
            sam_prompt=sam_prompt,
            tile_size=tile_size,
            overlap=overlap,
            maximum_tiles=min(
                100_000,
                max(1, int(os.getenv("DETECTION_MAX_TILES", "4096"))),
            ),
            maximum_raw_detections=min(
                1_000_000,
                max(
                    1,
                    int(os.getenv("DETECTION_MAX_RAW_DETECTIONS", "100000")),
                ),
            ),
        )


class DetectionStageRunner:
    """Stream raster tiles through one cached model and publish vector output."""

    def __init__(
        self,
        context: StageExecutionContext,
        control: StageExecutionControl,
        workspace: Path,
        config: DetectionStageConfig,
    ) -> None:
        self.context = context
        self.control = control
        self.workspace = workspace
        self.config = config
        self.sam3 = Sam3Backend(logger=logger)

    def _infer(
        self,
        tile_path: Path,
    ) -> tuple[list[DetectionRecord], dict[str, Any]]:
        if self.config.backend == "sam3":
            return self.sam3.run(
                str(tile_path),
                self.config.sam_prompt,
                self.config.confidence,
            )
        return run_yolo_detection(
            str(tile_path),
            list(self.config.classes),
            self.config.confidence,
            self.config.model_variant,
        )

    @staticmethod
    def _write_tile(source: Any, window: Window, path: Path) -> None:
        indexes = list(range(1, min(source.count, 3) + 1))
        data = source.read(indexes, window=window)
        if data.shape[0] == 1:
            data = np.repeat(data, 3, axis=0)
        elif data.shape[0] == 2:
            data = np.concatenate([data, data[:1]], axis=0)
        rgb = data[:3].transpose(1, 2, 0)
        if rgb.dtype != np.uint8:
            rgb = np.clip(rgb, 0, 255).astype(np.uint8)
        Image.fromarray(rgb, mode="RGB").save(path, format="JPEG", quality=95)

    @staticmethod
    def _global_detection(
        detection: DetectionRecord,
        *,
        tile_index: int,
        offset_x: int,
        offset_y: int,
    ) -> DetectionRecord:
        segment = cast(list[list[float]], detection.get("polygon") or [])
        return {
            "global_pixel_x": float(detection["center_x"]) + offset_x,
            "global_pixel_y": float(detection["center_y"]) + offset_y,
            "confidence": float(detection["confidence"]),
            "class_id": int(detection["class_id"]),
            "class_name": str(detection["class_name"]),
            "segment": [
                [float(point[0]) + offset_x, float(point[1]) + offset_y]
                for point in segment
            ],
            "tile_index": tile_index,
        }

    def run_shard(
        self,
        raster_path: Path,
        plan: DetectionShardPlan,
        shard_index: int,
    ) -> tuple[list[DetectionRecord], dict[str, Any], dict[str, Any]]:
        if plan.tile_size != self.config.tile_size or plan.overlap != self.config.overlap:
            raise ValueError("Detection shard plan does not match the stage configuration")
        shard = plan.shard(shard_index)
        tiles_dir = (
            self.workspace / ".droneai" / "detection-tiles" / f"shard-{shard_index:04d}"
        )
        tiles_dir.mkdir(parents=True, exist_ok=True)
        raw_detections: list[DetectionRecord] = []
        model_manifest: dict[str, Any] | None = None
        with rasterio.open(raster_path) as source:
            if source.count < 1:
                raise ValueError("Detection raster must expose at least one band")
            if source.width != plan.width or source.height != plan.height:
                raise ValueError("Detection shard plan does not match the raster dimensions")
            for tile in plan.tiles(shard_index):
                self.control.raise_if_cancelled()
                window = Window(
                    tile.offset_x,
                    tile.offset_y,
                    tile.width,
                    tile.height,
                )
                tile_path = tiles_dir / f"tile-{tile.tile_index:06d}.jpg"
                try:
                    self._write_tile(source, window, tile_path)
                    detections, attempt = self._infer(tile_path)
                finally:
                    tile_path.unlink(missing_ok=True)
                current_manifest = validate_model_manifest(
                    attempt.get("model_manifest")
                )
                if model_manifest is None:
                    model_manifest = current_manifest
                elif current_manifest != model_manifest:
                    raise RuntimeError("AI model provenance changed between tiles")
                raw_detections.extend(
                    self._global_detection(
                        detection,
                        tile_index=tile.tile_index,
                        offset_x=tile.offset_x,
                        offset_y=tile.offset_y,
                    )
                    for detection in detections
                )
                if len(raw_detections) > self.config.maximum_raw_detections:
                    raise RuntimeError("Detection result exceeds its safety limit")
            metadata = {
                "width": source.width,
                "height": source.height,
                "crs": source.crs.to_string() if source.crs else None,
                "transform": list(source.transform.to_gdal()),
                "tile_size": self.config.tile_size,
                "tile_overlap": self.config.overlap,
                "tile_count": plan.tile_count,
                "planned_inference_pixels": plan.planned_inference_pixels,
                "pixel_amplification_ratio": plan.pixel_amplification_ratio,
                "plan_checksum_sha256": plan.checksum_sha256,
                "shard_index": shard.shard_index,
                "shard_tile_count": shard.tile_count,
                "shard_count": plan.shard_count,
            }
        shutil.rmtree(tiles_dir.parent, ignore_errors=True)
        if model_manifest is None:
            raise RuntimeError("Detection stage did not execute any tile")
        return raw_detections, model_manifest, metadata

    def run(
        self,
        raster_path: Path,
    ) -> tuple[list[DetectionRecord], dict[str, Any], dict[str, Any]]:
        with rasterio.open(raster_path) as source:
            plan = build_detection_shard_plan(
                source.width,
                source.height,
                self.config.tile_size,
                self.config.overlap,
                tiles_per_shard=self.config.maximum_tiles,
                maximum_tiles=self.config.maximum_tiles,
                maximum_shards=1,
            )
        return self.run_shard(raster_path, plan, 0)


def _workspace_path(run_id: str) -> Path:
    root = Path(os.getenv("DRONEAI_STAGE_WORK_ROOT", "/work")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return cast(Path, safe_child_path(root, run_id, field_name="stage run id"))


def _restore_raster_workspace(
    context: StageExecutionContext,
    control: StageExecutionControl,
    workspace: Path,
    *,
    selective_restore: bool = False,
) -> tuple[Path, RestoredWorkspace]:
    if len(context.inputs) != 1 or context.inputs[0].kind != "raster_product_workspace":
        raise ValueError("Detection requires exactly one raster product workspace")
    source = context.inputs[0]
    manifest_key = source.metadata.get("manifest_key")
    ortho_relative = source.metadata.get("ortho_file")
    if not isinstance(manifest_key, str) or not manifest_key:
        raise ValueError("Raster workspace artifact has no manifest key")
    if not isinstance(ortho_relative, str) or not ortho_relative:
        raise ValueError("Raster workspace artifact has no orthomosaic path")
    if selective_restore:
        restored = restore_workspace_measured(
            manifest_key,
            workspace,
            source.checksum_sha256,
            cancellation_check=control.raise_if_cancelled,
            selection=WorkspaceSelection(paths=frozenset({ortho_relative})),
        )
    else:
        restored = restore_workspace_measured(
            manifest_key,
            workspace,
            source.checksum_sha256,
            cancellation_check=control.raise_if_cancelled,
        )
    raster_path = resolve_workspace_path(workspace, ortho_relative)
    if not raster_path.is_file():
        raise FileNotFoundError(raster_path)
    return raster_path, restored


def _raster_metadata_for_plan(
    raster_path: Path,
    config: DetectionStageConfig,
    plan: DetectionShardPlan,
) -> dict[str, Any]:
    if plan.tile_size != config.tile_size or plan.overlap != config.overlap:
        raise ValueError("Detection shard plan does not match the stage configuration")
    with rasterio.open(raster_path) as source:
        if source.width != plan.width or source.height != plan.height:
            raise ValueError("Detection shard plan does not match the raster dimensions")
        return {
            "width": source.width,
            "height": source.height,
            "crs": source.crs.to_string() if source.crs else None,
            "transform": list(source.transform.to_gdal()),
            "tile_size": plan.tile_size,
            "tile_overlap": plan.overlap,
            "tile_count": plan.tile_count,
            "planned_inference_pixels": plan.planned_inference_pixels,
            "pixel_amplification_ratio": plan.pixel_amplification_ratio,
            "plan_checksum_sha256": plan.checksum_sha256,
            "shard_count": plan.shard_count,
        }


def run_detection_stage(
    context: StageExecutionContext,
    control: StageExecutionControl,
    *,
    aggregate: DetectionAggregate | None = None,
    plan: DetectionShardPlan | None = None,
) -> StageExecutionResult:
    workspace = _workspace_path(context.run_id)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    try:
        selective_restore = artifact_selective_restore_enabled()
        raster_path, restored = _restore_raster_workspace(
            context,
            control,
            workspace,
            selective_restore=selective_restore,
        )
        config = DetectionStageConfig.from_context(context)
        if aggregate is None:
            if plan is not None:
                raise ValueError("Monolithic detection must not receive a shard plan")
            raw, model_manifest, raster_metadata = DetectionStageRunner(
                context,
                control,
                workspace,
                config,
            ).run(raster_path)
        else:
            if plan is None:
                raise ValueError("Detection finalization requires its shard plan")
            if (
                aggregate.shard_count != plan.shard_count
                or aggregate.tile_count != plan.tile_count
            ):
                raise ValueError("Detection aggregate does not match its shard plan")
            raw = list(aggregate.raw_detections)
            model_manifest = aggregate.model_manifest
            raster_metadata = _raster_metadata_for_plan(raster_path, config, plan)
        detections = dedupe_mission_detections(raw)
        collection = detections_feature_collection(
            detections,
            geotransform=cast(list[float], raster_metadata["transform"]),
            source_crs=cast(str | None, raster_metadata["crs"]),
            vol_id=context.vol_id,
        )
        collection["properties"].update(
            {
                "backend": config.backend,
                "model_manifest": model_manifest,
                "raw_detection_count": len(raw),
                "deduplicated_detection_count": len(detections),
                "tile_count": raster_metadata["tile_count"],
            }
        )
        result_dir = workspace / ".droneai" / "detection"
        raw_path = result_dir / "detections.json"
        geojson_path = result_dir / "detections.geojson"
        atomic_write_json(
            raw_path,
            {
                "schema_version": 1,
                "model_manifest": model_manifest,
                "raster": raster_metadata,
                "detections": detections,
            },
        )
        atomic_write_json(geojson_path, collection)
        control.raise_if_cancelled()
        prefix = (
            f"missions/{context.vol_id}/stage-runs/"
            f"{context.run_id}/detection-workspace"
        )
        if artifact_manifest_v2_write_enabled():
            source = context.inputs[0]
            manifest_key = source.metadata.get("manifest_key")
            if not isinstance(manifest_key, str) or not manifest_key:
                raise ValueError("Raster workspace artifact has no manifest key")
            published = publish_workspace_v2(
                workspace,
                prefix,
                default_role="detection-workspace",
                role_overrides={
                    raw_path.relative_to(workspace).as_posix(): "detection-records",
                    geojson_path.relative_to(workspace).as_posix(): (
                        "detection-features"
                    ),
                },
                parents=(
                    ManifestParent(
                        artifact_id=source.artifact_id,
                        manifest_key=manifest_key,
                        checksum_sha256=source.checksum_sha256,
                    ),
                ),
                allow_partial_workspace=selective_restore,
                cancellation_check=control.raise_if_cancelled,
            )
        else:
            published = publish_workspace(
                workspace,
                prefix,
                cancellation_check=control.raise_if_cancelled,
            )
        return StageExecutionResult(
            kind="detection_workspace",
            uri=published.uri,
            checksum_sha256=published.checksum_sha256,
            size_bytes=published.size_bytes,
            metadata={
                "manifest_key": published.manifest_key,
                "file_count": published.file_count,
                "detections_file": raw_path.relative_to(workspace).as_posix(),
                "geojson_file": geojson_path.relative_to(workspace).as_posix(),
                "feature_count": len(collection["features"]),
            },
            quality_metrics={
                "tile_count": raster_metadata["tile_count"],
                "raw_detection_count": len(raw),
                "deduplicated_detection_count": len(detections),
                "geolocated_feature_count": len(collection["features"]),
                "planned_inference_pixels": raster_metadata[
                    "planned_inference_pixels"
                ],
                "pixel_amplification_ratio": raster_metadata[
                    "pixel_amplification_ratio"
                ],
            },
            provenance={
                "stage_adapter": "detection-v1",
                "backend": config.backend,
                "model_manifest": model_manifest,
                "tile_plan": {
                    "plan_checksum_sha256": raster_metadata[
                        "plan_checksum_sha256"
                    ],
                    "shard_count": raster_metadata["shard_count"],
                    "tile_count": raster_metadata["tile_count"],
                    "tile_size": raster_metadata["tile_size"],
                    "tile_overlap": raster_metadata["tile_overlap"],
                    "planned_inference_pixels": raster_metadata[
                        "planned_inference_pixels"
                    ],
                    "pixel_amplification_ratio": raster_metadata[
                        "pixel_amplification_ratio"
                    ],
                },
                "workspace_transfer": workspace_transfer_provenance(
                    published,
                    restored,
                ),
                "workspace_materialization": {
                    "mode": "selective" if selective_restore else "full",
                    "selected_paths": [
                        context.inputs[0].metadata["ortho_file"]
                    ]
                    if selective_restore
                    else [],
                },
            },
        )
    finally:
        if workspace.exists():
            shutil.rmtree(workspace)
