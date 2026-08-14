"""Strict comparison of controlled DroneGS qualification runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .manifest_contract import load_run_manifest, validate_run_manifest


QUALIFICATION_VARIANT_PARAMETERS = frozenset(
    {
        "optimizer_profile",
        "growth_score",
        "absgrad_guidance",
        "absgrad_normalization",
        "absgrad_score_weight",
    }
)

PERFORMANCE_VARIANT_PARAMETERS = frozenset(
    {
        "prefetch_depth",
        "decode_workers",
        "host_image_cache_limit_mib",
        "host_image_cache_bytes",
        "checkpoint_every",
        "checkpoint_path",
        "resumed_from_checkpoint",
    }
)

NATIVE_CROP_TILING_VARIANT_PARAMETERS = frozenset(
    {
        "adaptive_native_crop_tiles",
        "native_crop_tile_policy",
    }
)


def _controlled_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in parameters.items() if key not in QUALIFICATION_VARIANT_PARAMETERS}


def _performance_controlled_parameters(
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    return {key: value for key, value in parameters.items() if key not in PERFORMANCE_VARIANT_PARAMETERS}


def _native_crop_tiling_controlled_parameters(
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    return {key: value for key, value in parameters.items() if key not in NATIVE_CROP_TILING_VARIANT_PARAMETERS}


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _number(mapping: Mapping[str, Any], key: str) -> int | float | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"run manifest has invalid numeric field {key}")
    return value


def _quality_timing_summary(
    metrics: Mapping[str, Any],
    timings: Mapping[str, Any],
) -> dict[str, int | float | None]:
    """Return the common scientific result fields for one trainer run."""

    return {
        "final_gaussians": _number(metrics, "final_gaussians"),
        "final_loss": _number(metrics, "final_loss"),
        "psnr": _number(metrics, "psnr"),
        "ssim": _number(metrics, "ssim"),
        "pixel_weighted_psnr": _number(metrics, "pixel_weighted_psnr"),
        "pixel_weighted_ssim": _number(metrics, "pixel_weighted_ssim"),
        "training_seconds": _number(timings, "training_seconds"),
        "wall_seconds": _number(timings, "wall_seconds"),
    }


def compare_qualification_manifests(
    manifest_paths: Iterable[str | Path],
    *,
    expected_profiles: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Compare completed manifests while enforcing a controlled experiment."""

    paths = [Path(path).resolve() for path in manifest_paths]
    if len(paths) < 2:
        raise ValueError("qualification comparison requires at least two runs")
    manifests: list[dict[str, Any]] = []
    for path in paths:
        manifest = load_run_manifest(path)
        validate_run_manifest(manifest)
        manifests.append(manifest)

    baseline = manifests[0]
    dataset_fingerprint = baseline["dataset"]["fingerprint"]
    binary_digest = baseline["trainer_binary_sha256"]
    controlled = _controlled_parameters(baseline["parameters"])
    for manifest in manifests[1:]:
        if manifest["dataset"]["fingerprint"] != dataset_fingerprint:
            raise ValueError("qualification runs use different datasets")
        if manifest["trainer_binary_sha256"] != binary_digest:
            raise ValueError("qualification runs use different trainer binaries")
        if _controlled_parameters(manifest["parameters"]) != controlled:
            raise ValueError("qualification runs differ outside the allowed AbsGrad variant")

    profiles = [str(manifest["parameters"]["optimizer_profile"]) for manifest in manifests]
    if len(set(profiles)) != len(profiles):
        raise ValueError("qualification optimizer profiles must be unique")
    if expected_profiles is not None and set(profiles) != set(expected_profiles):
        raise ValueError("qualification optimizer profile set is incomplete")

    runs: list[dict[str, Any]] = []
    for path, manifest, profile in zip(paths, manifests, profiles, strict=True):
        parameters = manifest["parameters"]
        metrics = manifest["metrics"]
        timings = manifest["timings"]
        runs.append(
            {
                "manifest": str(path),
                "optimizer_profile": profile,
                "absgrad_score_weight": _number(parameters, "absgrad_score_weight"),
                **_quality_timing_summary(metrics, timings),
                "lpips": _number(metrics, "lpips"),
                "topology_refinement_seconds": _number(
                    timings,
                    "topology_refinement_seconds",
                ),
                "periodic_checkpoint_seconds": _number(
                    timings,
                    "periodic_checkpoint_seconds",
                ),
                "checkpoint_snapshot_seconds": _number(
                    timings,
                    "checkpoint_snapshot_seconds",
                ),
                "checkpoint_wait_seconds": _number(
                    timings,
                    "checkpoint_wait_seconds",
                ),
                "checkpoint_write_seconds": _number(
                    timings,
                    "checkpoint_write_seconds",
                ),
                "periodic_checkpoints": _number(
                    metrics,
                    "periodic_checkpoints",
                ),
            }
        )

    baseline_run = runs[0]
    for run in runs:
        run["delta_from_baseline"] = {
            metric: (
                None if run[metric] is None or baseline_run[metric] is None else run[metric] - baseline_run[metric]
            )
            for metric in (
                "final_gaussians",
                "final_loss",
                "psnr",
                "ssim",
                "pixel_weighted_psnr",
                "pixel_weighted_ssim",
                "lpips",
                "training_seconds",
                "topology_refinement_seconds",
                "periodic_checkpoint_seconds",
                "checkpoint_snapshot_seconds",
                "checkpoint_wait_seconds",
                "checkpoint_write_seconds",
                "periodic_checkpoints",
                "wall_seconds",
            )
        }
    return {
        "schema_version": 1,
        "dataset_fingerprint": dataset_fingerprint,
        "trainer_binary_sha256": binary_digest,
        "controlled_parameters_sha256": _canonical_digest(controlled),
        "baseline_optimizer_profile": profiles[0],
        "runs": runs,
    }


