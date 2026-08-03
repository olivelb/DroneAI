"""Bounded sparse mapping engine selection and quality promotion."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from alignment_support import (
    build_mapping_command,
    caspar_compatibility,
    choose_auto_fallback,
    choose_primary_engine,
)
from pipeline_support import inspect_sparse_quality
from runtime_support import run_command

from . import runtime
from .contracts import PipelinePreparation


@dataclass(frozen=True)
class SparseQualityGate:
    total_images: int
    minimum_registered_images: int
    maximum_reprojection_error: float
    minimum_track_length: float

    @classmethod
    def from_preparation(cls, preparation: PipelinePreparation) -> SparseQualityGate:
        params = preparation.params
        minimum_registration_ratio = float(params["minimum_registration_ratio"])
        return cls(
            total_images=len(preparation.images),
            minimum_registered_images=max(
                3,
                math.ceil(len(preparation.images) * minimum_registration_ratio),
            ),
            maximum_reprojection_error=float(
                params["maximum_mean_reprojection_error_px"]
            ),
            minimum_track_length=float(params["minimum_median_track_length"]),
        )

    def accepts(self, quality: Mapping[str, Any]) -> bool:
        reprojection_error = quality["mean_reprojection_error_px"]
        track_length = quality["median_track_length"]
        return (
            quality["registered_images"] >= self.minimum_registered_images
            and quality["points3D"] > 0
            and reprojection_error is not None
            and reprojection_error <= self.maximum_reprojection_error
            and track_length is not None
            and track_length >= self.minimum_track_length
        )


@dataclass(frozen=True)
class MappingBudget:
    timeout_seconds: float
    started_at: float

    @classmethod
    def start(cls, timeout_seconds: float) -> MappingBudget:
        return cls(timeout_seconds=timeout_seconds, started_at=time.monotonic())

    def remaining(self, now: float | None = None) -> float:
        current_time = time.monotonic() if now is None else now
        remaining = self.timeout_seconds - (current_time - self.started_at)
        if remaining <= 0:
            raise TimeoutError(
                f"The shared {self.timeout_seconds:.0f}s mapping budget is exhausted."
            )
        return remaining


def run_sparse_mapping(
    preparation: PipelinePreparation,
    vol_id: str,
    *,
    gravity_available: bool,
    match_counts: Mapping[str, Any],
) -> None:
    params = preparation.params
    db_path = preparation.db_path
    sparse_path = preparation.sparse_path
    os.makedirs(sparse_path, exist_ok=True)

    requested_engine = str(params.get("alignment_engine", "auto")).lower()
    if requested_engine not in {"auto", "glomap", "caspar", "ceres"}:
        raise ValueError(f"Unsupported alignment engine: {requested_engine}")

    quality_gate = SparseQualityGate.from_preparation(preparation)
    budget = MappingBudget.start(float(params["mapping_timeout_seconds"]))

    def run_mapping_engine(engine: str, progress: int) -> None:
        engine_timeout = budget.remaining()
        command = build_mapping_command(
            engine,
            database_path=db_path,
            image_path=preparation.clean_images_dir,
            output_path=sparse_path,
            gpu_index=preparation.ba_gpu_index,
            global_max_tracks=int(float(params["global_mapper_max_tracks"])),
            global_ba_iterations=int(float(params["global_mapper_ba_iterations"])),
            global_ceres_iterations=int(float(params["global_mapper_ceres_iterations"])),
            global_skip_retriangulation=bool(
                params.get("global_mapper_skip_retriangulation", True)
            ),
            global_random_seed=int(float(params["global_mapper_random_seed"])),
            global_ba_min_track_length=int(
                float(params["global_mapper_ba_min_track_length"])
            ),
            global_tri_complete_max_reproj_error=float(
                params["global_mapper_tri_complete_max_reproj_error"]
            ),
            global_tri_merge_max_reproj_error=float(
                params["global_mapper_tri_merge_max_reproj_error"]
            ),
            global_tri_min_angle=float(params["global_mapper_tri_min_angle"]),
            global_use_gravity=gravity_available,
        )
        runtime.report_mission_progress(
            vol_id,
            "MAPPING",
            progress,
            log=(
                f"Starting alignment engine={engine} with a {engine_timeout:.0f}s "
                "remaining shared time budget."
            ),
            details={
                "event": "alignment_engine_started",
                "engine": engine,
                "timeout_seconds": engine_timeout,
            },
        )
        run_command(
            command,
            vol_id,
            "MAPPING",
            progress,
            runtime.report_mission_progress,
            runtime.ensure_not_cancelled,
            timeout_seconds=engine_timeout,
        )

    primary_engine = choose_primary_engine(
        requested_engine,
        facade=preparation.facade_mode,
    )
    if primary_engine == "caspar":
        caspar_supported, camera_models = caspar_compatibility(db_path)
        if not caspar_supported:
            raise RuntimeError(
                "Caspar only supports PINHOLE and SIMPLE_RADIAL cameras; "
                f"database contains {sorted(camera_models)}."
            )

    primary_error: Exception | None = None
    try:
        run_mapping_engine(primary_engine, 45)
    except (RuntimeError, subprocess.CalledProcessError, TimeoutError) as error:
        primary_error = error
        if requested_engine != "auto":
            raise
        runtime.report_mission_progress(
            vol_id,
            "MAPPING",
            46,
            log=(
                f"Primary {primary_engine.upper()} attempt failed within its bounded budget: "
                f"{type(error).__name__}: {error}"
            ),
            details={
                "event": "alignment_engine_failed",
                "engine": primary_engine,
                "error": str(error),
            },
        )

    sparse_model_path = os.path.join(sparse_path, "0")
    quality = inspect_sparse_quality(sparse_model_path)
    registered_images = quality["registered_images"]
    sparse_points = quality["points3D"]
    primary_usable = primary_error is None and quality_gate.accepts(quality)
    runtime.report_mission_progress(
        vol_id,
        "MAPPING",
        46,
        log=(
            f"{primary_engine} registered {registered_images}/{quality_gate.total_images} "
            f"images with {sparse_points} points; "
            f"required={quality_gate.minimum_registered_images}."
        ),
        details={
            "event": "alignment_quality_gate",
            "engine": primary_engine,
            "registered_images": registered_images,
            "total_images": quality_gate.total_images,
            "points3D": sparse_points,
            "minimum_registered_images": quality_gate.minimum_registered_images,
            "maximum_mean_reprojection_error_px": quality_gate.maximum_reprojection_error,
            "minimum_median_track_length": quality_gate.minimum_track_length,
            **quality,
            "accepted": primary_usable,
        },
    )

    if not primary_usable and requested_engine == "auto":
        caspar_supported, camera_models = caspar_compatibility(db_path)
        fallback_engine = (
            "ceres"
            if primary_engine == "caspar"
            else choose_auto_fallback(camera_models)
        )
        runtime.report_mission_progress(
            vol_id,
            "MAPPING",
            47,
            log=(
                f"{primary_engine.upper()} quality gate failed. Reusing the existing features "
                f"and {match_counts['two_view_geometries']} verified pairs with "
                f"incremental {fallback_engine.upper()} BA. Camera models: {sorted(camera_models)}."
            ),
            details={
                "event": "alignment_fallback",
                "from_engine": primary_engine,
                "to_engine": fallback_engine,
                "caspar_supported": caspar_supported,
                "camera_models": sorted(camera_models),
            },
        )
        shutil.rmtree(sparse_path, ignore_errors=True)
        os.makedirs(sparse_path, exist_ok=True)
        run_mapping_engine(fallback_engine, 48)
        quality = inspect_sparse_quality(sparse_model_path)
        registered_images = quality["registered_images"]
        sparse_points = quality["points3D"]
        primary_engine = fallback_engine

    if not quality_gate.accepts(quality):
        raise RuntimeError(
            "Sparse reconstruction failed the alignment quality gate "
            f"after {primary_engine}: registered_images={registered_images}/"
            f"{quality_gate.total_images}, required={quality_gate.minimum_registered_images}, "
            f"points3D={sparse_points}, mean_reprojection_error_px="
            f"{quality['mean_reprojection_error_px']}, "
            f"median_track_length={quality['median_track_length']}. "
            "Exhaustive matching and unbounded CPU bundle adjustment are intentionally disabled."
        )
