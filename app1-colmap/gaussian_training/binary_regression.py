"""Strict scientific parity checks across different DroneGS binaries."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .manifest_contract import load_run_manifest, validate_run_manifest


LOCALITY_PARAMETERS = frozenset({"checkpoint_path"})
SCIENTIFIC_METRICS = (
    "final_gaussians",
    "final_loss",
    "psnr",
    "ssim",
)


def _controlled_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in parameters.items()
        if key not in LOCALITY_PARAMETERS
    }


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _point_cloud_digest(manifest: Mapping[str, Any]) -> str:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ValueError("run manifest has no artifact inventory")
    point_cloud = artifacts.get("point_cloud.ply")
    digest = (
        point_cloud.get("sha256")
        if isinstance(point_cloud, Mapping)
        else None
    )
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("binary regression run has no point-cloud SHA-256")
    return digest


def compare_binary_regression_manifests(
    manifest_paths: Iterable[str | Path],
) -> dict[str, Any]:
    """Require exact scientific output from controlled different binaries."""

    paths = [Path(path).resolve() for path in manifest_paths]
    if len(paths) < 2:
        raise ValueError("binary regression comparison requires at least two runs")
    manifests: list[dict[str, Any]] = []
    for path in paths:
        manifest = load_run_manifest(path)
        validate_run_manifest(manifest)
        manifests.append(manifest)

    baseline = manifests[0]
    dataset_fingerprint = baseline["dataset"]["fingerprint"]
    controlled = _controlled_parameters(baseline["parameters"])
    for manifest in manifests[1:]:
        if manifest["dataset"]["fingerprint"] != dataset_fingerprint:
            raise ValueError("binary regression runs use different datasets")
        if _controlled_parameters(manifest["parameters"]) != controlled:
            raise ValueError("binary regression runs changed scientific parameters")

    binary_digests = [
        str(manifest["trainer_binary_sha256"])
        for manifest in manifests
    ]
    if len(set(binary_digests)) < 2:
        raise ValueError("binary regression requires at least two different binaries")

    point_cloud_digests = [_point_cloud_digest(item) for item in manifests]
    if len(set(point_cloud_digests)) != 1:
        raise ValueError("binary change altered the final point cloud")

    baseline_metrics = baseline["metrics"]
    runs: list[dict[str, Any]] = []
    for path, manifest, binary_digest in zip(
        paths,
        manifests,
        binary_digests,
        strict=True,
    ):
        metrics = manifest["metrics"]
        metric_values = {
            name: metrics.get(name)
            for name in SCIENTIFIC_METRICS
        }
        if any(
            metric_values[name] != baseline_metrics.get(name)
            for name in SCIENTIFIC_METRICS
        ):
            raise ValueError("binary change altered scientific metrics")
        runs.append(
            {
                "manifest": str(path),
                "trainer_binary_sha256": binary_digest,
                "git_revision": manifest.get("git_revision"),
                "point_cloud_sha256": point_cloud_digests[0],
                **metric_values,
            }
        )

    return {
        "schema_version": 1,
        "dataset_fingerprint": dataset_fingerprint,
        "controlled_parameters_sha256": _canonical_digest(controlled),
        "scientific_output_parity": True,
        "point_cloud_sha256": point_cloud_digests[0],
        "runs": runs,
    }
