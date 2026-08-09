"""Portable, checksum-bound handoffs between Gaussian stage Jobs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from shared.json_io import atomic_write_json

from .generate_gaussian_orthophoto import (
    GaussianFilteringPhaseState,
    GaussianOrthoConfig,
    GaussianRenderState,
    GaussianSceneState,
    GaussianTrainingPhaseState,
    GaussianTrainingState,
)

if TYPE_CHECKING:
    from .gaussian_model import GaussianModel


TRAINING_ARTIFACT_PATH = Path(".droneai/gaussian-training-state.json")
FILTERING_ARTIFACT_PATH = Path(".droneai/gaussian-filtering-state.json")
ARTIFACT_SCHEMA_VERSION = 1

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
    }
)


@dataclass(frozen=True)
class GaussianTrainingArtifact:
    model_path: Path
    config_sha256: str
    backend_name: str
    trainer_binary_sha256: str
    gaussian_count: int
    facade_subset_result: dict[str, object] | None


@dataclass(frozen=True)
class GaussianSceneSummary:
    sim3_aligned: bool
    exif_altitude_available: bool
    colmap_to_meters: float
    scale_source: str
    facade_frame: dict[str, object] | None
    registered_camera_count: int
    texture_camera_count: int
    texture_filter_applied: bool
    minimum_sparse_observations: int
    seed_max_error: float
    seed_min_track: int
    gaussian_seed_point_count: int
    facade_subset_result: dict[str, object] | None


@dataclass(frozen=True)
class GaussianFilteringArtifact:
    model_path: Path
    config_sha256: str
    input_gaussians: int
    output_gaussians: int
    geo_origin: np.ndarray
    frame_origin: np.ndarray | None
    rotation_geo: np.ndarray | None
    sh_direction_rotation: np.ndarray | None
    facade_depth_bounds_model: tuple[float, float] | None
    render_extent: tuple[float, float, float, float, float, float]
    local_gsd: float
    resolution_units: str
    coverage_camera_positions: np.ndarray
    scene_summary: GaussianSceneSummary


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
    model_path: str | Path,
) -> Path:
    workspace = Path(workspace_dir).resolve(strict=True)
    identity = gaussian_config_identity(config)
    payload: dict[str, object] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": "training",
        "config_identity": identity,
        "config_sha256": hashlib.sha256(_canonical(identity)).hexdigest(),
        "model_file": _relative_file(workspace, model_path),
        "backend_name": phase.backend_name,
        "trainer_binary_sha256": phase.trainer_binary_sha256,
        "gaussian_count": int(phase.training_state.merged_model.num_gaussians),
        "facade_subset_result": phase.training_state.facade_subset_result,
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
    return GaussianTrainingArtifact(
        model_path=_workspace_file(workspace, payload.get("model_file")),
        config_sha256=digest,
        backend_name=_required_str(payload, "backend_name"),
        trainer_binary_sha256=_required_str(payload, "trainer_binary_sha256"),
        gaussian_count=_required_int(payload, "gaussian_count"),
        facade_subset_result=cast(dict[str, object] | None, subset),
    )


def hydrate_training_phase(
    artifact: GaussianTrainingArtifact,
    scene_state: GaussianSceneState,
    model: GaussianModel,
) -> GaussianTrainingPhaseState:
    """Rebuild the in-memory boundary after the model was loaded on this GPU."""
    if model.num_gaussians != artifact.gaussian_count:
        raise ValueError("Loaded Gaussian count does not match the training artifact")
    return GaussianTrainingPhaseState(
        scene_state=scene_state,
        training_state=GaussianTrainingState(
            merged_model=model,
            final_ply=str(artifact.model_path),
            facade_subset_result=artifact.facade_subset_result,
        ),
        backend_name=artifact.backend_name,
        trainer_binary_sha256=artifact.trainer_binary_sha256,
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
    model_path: str | Path,
) -> Path:
    workspace = Path(workspace_dir).resolve(strict=True)
    identity = gaussian_config_identity(config)
    render = filtering_phase.render_state
    payload: dict[str, object] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": "filtering",
        "config_identity": identity,
        "config_sha256": hashlib.sha256(_canonical(identity)).hexdigest(),
        "model_file": _relative_file(workspace, model_path),
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
    return GaussianFilteringArtifact(
        model_path=_workspace_file(workspace, payload.get("model_file")),
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
    )


def hydrate_filtering_phase(
    artifact: GaussianFilteringArtifact,
    model: GaussianModel,
) -> GaussianFilteringPhaseState:
    """Rebuild a raster-ready state without repeating alignment or filtering."""
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
    )