def compare_performance_manifests(
    manifest_paths: Iterable[str | Path],
) -> dict[str, Any]:
    """Compare operational tuning with strict trainer-output parity."""

    paths = [Path(path).resolve() for path in manifest_paths]
    if len(paths) < 2:
        raise ValueError("performance comparison requires at least two runs")
    manifests: list[dict[str, Any]] = []
    for path in paths:
        manifest = load_run_manifest(path)
        validate_run_manifest(manifest)
        manifests.append(manifest)

    baseline = manifests[0]
    dataset_fingerprint = baseline["dataset"]["fingerprint"]
    binary_digest = baseline["trainer_binary_sha256"]
    controlled = _performance_controlled_parameters(baseline["parameters"])
    for manifest in manifests[1:]:
        if manifest["dataset"]["fingerprint"] != dataset_fingerprint:
            raise ValueError("performance runs use different datasets")
        if manifest["trainer_binary_sha256"] != binary_digest:
            raise ValueError("performance runs use different trainer binaries")
        if _performance_controlled_parameters(manifest["parameters"]) != controlled:
            raise ValueError("performance runs differ outside the allowed I/O tuning")

    ply_digests: list[str] = []
    runs: list[dict[str, Any]] = []
    for path, manifest in zip(paths, manifests, strict=True):
        point_cloud = manifest["artifacts"].get("point_cloud.ply")
        digest = point_cloud.get("sha256") if isinstance(point_cloud, Mapping) else None
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("performance run has no point-cloud SHA-256")
        ply_digests.append(digest)
        parameters = manifest["parameters"]
        metrics = manifest["metrics"]
        timings = manifest["timings"]
        runs.append(
            {
                "manifest": str(path),
                "prefetch_depth": _number(parameters, "prefetch_depth"),
                "decode_workers": _number(parameters, "decode_workers"),
                "host_image_cache_limit_mib": _number(
                    parameters,
                    "host_image_cache_limit_mib",
                ),
                "host_image_cache_bytes": _number(
                    parameters,
                    "host_image_cache_bytes",
                ),
                "checkpoint_every": _number(
                    parameters,
                    "checkpoint_every",
                ),
                "image_cache_working_set_bytes": _number(
                    metrics,
                    "image_cache_working_set_bytes",
                ),
                "point_cloud_sha256": digest,
                **_quality_timing_summary(metrics, timings),
                "topology_refinement_seconds": _number(
                    timings,
                    "topology_refinement_seconds",
                ),
                "periodic_checkpoint_seconds": _number(
                    timings,
                    "periodic_checkpoint_seconds",
                ),
                "checkpoint_snapshot_seconds": _number(
                    timings,
                    "checkpoint_snapshot_seconds",
                ),
                "checkpoint_wait_seconds": _number(
                    timings,
                    "checkpoint_wait_seconds",
                ),
                "checkpoint_write_seconds": _number(
                    timings,
                    "checkpoint_write_seconds",
                ),
                "periodic_checkpoints": _number(
                    metrics,
                    "periodic_checkpoints",
                ),
                "data_loading_seconds": _number(
                    timings,
                    "data_loading_seconds",
                ),
                "foreground_image_wait_seconds": _number(
                    timings,
                    "image_wait_seconds",
                ),
                "cumulative_decode_seconds": _number(
                    timings,
                    "image_decode_seconds",
                ),
            }
        )

    baseline_run = runs[0]
    for run in runs:
        baseline_wall = baseline_run["wall_seconds"]
        wall = run["wall_seconds"]
        run["speedup_from_baseline"] = (
            None
            if not isinstance(baseline_wall, (int, float)) or not isinstance(wall, (int, float)) or wall <= 0
            else baseline_wall / wall
        )
        run["delta_from_baseline"] = {
            metric: (
                None if run[metric] is None or baseline_run[metric] is None else run[metric] - baseline_run[metric]
            )
            for metric in (
                "final_gaussians",
                "final_loss",
                "psnr",
                "ssim",
                "pixel_weighted_psnr",
                "pixel_weighted_ssim",
                "training_seconds",
                "topology_refinement_seconds",
                "periodic_checkpoint_seconds",
                "checkpoint_snapshot_seconds",
                "checkpoint_wait_seconds",
                "checkpoint_write_seconds",
                "periodic_checkpoints",
                "wall_seconds",
                "data_loading_seconds",
                "foreground_image_wait_seconds",
            )
        }
    if len(set(ply_digests)) != 1:
        raise ValueError("performance tuning changed the final point cloud")
    return {
        "schema_version": 1,
        "dataset_fingerprint": dataset_fingerprint,
        "trainer_binary_sha256": binary_digest,
        "controlled_parameters_sha256": _canonical_digest(controlled),
        "scientific_output_parity": True,
        "point_cloud_sha256": ply_digests[0],
        "runs": runs,
    }


