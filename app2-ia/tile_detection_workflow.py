"""Per-tile AI workflow independent from Kafka consumer lifecycle."""

from __future__ import annotations

import gc
import logging
import shutil
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast

from pyproj import Transformer

from detection_core import DetectionRecord, run_yolo_detection
from sam3_backend import JsonObject, Sam3Backend
from shared import storage
from shared.event_contracts import deterministic_event_id, make_event
from shared.kafka_reliability import publish_json
from shared.pipeline_params import normalize_ai_backend


class Producer(Protocol):
    def produce(self, topic: str, *, key: str, value: str) -> None: ...

    def flush(self) -> int: ...


class CancellationRegistry(Protocol):
    def is_cancelled(
        self,
        vol_id: str,
        run_id: str | None = None,
        attempt: int = 0,
    ) -> bool: ...

    def clear(
        self,
        vol_id: str,
        run_id: str | None = None,
        attempt: int = 0,
    ) -> None: ...


class ProgressReporter(Protocol):
    def __call__(
        self,
        vol_id: str,
        step: str,
        progress: int,
        status: str = "processing",
        log: str | None = None,
    ) -> None: ...


class MissionStats(TypedDict):
    processed: int
    detections: int
    total_tiles: int


def transform_detection_coordinates(
    ortho_transform: list[float] | None,
    transformer: Any | None,
    gx: float,
    gy: float,
) -> tuple[float | None, float | None]:
    if not ortho_transform or transformer is None:
        return None, None
    c, a, b, f, d, e = ortho_transform
    proj_x = c + a * gx + b * gy
    proj_y = f + d * gx + e * gy
    lon, lat = transformer.transform(proj_x, proj_y)
    return float(lon), float(lat)


def translate_segment(
    segment: list[list[float]],
    offset_x: float,
    offset_y: float,
) -> list[list[float]]:
    return [[float(point[0] + offset_x), float(point[1] + offset_y)] for point in segment]


