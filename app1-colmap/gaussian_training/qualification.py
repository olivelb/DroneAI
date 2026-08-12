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


def _controlled_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in parameters.items()
        if key not in QUALIFICATION_VARIANT_PARAMETERS
    }


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
            raise ValueError(
                "qualification runs differ outside the allowed AbsGrad variant"
            )

    profiles = [
        str(manifest["parameters"]["optimizer_profile"])
        for manifest in manifests
    ]
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
                "absgrad_score_weight": _number(
                    parameters, "absgrad_score_weight"
                ),
                "final_gaussians": _number(metrics, "final_gaussians"),
                "final_loss": _number(metrics, "final_loss"),
                "psnr": _number(metrics, "psnr"),
                "ssim": _number(metrics, "ssim"),
                "lpips": _number(metrics, "lpips"),
                "training_seconds": _number(timings, "training_seconds"),
                "wall_seconds": _number(timings, "wall_seconds"),
            }
        )

    baseline_run = runs[0]
    for run in runs:
        run["delta_from_baseline"] = {
            metric: (
                None
                if run[metric] is None or baseline_run[metric] is None
                else run[metric] - baseline_run[metric]
            )
            for metric in (
                "final_gaussians",
                "final_loss",
                "psnr",
                "ssim",
                "lpips",
                "training_seconds",
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
