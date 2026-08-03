"""Strict DroneGS run-manifest loading, validation and promotion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared.checksums import sha256_file


class DuplicateManifestKeyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateManifestKeyError(
                f"duplicate run-manifest key: {key}"
            )
        result[key] = value
    return result


def load_run_manifest(path: str | Path) -> dict[str, Any]:
    return json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_unique_object,
    )


def validate_run_manifest(manifest: dict[str, Any]) -> None:
    required_top_level = {
        "contract_version",
        "backend",
        "trainer_version",
        "trainer_binary_sha256",
        "git_revision",
        "status",
        "dataset",
        "parameters",
        "timings",
        "metrics",
        "artifacts",
    }
    missing = sorted(required_top_level - manifest.keys())
    if missing:
        raise ValueError(
            "run manifest is missing required keys: " + ", ".join(missing)
        )
    if manifest["contract_version"] != 1:
        raise ValueError("unsupported DroneGS run-manifest contract")
    if manifest["status"] != "completed":
        raise ValueError("DroneGS run manifest is not completed")
    binary_hash = manifest["trainer_binary_sha256"]
    if not isinstance(binary_hash, str) or len(binary_hash) != 64:
        raise ValueError("run manifest has no trainer binary SHA-256")
    dataset = manifest["dataset"]
    if not isinstance(dataset, dict) or not isinstance(
        dataset.get("fingerprint"), str
    ):
        raise ValueError("run manifest has no dataset fingerprint")
    parameters = manifest["parameters"]
    for key in (
        "profile_id",
        "optimizer_profile",
        "pruning_policy",
        "raster_profile",
        "effective_raster_profile",
        "test_split",
    ):
        if not isinstance(parameters.get(key), str):
            raise ValueError(f"run manifest has no valid parameters.{key}")
    if parameters["test_split"] not in {"modulo", "spatial-block"}:
        raise ValueError("run manifest has invalid parameters.test_split")
    guard = parameters.get("test_guard_percent")
    if (
        not isinstance(guard, int)
        or isinstance(guard, bool)
        or not 0 <= guard <= 100
    ):
        raise ValueError(
            "run manifest has invalid parameters.test_guard_percent"
        )
    artifacts = manifest["artifacts"]
    ply = artifacts.get("point_cloud.ply") if isinstance(artifacts, dict) else None
    if not isinstance(ply, dict) or not isinstance(ply.get("path"), str):
        raise ValueError("run manifest has no point_cloud.ply artifact")


def promote_run_manifest(
    path: str | Path,
    *,
    ply_path: str | Path,
    trainer_binary_sha256: str,
) -> dict[str, Any]:
    """Validate, add the PLY digest, and atomically publish the manifest."""

    manifest_path = Path(path)
    manifest = load_run_manifest(manifest_path)
    manifest["trainer_binary_sha256"] = trainer_binary_sha256
    validate_run_manifest(manifest)
    ply = Path(ply_path)
    artifact = manifest["artifacts"]["point_cloud.ply"]
    artifact["sha256"] = sha256_file(ply)
    artifact["bytes"] = ply.stat().st_size
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest


def manifest_matches_ply(
    manifest: dict[str, Any],
    ply_path: str | Path,
) -> bool:
    artifact = manifest.get("artifacts", {}).get("point_cloud.ply", {})
    expected_hash = artifact.get("sha256")
    expected_bytes = artifact.get("bytes")
    path = Path(ply_path)
    return (
        isinstance(expected_hash, str)
        and len(expected_hash) == 64
        and expected_bytes == path.stat().st_size
        and expected_hash == sha256_file(path)
    )