class TileDetectionWorkflow:
    """Download, infer, geolocate and publish one durable tile result."""

    def __init__(
        self,
        *,
        producer: Producer,
        output_topic: str,
        cancellation_registry: CancellationRegistry,
        progress_reporter: ProgressReporter,
        sam3_backend: Sam3Backend,
        logger: logging.Logger,
        workspace_root: Path = Path("/tmp/ia_tiles"),
    ) -> None:
        self.producer = producer
        self.output_topic = output_topic
        self.cancellation_registry = cancellation_registry
        self.progress_reporter = progress_reporter
        self.sam3_backend = sam3_backend
        self.logger = logger
        self.workspace_root = workspace_root
        self.mission_stats: dict[tuple[str, int], MissionStats] = {}

    def run_detection(
        self,
        tile_path: str,
        tile_info: JsonObject,
    ) -> tuple[list[DetectionRecord], JsonObject]:
        backend = normalize_ai_backend(cast(str | None, tile_info.get("ai_backend")))
        requested_confidence = float(tile_info.get("ai_confidence", 0.3))
        requested_classes = cast(
            list[str],
            tile_info.get("classes") or ["car"],
        )
        if backend == "sam3":
            return self.sam3_backend.run(
                tile_path,
                self.sam3_backend.resolve_prompt(tile_info),
                requested_confidence,
            )
        return run_yolo_detection(
            tile_path,
            requested_classes,
            requested_confidence,
            cast(str | None, tile_info.get("ai_model_variant")),
        )

    def _workspace(
        self,
        vol_id: str,
        analysis_run_id: str | None,
    ) -> Path:
        return self.workspace_root / vol_id / (analysis_run_id or "pipeline")

    def _cancelled(
        self,
        *,
        vol_id: str,
        analysis_run_id: str | None,
        analysis_attempt: int,
        stats_key: tuple[str, int],
        workspace: Path,
    ) -> bool:
        if not self.cancellation_registry.is_cancelled(
            vol_id,
            analysis_run_id,
            analysis_attempt,
        ):
            return False
        shutil.rmtree(workspace, ignore_errors=True)
        self.mission_stats.pop(stats_key, None)
        return True

    def _geolocate(
        self,
        detections_for_tile: list[DetectionRecord],
        *,
        tile_info: JsonObject,
        vol_id: str,
        offset_x: float,
        offset_y: float,
    ) -> list[DetectionRecord]:
        ortho_transform = cast(
            list[float] | None,
            tile_info.get("ortho_transform"),
        )
        ortho_crs = cast(str | None, tile_info.get("ortho_crs"))
        transformer: Any | None = None
        if ortho_crs and ortho_crs != "unknown":
            try:
                transformer = Transformer.from_crs(
                    ortho_crs,
                    "EPSG:4326",
                    always_xy=True,
                )
            except Exception as error:
                self.logger.warning(
                    "Failed to create CRS transformer for %s: %s",
                    vol_id,
                    error,
                )

        detections: list[DetectionRecord] = []
        for detection in detections_for_tile:
            gx = float(detection["center_x"]) + offset_x
            gy = float(detection["center_y"]) + offset_y
            geo_lon: float | None = None
            geo_lat: float | None = None
            if ortho_transform and transformer:
                try:
                    geo_lon, geo_lat = transform_detection_coordinates(
                        ortho_transform,
                        transformer,
                        gx,
                        gy,
                    )
                except Exception as error:
                    self.logger.debug(
                        "Failed to geolocate detection for %s tile %s: %s",
                        vol_id,
                        tile_info["tile_index"],
                        error,
                    )

            global_segment = translate_segment(
                cast(list[list[float]], detection["polygon"]),
                offset_x,
                offset_y,
            )
            detections.append(
                {
                    "vol_id": vol_id,
                    "global_pixel_x": gx,
                    "global_pixel_y": gy,
                    "geo_lon": geo_lon,
                    "geo_lat": geo_lat,
                    "confidence": round(float(detection["confidence"]), 2),
                    "class_id": int(detection["class_id"]),
                    "class_name": detection["class_name"],
                    "segment": global_segment,
                }
            )
        return detections

    def _publish_result(
        self,
        *,
        tile_info: JsonObject,
        vol_id: str,
        analysis_run_id: str | None,
        analysis_attempt: int,
        detections: list[DetectionRecord],
        attempt: JsonObject,
    ) -> None:
        tile_index = int(tile_info["tile_index"])
        tile_result = make_event(
            "tile_detection",
            {
                "vol_id": vol_id,
                "tile_index": tile_index,
                "detections": detections,
                "analysis_run_id": analysis_run_id,
                "model_manifest": attempt["model_manifest"],
            },
            event_id=deterministic_event_id(
                "tile_detection",
                vol_id,
                analysis_run_id or "pipeline",
                tile_index,
                analysis_attempt,
            ),
            correlation_id=cast(str | None, tile_info.get("correlation_id")),
            causation_id=cast(str | None, tile_info.get("event_id")),
            attempt=analysis_attempt,
        )
        publish_json(
            self.producer,
            self.output_topic,
            tile_result,
            key=vol_id,
        )

    def _report_tile(
        self,
        *,
        tile_info: JsonObject,
        vol_id: str,
        stats: MissionStats,
        detections: list[DetectionRecord],
        attempt: JsonObject,
    ) -> None:
        total = stats["total_tiles"] or stats["processed"]
        progress = min(
            99,
            int((stats["processed"] / max(total, 1)) * 100),
        )
        if detections:
            self.progress_reporter(
                vol_id,
                "DETECTING",
                progress,
                log=(f"Tile {tile_info['tile_index']} produced {len(detections)} detections via {attempt['label']}"),
            )
        elif stats["processed"] == 1 or stats["processed"] % 10 == 0:
            self.progress_reporter(
                vol_id,
                "DETECTING",
                progress,
                log=(
                    f"Processed {stats['processed']}/{total} tiles, "
                    f"detections={stats['detections']} ({attempt['label']})"
                ),
            )

    def _complete(
        self,
        *,
        vol_id: str,
        analysis_run_id: str | None,
        analysis_attempt: int,
        total_tiles: int,
        stats_key: tuple[str, int],
        stats: MissionStats,
        workspace: Path,
    ) -> None:
        if not total_tiles or stats["processed"] < total_tiles:
            return
        summary = f"IA finished {stats['processed']} tiles with {stats['detections']} detections"
        self.progress_reporter(
            vol_id,
            "DETECTING",
            100,
            status="success",
            log=summary,
        )
        self.mission_stats.pop(stats_key, None)
        self.cancellation_registry.clear(
            vol_id,
            analysis_run_id,
            analysis_attempt,
        )
        shutil.rmtree(workspace, ignore_errors=True)
        gc.collect()
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def process_tile(self, tile_info: JsonObject) -> None:
        vol_id = cast(str, tile_info["vol_id"])
        analysis_run_id = cast(
            str | None,
            tile_info.get("analysis_run_id"),
        )
        analysis_attempt = int(tile_info.get("attempt", 0))
        stats_key = (analysis_run_id or vol_id, analysis_attempt)
        total_tiles = int(tile_info.get("total_tiles", 0) or 0)
        workspace = self._workspace(vol_id, analysis_run_id)
        workspace.mkdir(parents=True, exist_ok=True)
        if self._cancelled(
            vol_id=vol_id,
            analysis_run_id=analysis_run_id,
            analysis_attempt=analysis_attempt,
            stats_key=stats_key,
            workspace=workspace,
        ):
            return

        tile_s3_key = str(tile_info.get("tile_s3_key") or tile_info.get("tile_path") or "")
        tile_path = workspace / Path(tile_s3_key).name
        try:
            storage.download_file(tile_s3_key, tile_path)
        except Exception as error:
            self.progress_reporter(
                vol_id,
                "ERROR",
                0,
                status="error",
                log=(f"Failed to download tile from S3: {tile_s3_key} — {error}"),
            )
            raise

        stats = self.mission_stats.setdefault(
            stats_key,
            {
                "processed": 0,
                "detections": 0,
                "total_tiles": total_tiles,
            },
        )
        if total_tiles:
            stats["total_tiles"] = total_tiles

        detections_for_tile, attempt = self.run_detection(
            str(tile_path),
            tile_info,
        )
        detections = self._geolocate(
            detections_for_tile,
            tile_info=tile_info,
            vol_id=vol_id,
            offset_x=float(tile_info["offset_x"]),
            offset_y=float(tile_info["offset_y"]),
        )
        self._publish_result(
            tile_info=tile_info,
            vol_id=vol_id,
            analysis_run_id=analysis_run_id,
            analysis_attempt=analysis_attempt,
            detections=detections,
            attempt=attempt,
        )
        stats["processed"] += 1
        stats["detections"] += len(detections)
        self._report_tile(
            tile_info=tile_info,
            vol_id=vol_id,
            stats=stats,
            detections=detections,
            attempt=attempt,
        )
        self._complete(
            vol_id=vol_id,
            analysis_run_id=analysis_run_id,
            analysis_attempt=analysis_attempt,
            total_tiles=total_tiles,
            stats_key=stats_key,
            stats=stats,
            workspace=workspace,
        )
