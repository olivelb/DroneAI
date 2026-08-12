"""
Main entry point for Gaussian Splatting orthophoto generation.

Provides `generate_gaussian_orthophoto()` with the same interface pattern
as `generate_true_orthophoto_pytorch()` from ortho_dsm.py, making it easy
to swap in from the existing pipeline.

Pipeline:
  1. Load COLMAP reconstruction + alignment transform
  2. Plan projected-ground resident core/buffer partitions when required
  3. Train Gaussian model per cell via the selected headless backend
  4. Persist core-owned cell models without a global GPU merge
  5. Render orthographic TDOM (custom CUDA rasterisation via CuPy)
  6. Write GeoTIFF
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

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
from .partition import partition_scene, plan_partition_grid
from .camera_footprint import geographic_scene_frame
from .exif_altitude import (
    extract_exif_altitudes,
    compute_colmap_scale,
    compute_colmap_scale_geodesic,
    compute_projected_geo_origin,
)
from .colmap_loader import CameraInfo, PointCloud, Sim3Transform
from .partition import CellBounds
from .scene_info import SceneInfo
from .render_geometry import GaussianRenderGeometry
from .capacity_planning import (
    GaussianCapacityPlan,
    GaussianDensityAssessment,
    assess_gaussian_density,
    detected_vram_bytes,
    plan_gaussian_capacity,
)

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
    resident_partitioning: bool
    sh_degree: int
    opacity_sh_enabled: bool
    checkpoint_dir: str
    data_factor: int
    max_width: int
    ortho_mip_filter_variance: float
    ortho_mip_filter_compensation: bool
    tile_mode: int
    cap_max: int
    capacity_mode: str
    capacity_floor: int
    target_gaussian_spacing_pixels: float
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
        cells=[(None, scene)],
        use_partition=False,
    )


def _apply_required_geographic_partition(
    scene_state: GaussianSceneState,
    config: GaussianOrthoConfig,
    *,
    required_cell_count: int,
) -> None:
    """Materialize the requested or density-required geographic grid."""
    requested_rows = max(1, int(getattr(config, "partition_m", 1)))
    requested_columns = max(1, int(getattr(config, "partition_n", 1)))
    target_cell_count = max(
        required_cell_count,
        requested_rows * requested_columns,
    )
    if target_cell_count == 1:
        return
    if (
        getattr(config, "render_mode", "map") != "map"
        or scene_state.transform_data is None
        or scene_state.scene is None
        or scene_state.point_cloud is None
    ):
        raise ValueError(
            "Partitioned Gaussian training requires a geographic Sim3 "
            "transform so blocks are defined in projected ground coordinates"
        )
    frame = geographic_scene_frame(
        scene_state.point_cloud.points,
        scene_state.transform_data,
    )
    if requested_rows * requested_columns >= target_cell_count:
        rows, columns = requested_rows, requested_columns
    else:
        rows, columns = plan_partition_grid(
            scene_state.scene,
            target_cell_count,
            model_to_ground_linear=frame.ground_linear,
            model_to_ground_offset=frame.ground_offset,
        )
    overlap = float(getattr(config, "partition_overlap", 0.20))
    _report(
        config.vol_id,
        "GAUSS",
        12,
        f"Partitioning projected ground into {rows}x{columns} "
        f"core/buffer cells (minimum {required_cell_count})…",
        config.report_fn,
    )
    cells = partition_scene(
        scene_state.scene,
        rows,
        columns,
        overlap,
        model_to_ground_linear=frame.ground_linear,
        model_to_ground_offset=frame.ground_offset,
        geographic_frame=frame,
    )
    if len(cells) < required_cell_count:
        raise RuntimeError(
            "Geographic camera visibility produced only "
            f"{len(cells)} active cells but Gaussian density requires "
            f"{required_cell_count}; inspect coverage or use a coarser GSD."
        )
    scene_state.cells = [(bounds, cell_scene) for bounds, cell_scene in cells]
    scene_state.use_partition = True
    _report(
        config.vol_id,
        "GAUSS",
        15,
        f"Created {len(cells)} footprint-visible resident cells",
        config.report_fn,
    )


@dataclass
class GaussianPartitionModel:
    """One buffer model that renders only its uniquely owned core."""

    bounds: CellBounds
    model_path: str
    gaussian_count: int
    core_gaussian_count: int


@dataclass
class GaussianTrainingState:
    merged_model: GaussianModel | None
    final_ply: str | None
    facade_subset_result: dict[str, object] | None
    partition_models: tuple[GaussianPartitionModel, ...] = ()

    @property
    def total_gaussians(self) -> int:
        if self.partition_models:
            return sum(part.core_gaussian_count for part in self.partition_models)
        if self.merged_model is None:
            return 0
        return int(self.merged_model.num_gaussians)


@dataclass(frozen=True)
class GaussianTrainingPhaseState:
    """Explicit output boundary between training and later GPU phases."""

    scene_state: GaussianSceneState
    training_state: GaussianTrainingState
    backend_name: str
    trainer_binary_sha256: str
    capacity_plan: GaussianCapacityPlan | None = None


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
    if scene_state.point_cloud is None:
        raise RuntimeError("Sparse point cloud is unavailable for capacity planning")
    detected_vram = detected_vram_bytes(cupy_module)
    overlap = float(getattr(config, "partition_overlap", 0.20))
    preliminary_plan = plan_gaussian_capacity(
        mode=config.capacity_mode,
        requested_cap=config.cap_max,
        capacity_floor=config.capacity_floor,
        target_spacing_pixels=config.target_gaussian_spacing_pixels,
        points=scene_state.point_cloud.points,
        meters_per_model_unit=scene_state.colmap_to_meters,
        requested_gsd_m=config.resolution,
        free_vram_bytes=detected_vram[0] if detected_vram else None,
        total_vram_bytes=detected_vram[1] if detected_vram else None,
        cell_count=1,
        partition_overlap=overlap,
        resident_partitioning=bool(
            getattr(config, "resident_partitioning", False)
        ),
    )
    _apply_required_geographic_partition(
        scene_state,
        config,
        required_cell_count=preliminary_plan.required_cell_count,
    )
    capacity_plan = plan_gaussian_capacity(
        mode=config.capacity_mode,
        requested_cap=config.cap_max,
        capacity_floor=config.capacity_floor,
        target_spacing_pixels=config.target_gaussian_spacing_pixels,
        points=scene_state.point_cloud.points,
        meters_per_model_unit=scene_state.colmap_to_meters,
        requested_gsd_m=config.resolution,
        free_vram_bytes=detected_vram[0] if detected_vram else None,
        total_vram_bytes=detected_vram[1] if detected_vram else None,
        cell_count=len(scene_state.cells),
        partition_overlap=overlap,
        resident_partitioning=bool(
            getattr(config, "resident_partitioning", False)
        ),
    )
    if not capacity_plan.cells_sufficient:
        raise RuntimeError(
            "Gaussian resident grid is insufficient after camera selection: "
            f"{capacity_plan.cell_count} active versus "
            f"{capacity_plan.required_cell_count} required cells."
        )
    if capacity_plan.mode == "adaptive":
        area = capacity_plan.robust_ground_area_m2 or 0.0
        vram_cap = (
            f"{capacity_plan.vram_cap:,}"
            if capacity_plan.vram_cap is not None
            else "unavailable"
        )
        _report(
            config.vol_id,
            "GAUSS",
            14,
            "Adaptive capacity: "
            f"{area:,.0f} m² at {config.resolution:.4f} m/px, "
            f"surface target {capacity_plan.surface_target:,}, "
            f"VRAM cap {vram_cap}, merged scene target "
            f"{capacity_plan.effective_scene_cap:,} "
            f"with {capacity_plan.cell_count} cells at up to "
            f"{capacity_plan.effective_cell_cap:,} resident Gaussians each "
            f"(hard resident cap {capacity_plan.resident_cap:,}).",
            config.report_fn,
        )
    training_config = replace(
        config,
        cap_max=capacity_plan.effective_cell_cap,
    )
    training_state = train_and_merge_gaussian_models(
        training_config,
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
        capacity_plan=capacity_plan,
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
    """Train cells and persist core-owned models within the resident cap."""
    if scene_state.point_cloud is None:
        raise RuntimeError("Sparse point cloud is unavailable for training")
    cell_models: list[tuple[CellBounds | None, GaussianModel]] = []
    partition_models: list[GaussianPartitionModel] = []
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
                image_crops=cell_scene.image_crops,
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
            opacity_sh_enabled=config.opacity_sh_enabled,
        )
        model.load_ply(str(training_result.ply_path))
        _report(
            config.vol_id,
            "GAUSS",
            pct_end,
            f"[{backend.name}] Loaded {model.num_gaussians} Gaussians from {training_result.ply_path}",
            config.report_fn,
        )
        if scene_state.use_partition:
            if cell_bounds is None:
                raise RuntimeError("Partitioned Gaussian cell has no core bounds")
            core_mask = cell_bounds.core_mask(
                model.positions,
                array_module=cupy_module,
            )
            core_gaussian_count = int(
                cupy_module.count_nonzero(core_mask).item()
            )
            if core_gaussian_count == 0:
                raise RuntimeError(f"Gaussian {cell_label} core retained no splats")
            buffer_path = os.path.join(cell_output, "buffer.ply")
            model.active_sh_degree = config.sh_degree
            model.save_ply(buffer_path)
            partition_models.append(
                GaussianPartitionModel(
                    bounds=cell_bounds,
                    model_path=buffer_path,
                    gaussian_count=model.num_gaussians,
                    core_gaussian_count=core_gaussian_count,
                )
            )
            _report(
                config.vol_id,
                "GAUSS",
                pct_end,
                f"[{backend.name}] Persisted {model.num_gaussians} resident "
                f"buffer Gaussians for {cell_label}; {core_gaussian_count} "
                "centres belong to its core",
                config.report_fn,
            )
            del model
            import gc

            gc.collect()
            cupy_module.get_default_memory_pool().free_all_blocks()
        else:
            cell_models.append((cell_bounds, model))

    if scene_state.use_partition:
        if not partition_models:
            raise RuntimeError("Partitioned training produced no resident core models")
        merged_model = None
        final_ply = None
        _report(
            config.vol_id,
            "GAUSS",
            85,
            f"Resident training complete: {len(partition_models)} core models, "
            f"{sum(part.core_gaussian_count for part in partition_models):,} "
            "uniquely owned core Gaussians without a global GPU merge",
            config.report_fn,
        )
    else:
        if not cell_models:
            raise RuntimeError("Gaussian training produced no model")
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
        partition_models=tuple(partition_models),
    )


@dataclass(frozen=True)
class GaussianRenderState(GaussianRenderGeometry):
    merged_model: GaussianModel


def prepare_gaussian_render_state(
    config: GaussianOrthoConfig,
    scene_state: GaussianSceneState,
    training_state: GaussianTrainingState,
    *,
    cupy_module: Any,
    release_scene: bool = True,
) -> GaussianRenderState:
    """Align, filter, and bound the trained model for raster rendering."""
    model = training_state.merged_model
    if model is None or training_state.final_ply is None:
        raise RuntimeError(
            "Partitioned Gaussian models require the resident streamed "
            "filtering path; a global GPU merge is intentionally forbidden."
        )
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
    if release_scene:
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
class GaussianFilteredPartition:
    """One filtered buffer model plus its unique output core."""

    bounds: CellBounds
    model_path: str
    gaussian_count: int
    core_gaussian_count: int
    render_extent: tuple[float, float, float, float, float, float]


@dataclass(frozen=True)
class GaussianFilteringPhaseState:
    """Filtered model plus the immutable geometry required for rasterization."""

    render_state: GaussianRenderState | None
    input_gaussians: int
    output_gaussians: int
    density_assessment: GaussianDensityAssessment | None = None
    partition_geometry: GaussianRenderGeometry | None = None
    partition_models: tuple[GaussianFilteredPartition, ...] = ()


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
    if training_phase.training_state.merged_model is None:
        raise RuntimeError(
            "Partitioned Gaussian filtering must stream resident core models"
        )
    input_gaussians = int(training_phase.training_state.merged_model.num_gaussians)
    render_state = prepare_gaussian_render_state(
        config,
        training_phase.scene_state,
        training_phase.training_state,
        cupy_module=cupy_module,
    )
    output_gaussians = int(render_state.merged_model.num_gaussians)
    density_assessment = None
    if config.render_mode == "map" and config.capacity_mode == "adaptive":
        if training_phase.capacity_plan is None:
            raise RuntimeError(
                "Adaptive Gaussian density cannot be verified because the "
                "training artifact has no capacity plan; rerun Gaussian training."
            )
        density_assessment = assess_gaussian_density(
            training_phase.capacity_plan,
            actual_gaussian_count=output_gaussians,
        )
        _report(
            config.vol_id,
            "GAUSS",
            95,
            "Achieved Gaussian density: "
            f"{density_assessment.actual_gaussian_count:,}/"
            f"{density_assessment.required_gaussian_count:,} required; "
            f"mean spacing {density_assessment.achieved_spacing_pixels:.2f} px "
            f"for target {density_assessment.target_spacing_pixels:.2f} px "
            f"({'accepted' if density_assessment.accepted else 'rejected'}).",
            config.report_fn,
        )
    return GaussianFilteringPhaseState(
        render_state=render_state,
        input_gaussians=input_gaussians,
        output_gaussians=output_gaussians,
        density_assessment=density_assessment,
    )


def _partition_core_mask_after_alignment(
    model: GaussianModel,
    bounds: CellBounds,
    geo_origin: np.ndarray,
    *,
    cupy_module: Any,
) -> Any:
    """Select unique core centres from an already Sim3-aligned map model."""
    ground_xy = model.positions[:, :2] + cupy_module.asarray(
        geo_origin[:2],
        dtype=model.positions.dtype,
    )
    x_upper = (
        ground_xy[:, 0] <= bounds.core_x_max
        if bounds.include_core_x_max
        else ground_xy[:, 0] < bounds.core_x_max
    )
    y_upper = (
        ground_xy[:, 1] <= bounds.core_y_max
        if bounds.include_core_y_max
        else ground_xy[:, 1] < bounds.core_y_max
    )
    return (
        (ground_xy[:, 0] >= bounds.core_x_min)
        & x_upper
        & (ground_xy[:, 1] >= bounds.core_y_min)
        & y_upper
    )


def execute_partitioned_gaussian_filtering_phase(
    config: GaussianOrthoConfig,
    scene_state: GaussianSceneState,
    partitions: Sequence[GaussianPartitionModel],
    capacity_plan: GaussianCapacityPlan,
    *,
    model_class: ModelFactory | None = None,
    cupy_module: Any | None = None,
) -> GaussianFilteringPhaseState:
    """Filter resident buffers sequentially and retain portable core evidence."""
    if config.render_mode != "map" or scene_state.transform_data is None:
        raise ValueError("Resident partition filtering requires a geographic map")
    if not partitions:
        raise ValueError("Resident partition filtering requires buffer models")
    if model_class is None:
        from .gaussian_model import GaussianModel

        model_class = GaussianModel
    if cupy_module is None:
        import cupy as cp

        cupy_module = cp
    filtered: list[GaussianFilteredPartition] = []
    geometry: GaussianRenderGeometry | None = None
    extents: list[tuple[float, float, float, float, float, float]] = []
    for index, partition in enumerate(partitions):
        model = model_class(
            sh_degree=config.sh_degree,
            opacity_sh_enabled=config.opacity_sh_enabled,
        )
        model.load_ply(partition.model_path)
        if model.num_gaussians > capacity_plan.resident_cap:
            raise RuntimeError(
                f"Resident cell {partition.bounds.row},{partition.bounds.col} "
                f"contains {model.num_gaussians:,} Gaussians, above the "
                f"{capacity_plan.resident_cap:,} hard cap"
            )
        render = prepare_gaussian_render_state(
            config,
            scene_state,
            GaussianTrainingState(
                merged_model=model,
                final_ply=partition.model_path,
                facade_subset_result=None,
            ),
            cupy_module=cupy_module,
            release_scene=index == len(partitions) - 1,
        )
        core_mask = _partition_core_mask_after_alignment(
            model,
            partition.bounds,
            render.geo_origin,
            cupy_module=cupy_module,
        )
        core_count = int(cupy_module.count_nonzero(core_mask).item())
        if core_count == 0:
            raise RuntimeError(
                f"Filtered resident cell {partition.bounds.row},"
                f"{partition.bounds.col} retained no core Gaussians"
            )
        filtered.append(
            GaussianFilteredPartition(
                bounds=partition.bounds,
                model_path=partition.model_path,
                gaussian_count=model.num_gaussians,
                core_gaussian_count=core_count,
                render_extent=render.render_extent,
            )
        )
        extents.append(render.render_extent)
        candidate_geometry = GaussianRenderGeometry(
            geo_origin=render.geo_origin,
            frame_origin=render.frame_origin,
            rotation_geo=render.rotation_geo,
            sh_direction_rotation=render.sh_direction_rotation,
            facade_depth_bounds_model=render.facade_depth_bounds_model,
            render_extent=render.render_extent,
            local_gsd=render.local_gsd,
            resolution_units=render.resolution_units,
            coverage_camera_positions=render.coverage_camera_positions,
        )
        if geometry is None:
            geometry = candidate_geometry
        elif not (
            np.allclose(geometry.geo_origin, candidate_geometry.geo_origin)
            and geometry.frame_origin is None
            and candidate_geometry.frame_origin is None
            and geometry.rotation_geo is None
            and candidate_geometry.rotation_geo is None
            and np.allclose(
                geometry.coverage_camera_positions,
                candidate_geometry.coverage_camera_positions,
            )
            and geometry.local_gsd == candidate_geometry.local_gsd
        ):
            raise RuntimeError("Resident Gaussian cells produced inconsistent geometry")
        del model, render, core_mask
        import gc

        gc.collect()
        cupy_module.get_default_memory_pool().free_all_blocks()
    if geometry is None:
        raise RuntimeError("Resident Gaussian filtering produced no geometry")
    global_extent = (
        min(part.bounds.core_x_min for part in filtered)
        - float(geometry.geo_origin[0]),
        max(part.bounds.core_x_max for part in filtered)
        - float(geometry.geo_origin[0]),
        min(part.bounds.core_y_min for part in filtered)
        - float(geometry.geo_origin[1]),
        max(part.bounds.core_y_max for part in filtered)
        - float(geometry.geo_origin[1]),
        min(extent[4] for extent in extents),
        max(extent[5] for extent in extents),
    )
    geometry = replace(geometry, render_extent=global_extent)
    output_gaussians = sum(part.core_gaussian_count for part in filtered)
    input_gaussians = sum(part.core_gaussian_count for part in partitions)
    density_assessment = assess_gaussian_density(
        capacity_plan,
        actual_gaussian_count=output_gaussians,
    )
    _report(
        config.vol_id,
        "GAUSS",
        95,
        f"Resident filtering retained {output_gaussians:,}/"
        f"{input_gaussians:,} unique core Gaussians across "
        f"{len(filtered)} buffers",
        config.report_fn,
    )
    return GaussianFilteringPhaseState(
        render_state=None,
        input_gaussians=input_gaussians,
        output_gaussians=output_gaussians,
        density_assessment=density_assessment,
        partition_geometry=geometry,
        partition_models=tuple(filtered),
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
    if filtering_phase.partition_models:
        return execute_partitioned_gaussian_rasterization_phase(
            config,
            filtering_phase,
            render_fn=render_fn,
        )
    density = filtering_phase.density_assessment
    if config.render_mode == "map" and config.capacity_mode == "adaptive":
        if density is None:
            raise RuntimeError(
                "Adaptive Gaussian rasterization requires a post-filter "
                "density assessment; rerun training and filtering."
            )
        if not density.accepted:
            raise RuntimeError(
                f"Requested GSD {density.requested_gsd_m:.4f} m/px is "
                "incompatible with the achieved Gaussian density: "
                f"{density.actual_gaussian_count:,} retained versus "
                f"{density.required_gaussian_count:,} required. Use at least "
                f"{density.minimum_compatible_gsd_m:.4f} m/px or increase "
                "resident capacity through geographic partitioning."
            )
    if render_fn is None:
        from .ortho_renderer import render_orthophoto

        render_fn = cast(Callable[..., dict[str, Any]], render_orthophoto)
    render_state = filtering_phase.render_state
    if render_state is None:
        raise RuntimeError("Gaussian filtering artifact has no render state")
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


def _core_pixel_window(
    bounds: CellBounds,
    geometry: GaussianRenderGeometry,
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """Snap one geographic core to the shared raster grid exactly once."""
    x_min, _x_max, _y_min, y_max, _z_min, _z_max = geometry.render_extent
    origin_x = float(geometry.geo_origin[0])
    origin_y = float(geometry.geo_origin[1])
    gsd = geometry.local_gsd
    px0 = max(0, min(width, round((bounds.core_x_min - origin_x - x_min) / gsd)))
    px1 = max(0, min(width, round((bounds.core_x_max - origin_x - x_min) / gsd)))
    py0 = max(0, min(height, round((y_max - (bounds.core_y_max - origin_y)) / gsd)))
    py1 = max(0, min(height, round((y_max - (bounds.core_y_min - origin_y)) / gsd)))
    return px0, px1, py0, py1


def execute_partitioned_gaussian_rasterization_phase(
    config: GaussianOrthoConfig,
    filtering_phase: GaussianFilteringPhaseState,
    *,
    model_class: ModelFactory | None = None,
    render_fn: Callable[..., dict[str, Any]] | None = None,
    cupy_module: Any | None = None,
) -> GaussianRasterizationPhaseState:
    """Render buffer-supported cores sequentially on one global pixel grid."""
    geometry = filtering_phase.partition_geometry
    partitions = filtering_phase.partition_models
    if geometry is None or not partitions:
        raise ValueError("Partitioned rasterization requires resident geometry")
    density = filtering_phase.density_assessment
    if config.capacity_mode == "adaptive" and (
        density is None or not density.accepted
    ):
        if density is None:
            raise RuntimeError("Resident rasterization has no density assessment")
        raise RuntimeError(
            f"Requested GSD {density.requested_gsd_m:.4f} m/px is "
            f"incompatible with {density.actual_gaussian_count:,} retained "
            "unique core Gaussians"
        )
    if model_class is None:
        from .gaussian_model import GaussianModel

        model_class = GaussianModel
    if render_fn is None:
        from .ortho_renderer import render_orthophoto

        render_fn = cast(Callable[..., dict[str, Any]], render_orthophoto)
    if cupy_module is None:
        import cupy as cp

        cupy_module = cp
    x_min, x_max, y_min, y_max, z_min, z_max = geometry.render_extent
    width = int(np.ceil((x_max - x_min) / geometry.local_gsd))
    height = int(np.ceil((y_max - y_min) / geometry.local_gsd))
    if width < 1 or height < 1:
        raise RuntimeError("Resident Gaussian raster extent is empty")
    rgb: np.ndarray = np.full((height, width, 3), 255, dtype=np.uint8)
    height_map: np.ndarray = np.full(
        (height, width),
        np.nan,
        dtype=np.float32,
    )
    for index, partition in enumerate(partitions):
        px0, px1, py0, py1 = _core_pixel_window(
            partition.bounds,
            geometry,
            width=width,
            height=height,
        )
        if px1 <= px0 or py1 <= py0:
            continue
        model = model_class(
            sh_degree=config.sh_degree,
            opacity_sh_enabled=config.opacity_sh_enabled,
        )
        model.load_ply(partition.model_path)
        tile_extent = (
            x_min + px0 * geometry.local_gsd,
            x_min + px1 * geometry.local_gsd,
            y_max - py1 * geometry.local_gsd,
            y_max - py0 * geometry.local_gsd,
            z_min,
            z_max,
        )
        tile = render_fn(
            model,
            gsd=geometry.local_gsd,
            extent=tile_extent,
            R_geo=geometry.rotation_geo,
            frame_origin=geometry.frame_origin,
            sh_direction_rotation=geometry.sh_direction_rotation,
            mip_filter_variance=config.ortho_mip_filter_variance,
            mip_filter_compensation=config.ortho_mip_filter_compensation,
        )
        expected_shape = (py1 - py0, px1 - px0)
        if tile["rgb"].shape[:2] != expected_shape:
            raise RuntimeError("Resident Gaussian core raster shape drifted")
        rgb[py0:py1, px0:px1] = tile["rgb"]
        height_map[py0:py1, px0:px1] = tile["height"]
        _report(
            config.vol_id,
            "GAUSS",
            96 + int((index + 1) / len(partitions)),
            f"Rendered resident core {index + 1}/{len(partitions)}",
            config.report_fn,
        )
        del model, tile
        import gc

        gc.collect()
        cupy_module.get_default_memory_pool().free_all_blocks()
    result: dict[str, Any] = {
        "rgb": rgb,
        "height": height_map,
        "extent": (x_min, x_min + width * geometry.local_gsd,
                   y_max - height * geometry.local_gsd, y_max),
        "gsd": geometry.local_gsd,
    }
    return GaussianRasterizationPhaseState(
        result=result,
        width=width,
        height=height,
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
    resident_partitioning: bool = False,
    sh_degree: int = 3,
    opacity_sh_enabled: bool = True,
    checkpoint_dir: str | None = None,
    data_factor: int = DRONEGS_PRODUCTION_PROFILE_V1.data_factor,
    max_width: int = DRONEGS_PRODUCTION_PROFILE_V1.max_width,
    ortho_mip_filter_variance: float = 0.03,
    ortho_mip_filter_compensation: bool = True,
    tile_mode: int = DRONEGS_PRODUCTION_PROFILE_V1.tile_mode,
    cap_max: int = DRONEGS_PRODUCTION_PROFILE_V1.cap_max,
    capacity_mode: str = "fixed",
    capacity_floor: int = DRONEGS_PRODUCTION_PROFILE_V1.cap_max,
    target_gaussian_spacing_pixels: float = 0.0,
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
    resident_partitioning : bool
        Enable projected geographic core/buffer streaming. Versioned legacy
        profiles keep this disabled; the HQ v3 candidate enables it.
    sh_degree : int
        Maximum spherical harmonics degree.
    opacity_sh_enabled : bool
        Enable view-dependent opacity-logit SH residuals. Scale and rotation
        remain view-independent; this is not full FAGK.
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
        resident_partitioning=resident_partitioning,
        sh_degree=sh_degree,
        opacity_sh_enabled=opacity_sh_enabled,
        checkpoint_dir=checkpoint_dir,
        data_factor=data_factor,
        max_width=max_width,
        ortho_mip_filter_variance=ortho_mip_filter_variance,
        ortho_mip_filter_compensation=ortho_mip_filter_compensation,
        tile_mode=tile_mode,
        cap_max=cap_max,
        capacity_mode=capacity_mode,
        capacity_floor=capacity_floor,
        target_gaussian_spacing_pixels=target_gaussian_spacing_pixels,
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
    filtering_phase = execute_gaussian_filtering_phase(
        config,
        training_phase,
        cupy_module=cp,
    )
    rasterization_phase = execute_gaussian_rasterization_phase(
        config,
        filtering_phase,
    )
    from .raster_product import (
        GaussianSceneSummary,
        finalize_gaussian_raster_product,
    )

    summary = GaussianSceneSummary(
        sim3_aligned=scene_state.transform_data is not None,
        exif_altitude_available=scene_state.mean_exif_alt is not None,
        colmap_to_meters=scene_state.colmap_to_meters,
        scale_source=scene_state.scale_source,
        facade_frame=(
            scene_state.facade_frame.as_dict()
            if scene_state.facade_frame is not None
            else None
        ),
        registered_camera_count=len(scene_state.registered_cameras),
        texture_camera_count=scene_state.texture_camera_count,
        texture_filter_applied=scene_state.texture_filter_applied,
        minimum_sparse_observations=scene_state.minimum_sparse_observations,
        seed_max_error=scene_state.seed_max_error,
        seed_min_track=scene_state.seed_min_track,
        gaussian_seed_point_count=scene_state.gaussian_seed_point_count,
        facade_subset_result=training_phase.training_state.facade_subset_result,
    )
    return finalize_gaussian_raster_product(
        config,
        filtering_phase,
        rasterization_phase,
        summary,
        final_ply=cast(str, training_phase.training_state.final_ply),
        cupy_version=cp.__version__,
    )
