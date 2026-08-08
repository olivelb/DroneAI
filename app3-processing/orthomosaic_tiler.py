"""Orthomosaic tiling and durable tile publication.

The Kafka worker delegates the complete raster-to-tile workflow to this
service.  Database journaling happens before an event is published so a
different worker replica can safely resume an interrupted analysis.
"""

import json
import os
import shutil
from datetime import datetime, UTC
from itertools import product
from pathlib import Path

import numpy as np
import rasterio
from processing_core import build_tile_starts
from rasterio.warp import transform_bounds
from rasterio.windows import Window

from shared import storage
from shared.database import (
    AIAnalysisRun,
    AIAnalysisTile,
    Mission,
    count_received_tiles,
    get_or_create_mission,
    get_session,
)
from shared.event_contracts import deterministic_event_id, make_event
from shared.pipeline_params import normalize_ai_backend


class OrthomosaicTiler:
    """Create JPEG inference tiles and publish their durable work journal."""

    def __init__(
        self,
        *,
        producer,
        tile_topic,
        is_cancelled,
        report_progress,
        logger,
    ):
        self.producer = producer
        self.tile_topic = tile_topic
        self.is_cancelled = is_cancelled
        self.report_progress = report_progress
        self.logger = logger

    @staticmethod
    def _workspace(vol_id, analysis_run_id):
        workspace_id = analysis_run_id or "pipeline"
        return Path("/tmp/processing") / vol_id / workspace_id

    def _cleanup_tiles(self, tiles_dir):
        for tile_path in tiles_dir.glob("tile_*.jpg"):
            try:
                tile_path.unlink()
            except OSError as error:
                self.logger.warning(
                    "Failed to remove stale tile %s: %s",
                    tile_path,
                    error,
                )

    def _download(self, ortho_s3_key, local_ortho, vol_id):
        try:
            storage.download_file(ortho_s3_key, local_ortho)
        except Exception as error:
            self.report_progress(
                vol_id,
                "ERROR",
                0,
                status="error",
                log=f"Failed to download orthomosaic from S3: {error}",
            )
            raise

    @staticmethod
    def _build_plan(src, tile_size):
        overlap = max(
            0,
            min(
                tile_size // 2,
                int(os.getenv("TILE_OVERLAP", str(tile_size // 4))),
            ),
        )
        x_starts = build_tile_starts(src.width, tile_size, overlap)
        y_starts = build_tile_starts(src.height, tile_size, overlap)
        return {
            "transform": list(src.transform.to_gdal()) if src.transform else None,
            "crs": src.crs.to_string() if src.crs else "unknown",
            "width": src.width,
            "height": src.height,
            "tile_size": tile_size,
            "overlap": overlap,
            "x_starts": x_starts,
            "y_starts": y_starts,
            "total_tiles": len(x_starts) * len(y_starts),
        }

    @staticmethod
    def _public_metadata(plan):
        return {key: value for key, value in plan.items() if key not in {"x_starts", "y_starts", "total_tiles"}}

    def _persist_plan(
        self,
        *,
        vol_id,
        ortho_s3_key,
        analysis_run_id,
        analysis_attempt,
        plan,
    ):
        with get_session() as session:
            mission = get_or_create_mission(session, vol_id)
            metadata = self._public_metadata(plan)
            if analysis_run_id:
                run = (
                    session.query(AIAnalysisRun)
                    .filter(
                        AIAnalysisRun.run_id == analysis_run_id,
                        AIAnalysisRun.vol_id == vol_id,
                    )
                    .with_for_update()
                    .one()
                )
                if run.status == "cancelled":
                    return False
                if int(run.retry_count or 0) != int(analysis_attempt):
                    return False
                now = datetime.now(UTC)
                run.status = "tiling"
                run.phase = "tiling"
                run.total_tiles = plan["total_tiles"]
                run.tiling_metadata = metadata
                run.heartbeat_at = now
                run.started_at = run.started_at or now
                run.error_message = None
                return True

            if int(mission.retry_count or 0) != int(analysis_attempt):
                return False
            mission.ortho_s3_key = ortho_s3_key
            mission.total_tiles = plan["total_tiles"]
            mission.tiles_received = count_received_tiles(session, vol_id)
            mission.tiling_metadata = metadata
            mission.aggregation_status = "collecting"
            return True

    @staticmethod
    def _write_jpeg(src, window, tile_path):
        tile_data = src.read(window=window)
        if src.count > 3:
            tile_data = tile_data[:3, :, :]
        elif src.count == 1:
            tile_data = np.repeat(tile_data, 3, axis=0)

        tile_meta = src.meta.copy()
        tile_meta.update(
            {
                "driver": "JPEG",
                "height": window.height,
                "width": window.width,
                "transform": src.window_transform(window),
                "count": 3,
            }
        )
        with rasterio.open(tile_path, "w", **tile_meta) as destination:
            destination.write(tile_data)

    @staticmethod
    def _wgs84_bounds(src, window):
        if not src.crs:
            return None
        native_bounds = rasterio.windows.bounds(window, src.transform)
        return list(
            transform_bounds(
                src.crs,
                "EPSG:4326",
                *native_bounds,
                densify_pts=5,
            )
        )

    @staticmethod
    def _journal_analysis_tile(
        *,
        analysis_run_id,
        analysis_attempt,
        tile_index,
        tile_s3_key,
        x,
        y,
        window,
        bounds,
    ):
        with get_session() as session:
            run = session.query(AIAnalysisRun).filter(AIAnalysisRun.run_id == analysis_run_id).with_for_update().one()
            if run.status == "cancelled" or int(run.retry_count or 0) != int(analysis_attempt):
                return False
            receipt = (
                session.query(AIAnalysisTile)
                .filter(
                    AIAnalysisTile.analysis_run_id == run.id,
                    AIAnalysisTile.tile_index == tile_index,
                )
                .first()
            )
            if receipt is None:
                session.add(
                    AIAnalysisTile(
                        analysis_run_id=run.id,
                        tile_index=tile_index,
                        tile_s3_key=tile_s3_key,
                        offset_x=x,
                        offset_y=y,
                        width=int(window.width),
                        height=int(window.height),
                        bounds_wgs84=bounds,
                        attempts=1,
                    )
                )
            else:
                receipt.tile_s3_key = tile_s3_key
                receipt.bounds_wgs84 = bounds
                if receipt.status != "completed":
                    receipt.status = "queued"
                    receipt.attempts = max(1, receipt.attempts)
            run.heartbeat_at = datetime.now(UTC)
            return True

    @staticmethod
    def _tile_event(
        *,
        vol_id,
        analysis_run_id,
        analysis_attempt,
        tile_index,
        tile_s3_key,
        x,
        y,
        plan,
        options,
    ):
        payload = {
            "vol_id": vol_id,
            "tile_index": tile_index,
            "tile_s3_key": tile_s3_key,
            "offset_x": x,
            "offset_y": y,
            "total_tiles": plan["total_tiles"],
            "ortho_transform": plan["transform"],
            "ortho_crs": plan["crs"],
            "analysis_run_id": analysis_run_id,
            **options,
        }
        return make_event(
            "image_tile",
            payload,
            event_id=deterministic_event_id(
                "image_tile",
                vol_id,
                analysis_run_id or "pipeline",
                tile_index,
                analysis_attempt,
            ),
            correlation_id=analysis_run_id or vol_id,
            attempt=analysis_attempt,
        )

    def _create_tile(
        self,
        *,
        src,
        tiles_dir,
        tiles_s3_prefix,
        vol_id,
        analysis_run_id,
        analysis_attempt,
        tile_index,
        x,
        y,
        plan,
        options,
    ):
        window = Window(
            x,
            y,
            min(plan["tile_size"], src.width - x),
            min(plan["tile_size"], src.height - y),
        )
        tile_filename = f"tile_{tile_index}.jpg"
        tile_path = tiles_dir / tile_filename
        self._write_jpeg(src, window, tile_path)
        tile_s3_key = f"{tiles_s3_prefix}/{tile_filename}"
        try:
            storage.upload_file(tile_path, tile_s3_key)
        except Exception as error:
            raise RuntimeError(f"Failed to upload tile {tile_filename}: {error}") from error

        if analysis_run_id and not self._journal_analysis_tile(
            analysis_run_id=analysis_run_id,
            analysis_attempt=analysis_attempt,
            tile_index=tile_index,
            tile_s3_key=tile_s3_key,
            x=x,
            y=y,
            window=window,
            bounds=self._wgs84_bounds(src, window),
        ):
            return False
        event = self._tile_event(
            vol_id=vol_id,
            analysis_run_id=analysis_run_id,
            analysis_attempt=analysis_attempt,
            tile_index=tile_index,
            tile_s3_key=tile_s3_key,
            x=x,
            y=y,
            plan=plan,
            options=options,
        )
        self.producer.produce(
            self.tile_topic,
            key=f"{vol_id}_{tile_index}",
            value=json.dumps(event),
        )
        return True

    @staticmethod
    def _complete_database_state(
        vol_id,
        analysis_run_id,
        analysis_attempt,
        tile_count,
    ):
        with get_session() as session:
            if analysis_run_id:
                run = (
                    session.query(AIAnalysisRun).filter(AIAnalysisRun.run_id == analysis_run_id).with_for_update().one()
                )
                if int(run.retry_count or 0) != int(analysis_attempt):
                    return False
                run.total_tiles = tile_count
                run.status = "running"
                run.phase = "detecting"
                run.heartbeat_at = datetime.now(UTC)
                run.progress = min(
                    99,
                    int(100 * run.tiles_completed / max(run.total_tiles, 1)),
                )
                return True
            mission = session.query(Mission).filter(Mission.vol_id == vol_id).with_for_update().one()
            if int(mission.retry_count or 0) != int(analysis_attempt):
                return False
            mission.total_tiles = tile_count
            return True

    def _mark_failed(
        self,
        vol_id,
        analysis_run_id,
        analysis_attempt,
        error,
    ):
        self.logger.exception("Failed to tile orthomosaic for %s", vol_id)
        message = f"Failed to tile orthomosaic: {error}"
        self.report_progress(
            vol_id,
            "ERROR",
            0,
            status="error",
            log=message,
        )
        if not analysis_run_id:
            return
        with get_session() as session:
            run = session.query(AIAnalysisRun).filter(AIAnalysisRun.run_id == analysis_run_id).with_for_update().first()
            if run is not None and run.status != "cancelled" and int(run.retry_count or 0) == int(analysis_attempt):
                run.status = "failed"
                run.phase = "tiling_failed"
                run.error_message = str(error)
                run.heartbeat_at = datetime.now(UTC)

    def _publish_tiles(
        self,
        *,
        local_ortho,
        tiles_dir,
        tiles_s3_prefix,
        ortho_s3_key,
        vol_id,
        analysis_run_id,
        analysis_attempt,
        tile_size,
        options,
    ):
        with rasterio.open(local_ortho) as src:
            plan = self._build_plan(src, tile_size)
            if not self._persist_plan(
                vol_id=vol_id,
                ortho_s3_key=ortho_s3_key,
                analysis_run_id=analysis_run_id,
                analysis_attempt=analysis_attempt,
                plan=plan,
            ):
                return None
            self.report_progress(
                vol_id,
                "TILING_START",
                0,
                log=(f"Writing {plan['total_tiles']} overlapping tiles (size={tile_size}, overlap={plan['overlap']})"),
            )
            tile_count = 0
            coordinates = product(plan["y_starts"], plan["x_starts"])
            for tile_index, (y, x) in enumerate(coordinates):
                if self.is_cancelled(
                    vol_id,
                    analysis_run_id,
                    analysis_attempt,
                ):
                    self.logger.info(
                        "Tiling cancelled mid-loop for %s",
                        vol_id,
                    )
                    return None
                if not self._create_tile(
                    src=src,
                    tiles_dir=tiles_dir,
                    tiles_s3_prefix=tiles_s3_prefix,
                    vol_id=vol_id,
                    analysis_run_id=analysis_run_id,
                    analysis_attempt=analysis_attempt,
                    tile_index=tile_index,
                    x=x,
                    y=y,
                    plan=plan,
                    options=options,
                ):
                    return None
                tile_count = tile_index + 1
                if tile_count % 10 == 0:
                    progress = int(tile_count / plan["total_tiles"] * 100)
                    self.report_progress(
                        vol_id,
                        "TILING_IN_PROGRESS",
                        progress,
                    )
        return tile_count

    def slice(
        self,
        ortho_s3_key,
        vol_id,
        *,
        tile_size=1024,
        classes=None,
        ai_confidence=0.3,
        ai_backend="yolo",
        ai_model_variant="yolo26l",
        sam_prompt="car",
        analysis_run_id=None,
        analysis_attempt=0,
    ):
        """Tile one orthomosaic and publish inference work."""

        workspace = self._workspace(vol_id, analysis_run_id)
        local_ortho = workspace / "orthomosaic.tif"
        tiles_dir = workspace / "tiles"
        tiles_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_tiles(tiles_dir)
        try:
            self._download(ortho_s3_key, local_ortho, vol_id)
            tiles_s3_prefix = (
                f"missions/{vol_id}/analyses/{analysis_run_id}/tiles" if analysis_run_id else f"missions/{vol_id}/tiles"
            )
            options = {
                "ai_backend": normalize_ai_backend(ai_backend),
                "ai_model_variant": ai_model_variant,
                "sam_prompt": sam_prompt,
                "classes": classes or ["car"],
                "ai_confidence": ai_confidence,
            }
            self.report_progress(vol_id, "TILING_START", 0)
            tile_count = self._publish_tiles(
                local_ortho=local_ortho,
                tiles_dir=tiles_dir,
                tiles_s3_prefix=tiles_s3_prefix,
                ortho_s3_key=ortho_s3_key,
                vol_id=vol_id,
                analysis_run_id=analysis_run_id,
                analysis_attempt=analysis_attempt,
                tile_size=tile_size,
                options=options,
            )
            if tile_count is None:
                return
            if not self._complete_database_state(
                vol_id,
                analysis_run_id,
                analysis_attempt,
                tile_count,
            ):
                return
            if self.producer.flush():
                raise RuntimeError("one or more tile events were not delivered")
            self.report_progress(
                vol_id,
                "TILING_DONE",
                100,
                status="success",
            )
            self.logger.info(
                "Orthomosaic tiled into %s images for %s",
                tile_count,
                vol_id,
            )
        except Exception as error:
            self._mark_failed(
                vol_id,
                analysis_run_id,
                analysis_attempt,
                error,
            )
            raise
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