def compare_native_crop_tiling_manifests(
    manifest_paths: Iterable[str | Path],
) -> dict[str, Any]:
    """Compare fixed and adaptive native-crop tiling on one exact binary."""

    paths = [Path(path).resolve() for path in manifest_paths]
    if len(paths) != 2:
        raise ValueError("native-crop tiling comparison requires exactly two runs")
    manifests: list[dict[str, Any]] = []
    for path in paths:
        manifest = load_run_manifest(path)
        validate_run_manifest(manifest)
        manifests.append(manifest)

    baseline = manifests[0]
    dataset_fingerprint = baseline["dataset"]["fingerprint"]
    binary_digest = baseline["trainer_binary_sha256"]
    controlled = _native_crop_tiling_controlled_parameters(baseline["parameters"])
    for manifest in manifests[1:]:
        if manifest["dataset"]["fingerprint"] != dataset_fingerprint:
            raise ValueError("native-crop tiling runs use different datasets")
        if manifest["trainer_binary_sha256"] != binary_digest:
            raise ValueError("native-crop tiling runs use different trainer binaries")
        if _native_crop_tiling_controlled_parameters(manifest["parameters"]) != controlled:
            raise ValueError("native-crop tiling runs differ outside the allowed policy")

    modes = [_number(manifest["parameters"], "adaptive_native_crop_tiles") for manifest in manifests]
    if modes != [0, 1]:
        raise ValueError("native-crop tiling runs must order fixed mode before adaptive mode")
    policies = [manifest["parameters"].get("native_crop_tile_policy") for manifest in manifests]
    if policies != [
        "fixed-tile-mode-v1",
        "sensor-pixel-budget-up-to-tile-mode-v1",
    ]:
        raise ValueError("native-crop tiling policy provenance is invalid")

    runs: list[dict[str, Any]] = []
    for path, manifest in zip(paths, manifests, strict=True):
        parameters = manifest["parameters"]
        metrics = manifest["metrics"]
        timings = manifest["timings"]
        dataset = manifest["dataset"]
        runs.append(
            {
                "manifest": str(path),
                "adaptive_native_crop_tiles": _number(
                    parameters,
                    "adaptive_native_crop_tiles",
                ),
                "native_crop_tile_policy": parameters.get("native_crop_tile_policy"),
                "training_image_count": _number(
                    dataset,
                    "training_image_count",
                ),
                "held_out_image_count": _number(
                    dataset,
                    "held_out_image_count",
                ),
                "frame_descriptor_count": _number(
                    metrics,
                    "frame_descriptor_count",
                ),
                "training_frame_count": _number(
                    metrics,
                    "training_frame_count",
                ),
                "held_out_frame_count": _number(
                    metrics,
                    "held_out_frame_count",
                ),
                **_quality_timing_summary(metrics, timings),
            }
        )

    baseline_run = runs[0]
    for run in runs:
        run["delta_from_fixed"] = {
            metric: (
                None if run[metric] is None or baseline_run[metric] is None else run[metric] - baseline_run[metric]
            )
            for metric in (
                "training_image_count",
                "held_out_image_count",
                "frame_descriptor_count",
                "training_frame_count",
                "held_out_frame_count",
                "final_gaussians",
                "final_loss",
                "psnr",
                "ssim",
                "pixel_weighted_psnr",
                "pixel_weighted_ssim",
                "training_seconds",
                "wall_seconds",
            )
        }
    return {
        "schema_version": 1,
        "dataset_fingerprint": dataset_fingerprint,
        "trainer_binary_sha256": binary_digest,
        "controlled_parameters_sha256": _canonical_digest(controlled),
        "baseline_policy": "fixed-tile-mode-v1",
        "runs": runs,
    }


