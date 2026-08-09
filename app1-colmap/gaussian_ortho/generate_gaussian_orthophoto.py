"""
Main entry point for Gaussian Splatting orthophoto generation.

Provides `generate_gaussian_orthophoto()` with the same interface pattern
as `generate_true_orthophoto_pytorch()` from ortho_dsm.py, making it easy
to swap in from the existing pipeline.

Pipeline:
  1. Load COLMAP reconstruction + alignment transform
  2. Partition scene (VastGaussian, if m x n > 1 x 1)
  3. Train Gaussian model per cell via the selected headless backend
  4. Merge cell models
  5. Render orthographic TDOM (custom CUDA rasterisation via CuPy)
  6. Write GeoTIFF
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from shared.dronegs_profile import (
    DRONEGS_PRODUCTION_PROFILE_V1,
    DRONEGS_QUALIFICATION_POLICY_ID,
    effective_raster_profile,
)
from shared.facade_process import FACADE_PARAMETER_DEFAULTS

from .colmap_loader import (
    load_colmap_reconstruction,
    apply_sim3_to_points,
)
from .scene_info import build_scene_info
from .colmap_subset import (
    export_colmap_subset,
)
from gaussian_training import (
    DroneGSTuning,
    TrainingRequest,
    TrainingResult,
    evaluate_quality_canary,
    resolve_training_backend,
    write_quality_canary,
)
from gaussian_training.backends import (
    CancellationCheck,
    CheckpointCallback,
    TrainingBackend,
)
from gaussian_training.dataset_identity import compute_dataset_identity
from gaussian_training.manifest_contract import (
    load_run_manifest,
    manifest_matches_ply,
    validate_run_manifest,
)
from .partition import partition_scene
from .geo_writer import write_geotiff
from .exif_altitude import (
    extract_exif_altitudes,
    compute_colmap_scale,
    compute_colmap_scale_geodesic,
    compute_projected_geo_origin,
)
from .height_reference import georeference_height_map, georeference_raster_origin
from .colmap_loader import CameraInfo, PointCloud, Sim3Transform
from .partition import CellBounds
from .scene_info import SceneInfo

if TYPE_CHECKING:
    from .facade_frame import FacadeFrame
    from .gaussian_model import GaussianModel


class ProgressReport(Protocol):
    """Callback contract used by the COLMAP worker progress bridge."""

    def __call__(
        self,
        vol_id: str,
        step: str,
        progress: int,
        *,
        log: str,
    ) -> object: ...


type ModelFactory = Callable[..., "GaussianModel"]
type MergeModels = Callable[..., "GaussianModel"]


def _report(
    vol_id: str,
    step: str,
    progress: int,
    msg: str,
    report_fn: ProgressReport | None,
) -> None:
    if report_fn:
        report_fn(vol_id, step, progress, log=msg)
    else:
        print(f"[{step} {progress}%] {msg}")


def _facade_metadata_image_dirs(dense_path: str) -> list[str]:
    """Return likely image directories, preferring untouched source JPEGs.

    COLMAP's undistorter rewrites images under ``dense/images`` and those
    copies do not reliably retain DJI EXIF/XMP.  The sibling ``images``
    directory in a normal COLMAP workspace contains the staged originals and
    is therefore the authoritative source for GPS-baseline scale estimation.
    """

    dense = Path(dense_path)
    candidates = [dense.parent / "images", dense / "images"]
    unique = []
    for candidate in candidates:
        value = str(candidate)
        if candidate.is_dir() and value not in unique:
            unique.append(value)
    return unique


def _compute_facade_gps_scale(
    cameras: Sequence[CameraInfo],
    dense_path: str,
) -> tuple[float, str, str]:
    """Try source metadata first and fall back only when it is insufficient."""

    for images_dir in _facade_metadata_image_dirs(dense_path):
        scale, source = compute_colmap_scale_geodesic(cameras, images_dir)
        if source != "model-units":
            return scale, source, images_dir
    return 1.0, "model-units", os.path.join(dense_path, "images")


def _reusable_dronegs_result(
    request: TrainingRequest,
    *,
    trainer_binary_sha256: str,
) -> TrainingResult | None:
    """Return a previously promoted result only when its contract matches."""
    output = Path(request.output_path)
    manifest_path = output / "trainer_run.json"
    ply_path = output / "point_cloud.ply"
    if not (manifest_path.is_file() and ply_path.is_file()):
        return None
    try:
        manifest = load_run_manifest(manifest_path)
        validate_run_manifest(manifest)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    expected = {
        "iterations": request.iterations,
        "strategy": request.strategy,
        "sh_degree": request.sh_degree,
        "max_cap": request.max_cap,
        "resize_factor": request.resize_factor,
        "max_width": request.max_width,
        "tile_mode": request.tile_mode,
        "seed": request.seed,
        "profile_id": request.dronegs.profile_id,
        "optimizer_profile": request.dronegs.optimizer_profile,
        "pruning_policy": request.dronegs.pruning_policy,
        "raster_profile": request.dronegs.raster_profile,
        "effective_raster_profile": effective_raster_profile(
            request.dronegs.raster_profile,
            request.dronegs.optimizer_profile,
        ),
        "sh_degree_interval": request.dronegs.sh_degree_interval,
        "checkpoint_every": request.dronegs.checkpoint_every,
        "test_every": request.dronegs.test_every,
        "test_split": request.dronegs.test_split,
        "test_guard_percent": request.dronegs.test_guard_percent,
        "topology_cooldown_iterations": request.dronegs.topology_cooldown,
        "photometric_finish_iterations": request.dronegs.photometric_finish,
        "photometric_final_mse_percent": (request.dronegs.photometric_mse_percent),
    }
    parameters = manifest.get("parameters", {})
    if (
        manifest.get("contract_version") != 1
        or manifest.get("status") != "completed"
        or manifest.get("trainer_binary_sha256") != trainer_binary_sha256
        or manifest.get("dataset", {}).get("fingerprint") != request.dataset_fingerprint
        or any(parameters.get(key) != value for key, value in expected.items())
        or not manifest_matches_ply(manifest, ply_path)
    ):
        return None
    canary = evaluate_quality_canary(manifest, request.dronegs)
    write_quality_canary(output, canary)
    if canary["failed_metrics"]:
        raise RuntimeError("DroneGS quality canary failed: " + ", ".join(canary["failed_metrics"]))
    # The promoted PLY + manifest + canary are the durable result. Keeping the
    # much larger optimizer checkpoint after a successful recovery wastes disk.
    (output / "training.ckpt").unlink(missing_ok=True)
    return TrainingResult(
        backend="dronegs",
        ply_path=ply_path,
        manifest_path=manifest_path,
        effective_seed=request.seed,
    )


def _quarantine_incompatible_dronegs_output(
    output_path: str | Path,
) -> Path | None:
    """Move an incompatible result aside so retraining remains recoverable."""

    output = Path(output_path)
    if not output.is_dir() or not any(output.iterdir()):
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    quarantine_root = output.parent.parent / ".incompatible" / output.parent.name
    quarantine_root.mkdir(parents=True, exist_ok=True)
    candidate = quarantine_root / f"{output.name}-{stamp}"
    suffix = 1
    while candidate.exists():
        candidate = quarantine_root / f"{output.name}-{stamp}-{suffix}"
        suffix += 1
    output.rename(candidate)
    return candidate


@dataclass(frozen=True)
class GaussianOrthoConfig:
    """Validated inputs shared by the Gaussian orthophoto workflow stages."""

    dense_path: str
    ortho_file: str
    utm_crs: str | None
    vol_id: str
    transform_file: str | None
    report_fn: ProgressReport | None
    resolution: float
    iterations: int
    partition_m: int
    partition_n: int
    partition_overlap: float
    sh_degree: int
    fagk: bool
    checkpoint_dir: str
    data_factor: int
    max_width: int
    ortho_mip_filter_variance: float
    ortho_mip_filter_compensation: bool
    tile_mode: int
    cap_max: int
    filter_enabled: bool
    filter_max_scale: float
    filter_min_retained_ratio: float
    filter_dist_multiplier: float
    filter_opacity_threshold: float
    filter_needle_ratio: float
    filter_sor: bool
    filter_sor_sigma: float
    filter_cc: bool
    filter_z_floater: bool
    coverage_gate_enabled: bool
    coverage_grid_size: int
    coverage_min_valid_ratio: float
    coverage_cell_threshold: float
    coverage_min_covered_cells_ratio: float
    coverage_min_worst_cell_ratio: float
    coverage_min_camera_cell_ratio: float
    verbose: bool
    training_seed: int
    dronegs_profile_id: str
    dronegs_qualification_policy_id: str
    dronegs_optimizer_profile: str
    dronegs_pruning_policy: str
    dronegs_raster_profile: str
    dronegs_sh_degree_interval: int
    dronegs_topology_cooldown: int
    dronegs_photometric_finish: int
    dronegs_photometric_mse_percent: int
    dronegs_checkpoint_every: int
    dronegs_test_every: int
    dronegs_test_split: str
    dronegs_test_guard_percent: int
    dronegs_canary_min_psnr: float
    dronegs_canary_min_ssim: float
    cancellation_check: CancellationCheck | None
    checkpoint_callback: CheckpointCallback | None
    render_mode: str
    facade_scale_mode: str
    facade_meters_per_model_unit: float
    facade_frame_report: str | None
    facade_texture_max_incidence_deg: float
    facade_depth_iqr_multiplier: float
    facade_seed_max_reprojection_error: float
    facade_seed_min_track_length: int


@dataclass
class GaussianSceneState:
    train_cameras: list[CameraInfo]
    test_cameras: list[CameraInfo]
    registered_cameras: list[CameraInfo]
    point_cloud: PointCloud | None
    transform_data: Sim3Transform | None
    mean_exif_alt: float | None
    colmap_to_meters: float
    scale_source: str
    facade_frame: FacadeFrame | None
    texture_camera_count: int
    texture_filter_applied: bool
    minimum_sparse_observations: int
    seed_max_error: float
    seed_min_track: int
    gaussian_seed_point_count: int
    images_dir: str
    scene: SceneInfo | None
    cells: list[tuple[CellBounds | None, SceneInfo]]
    use_partition: bool


def prepare_gaussian_scene(config: GaussianOrthoConfig) -> GaussianSceneState:
    """Load COLMAP data, establish metric scale, and select training views."""
    seed_max_error = config.facade_seed_max_reprojection_error if config.render_mode == "facade" else 1.0
    seed_min_track = config.facade_seed_min_track_length if config.render_mode == "facade" else 3
    _report(
        config.vol_id,
        "GAUSS",
        5,
        "Loading COLMAP reconstruction…",
        config.report_fn,
    )
    train_cameras, test_cameras, point_cloud, transform_data = load_colmap_reconstruction(
        config.dense_path,
        config.transform_file,
        max_reproj_error=seed_max_error,
        min_track_length=seed_min_track,
    )
    gaussian_seed_point_count = len(point_cloud.points)
    if config.render_mode == "facade" and transform_data:
        raise ValueError("Facade rendering must not receive a geographic Sim3 transform")

    images_dir = os.path.join(config.dense_path, "images")
    exif_altitudes = extract_exif_altitudes(images_dir)
    valid_altitudes = [
        exif_altitudes[camera.image_name]
        for camera in train_cameras
        if exif_altitudes.get(camera.image_name) is not None
    ]
    mean_exif_alt = float(np.mean(valid_altitudes)) if valid_altitudes else None

    scale_source = "geographic-sim3"
    if config.render_mode == "facade":
        scale_mode = config.facade_scale_mode.strip().lower()
        if scale_mode == "manual":
            colmap_to_meters = float(config.facade_meters_per_model_unit)
            if colmap_to_meters <= 0:
                raise ValueError("facade_meters_per_model_unit must be positive")
            scale_source = "manual"
        elif scale_mode == "model-units":
            colmap_to_meters = 1.0
            scale_source = "model-units"
        elif scale_mode == "gps-baseline":
            colmap_to_meters, scale_source, scale_images_dir = _compute_facade_gps_scale(
                train_cameras, config.dense_path
            )
            if scale_source != "model-units":
                scale_source = f"{scale_source}:{Path(scale_images_dir).name}"
        else:
            raise ValueError(f"Unsupported facade scale mode: {scale_mode}")
        _report(
            config.vol_id,
            "GAUSS",
            6,
            f"Facade scale: 1 model unit = {colmap_to_meters:.6f} m ({scale_source})",
            config.report_fn,
        )
    elif transform_data:
        colmap_to_meters = float(transform_data.get("scale", 1.0))
    else:
        colmap_to_meters = compute_colmap_scale(
            train_cameras,
            images_dir,
            config.utm_crs,
        )
        scale_source = "projected-gps-baselines"
        _report(
            config.vol_id,
            "GAUSS",
            6,
            f"COLMAP scale: 1 unit = {colmap_to_meters:.2f} m (from GPS)",
            config.report_fn,
        )

    registered_cameras = list(train_cameras)
    facade_frame = None
    texture_camera_count = len(train_cameras)
    texture_filter_applied = False
    minimum_sparse_observations = 20
    if config.render_mode == "facade":
        from .facade_frame import estimate_facade_frame

        facade_frame = estimate_facade_frame(
            point_cloud.points,
            registered_cameras,
        )
        normal = facade_frame.world_to_facade[2]
        incidence_limit = float(config.facade_texture_max_incidence_deg)
        texture_cameras = []
        for camera in registered_cameras:
            outward_axis = -np.asarray(camera.R, dtype=np.float64)[:, 2]
            outward_axis /= max(float(np.linalg.norm(outward_axis)), 1e-12)
            incidence = np.degrees(np.arccos(np.clip(float(outward_axis @ normal), -1.0, 1.0)))
            if (
                incidence <= incidence_limit
                and int(getattr(camera, "sparse_observations", 0)) >= minimum_sparse_observations
            ):
                texture_cameras.append(camera)
        minimum_texture_cameras = max(
            30,
            int(np.ceil(len(registered_cameras) * 0.30)),
        )
        if len(texture_cameras) >= minimum_texture_cameras:
            train_cameras = texture_cameras
            texture_camera_count = len(texture_cameras)
            texture_filter_applied = True
        else:
            _report(
                config.vol_id,
                "GAUSS",
                7,
                "Facade incidence filter would retain only "
                f"{len(texture_cameras)}/{len(registered_cameras)} cameras; "
                "using every registered camera for texture training.",
                config.report_fn,
            )
        _report(
            config.vol_id,
            "GAUSS",
            8,
            f"Facade texture training views: {texture_camera_count}/"
            f"{len(registered_cameras)}; requested filter is "
            f"≤{incidence_limit:.1f}° incidence and at least "
            f"{minimum_sparse_observations} sparse observations "
            f"({'applied' if texture_filter_applied else 'not applied'}).",
            config.report_fn,
        )

    _report(
        config.vol_id,
        "GAUSS",
        10,
        f"Loaded {len(train_cameras)} cameras, {point_cloud.points.shape[0]} points",
        config.report_fn,
    )
    scene = build_scene_info(
        train_cameras,
        test_cameras,
        point_cloud,
        dense_path=config.dense_path,
    )
    use_partition = config.partition_m > 1 or config.partition_n > 1
    cells: list[tuple[CellBounds | None, SceneInfo]]
    if use_partition:
        _report(
            config.vol_id,
            "GAUSS",
            12,
            f"Partitioning scene into {config.partition_m}x{config.partition_n} cells…",
            config.report_fn,
        )
        cells = [
            (bounds, cell_scene)
            for bounds, cell_scene in partition_scene(
            scene,
            config.partition_m,
            config.partition_n,
            config.partition_overlap,
            )
        ]
        _report(
            config.vol_id,
            "GAUSS",
            15,
            f"Created {len(cells)} active cells",
            config.report_fn,
        )
    else:
        cells = [(None, scene)]
    return GaussianSceneState(
        train_cameras=train_cameras,
        test_cameras=test_cameras,
        registered_cameras=registered_cameras,
        point_cloud=point_cloud,
        transform_data=transform_data,
        mean_exif_alt=mean_exif_alt,
        colmap_to_meters=colmap_to_meters,
        scale_source=scale_source,
        facade_frame=facade_frame,
        texture_camera_count=texture_camera_count,
        texture_filter_applied=texture_filter_applied,
        minimum_sparse_observations=minimum_sparse_observations,
        seed_max_error=seed_max_error,
        seed_min_track=seed_min_track,
        gaussian_seed_point_count=gaussian_seed_point_count,
        images_dir=images_dir,
        scene=scene,
        cells=cells,
        use_partition=use_partition,
    )


@dataclass
class GaussianTrainingState:
    merged_model: GaussianModel
    final_ply: str
    facade_subset_result: dict[str, object] | None


@dataclass(frozen=True)
class GaussianTrainingPhaseState:
    """Explicit output boundary between training and later GPU phases."""

    scene_state: GaussianSceneState
    training_state: GaussianTrainingState
    backend_name: str
    trainer_binary_sha256: str


def execute_gaussian_training_phase(
    config: GaussianOrthoConfig,
    *,
    trainer_backend: str | None = None,
    backend: TrainingBackend | None = None,
    model_class: ModelFactory | None = None,
    merge_models_fn: MergeModels | None = None,
    cupy_module: Any | None = None,
) -> GaussianTrainingPhaseState:
    """Prepare the scene and train its merged, unfiltered Gaussian model."""
    if backend is None:
        backend = resolve_training_backend(trainer_backend)
    if model_class is None:
        from .gaussian_model import GaussianModel

        model_class = GaussianModel
    if merge_models_fn is None:
        from .merge import merge_models

        merge_models_fn = merge_models
    if cupy_module is None:
        import cupy as cp

        cupy_module = cp
    trainer_binary_sha256 = backend.binary_sha256()
    scene_state = prepare_gaussian_scene(config)
    training_state = train_and_merge_gaussian_models(
        config,
        scene_state,
        backend=backend,
        trainer_binary_sha256=trainer_binary_sha256,
        model_class=model_class,
        merge_models_fn=merge_models_fn,
        cupy_module=cupy_module,
    )
    return GaussianTrainingPhaseState(
        scene_state=scene_state,
        training_state=training_state,
        backend_name=backend.name,
        trainer_binary_sha256=trainer_binary_sha256,
    )


def _make_training_reporter(
    pct_start: int,
    pct_end: int,
    config: GaussianOrthoConfig,
) -> Callable[[int, float, int], None]:
    def reporter(iteration: int, loss_value: float, gaussian_count: int) -> None:
        progress = pct_start + int((pct_end - pct_start) * iteration / max(1, config.iterations))
        _report(
            config.vol_id,
            "GAUSS",
            progress,
            f"[MRNF] iter {iteration}: loss={loss_value:.4f}, N={gaussian_count}",
            config.report_fn,
        )

    return reporter


def train_and_merge_gaussian_models(
    config: GaussianOrthoConfig,
    scene_state: GaussianSceneState,
    *,
    backend: TrainingBackend,
    trainer_binary_sha256: str,
    model_class: ModelFactory,
    merge_models_fn: MergeModels,
    cupy_module: Any,
) -> GaussianTrainingState:
    """Train/reuse every cell, merge it, and persist the local-frame PLY."""
    if scene_state.point_cloud is None:
        raise RuntimeError("Sparse point cloud is unavailable for training")
    cell_models: list[tuple[CellBounds | None, GaussianModel]] = []
    n_cells = len(scene_state.cells)
    facade_subset_result: dict[str, object] | None = None
    sparse_dir = os.path.join(config.dense_path, "sparse", "0")
    if not os.path.isdir(sparse_dir):
        sparse_dir = os.path.join(config.dense_path, "sparse")
    images_dir_path = os.path.join(config.dense_path, "images")

    for index, (cell_bounds, cell_scene) in enumerate(scene_state.cells):
        cell_label = f"cell_{index}" if scene_state.use_partition else "full"
        pct_start = 15 + int(65 * index / n_cells)
        pct_end = 15 + int(65 * (index + 1) / n_cells)
        _report(
            config.vol_id,
            "GAUSS",
            pct_start,
            f"[{backend.name} MRNF] Training {cell_label}: "
            f"{len(cell_scene.train_cameras)} cameras, "
            f"{cell_scene.point_cloud.points.shape[0]} points",
            config.report_fn,
        )
        cell_output = os.path.join(config.checkpoint_dir, cell_label)

        if scene_state.use_partition:
            cell_workspace = os.path.join(
                config.checkpoint_dir,
                f"{cell_label}_workspace",
            )
            export_colmap_subset(
                source_sparse_dir=sparse_dir,
                target_dir=cell_workspace,
                camera_names=[camera.image_name for camera in cell_scene.train_cameras],
                images_dir=images_dir_path,
                max_point_error=1.0,
                min_track_length=3,
            )
            training_data_path = cell_workspace
        elif config.render_mode == "facade":
            texture_workspace = os.path.join(
                config.checkpoint_dir,
                "facade_texture_workspace",
            )
            subset_export = export_colmap_subset(
                source_sparse_dir=sparse_dir,
                target_dir=texture_workspace,
                camera_names=[camera.image_name for camera in cell_scene.train_cameras],
                images_dir=images_dir_path,
                max_point_error=scene_state.seed_max_error,
                min_track_length=scene_state.seed_min_track,
                max_points=max(1, int(config.cap_max * 0.85)),
                return_report=True,
            )
            if isinstance(subset_export, str):
                raise RuntimeError("Facade subset export did not return its report")
            facade_subset_result = subset_export
            if facade_subset_result["coverage_balanced"]:
                _report(
                    config.vol_id,
                    "GAUSS",
                    pct_start,
                    "Coverage-balanced facade seed: "
                    f"{facade_subset_result['points_before_cap']} → "
                    f"{facade_subset_result['exported_points']} points "
                    f"(85% of the {config.cap_max} Gaussian GPU cap).",
                    config.report_fn,
                )
            training_data_path = texture_workspace
        else:
            training_data_path = config.dense_path

        checkpoint_path = os.path.join(cell_output, "training.ckpt")
        resume_from = (
            checkpoint_path
            if os.path.isfile(checkpoint_path) and not os.path.isfile(os.path.join(cell_output, "trainer_run.json"))
            else None
        )
        if resume_from:
            _report(
                config.vol_id,
                "GAUSS",
                pct_start,
                f"[DroneGS] Resuming {cell_label} from its validated checkpoint",
                config.report_fn,
            )

        dataset_identity = compute_dataset_identity(training_data_path)
        training_request = TrainingRequest(
            data_path=training_data_path,
            output_path=cell_output,
            iterations=config.iterations,
            strategy="mrnf",
            sh_degree=config.sh_degree,
            max_cap=config.cap_max,
            resize_factor=config.data_factor,
            max_width=config.max_width,
            tile_mode=config.tile_mode,
            seed=config.training_seed,
            dataset_fingerprint=dataset_identity.fingerprint,
            dronegs=DroneGSTuning(
                profile_id=config.dronegs_profile_id,
                qualification_policy_id=config.dronegs_qualification_policy_id,
                optimizer_profile=config.dronegs_optimizer_profile,
                pruning_policy=config.dronegs_pruning_policy,
                raster_profile=config.dronegs_raster_profile,
                sh_degree_interval=config.dronegs_sh_degree_interval,
                topology_cooldown=min(
                    config.dronegs_topology_cooldown,
                    max(1, config.iterations // 5),
                ),
                photometric_finish=min(
                    config.dronegs_photometric_finish,
                    max(1, config.iterations // 5),
                ),
                photometric_mse_percent=config.dronegs_photometric_mse_percent,
                checkpoint_every=config.dronegs_checkpoint_every,
                resume_from=resume_from,
                test_every=config.dronegs_test_every,
                test_split=config.dronegs_test_split,
                test_guard_percent=config.dronegs_test_guard_percent,
                save_eval_images=config.dronegs_test_every > 0,
                canary_min_psnr=config.dronegs_canary_min_psnr,
                canary_min_ssim=config.dronegs_canary_min_ssim,
            ),
        )
        training_result = _reusable_dronegs_result(
            training_request,
            trainer_binary_sha256=trainer_binary_sha256,
        )
        if training_result is None:
            if training_request.dronegs.resume_from is None:
                quarantined = _quarantine_incompatible_dronegs_output(training_request.output_path)
                if quarantined is not None:
                    _report(
                        config.vol_id,
                        "GAUSS",
                        pct_start,
                        "[DroneGS] Incompatible prior output preserved at "
                        f"{quarantined}; starting a clean training run.",
                        config.report_fn,
                    )
            training_result = backend.train(
                training_request,
                report_fn=_make_training_reporter(
                    pct_start,
                    pct_end,
                    config,
                ),
                verbose=config.verbose,
                cancellation_check=config.cancellation_check,
                checkpoint_fn=config.checkpoint_callback,
            )
        else:
            _report(
                config.vol_id,
                "GAUSS",
                pct_end,
                f"[DroneGS] Reusing completed, canary-approved {cell_label}",
                config.report_fn,
            )

        model = model_class(
            sh_degree=config.sh_degree,
            fagk_enabled=config.fagk,
        )
        model.load_ply(str(training_result.ply_path))
        _report(
            config.vol_id,
            "GAUSS",
            pct_end,
            f"[{backend.name}] Loaded {model.num_gaussians} Gaussians from {training_result.ply_path}",
            config.report_fn,
        )
        cell_models.append((cell_bounds, model))

    if scene_state.use_partition and len(cell_models) > 1:
        _report(
            config.vol_id,
            "GAUSS",
            82,
            "Merging cell models…",
            config.report_fn,
        )
        camera_positions = np.stack([camera.T for camera in scene_state.train_cameras])
        points_xy = np.concatenate([scene_state.point_cloud.points[:, :2], camera_positions[:, :2]])
        merged_model = merge_models_fn(
            cell_models,
            (float(points_xy[:, 0].min()), float(points_xy[:, 0].max())),
            (float(points_xy[:, 1].min()), float(points_xy[:, 1].max())),
            config.partition_m,
            config.partition_n,
            config.partition_overlap,
        )
        _report(
            config.vol_id,
            "GAUSS",
            85,
            f"Merged model: {merged_model.num_gaussians} Gaussians",
            config.report_fn,
        )
    else:
        merged_model = cell_models[0][1]

    final_ply = os.path.join(config.checkpoint_dir, "final.ply")
    os.makedirs(config.checkpoint_dir, exist_ok=True)
    merged_model.active_sh_degree = config.sh_degree
    merged_model.save_ply(final_ply)
    _report(
        config.vol_id,
        "GAUSS",
        88,
        f"Saved final model: {final_ply}",
        config.report_fn,
    )
    scene_state.cells = []
    del cell_models
    import gc

    gc.collect()
    cupy_module.get_default_memory_pool().free_all_blocks()
    return GaussianTrainingState(
        merged_model=merged_model,
        final_ply=final_ply,
        facade_subset_result=facade_subset_result,
    )


@dataclass
class GaussianRenderState:
    merged_model: GaussianModel
    geo_origin: np.ndarray
    frame_origin: np.ndarray | None
    rotation_geo: np.ndarray | None
    sh_direction_rotation: np.ndarray | None
    facade_depth_bounds_model: tuple[float, float] | None
    render_extent: tuple[float, float, float, float, float, float]
    local_gsd: float
    resolution_units: str
    coverage_camera_positions: np.ndarray


def prepare_gaussian_render_state(
    config: GaussianOrthoConfig,
    scene_state: GaussianSceneState,
    training_state: GaussianTrainingState,
    *,
    cupy_module: Any,
) -> GaussianRenderState:
    """Align, filter, and bound the trained model for raster rendering."""
    model = training_state.merged_model
    cameras = scene_state.registered_cameras
    geo_origin: np.ndarray = np.zeros(3, dtype=np.float64)
    frame_origin: np.ndarray | None = None
    rotation_geo: np.ndarray | None = None
    sh_direction_rotation: np.ndarray | None = None

    if config.render_mode == "facade":
        _report(
            config.vol_id,
            "GAUSS",
            89,
            "Applying optimized-camera facade frame…",
            config.report_fn,
        )
        if scene_state.facade_frame is None:
            raise RuntimeError("Facade frame is unavailable")
        rotation_geo = scene_state.facade_frame.world_to_facade.astype(np.float32)
        frame_origin = scene_state.facade_frame.origin.astype(np.float64)
        _report(
            config.vol_id,
            "GAUSS",
            89,
            "Facade frame fitted: "
            f"{scene_state.facade_frame.inlier_ratio:.1%} plane inliers, "
            "RMSE="
            f"{scene_state.facade_frame.plane_rmse * scene_state.colmap_to_meters:.3f} m",
            config.report_fn,
        )
    elif scene_state.transform_data:
        _report(
            config.vol_id,
            "GAUSS",
            89,
            "Applying geo-alignment to Gaussian model…",
            config.report_fn,
        )
        transform = scene_state.transform_data
        rotation = cupy_module.array(transform["R"], dtype=cupy_module.float32)
        scale = float(transform["scale"])
        model._xyz = (scale * (rotation @ model._xyz.T)).T
        import math as _math

        model._scaling += _math.log(scale)
        rotation_quaternion = model._matrix_to_quaternion(cupy_module.asnumpy(rotation))
        model._rotation = model._quaternion_multiply(
            cupy_module.array(
                rotation_quaternion,
                dtype=cupy_module.float32,
            )[None, :],
            model._rotation,
        )
        sh_direction_rotation = cupy_module.asnumpy(rotation).astype(np.float32).T
        geo_origin = np.array(transform["t"], dtype=np.float64)
        geo_camera_positions = apply_sim3_to_points(
            np.array([camera.T for camera in cameras], dtype=np.float64),
            transform,
        )
    else:
        _report(
            config.vol_id,
            "GAUSS",
            89,
            "Computing PCA nadir direction…",
            config.report_fn,
        )
        from .pca_alignment import compute_pca_rotation

        if scene_state.point_cloud is None:
            raise RuntimeError("Sparse point cloud is unavailable")
        camera_positions = np.array(
            [camera.T for camera in cameras],
            dtype=np.float64,
        )
        rotation_align, angle_deg = compute_pca_rotation(
            cameras,
            scene_state.point_cloud.points,
        )
        rotation_geo = rotation_align.astype(np.float32)
        _report(
            config.vol_id,
            "GAUSS",
            89,
            f"PCA nadir direction: {angle_deg:.1f}° from Z (using R_geo for rendering)",
            config.report_fn,
        )
        geo_camera_positions = (rotation_align @ camera_positions.T).T
        projected_origin = compute_projected_geo_origin(
            cameras,
            scene_state.images_dir,
            config.utm_crs,
            geo_camera_positions,
            scene_state.colmap_to_meters,
            scene_state.mean_exif_alt,
        )
        if projected_origin is not None:
            geo_origin = projected_origin
            _report(
                config.vol_id,
                "GAUSS",
                89,
                f"GeoTIFF origin from GPS: E={geo_origin[0]:.2f}, N={geo_origin[1]:.2f}",
                config.report_fn,
            )

    raw_camera_positions = np.array(
        [camera.T for camera in cameras],
        dtype=np.float64,
    )
    if config.render_mode == "facade" or not scene_state.transform_data:
        local_camera_positions = raw_camera_positions
    else:
        local_camera_positions = geo_camera_positions - geo_origin
    if config.render_mode == "facade":
        coverage_camera_positions: np.ndarray = np.empty((0, 3), dtype=np.float64)
    elif scene_state.transform_data:
        coverage_camera_positions = geo_camera_positions - geo_origin
    else:
        coverage_camera_positions = geo_camera_positions

    if not config.filter_enabled:
        _report(
            config.vol_id,
            "GAUSS",
            89,
            f"Filtering disabled — keeping all {model.num_gaussians} Gaussians",
            config.report_fn,
        )
    else:
        from .model_filtering import filter_gaussians

        _report(
            config.vol_id,
            "GAUSS",
            89,
            "Filtering Gaussians…",
            config.report_fn,
        )
        filter_gaussians(
            model,
            local_camera_positions,
            max_scale=config.filter_max_scale,
            minimum_retained_ratio=config.filter_min_retained_ratio,
            dist_multiplier=config.filter_dist_multiplier,
            opacity_threshold=config.filter_opacity_threshold,
            needle_ratio=config.filter_needle_ratio,
            sor_sigma=config.filter_sor_sigma,
            sor_enabled=config.filter_sor,
            cc_enabled=config.filter_cc,
            z_floater_enabled=config.filter_z_floater,
            R_geo=rotation_geo,
            report_fn=lambda message: _report(
                config.vol_id,
                "GAUSS",
                89,
                message,
                config.report_fn,
            ),
        )
        _report(
            config.vol_id,
            "GAUSS",
            89,
            f"After filtering: {model.num_gaussians} Gaussians",
            config.report_fn,
        )

    depth_bounds = None
    if config.render_mode == "facade" and config.facade_depth_iqr_multiplier > 0:
        if frame_origin is None or rotation_geo is None:
            raise RuntimeError("Facade depth filtering requires a local frame")
        local_xyz = (
            model.positions
            - cupy_module.array(
                frame_origin,
                dtype=cupy_module.float32,
            )[None, :]
        )
        depths = (
            cupy_module.array(
                rotation_geo[2],
                dtype=cupy_module.float32,
            )
            @ local_xyz.T
        )
        q25 = float(cupy_module.quantile(depths, 0.25))
        q75 = float(cupy_module.quantile(depths, 0.75))
        iqr = max(q75 - q25, 1e-6)
        multiplier = config.facade_depth_iqr_multiplier
        depth_bounds = (q25 - multiplier * iqr, q75 + multiplier * iqr)
        before_filter = model.num_gaussians
        model.filter_by_mask((depths >= depth_bounds[0]) & (depths <= depth_bounds[1]))
        _report(
            config.vol_id,
            "GAUSS",
            90,
            f"Facade depth filter ({multiplier:.2f}xIQR): "
            f"{before_filter} → {model.num_gaussians}; window "
            f"[{depth_bounds[0] * scene_state.colmap_to_meters:.2f}, "
            f"{depth_bounds[1] * scene_state.colmap_to_meters:.2f}] m.",
            config.report_fn,
        )

    model.save_ply(training_state.final_ply)
    _report(
        config.vol_id,
        "GAUSS",
        95,
        f"Saved filtered model: {training_state.final_ply} ({model.num_gaussians} Gaussians)",
        config.report_fn,
    )
    from .ortho_renderer import compute_ortho_extent

    render_extent = compute_ortho_extent(
        model,
        pad=(1.0 / scene_state.colmap_to_meters if config.render_mode == "facade" else 2.0),
        R_geo=rotation_geo,
        frame_origin=frame_origin,
        quantile=0.001,
    )
    scene_state.point_cloud = None
    scene_state.scene = None
    import gc

    gc.collect()
    cupy_module.get_default_memory_pool().free_all_blocks()

    if config.render_mode == "facade":
        local_gsd = config.resolution / scene_state.colmap_to_meters
        resolution_units = "metres" if scene_state.scale_source != "model-units" else "model-units"
    elif scene_state.transform_data:
        local_gsd = config.resolution
        resolution_units = "metres"
    else:
        local_gsd = config.resolution / scene_state.colmap_to_meters
        resolution_units = "metres"
    return GaussianRenderState(
        merged_model=model,
        geo_origin=geo_origin,
        frame_origin=frame_origin,
        rotation_geo=rotation_geo,
        sh_direction_rotation=sh_direction_rotation,
        facade_depth_bounds_model=depth_bounds,
        render_extent=render_extent,
        local_gsd=local_gsd,
        resolution_units=resolution_units,
        coverage_camera_positions=coverage_camera_positions,
    )


@dataclass(frozen=True)
class GaussianFilteringPhaseState:
    """Filtered model plus the immutable geometry required for rasterization."""

    render_state: GaussianRenderState
    input_gaussians: int
    output_gaussians: int


def execute_gaussian_filtering_phase(
    config: GaussianOrthoConfig,
    training_phase: GaussianTrainingPhaseState,
    *,
    cupy_module: Any | None = None,
) -> GaussianFilteringPhaseState:
    """Apply alignment/filtering exactly once and prepare raster geometry."""
    if cupy_module is None:
        import cupy as cp

        cupy_module = cp
    input_gaussians = int(training_phase.training_state.merged_model.num_gaussians)
    render_state = prepare_gaussian_render_state(
        config,
        training_phase.scene_state,
        training_phase.training_state,
        cupy_module=cupy_module,
    )
    return GaussianFilteringPhaseState(
        render_state=render_state,
        input_gaussians=input_gaussians,
        output_gaussians=int(render_state.merged_model.num_gaussians),
    )


@dataclass(frozen=True)
class GaussianRasterizationPhaseState:
    """Raw raster buffers produced only from an already filtered model."""

    result: dict[str, Any]
    width: int
    height: int


def execute_gaussian_rasterization_phase(
    config: GaussianOrthoConfig,
    filtering_phase: GaussianFilteringPhaseState,
    *,
    render_fn: Callable[..., dict[str, Any]] | None = None,
) -> GaussianRasterizationPhaseState:
    """Render RGB/height buffers without training or filtering side effects."""
    if render_fn is None:
        from .ortho_renderer import render_orthophoto

        render_fn = render_orthophoto
    render_state = filtering_phase.render_state
    _report(
        config.vol_id,
        "GAUSS",
        96,
        "Rendering orthographic TDOM at "
        f"{config.resolution} {render_state.resolution_units}/px "
        f"(local GSD={render_state.local_gsd:.6f})…",
        config.report_fn,
    )
    result = render_fn(
        render_state.merged_model,
        gsd=render_state.local_gsd,
        extent=render_state.render_extent,
        R_geo=render_state.rotation_geo,
        frame_origin=render_state.frame_origin,
        sh_direction_rotation=render_state.sh_direction_rotation,
        mip_filter_variance=config.ortho_mip_filter_variance,
        mip_filter_compensation=config.ortho_mip_filter_compensation,
    )
    rgb = result["rgb"]
    height, width = rgb.shape[:2]
    _report(
        config.vol_id,
        "GAUSS",
        97,
        f"Orthophoto rendered: {width}x{height} px at GSD={config.resolution} m/px",
        config.report_fn,
    )
    return GaussianRasterizationPhaseState(
        result=result,
        width=int(width),
        height=int(height),
    )


def generate_gaussian_orthophoto(
    dense_path: str,
    ortho_file: str,
    utm_crs: str | None,
    vol_id: str = "vol",
    transform_file: str | None = None,
    report_fn: ProgressReport | None = None,
    resolution: float = 0.02,
    # Gaussian-specific params
    iterations: int = DRONEGS_PRODUCTION_PROFILE_V1.iterations,
    partition_m: int = 1,
    partition_n: int = 1,
    partition_overlap: float = 0.20,
    sh_degree: int = 3,
    fagk: bool = True,
    checkpoint_dir: str | None = None,
    data_factor: int = DRONEGS_PRODUCTION_PROFILE_V1.data_factor,
    max_width: int = DRONEGS_PRODUCTION_PROFILE_V1.max_width,
    ortho_mip_filter_variance: float = 0.03,
    ortho_mip_filter_compensation: bool = True,
    tile_mode: int = DRONEGS_PRODUCTION_PROFILE_V1.tile_mode,
    cap_max: int = DRONEGS_PRODUCTION_PROFILE_V1.cap_max,
    filter_enabled: bool = True,
    filter_max_scale: float = 5.0,
    filter_min_retained_ratio: float = 0.80,
    filter_dist_multiplier: float = 1.0,
    filter_opacity_threshold: float = 0.005,
    filter_needle_ratio: float = 0.0,
    filter_sor: bool = False,
    filter_sor_sigma: float = 4.0,
    filter_cc: bool = False,
    filter_z_floater: bool = False,
    coverage_gate_enabled: bool = True,
    coverage_grid_size: int = 16,
    coverage_min_valid_ratio: float = 0.50,
    coverage_cell_threshold: float = 0.25,
    coverage_min_covered_cells_ratio: float = 0.75,
    coverage_min_worst_cell_ratio: float = 0.01,
    coverage_min_camera_cell_ratio: float = 0.10,
    verbose: bool = False,
    trainer_backend: str | None = None,
    training_seed: int = DRONEGS_PRODUCTION_PROFILE_V1.seed,
    dronegs_profile_id: str = DRONEGS_PRODUCTION_PROFILE_V1.profile_id,
    dronegs_qualification_policy_id: str = DRONEGS_QUALIFICATION_POLICY_ID,
    dronegs_optimizer_profile: str = (DRONEGS_PRODUCTION_PROFILE_V1.optimizer_profile),
    dronegs_pruning_policy: str = (DRONEGS_PRODUCTION_PROFILE_V1.pruning_policy),
    dronegs_raster_profile: str = (DRONEGS_PRODUCTION_PROFILE_V1.raster_profile),
    dronegs_sh_degree_interval: int = (DRONEGS_PRODUCTION_PROFILE_V1.sh_degree_interval),
    dronegs_topology_cooldown: int = (DRONEGS_PRODUCTION_PROFILE_V1.topology_cooldown),
    dronegs_photometric_finish: int = (DRONEGS_PRODUCTION_PROFILE_V1.photometric_finish),
    dronegs_photometric_mse_percent: int = (DRONEGS_PRODUCTION_PROFILE_V1.photometric_mse_percent),
    dronegs_checkpoint_every: int = (DRONEGS_PRODUCTION_PROFILE_V1.checkpoint_every),
    dronegs_test_every: int = DRONEGS_PRODUCTION_PROFILE_V1.test_every,
    dronegs_test_split: str = DRONEGS_PRODUCTION_PROFILE_V1.test_split,
    dronegs_test_guard_percent: int = (DRONEGS_PRODUCTION_PROFILE_V1.test_guard_percent),
    dronegs_canary_min_psnr: float = (DRONEGS_PRODUCTION_PROFILE_V1.canary_min_psnr),
    dronegs_canary_min_ssim: float = (DRONEGS_PRODUCTION_PROFILE_V1.canary_min_ssim),
    cancellation_check: CancellationCheck | None = None,
    checkpoint_callback: CheckpointCallback | None = None,
    render_mode: str = "map",
    facade_scale_mode: str = FACADE_PARAMETER_DEFAULTS["facade_scale_mode"],
    facade_meters_per_model_unit: float = float(FACADE_PARAMETER_DEFAULTS["facade_meters_per_model_unit"]),
    facade_frame_report: str | None = None,
    facade_texture_max_incidence_deg: float = float(FACADE_PARAMETER_DEFAULTS["facade_texture_max_incidence_deg"]),
    facade_depth_iqr_multiplier: float = float(FACADE_PARAMETER_DEFAULTS["facade_depth_iqr_multiplier"]),
    facade_seed_max_reprojection_error: float = float(FACADE_PARAMETER_DEFAULTS["facade_seed_max_reprojection_error"]),
    facade_seed_min_track_length: int = int(FACADE_PARAMETER_DEFAULTS["facade_seed_min_track_length"]),
) -> dict[str, Any]:
    """
    Generate a True Digital Orthophoto Map using 3D Gaussian Splatting.

    Parameters
    ----------
    dense_path : str
        COLMAP dense workspace (contains sparse/, images/, stereo/).
    ortho_file : str
        Output GeoTIFF path.
    utm_crs : str
        Coordinate reference system (e.g. 'EPSG:32631').
    vol_id : str
        Volume ID for progress reporting.
    transform_file : str, optional
        Path to alignment_transform.json.
    report_fn : callable, optional
        Progress callback: report_fn(vol_id, step, progress, msg).
    resolution : float
        Ground sample distance in metres.
    iterations : int
        Training iterations per cell.
    partition_m, partition_n : int
        Grid partition dimensions (1 x 1 = no partition).
    partition_overlap : float
        Overlap fraction for partitioning.
    sh_degree : int
        Maximum spherical harmonics degree.
    fagk : bool
        Enable Fully Anisotropic Gaussian Kernel.
    checkpoint_dir : str, optional
        Directory for training checkpoints.
    data_factor : int
        Trainer image downscaling factor (1, 2, 4, or 8).
    max_width : int
        Maximum training image dimension after downscaling.
    tile_mode : int
        Backend memory-saving tile mode (1, 2, or 4).
    cap_max : int
        Maximum Gaussian count for MRNF strategy.
    trainer_backend : str, optional
        ``dronegs``. The environment variable
        DRONEAI_GAUSSIAN_BACKEND may override it.
    training_seed : int
        Requested base seed; each partition receives the base plus its index.
    dronegs_* :
        Native convergence controls. Defaults reproduce the Albagnac dev.45
        production profile.
    """
    import cupy as cp

    # Ensure any stale CUDA allocations from a previous crashed run are freed
    import gc

    gc.collect()
    cp.get_default_memory_pool().free_all_blocks()
    try:
        free_bytes, total_bytes = cp.cuda.Device(0).mem_info
        vram_total = total_bytes / (1024**3)
        vram_free = free_bytes / (1024**3)
        dev_name = cp.cuda.runtime.getDeviceProperties(0)["name"]
        _report(
            vol_id,
            "GAUSS",
            0,
            f"Starting Gaussian Splatting on {dev_name} ({vram_free:.1f}/{vram_total:.1f} GB free)",
            report_fn,
        )
    except Exception:
        _report(vol_id, "GAUSS", 0, "Starting Gaussian Splatting", report_fn)

    if checkpoint_dir is None:
        checkpoint_dir = str(Path(ortho_file).parent / "gaussian_checkpoints")
    render_mode = str(render_mode).strip().lower()
    if render_mode not in {"map", "facade"}:
        raise ValueError(f"Unsupported orthophoto render mode: {render_mode}")

    config = GaussianOrthoConfig(
        dense_path=dense_path,
        ortho_file=ortho_file,
        utm_crs=utm_crs,
        vol_id=vol_id,
        transform_file=transform_file,
        report_fn=report_fn,
        resolution=resolution,
        iterations=iterations,
        partition_m=partition_m,
        partition_n=partition_n,
        partition_overlap=partition_overlap,
        sh_degree=sh_degree,
        fagk=fagk,
        checkpoint_dir=checkpoint_dir,
        data_factor=data_factor,
        max_width=max_width,
        ortho_mip_filter_variance=ortho_mip_filter_variance,
        ortho_mip_filter_compensation=ortho_mip_filter_compensation,
        tile_mode=tile_mode,
        cap_max=cap_max,
        filter_enabled=filter_enabled,
        filter_max_scale=filter_max_scale,
        filter_min_retained_ratio=filter_min_retained_ratio,
        filter_dist_multiplier=filter_dist_multiplier,
        filter_opacity_threshold=filter_opacity_threshold,
        filter_needle_ratio=filter_needle_ratio,
        filter_sor=filter_sor,
        filter_sor_sigma=filter_sor_sigma,
        filter_cc=filter_cc,
        filter_z_floater=filter_z_floater,
        coverage_gate_enabled=coverage_gate_enabled,
        coverage_grid_size=coverage_grid_size,
        coverage_min_valid_ratio=coverage_min_valid_ratio,
        coverage_cell_threshold=coverage_cell_threshold,
        coverage_min_covered_cells_ratio=coverage_min_covered_cells_ratio,
        coverage_min_worst_cell_ratio=coverage_min_worst_cell_ratio,
        coverage_min_camera_cell_ratio=coverage_min_camera_cell_ratio,
        verbose=verbose,
        training_seed=training_seed,
        dronegs_profile_id=dronegs_profile_id,
        dronegs_qualification_policy_id=dronegs_qualification_policy_id,
        dronegs_optimizer_profile=dronegs_optimizer_profile,
        dronegs_pruning_policy=dronegs_pruning_policy,
        dronegs_raster_profile=dronegs_raster_profile,
        dronegs_sh_degree_interval=dronegs_sh_degree_interval,
        dronegs_topology_cooldown=dronegs_topology_cooldown,
        dronegs_photometric_finish=dronegs_photometric_finish,
        dronegs_photometric_mse_percent=dronegs_photometric_mse_percent,
        dronegs_checkpoint_every=dronegs_checkpoint_every,
        dronegs_test_every=dronegs_test_every,
        dronegs_test_split=dronegs_test_split,
        dronegs_test_guard_percent=dronegs_test_guard_percent,
        dronegs_canary_min_psnr=dronegs_canary_min_psnr,
        dronegs_canary_min_ssim=dronegs_canary_min_ssim,
        cancellation_check=cancellation_check,
        checkpoint_callback=checkpoint_callback,
        render_mode=render_mode,
        facade_scale_mode=facade_scale_mode,
        facade_meters_per_model_unit=facade_meters_per_model_unit,
        facade_frame_report=facade_frame_report,
        facade_texture_max_incidence_deg=facade_texture_max_incidence_deg,
        facade_depth_iqr_multiplier=facade_depth_iqr_multiplier,
        facade_seed_max_reprojection_error=facade_seed_max_reprojection_error,
        facade_seed_min_track_length=facade_seed_min_track_length,
    )
    training_phase = execute_gaussian_training_phase(
        config,
        trainer_backend=trainer_backend,
        cupy_module=cp,
    )
    scene_state = training_phase.scene_state
    registered_cameras = scene_state.registered_cameras
    transform_data = scene_state.transform_data
    mean_exif_alt = scene_state.mean_exif_alt
    colmap_to_meters = scene_state.colmap_to_meters
    scale_source = scene_state.scale_source
    facade_frame = scene_state.facade_frame
    texture_camera_count = scene_state.texture_camera_count
    texture_filter_applied = scene_state.texture_filter_applied
    minimum_sparse_observations = scene_state.minimum_sparse_observations
    seed_max_error = scene_state.seed_max_error
    seed_min_track = scene_state.seed_min_track
    gaussian_seed_point_count = scene_state.gaussian_seed_point_count

    # --- 3. Train per cell through the stable backend boundary ---
    training_state = training_phase.training_state
    merged_model = training_state.merged_model
    final_ply = training_state.final_ply
    facade_subset_result = training_state.facade_subset_result
    filtering_phase = execute_gaussian_filtering_phase(
        config,
        training_phase,
        cupy_module=cp,
    )
    render_state = filtering_phase.render_state
    merged_model = render_state.merged_model
    geo_origin = render_state.geo_origin
    facade_depth_bounds_model = render_state.facade_depth_bounds_model
    resolution_units = render_state.resolution_units
    coverage_camera_positions = render_state.coverage_camera_positions
    rasterization_phase = execute_gaussian_rasterization_phase(
        config,
        filtering_phase,
    )
    result = rasterization_phase.result

    rgb = result["rgb"]
    height = result["height"]
    x_min, x_max, y_min, y_max = result["extent"]
    H = rasterization_phase.height
    W = rasterization_phase.width

    coverage_report_path = None
    coverage_report = None
    if render_mode == "map":
        from .coverage_quality import (
            SpatialCoveragePolicy,
            evaluate_spatial_coverage,
        )

        coverage_policy = SpatialCoveragePolicy(
            grid_size=coverage_grid_size,
            minimum_valid_ratio=coverage_min_valid_ratio,
            cell_coverage_threshold=coverage_cell_threshold,
            minimum_covered_cells_ratio=coverage_min_covered_cells_ratio,
            minimum_worst_cell_ratio=coverage_min_worst_cell_ratio,
            minimum_camera_cell_ratio=coverage_min_camera_cell_ratio,
        )
        coverage_report = evaluate_spatial_coverage(
            height,
            extent=(x_min, x_max, y_min, y_max),
            camera_positions=coverage_camera_positions,
            policy=coverage_policy,
            enforced=coverage_gate_enabled,
        )
        coverage_report_path = str(
            Path(ortho_file).with_name("gaussian_coverage_report.json")
        )
        coverage_path = Path(coverage_report_path)
        coverage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_coverage_path = coverage_path.with_suffix(".json.tmp")
        temporary_coverage_path.write_text(
            json.dumps(coverage_report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_coverage_path, coverage_path)
        _report(
            vol_id,
            "GAUSS",
            97,
            "Gaussian spatial coverage: "
            f"valid={coverage_report['valid_pixel_ratio']:.1%}, "
            f"covered cells={coverage_report['covered_cells_ratio']:.1%}, "
            f"worst cell={coverage_report['worst_cell_ratio']:.1%} "
            f"({coverage_report['status']}).",
            report_fn,
        )
        if coverage_gate_enabled and not coverage_report["accepted"]:
            failed_checks = ", ".join(
                check["name"]
                for check in coverage_report["checks"]
                if not check["passed"]
            )
            raise RuntimeError(
                "Gaussian spatial coverage gate rejected the product: "
                f"{failed_checks}. Report: {coverage_report_path}"
            )

    # --- 7. Write GeoTIFF ---
    # Translate the local-coordinate extent back to geographic (UTM) coords.
    # With Sim3: model is metric, geo_origin is the Sim3 translation (float64).
    # With PCA: model is in COLMAP units, scale to metres + add GPS-derived origin.
    geo_x_min, geo_y_max = georeference_raster_origin(
        x_min,
        y_max,
        geo_origin=geo_origin,
        colmap_to_meters=colmap_to_meters,
        sim3_aligned=bool(transform_data),
        facade=render_mode == "facade",
    )

    # --- Altitude correction: convert local model Z to the georeferenced datum ---
    if render_mode == "facade":
        height = height * colmap_to_meters
        z_offset = 0.0
        vertical_reference = "local-facade-depth"
    else:
        height, z_offset, vertical_reference = georeference_height_map(
            height,
            sim3_aligned=bool(transform_data),
            geo_origin_z=float(geo_origin[2]),
            colmap_to_meters=colmap_to_meters,
            exif_altitude_available=mean_exif_alt is not None,
        )
    if vertical_reference == "sim3":
        _report(
            vol_id,
            "GAUSS",
            97,
            f"Applied Sim3 vertical translation ({z_offset:+.2f} m) to height map.",
            report_fn,
        )
    elif vertical_reference == "exif":
        _report(
            vol_id,
            "GAUSS",
            97,
            f"Applied GPS/EXIF vertical origin ({z_offset:+.2f} m) to height map.",
            report_fn,
        )
    else:
        _report(
            vol_id,
            "GAUSS",
            97,
            "No absolute altitude reference found; height map remains in local model Z.",
            report_fn,
        )

    _report(vol_id, "GAUSS", 98, "Writing GeoTIFF\u2026", report_fn)

    height_file = str(Path(ortho_file).with_suffix(".height.tif"))
    output_crs = None if render_mode == "facade" else utm_crs
    write_geotiff(
        output_path=ortho_file,
        rgb=rgb,
        x_min=geo_x_min,
        y_max=geo_y_max,
        gsd=resolution,
        crs=output_crs,
        height_map=height,
        height_output_path=height_file,
    )

    if render_mode == "facade":
        if facade_frame is None:
            raise RuntimeError("Facade frame is unavailable for reporting")
        report_path = Path(facade_frame_report or str(Path(ortho_file).with_name("facade_frame.json")))
        frame_payload = {
            "schema_version": 1,
            "coordinate_system": "LOCAL_FACADE",
            "units": "metres" if scale_source != "model-units" else "model-units",
            "axis_definition": {
                "x": "horizontal-right",
                "y": "vertical-up",
                "z": "outward-toward-cameras",
            },
            "scale": {
                "meters_per_model_unit": colmap_to_meters,
                "source": scale_source,
                "uses_absolute_position": False,
                "uses_rtk_adjustment": False,
            },
            "frame": facade_frame.as_dict(),
            "texture_selection": {
                "registered_cameras": len(registered_cameras),
                "training_cameras": texture_camera_count,
                "maximum_incidence_deg": float(facade_texture_max_incidence_deg),
                "minimum_sparse_observations": minimum_sparse_observations,
                "filter_applied": texture_filter_applied,
            },
            "gaussian_seed": {
                "maximum_reprojection_error_px": seed_max_error,
                "minimum_track_length": seed_min_track,
                "points_after_loader_filter": gaussian_seed_point_count,
                "training_workspace_points": (
                    facade_subset_result["exported_points"]
                    if facade_subset_result is not None
                    else gaussian_seed_point_count
                ),
                "coverage_balanced_cap_applied": bool(
                    facade_subset_result and facade_subset_result["coverage_balanced"]
                ),
            },
            "depth_filter": {
                "iqr_multiplier": float(facade_depth_iqr_multiplier),
                "bounds_model_units": (
                    list(facade_depth_bounds_model) if facade_depth_bounds_model is not None else None
                ),
                "bounds_metres": (
                    [
                        facade_depth_bounds_model[0] * colmap_to_meters,
                        facade_depth_bounds_model[1] * colmap_to_meters,
                    ]
                    if facade_depth_bounds_model is not None
                    else None
                ),
            },
            "raster": {
                "width": W,
                "height": H,
                "pixel_size": resolution,
                "pixel_size_units": resolution_units,
                "extent": [
                    geo_x_min,
                    geo_y_max - H * resolution,
                    geo_x_min + W * resolution,
                    geo_y_max,
                ],
                "crs": None,
            },
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = report_path.with_suffix(report_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(frame_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, report_path)
    else:
        report_path = None

    _report(vol_id, "GAUSS", 100, f"Done. Orthomosaic: {ortho_file}, Height: {height_file}", report_fn)

    return {
        "ortho_file": ortho_file,
        "height_file": height_file,
        "checkpoint_dir": checkpoint_dir,
        "final_ply": final_ply,
        "width": W,
        "height": H,
        "gsd": resolution,
        "gsd_units": resolution_units,
        "raster_extent": [
            geo_x_min,
            geo_y_max - H * resolution,
            geo_x_min + W * resolution,
            geo_y_max,
        ],
        "projected_extent": (
            None
            if render_mode == "facade"
            else [
                geo_x_min,
                geo_y_max - H * resolution,
                geo_x_min + W * resolution,
                geo_y_max,
            ]
        ),
        "vertical_reference": vertical_reference,
        "vertical_offset_m": z_offset,
        "render_mode": render_mode,
        "coordinate_system": "LOCAL_FACADE" if render_mode == "facade" else utm_crs,
        "facade_frame_report": str(report_path) if report_path else None,
        "gaussian_coverage_report": coverage_report_path,
        "gaussian_coverage": coverage_report,
        "scale_source": scale_source,
        "meters_per_model_unit": colmap_to_meters,
        "registered_cameras": len(registered_cameras),
        "texture_cameras": texture_camera_count,
        "renderer_contract": "cupy-ortho-v2-sh-frame",
        "cupy_version": cp.__version__,
        "n_gaussians": merged_model.num_gaussians,
        "ortho_mip_filter_variance": ortho_mip_filter_variance,
        "ortho_mip_filter_compensation": ortho_mip_filter_compensation,
    }
