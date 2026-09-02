"""Portable, checksum-bound handoffs between Gaussian stage Jobs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from shared.json_io import atomic_write_json
from shared.checksums import sha256_file

from .capacity_planning import (
    GaussianCapacityPlan,
    GaussianDensityAssessment,
    capacity_plan_from_dict,
    density_assessment_from_dict,
)

from .generate_gaussian_orthophoto import (
    GaussianFilteringPhaseState,
    GaussianFilteredPartition,
    GaussianOrthoConfig,
    GaussianPartitionModel,
    GaussianRenderState,
    GaussianSceneState,
    GaussianTrainingPhaseState,
    GaussianTrainingState,
)
from .colmap_loader import CameraInfo, Sim3Transform
from .facade_frame import FacadeFrame
from .partition import CellBounds, cell_bounds_from_dict
from .raster_product import GaussianSceneSummary
from .render_geometry import GaussianRenderGeometry

if TYPE_CHECKING:
    from .gaussian_model import GaussianModel


TRAINING_ARTIFACT_PATH = Path(".droneai/gaussian-training-state.json")
FILTERING_ARTIFACT_PATH = Path(".droneai/gaussian-filtering-state.json")
FILTER_SCENE_ARTIFACT_PATH = Path(".droneai/gaussian-filter-scene.json")
ARTIFACT_SCHEMA_VERSION = 3
FILTER_SCENE_SCHEMA_VERSION = 1

_RUNTIME_CONFIG_FIELDS = frozenset(
    {
        "dense_path",
        "ortho_file",
        "vol_id",
        "transform_file",
        "report_fn",
        "checkpoint_dir",
        "cancellation_check",
        "checkpoint_callback",
        "facade_frame_report",
        "density_gate_enabled",
    }
)


@dataclass(frozen=True)
class GaussianPartitionArtifact:
    bounds: CellBounds
    model_path: Path
    gaussian_count: int
    core_gaussian_count: int


@dataclass(frozen=True)
class GaussianTrainingArtifact:
    model_path: Path | None
    config_sha256: str
    backend_name: str
    trainer_binary_sha256: str
    gaussian_count: int
    facade_subset_result: dict[str, object] | None
    filter_scene_path: Path
    filter_scene_checksum_sha256: str
    capacity_plan: GaussianCapacityPlan | None = None
    partition_models: tuple[GaussianPartitionArtifact, ...] = ()
    quality_alerts: tuple[dict[str, object], ...] = ()

@dataclass(frozen=True)
class GaussianFilteredPartitionArtifact:
    bounds: CellBounds
    model_path: Path
    gaussian_count: int
    core_gaussian_count: int
    render_extent: tuple[float, float, float, float, float, float]
    facade_depth_bounds_model: tuple[float, float] | None = None


@dataclass(frozen=True)
class GaussianFilteringArtifact(GaussianRenderGeometry):
    model_path: Path | None
    config_sha256: str
    input_gaussians: int
    output_gaussians: int
    scene_summary: GaussianSceneSummary
    density_assessment: GaussianDensityAssessment | None = None
    partition_models: tuple[GaussianFilteredPartitionArtifact, ...] = ()


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def gaussian_config_identity(config: GaussianOrthoConfig) -> dict[str, object]:
    """Return only deterministic product parameters, excluding Job-local state."""
    identity: dict[str, object] = {}
    for field in fields(config):
        if field.name in _RUNTIME_CONFIG_FIELDS:
            continue
        value = getattr(config, field.name)
        if value is not None and not isinstance(value, (bool, float, int, str)):
            raise TypeError(f"Gaussian config field is not portable: {field.name}")
        identity[field.name] = value
    return identity


def gaussian_config_sha256(config: GaussianOrthoConfig) -> str:
    return hashlib.sha256(_canonical(gaussian_config_identity(config))).hexdigest()


def _relative_file(workspace: Path, raw_path: str | Path) -> str:
    path = Path(raw_path).resolve(strict=True)
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError as error:
        raise ValueError("Gaussian artifact file must be inside its workspace") from error


def _workspace_file(workspace: Path, raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("Gaussian artifact contains an invalid file path")
    path = (workspace / raw_path).resolve()
    if workspace not in path.parents or not path.is_file():
        raise ValueError("Gaussian artifact file is missing or escapes its workspace")
    return path


def _read_payload(path: Path, expected_kind: str) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION
        or payload.get("kind") != expected_kind
    ):
        raise ValueError(f"Unsupported Gaussian {expected_kind} artifact schema")
    return cast(dict[str, Any], payload)


def _camera_payload(camera: CameraInfo) -> dict[str, object]:
    return {
        "uid": int(camera.uid),
        "image_name": camera.image_name,
        "width": int(camera.width),
        "height": int(camera.height),
        "fx": float(camera.fx),
        "fy": float(camera.fy),
        "cx": float(camera.cx),
        "cy": float(camera.cy),
        "R": cast(list[object], np.asarray(camera.R).tolist()),
        "T": cast(list[object], np.asarray(camera.T).tolist()),
        "sparse_observations": int(camera.sparse_observations),
        "camera_model": camera.camera_model,
    }


def _sim3_payload(transform: Sim3Transform | None) -> dict[str, object] | None:
    if transform is None:
        return None
    return {
        "R": cast(list[object], np.asarray(transform["R"]).tolist()),
        "scale": float(transform["scale"]),
        "t": cast(list[object], np.asarray(transform["t"]).tolist()),
    }


def write_filter_scene_artifact(
    workspace_dir: str | Path,
    scene: GaussianSceneState,
) -> Path:
    """Persist only the immutable scene geometry needed after training."""

    workspace = Path(workspace_dir).resolve(strict=True)
    payload: dict[str, object] = {
        "schema_version": FILTER_SCENE_SCHEMA_VERSION,
        "kind": "gaussian-filter-scene",
        "registered_cameras": [
            _camera_payload(camera) for camera in scene.registered_cameras
        ],
        "transform": _sim3_payload(scene.transform_data),
        "mean_exif_alt": scene.mean_exif_alt,
        "colmap_to_meters": float(scene.colmap_to_meters),
        "scale_source": scene.scale_source,
        "facade_frame": (
            scene.facade_frame.as_dict() if scene.facade_frame is not None else None
        ),
        "texture_camera_count": int(scene.texture_camera_count),
        "texture_filter_applied": bool(scene.texture_filter_applied),
        "minimum_sparse_observations": int(scene.minimum_sparse_observations),
        "seed_max_error": float(scene.seed_max_error),
        "seed_min_track": int(scene.seed_min_track),
        "gaussian_seed_point_count": int(scene.gaussian_seed_point_count),
        "pca_rotation_geo": _optional_array(scene.pca_rotation_geo),
        "pca_alignment_angle_deg": scene.pca_alignment_angle_deg,
        "projected_geo_origin": _optional_array(scene.projected_geo_origin),
    }
    path = workspace / FILTER_SCENE_ARTIFACT_PATH
    atomic_write_json(path, payload)
    return path


def _required_int(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Gaussian artifact integer is invalid: {name}")
    return value


def _required_float(payload: dict[str, Any], name: str) -> float:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"Gaussian artifact number is invalid: {name}")
    return float(value)


def _required_str(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Gaussian artifact string is invalid: {name}")
    return value


def _quality_alerts(payload: dict[str, Any]) -> tuple[dict[str, object], ...]:
    raw_alerts = payload.get("quality_alerts", [])
    if not isinstance(raw_alerts, list):
        raise ValueError("Gaussian training quality alerts are invalid")
    alerts: list[dict[str, object]] = []
    for raw in raw_alerts:
        if not isinstance(raw, dict):
            raise ValueError("Gaussian training quality alert is invalid")
        alert = cast(dict[str, object], raw)
        failed = alert.get("failed_metrics")
        if (
            alert.get("severity") != "warning"
            or not isinstance(alert.get("cell"), str)
            or not isinstance(failed, list)
            or not failed
            or any(not isinstance(metric, str) for metric in failed)
        ):
            raise ValueError("Gaussian training quality alert is inconsistent")
        alerts.append(alert)
    return tuple(alerts)


def _verified_config_sha256(
    payload: dict[str, Any],
    config: GaussianOrthoConfig,
) -> str:
    identity = payload.get("config_identity")
    digest = payload.get("config_sha256")
    if not isinstance(identity, dict) or not isinstance(digest, str):
        raise ValueError("Gaussian artifact config identity is invalid")
    actual = hashlib.sha256(_canonical(cast(dict[str, object], identity))).hexdigest()
    expected = gaussian_config_sha256(config)
    if actual != digest or digest != expected:
        raise ValueError("Gaussian artifact config identity does not match this stage")
    return digest


def write_training_artifact(
    workspace_dir: str | Path,
    config: GaussianOrthoConfig,
    phase: GaussianTrainingPhaseState,
    *,
    model_path: str | Path | None,
    filter_scene_path: str | Path,
) -> Path:
    workspace = Path(workspace_dir).resolve(strict=True)
    identity = gaussian_config_identity(config)
    capacity_plan = getattr(phase, "capacity_plan", None)
    partition_models = [
        {
            "bounds": partition.bounds.as_dict(),
            "model_file": _relative_file(workspace, partition.model_path),
            "gaussian_count": partition.gaussian_count,
            "core_gaussian_count": partition.core_gaussian_count,
        }
        for partition in getattr(phase.training_state, "partition_models", ())
    ]
    total_gaussians = getattr(phase.training_state, "total_gaussians", None)
    if total_gaussians is None:
        merged_model = phase.training_state.merged_model
        if merged_model is None:
            raise ValueError("Gaussian training state has no population")
        total_gaussians = int(merged_model.num_gaussians)
    if model_path is None and not partition_models:
        raise ValueError("Gaussian training artifact has no resident model")
    if model_path is not None and partition_models:
        raise ValueError("Gaussian training artifact cannot mix model layouts")
    payload: dict[str, object] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": "training",
        "config_identity": identity,
        "config_sha256": hashlib.sha256(_canonical(identity)).hexdigest(),
        "model_file": (
            _relative_file(workspace, model_path)
            if model_path is not None
            else None
        ),
        "partition_models": partition_models,
        "backend_name": phase.backend_name,
        "trainer_binary_sha256": phase.trainer_binary_sha256,
        "gaussian_count": int(total_gaussians),
        "facade_subset_result": phase.training_state.facade_subset_result,
        "quality_alerts": list(
            getattr(phase.training_state, "quality_alerts", ())
        ),
        "filter_scene_file": _relative_file(workspace, filter_scene_path),
        "filter_scene_sha256": str(sha256_file(filter_scene_path)),
        "capacity_plan": (
            capacity_plan.as_dict()
            if capacity_plan is not None
            else None
        ),
    }
    path = workspace / TRAINING_ARTIFACT_PATH
    atomic_write_json(path, payload)
    return path


def read_training_artifact(
    workspace_dir: str | Path,
    config: GaussianOrthoConfig,
) -> GaussianTrainingArtifact:
    workspace = Path(workspace_dir).resolve(strict=True)
    payload = _read_payload(workspace / TRAINING_ARTIFACT_PATH, "training")
    digest = _verified_config_sha256(payload, config)
    subset = payload.get("facade_subset_result")
    if subset is not None and not isinstance(subset, dict):
        raise ValueError("Gaussian training facade subset result is invalid")
    raw_partitions = payload.get("partition_models", [])
    if not isinstance(raw_partitions, list):
        raise ValueError("Gaussian training partition list is invalid")
    partitions: list[GaussianPartitionArtifact] = []
    for raw in raw_partitions:
        if not isinstance(raw, dict):
            raise ValueError("Gaussian training partition entry is invalid")
        entry = cast(dict[str, Any], raw)
        partitions.append(
            GaussianPartitionArtifact(
                bounds=cell_bounds_from_dict(entry.get("bounds")),
                model_path=_workspace_file(workspace, entry.get("model_file")),
                gaussian_count=_required_int(entry, "gaussian_count"),
                core_gaussian_count=_required_int(entry, "core_gaussian_count"),
            )
        )
    raw_model = payload.get("model_file")
    model_path = (
        _workspace_file(workspace, raw_model)
        if raw_model is not None
        else None
    )
    if (model_path is None) == (not partitions):
        raise ValueError("Gaussian training artifact model layout is ambiguous")
    filter_scene_path = _workspace_file(
        workspace,
        payload.get("filter_scene_file"),
    )
    filter_scene_checksum = payload.get("filter_scene_sha256")
    if not isinstance(filter_scene_checksum, str) or len(filter_scene_checksum) != 64:
        raise ValueError("Gaussian training filter scene checksum is invalid")
    if str(sha256_file(filter_scene_path)) != filter_scene_checksum:
        raise ValueError("Gaussian training filter scene checksum does not match")
    artifact = GaussianTrainingArtifact(
        model_path=model_path,
        config_sha256=digest,
        backend_name=_required_str(payload, "backend_name"),
        trainer_binary_sha256=_required_str(payload, "trainer_binary_sha256"),
        gaussian_count=_required_int(payload, "gaussian_count"),
        facade_subset_result=cast(dict[str, object] | None, subset),
        capacity_plan=(
            capacity_plan_from_dict(payload["capacity_plan"])
            if payload.get("capacity_plan") is not None
            else None
        ),
        partition_models=tuple(partitions),
        quality_alerts=_quality_alerts(payload),
        filter_scene_path=filter_scene_path,
        filter_scene_checksum_sha256=filter_scene_checksum,
    )
    if sum(part.core_gaussian_count for part in partitions) not in {
        0,
        artifact.gaussian_count,
    }:
        raise ValueError("Gaussian partition counts do not match their artifact")
    return artifact


def hydrate_training_phase(
    artifact: GaussianTrainingArtifact,
    scene_state: GaussianSceneState,
    model: GaussianModel,
) -> GaussianTrainingPhaseState:
    """Rebuild the in-memory boundary after the model was loaded on this GPU."""
    if artifact.model_path is None or artifact.partition_models:
        raise ValueError("A partitioned training artifact cannot be globally hydrated")
    if model.num_gaussians != artifact.gaussian_count:
        raise ValueError("Loaded Gaussian count does not match the training artifact")
    return GaussianTrainingPhaseState(
        scene_state=scene_state,
        training_state=GaussianTrainingState(
            merged_model=model,
            final_ply=str(artifact.model_path),
            facade_subset_result=artifact.facade_subset_result,
            partition_models=(),
            quality_alerts=artifact.quality_alerts,
        ),
        backend_name=artifact.backend_name,
        trainer_binary_sha256=artifact.trainer_binary_sha256,
        capacity_plan=artifact.capacity_plan,
    )


def hydrate_partitioned_training_phase(
    artifact: GaussianTrainingArtifact,
    scene_state: GaussianSceneState,
) -> GaussianTrainingPhaseState:
    """Rebuild resident training metadata without loading every GPU model."""
    if artifact.model_path is not None or not artifact.partition_models:
        raise ValueError("Gaussian training artifact is not partitioned")
    return GaussianTrainingPhaseState(
        scene_state=scene_state,
        training_state=GaussianTrainingState(
            merged_model=None,
            final_ply=None,
            facade_subset_result=artifact.facade_subset_result,
            partition_models=tuple(
                GaussianPartitionModel(
                    bounds=partition.bounds,
                    model_path=str(partition.model_path),
                    gaussian_count=partition.gaussian_count,
                    core_gaussian_count=partition.core_gaussian_count,
                )
                for partition in artifact.partition_models
            ),
            quality_alerts=artifact.quality_alerts,
        ),
        backend_name=artifact.backend_name,
        trainer_binary_sha256=artifact.trainer_binary_sha256,
        capacity_plan=artifact.capacity_plan,
    )


def _optional_array(value: np.ndarray | None) -> list[object] | None:
    return cast(list[object], value.tolist()) if value is not None else None


def _scene_summary(phase: GaussianTrainingPhaseState) -> dict[str, object]:
    scene = phase.scene_state
    frame = scene.facade_frame.as_dict() if scene.facade_frame is not None else None
    return {
        "sim3_aligned": scene.transform_data is not None,
        "exif_altitude_available": scene.mean_exif_alt is not None,
        "colmap_to_meters": float(scene.colmap_to_meters),
        "scale_source": scene.scale_source,
        "facade_frame": frame,
        "registered_camera_count": len(scene.registered_cameras),
        "texture_camera_count": scene.texture_camera_count,
        "texture_filter_applied": scene.texture_filter_applied,
        "minimum_sparse_observations": scene.minimum_sparse_observations,
        "seed_max_error": scene.seed_max_error,
        "seed_min_track": scene.seed_min_track,
        "gaussian_seed_point_count": scene.gaussian_seed_point_count,
        "facade_subset_result": phase.training_state.facade_subset_result,
    }


def write_filtering_artifact(
    workspace_dir: str | Path,
    config: GaussianOrthoConfig,
    training_phase: GaussianTrainingPhaseState,
    filtering_phase: GaussianFilteringPhaseState,
    *,
    model_path: str | Path | None,
) -> Path:
    workspace = Path(workspace_dir).resolve(strict=True)
    identity = gaussian_config_identity(config)
    render = filtering_phase.render_state or getattr(
        filtering_phase,
        "partition_geometry",
        None,
    )
    if render is None:
        raise ValueError("Gaussian filtering phase has no render geometry")
    partitions = [
        {
            "bounds": partition.bounds.as_dict(),
            "model_file": _relative_file(workspace, partition.model_path),
            "gaussian_count": partition.gaussian_count,
            "core_gaussian_count": partition.core_gaussian_count,
            "render_extent": list(partition.render_extent),
            "facade_depth_bounds_model": (
                list(partition.facade_depth_bounds_model)
                if partition.facade_depth_bounds_model is not None
                else None
            ),
        }
        for partition in getattr(filtering_phase, "partition_models", ())
    ]
    if model_path is None and not partitions:
        raise ValueError("Gaussian filtering artifact has no resident model")
    if model_path is not None and partitions:
        raise ValueError("Gaussian filtering artifact cannot mix model layouts")
    density_assessment = getattr(filtering_phase, "density_assessment", None)
    payload: dict[str, object] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": "filtering",
        "config_identity": identity,
        "config_sha256": hashlib.sha256(_canonical(identity)).hexdigest(),
        "model_file": (
            _relative_file(workspace, model_path)
            if model_path is not None
            else None
        ),
        "partition_models": partitions,
        "input_gaussians": filtering_phase.input_gaussians,
        "output_gaussians": filtering_phase.output_gaussians,
        "geo_origin": cast(list[object], render.geo_origin.tolist()),
        "frame_origin": _optional_array(render.frame_origin),
        "rotation_geo": _optional_array(render.rotation_geo),
        "sh_direction_rotation": _optional_array(render.sh_direction_rotation),
        "facade_depth_bounds_model": (
            list(render.facade_depth_bounds_model)
            if render.facade_depth_bounds_model is not None
            else None
        ),
        "render_extent": list(render.render_extent),
        "local_gsd": render.local_gsd,
        "resolution_units": render.resolution_units,
        "coverage_camera_positions": cast(
            list[object], render.coverage_camera_positions.tolist()
        ),
        "scene_summary": _scene_summary(training_phase),
        "density_assessment": (
            density_assessment.as_dict()
            if density_assessment is not None
            else None
        ),
    }
    path = workspace / FILTERING_ARTIFACT_PATH
    atomic_write_json(path, payload)
    return path


def _array(
    payload: dict[str, Any],
    name: str,
    shape: tuple[int | None, ...],
    *,
    optional: bool = False,
) -> np.ndarray | None:
    raw = payload.get(name)
    if raw is None and optional:
        return None
    try:
        value = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Gaussian artifact array is invalid: {name}") from error
    if value.ndim != len(shape) or any(
        expected is not None and value.shape[index] != expected
        for index, expected in enumerate(shape)
    ):
        raise ValueError(f"Gaussian artifact array has invalid shape: {name}")
    if not np.all(np.isfinite(value)):
        raise ValueError(f"Gaussian artifact array is not finite: {name}")
    return cast(np.ndarray, value)


def _required_bool(payload: dict[str, Any], name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"Gaussian artifact boolean is invalid: {name}")
    return value


def _optional_finite_float(payload: dict[str, Any], name: str) -> float | None:
    raw = payload.get(name)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (float, int)):
        raise ValueError(f"Gaussian artifact number is invalid: {name}")
    value = float(raw)
    if not np.isfinite(value):
        raise ValueError(f"Gaussian artifact number is not finite: {name}")
    return value


def _facade_frame_from_payload(raw: object) -> FacadeFrame | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Gaussian filter scene facade frame is invalid")
    payload = cast(dict[str, Any], raw)
    origin = _array(payload, "origin_model_units", (3,))
    rotation = _array(payload, "world_to_facade", (3, 3))
    if origin is None or rotation is None:
        raise ValueError("Gaussian filter scene facade frame is incomplete")
    return FacadeFrame(
        origin=origin,
        world_to_facade=rotation,
        inlier_ratio=_required_float(payload, "plane_inlier_ratio"),
        plane_rmse=_required_float(payload, "plane_rmse_model_units"),
        camera_side_ratio=_required_float(payload, "camera_side_ratio"),
        median_view_incidence_deg=_required_float(
            payload, "median_view_incidence_deg"
        ),
        p90_view_incidence_deg=_required_float(payload, "p90_view_incidence_deg"),
        orientation_source=_required_str(payload, "orientation_source"),
    )


def _sim3_from_payload(raw: object) -> Sim3Transform | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Gaussian filter scene transform is invalid")
    payload = cast(dict[str, Any], raw)
    rotation = _array(payload, "R", (3, 3))
    translation = _array(payload, "t", (3,))
    scale = _required_float(payload, "scale")
    if rotation is None or translation is None or scale <= 0:
        raise ValueError("Gaussian filter scene transform is incomplete")
    return {
        "R": rotation.tolist(),
        "scale": scale,
        "t": translation.tolist(),
    }


def read_filter_scene_artifact(
    workspace_dir: str | Path,
    raw_path: str | Path,
    expected_checksum_sha256: str,
    *,
    render_mode: str,
) -> GaussianSceneState:
    """Hydrate a filter-only scene without COLMAP databases or images."""

    workspace = Path(workspace_dir).resolve(strict=True)
    path = Path(raw_path).resolve(strict=True)
    try:
        path.relative_to(workspace)
    except ValueError as error:
        raise ValueError("Gaussian filter scene escapes its workspace") from error
    actual_checksum = str(sha256_file(path))
    if actual_checksum != expected_checksum_sha256:
        raise ValueError("Gaussian filter scene checksum does not match training state")
    with path.open(encoding="utf-8") as handle:
        raw_payload = json.load(handle)
    if (
        not isinstance(raw_payload, dict)
        or raw_payload.get("schema_version") != FILTER_SCENE_SCHEMA_VERSION
        or raw_payload.get("kind") != "gaussian-filter-scene"
    ):
        raise ValueError("Unsupported Gaussian filter scene schema")
    payload = cast(dict[str, Any], raw_payload)
    raw_cameras = payload.get("registered_cameras")
    if not isinstance(raw_cameras, list) or not raw_cameras:
        raise ValueError("Gaussian filter scene has no registered cameras")
    cameras: list[CameraInfo] = []
    for raw_camera in raw_cameras:
        if not isinstance(raw_camera, dict):
            raise ValueError("Gaussian filter scene camera is invalid")
        camera = cast(dict[str, Any], raw_camera)
        rotation = _array(camera, "R", (3, 3))
        translation = _array(camera, "T", (3,))
        if rotation is None or translation is None:
            raise ValueError("Gaussian filter scene camera is incomplete")
        width = _required_int(camera, "width")
        height = _required_int(camera, "height")
        if width <= 0 or height <= 0:
            raise ValueError("Gaussian filter scene camera dimensions are invalid")
        cameras.append(
            CameraInfo(
                uid=_required_int(camera, "uid"),
                image_name=_required_str(camera, "image_name"),
                width=width,
                height=height,
                fx=_required_float(camera, "fx"),
                fy=_required_float(camera, "fy"),
                cx=_required_float(camera, "cx"),
                cy=_required_float(camera, "cy"),
                R=rotation.astype(np.float32),
                T=translation.astype(np.float32),
                sparse_observations=_required_int(camera, "sparse_observations"),
                camera_model=_required_str(camera, "camera_model"),
            )
        )
    transform = _sim3_from_payload(payload.get("transform"))
    facade_frame = _facade_frame_from_payload(payload.get("facade_frame"))
    pca_rotation = _array(payload, "pca_rotation_geo", (3, 3), optional=True)
    projected_origin = _array(
        payload,
        "projected_geo_origin",
        (3,),
        optional=True,
    )
    pca_angle = _optional_finite_float(payload, "pca_alignment_angle_deg")
    if render_mode == "facade" and facade_frame is None:
        raise ValueError("Portable facade filtering requires its fitted frame")
    if render_mode == "map" and transform is None and pca_rotation is None:
        raise ValueError("Portable map filtering requires cached PCA geometry")
    colmap_to_meters = _required_float(payload, "colmap_to_meters")
    if colmap_to_meters <= 0:
        raise ValueError("Gaussian filter scene scale must be positive")
    return GaussianSceneState(
        train_cameras=[],
        test_cameras=[],
        registered_cameras=cameras,
        point_cloud=None,
        transform_data=transform,
        mean_exif_alt=_optional_finite_float(payload, "mean_exif_alt"),
        colmap_to_meters=colmap_to_meters,
        scale_source=_required_str(payload, "scale_source"),
        facade_frame=facade_frame,
        texture_camera_count=_required_int(payload, "texture_camera_count"),
        texture_filter_applied=_required_bool(payload, "texture_filter_applied"),
        minimum_sparse_observations=_required_int(
            payload, "minimum_sparse_observations"
        ),
        seed_max_error=_required_float(payload, "seed_max_error"),
        seed_min_track=_required_int(payload, "seed_min_track"),
        gaussian_seed_point_count=_required_int(
            payload, "gaussian_seed_point_count"
        ),
        images_dir="",
        scene=None,
        cells=[],
        use_partition=False,
        pca_rotation_geo=pca_rotation,
        pca_alignment_angle_deg=pca_angle,
        projected_geo_origin=projected_origin,
    )


def _scene_summary_from_payload(raw: object) -> GaussianSceneSummary:
    if not isinstance(raw, dict):
        raise ValueError("Gaussian filtering scene summary is invalid")
    payload = cast(dict[str, Any], raw)
    frame = payload.get("facade_frame")
    subset = payload.get("facade_subset_result")
    if frame is not None and not isinstance(frame, dict):
        raise ValueError("Gaussian facade frame summary is invalid")
    if subset is not None and not isinstance(subset, dict):
        raise ValueError("Gaussian facade subset summary is invalid")
    return GaussianSceneSummary(
        sim3_aligned=bool(payload["sim3_aligned"]),
        exif_altitude_available=bool(payload["exif_altitude_available"]),
        colmap_to_meters=float(payload["colmap_to_meters"]),
        scale_source=str(payload["scale_source"]),
        facade_frame=cast(dict[str, object] | None, frame),
        registered_camera_count=int(payload["registered_camera_count"]),
        texture_camera_count=int(payload["texture_camera_count"]),
        texture_filter_applied=bool(payload["texture_filter_applied"]),
        minimum_sparse_observations=int(payload["minimum_sparse_observations"]),
        seed_max_error=float(payload["seed_max_error"]),
        seed_min_track=int(payload["seed_min_track"]),
        gaussian_seed_point_count=int(payload["gaussian_seed_point_count"]),
        facade_subset_result=cast(dict[str, object] | None, subset),
    )


def read_filtering_artifact(
    workspace_dir: str | Path,
    config: GaussianOrthoConfig,
) -> GaussianFilteringArtifact:
    workspace = Path(workspace_dir).resolve(strict=True)
    payload = _read_payload(workspace / FILTERING_ARTIFACT_PATH, "filtering")
    digest = _verified_config_sha256(payload, config)
    geo_origin = _array(payload, "geo_origin", (3,))
    render_extent = _array(payload, "render_extent", (6,))
    coverage_positions = _array(payload, "coverage_camera_positions", (None, 3))
    depth_bounds = _array(payload, "facade_depth_bounds_model", (2,), optional=True)
    if geo_origin is None or render_extent is None or coverage_positions is None:
        raise ValueError("Gaussian filtering artifact is incomplete")
    raw_partitions = payload.get("partition_models", [])
    if not isinstance(raw_partitions, list):
        raise ValueError("Gaussian filtering partition list is invalid")
    partitions: list[GaussianFilteredPartitionArtifact] = []
    for raw in raw_partitions:
        if not isinstance(raw, dict):
            raise ValueError("Gaussian filtering partition entry is invalid")
        entry = cast(dict[str, Any], raw)
        extent = _array(entry, "render_extent", (6,))
        partition_depth_bounds = _array(
            entry,
            "facade_depth_bounds_model",
            (2,),
            optional=True,
        )
        if extent is None:
            raise ValueError("Gaussian partition render extent is missing")
        partitions.append(
            GaussianFilteredPartitionArtifact(
                bounds=cell_bounds_from_dict(entry.get("bounds")),
                model_path=_workspace_file(workspace, entry.get("model_file")),
                gaussian_count=_required_int(entry, "gaussian_count"),
                core_gaussian_count=_required_int(entry, "core_gaussian_count"),
                render_extent=cast(
                    tuple[float, float, float, float, float, float],
                    tuple(float(value) for value in extent),
                ),
                facade_depth_bounds_model=(
                    (
                        float(partition_depth_bounds[0]),
                        float(partition_depth_bounds[1]),
                    )
                    if partition_depth_bounds is not None
                    else None
                ),
            )
        )
    raw_model = payload.get("model_file")
    model_path = (
        _workspace_file(workspace, raw_model)
        if raw_model is not None
        else None
    )
    if (model_path is None) == (not partitions):
        raise ValueError("Gaussian filtering artifact model layout is ambiguous")
    return GaussianFilteringArtifact(
        model_path=model_path,
        config_sha256=digest,
        input_gaussians=_required_int(payload, "input_gaussians"),
        output_gaussians=_required_int(payload, "output_gaussians"),
        geo_origin=geo_origin,
        frame_origin=_array(payload, "frame_origin", (3,), optional=True),
        rotation_geo=_array(payload, "rotation_geo", (3, 3), optional=True),
        sh_direction_rotation=_array(
            payload,
            "sh_direction_rotation",
            (3, 3),
            optional=True,
        ),
        facade_depth_bounds_model=(
            (float(depth_bounds[0]), float(depth_bounds[1]))
            if depth_bounds is not None
            else None
        ),
        render_extent=cast(
            tuple[float, float, float, float, float, float],
            tuple(float(item) for item in render_extent),
        ),
        local_gsd=_required_float(payload, "local_gsd"),
        resolution_units=_required_str(payload, "resolution_units"),
        coverage_camera_positions=coverage_positions,
        scene_summary=_scene_summary_from_payload(payload.get("scene_summary")),
        density_assessment=(
            density_assessment_from_dict(payload["density_assessment"])
            if payload.get("density_assessment") is not None
            else None
        ),
        partition_models=tuple(partitions),
    )


def hydrate_filtering_phase(
    artifact: GaussianFilteringArtifact,
    model: GaussianModel,
) -> GaussianFilteringPhaseState:
    """Rebuild a raster-ready state without repeating alignment or filtering."""
    if artifact.model_path is None or artifact.partition_models:
        raise ValueError("A partitioned filtering artifact cannot be globally hydrated")
    if model.num_gaussians != artifact.output_gaussians:
        raise ValueError("Loaded Gaussian count does not match the filtering artifact")
    return GaussianFilteringPhaseState(
        render_state=GaussianRenderState(
            merged_model=model,
            geo_origin=artifact.geo_origin,
            frame_origin=artifact.frame_origin,
            rotation_geo=artifact.rotation_geo,
            sh_direction_rotation=artifact.sh_direction_rotation,
            facade_depth_bounds_model=artifact.facade_depth_bounds_model,
            render_extent=artifact.render_extent,
            local_gsd=artifact.local_gsd,
            resolution_units=artifact.resolution_units,
            coverage_camera_positions=artifact.coverage_camera_positions,
        ),
        input_gaussians=artifact.input_gaussians,
        output_gaussians=artifact.output_gaussians,
        density_assessment=artifact.density_assessment,
    )


def hydrate_partitioned_filtering_phase(
    artifact: GaussianFilteringArtifact,
) -> GaussianFilteringPhaseState:
    """Rebuild a streamable resident raster boundary without loading a model."""
    if artifact.model_path is not None or not artifact.partition_models:
        raise ValueError("Gaussian filtering artifact is not partitioned")
    geometry = GaussianRenderGeometry(
        geo_origin=artifact.geo_origin,
        frame_origin=artifact.frame_origin,
        rotation_geo=artifact.rotation_geo,
        sh_direction_rotation=artifact.sh_direction_rotation,
        facade_depth_bounds_model=artifact.facade_depth_bounds_model,
        render_extent=artifact.render_extent,
        local_gsd=artifact.local_gsd,
        resolution_units=artifact.resolution_units,
        coverage_camera_positions=artifact.coverage_camera_positions,
    )
    return GaussianFilteringPhaseState(
        render_state=None,
        input_gaussians=artifact.input_gaussians,
        output_gaussians=artifact.output_gaussians,
        density_assessment=artifact.density_assessment,
        partition_geometry=geometry,
        partition_models=tuple(
            GaussianFilteredPartition(
                bounds=partition.bounds,
                model_path=str(partition.model_path),
                gaussian_count=partition.gaussian_count,
                core_gaussian_count=partition.core_gaussian_count,
                render_extent=partition.render_extent,
                facade_depth_bounds_model=(
                    partition.facade_depth_bounds_model
                ),
            )
            for partition in artifact.partition_models
        ),
    )