def compare_resident_seed_contract_manifests(
    manifest_paths: Iterable[str | Path],
    subset_report_paths: Iterable[str | Path],
) -> dict[str, Any]:
    """Compare camera-only and crop-aware resident seed contracts."""

    paths = [Path(path).resolve() for path in manifest_paths]
    report_paths = [Path(path).resolve() for path in subset_report_paths]
    if len(paths) != 2 or len(report_paths) != 2:
        raise ValueError("resident seed comparison requires exactly two runs and reports")
    manifests = [load_run_manifest(path) for path in paths]
    for manifest in manifests:
        validate_run_manifest(manifest)
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
    if not all(isinstance(report, dict) for report in reports):
        raise ValueError("resident seed subset report is invalid")

    baseline, candidate = manifests
    if baseline["trainer_binary_sha256"] != candidate["trainer_binary_sha256"]:
        raise ValueError("resident seed runs use different trainer binaries")
    if baseline["parameters"] != candidate["parameters"]:
        raise ValueError("resident seed runs changed scientific parameters")
    source_count_fields = (
        "image_count",
        "training_image_count",
        "held_out_image_count",
        "ignored_image_count",
    )
    for field in source_count_fields:
        if baseline["dataset"].get(field) != candidate["dataset"].get(field):
            raise ValueError("resident seed runs changed source image splits")
    frame_count_fields = (
        "frame_descriptor_count",
        "training_frame_count",
        "held_out_frame_count",
    )
    for field in frame_count_fields:
        baseline_count = baseline["metrics"].get(field)
        candidate_count = candidate["metrics"].get(field)
        if baseline_count is None or candidate_count is None or baseline_count != candidate_count:
            raise ValueError("resident seed runs changed expanded frames")
    if baseline["dataset"]["fingerprint"] == candidate["dataset"]["fingerprint"]:
        raise ValueError("resident seed contract did not change the dataset")

    scopes = [report.get("track_scope", "selected-cameras-v1") for report in reports]
    if scopes != [
        "selected-cameras-v1",
        "selected-cameras-and-native-crops-v1",
    ]:
        raise ValueError("resident seed reports use invalid track scopes")
    if reports[0].get("selected_images") != reports[1].get("selected_images"):
        raise ValueError("resident seed reports changed selected images")

    runs: list[dict[str, Any]] = []
    for path, report_path, manifest, report, scope in zip(
        paths,
        report_paths,
        manifests,
        reports,
        scopes,
        strict=True,
    ):
        metrics = manifest["metrics"]
        timings = manifest["timings"]
        runs.append(
            {
                "manifest": str(path),
                "subset_report": str(report_path),
                "dataset_fingerprint": manifest["dataset"]["fingerprint"],
                "track_scope": scope,
                "exported_points": _number(report, "exported_points"),
                "exported_observations": _number(
                    report,
                    "exported_observations",
                ),
                "crop_rejected_observations": _number(
                    report,
                    "observations_rejected_outside_native_crops",
                ),
                "mean_exported_track_length": _number(
                    report,
                    "mean_exported_track_length",
                ),
                "frame_descriptor_count": _number(
                    metrics,
                    "frame_descriptor_count",
                ),
                "final_gaussians": _number(metrics, "final_gaussians"),
                "final_loss": _number(metrics, "final_loss"),
                "psnr": _number(metrics, "psnr"),
                "ssim": _number(metrics, "ssim"),
                "pixel_weighted_psnr": _number(
                    metrics,
                    "pixel_weighted_psnr",
                ),
                "pixel_weighted_ssim": _number(
                    metrics,
                    "pixel_weighted_ssim",
                ),
                "wall_seconds": _number(timings, "wall_seconds"),
            }
        )
    baseline_run = runs[0]
    for run in runs:
        run["delta_from_camera_only"] = {
            metric: (
                None if run[metric] is None or baseline_run[metric] is None else run[metric] - baseline_run[metric]
            )
            for metric in (
                "exported_points",
                "exported_observations",
                "mean_exported_track_length",
                "frame_descriptor_count",
                "final_gaussians",
                "final_loss",
                "psnr",
                "ssim",
                "pixel_weighted_psnr",
                "pixel_weighted_ssim",
                "wall_seconds",
            )
        }
    return {
        "schema_version": 1,
        "trainer_binary_sha256": baseline["trainer_binary_sha256"],
        "controlled_parameters_sha256": _canonical_digest(baseline["parameters"]),
        "runs": runs,
    }
