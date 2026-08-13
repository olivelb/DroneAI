from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

from gaussian_training.qualification import (  # noqa: E402
    compare_native_crop_tiling_manifests,
    compare_performance_manifests,
    compare_qualification_manifests,
)


def _manifest(
    path: Path,
    profile: str,
    weight: float,
    *,
    seed: int = 42,
    adaptive_native_crop_tiles: int = 0,
) -> Path:
    payload = {
        "contract_version": 1,
        "backend": "dronegs-native-mrnf-fastgs",
        "trainer_version": "test",
        "trainer_binary_sha256": "a" * 64,
        "git_revision": "test",
        "status": "completed",
        "dataset": {"fingerprint": "dataset"},
        "parameters": {
            "profile_id": "high-quality-v3",
            "optimizer_profile": profile,
            "pruning_policy": "spatial-bounds",
            "raster_profile": "fastgs",
            "effective_raster_profile": "fastgs",
            "test_split": "modulo",
            "test_guard_percent": 0,
            "seed": seed,
            "iterations": 30_000,
            "absgrad_score_weight": weight,
            "growth_score": "variant",
            "absgrad_guidance": None if weight == 0 else "enabled",
            "absgrad_normalization": None if weight == 0 else "median",
            "adaptive_native_crop_tiles": adaptive_native_crop_tiles,
            "native_crop_tile_policy": (
                "sensor-pixel-budget-up-to-tile-mode-v1"
                if adaptive_native_crop_tiles
                else "fixed-tile-mode-v1"
            ),
        },
        "timings": {"training_seconds": 12.0, "wall_seconds": 14.0},
        "metrics": {
            "final_gaussians": 5_700_000,
            "final_loss": 0.1 - weight / 100,
            "psnr": 20.0 + weight,
            "ssim": 0.5 + weight / 10,
            "pixel_weighted_psnr": 20.5 + weight,
            "pixel_weighted_ssim": 0.55 + weight / 10,
            "lpips": None,
            "image_cache_working_set_bytes": 8_000_000_000,
            "training_image_count": (
                241 if adaptive_native_crop_tiles else 485
            ),
        },
        "artifacts": {"point_cloud.ply": {"path": "point_cloud.ply"}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_compare_controlled_qualification_runs(tmp_path):
    report = compare_qualification_manifests(
        [
            _manifest(tmp_path / "reference.json", "reference-absolute", 0.0),
            _manifest(
                tmp_path / "abs025.json",
                "reference-absolute-absgrad025",
                0.25,
            ),
        ],
        expected_profiles={
            "reference-absolute",
            "reference-absolute-absgrad025",
        },
    )

    assert report["baseline_optimizer_profile"] == "reference-absolute"
    assert report["runs"][1]["delta_from_baseline"]["psnr"] == pytest.approx(
        0.25
    )
    assert report["runs"][1]["lpips"] is None
    assert report["runs"][1]["delta_from_baseline"][
        "pixel_weighted_psnr"
    ] == pytest.approx(0.25)


def test_compare_rejects_uncontrolled_parameter_drift(tmp_path):
    reference = _manifest(tmp_path / "reference.json", "reference-absolute", 0.0)
    drifted = _manifest(
        tmp_path / "drifted.json",
        "reference-absolute-absgrad025",
        0.25,
        seed=43,
    )

    with pytest.raises(ValueError, match="outside the allowed"):
        compare_qualification_manifests([reference, drifted])


def test_compare_requires_unique_profiles(tmp_path):
    first = _manifest(tmp_path / "first.json", "reference-absolute", 0.0)
    second = _manifest(tmp_path / "second.json", "reference-absolute", 0.0)

    with pytest.raises(ValueError, match="must be unique"):
        compare_qualification_manifests([first, second])


def test_compare_native_crop_tiling_runs(tmp_path):
    report = compare_native_crop_tiling_manifests(
        [
            _manifest(
                tmp_path / "fixed.json",
                "reference-absolute",
                0.0,
            ),
            _manifest(
                tmp_path / "adaptive.json",
                "reference-absolute",
                0.0,
                adaptive_native_crop_tiles=1,
            ),
        ]
    )

    assert report["baseline_policy"] == "fixed-tile-mode-v1"
    assert report["runs"][1]["native_crop_tile_policy"] == (
        "sensor-pixel-budget-up-to-tile-mode-v1"
    )
    assert report["runs"][1]["delta_from_fixed"][
        "training_image_count"
    ] == -244


def test_compare_native_crop_tiling_rejects_parameter_drift(tmp_path):
    fixed = _manifest(
        tmp_path / "fixed.json",
        "reference-absolute",
        0.0,
    )
    adaptive = _manifest(
        tmp_path / "adaptive.json",
        "reference-absolute",
        0.0,
        seed=43,
        adaptive_native_crop_tiles=1,
    )

    with pytest.raises(ValueError, match="outside the allowed policy"):
        compare_native_crop_tiling_manifests([fixed, adaptive])


def test_compare_native_crop_tiling_rejects_invalid_policy(tmp_path):
    fixed = _manifest(
        tmp_path / "fixed.json",
        "reference-absolute",
        0.0,
    )
    adaptive = _manifest(
        tmp_path / "adaptive.json",
        "reference-absolute",
        0.0,
        adaptive_native_crop_tiles=1,
    )
    payload = json.loads(adaptive.read_text(encoding="utf-8"))
    payload["parameters"]["native_crop_tile_policy"] = "untracked-policy"
    adaptive.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="policy provenance"):
        compare_native_crop_tiling_manifests([fixed, adaptive])


def _performance_manifest(
    path: Path,
    *,
    prefetch_depth: int,
    decode_workers: int,
    wall_seconds: float,
    host_image_cache_mib: int = 2_048,
    checkpoint_every: int = 2_000,
    digest: str = "b" * 64,
    seed: int = 42,
) -> Path:
    payload = json.loads(
        _manifest(path, "reference-absolute", 0.0, seed=seed).read_text()
    )
    payload["parameters"].update(
        prefetch_depth=prefetch_depth,
        decode_workers=decode_workers,
        host_image_cache_limit_mib=host_image_cache_mib,
        host_image_cache_bytes=host_image_cache_mib * 1024 * 1024,
        checkpoint_every=checkpoint_every,
        checkpoint_path=f"/run-{prefetch_depth}/training.ckpt",
        resumed_from_checkpoint=False,
    )
    payload["timings"].update(
        wall_seconds=wall_seconds,
        data_loading_seconds=2.5 / prefetch_depth,
        image_decode_seconds=4.0,
        topology_refinement_seconds=1.5,
        periodic_checkpoint_seconds=0.5,
    )
    payload["artifacts"]["point_cloud.ply"]["sha256"] = digest
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_compare_performance_requires_exact_scientific_output(tmp_path):
    report = compare_performance_manifests(
        [
            _performance_manifest(
                tmp_path / "four.json",
                prefetch_depth=4,
                decode_workers=4,
                wall_seconds=20.0,
            ),
            _performance_manifest(
                tmp_path / "eight.json",
                prefetch_depth=8,
                decode_workers=8,
                wall_seconds=16.0,
                host_image_cache_mib=4_096,
                checkpoint_every=4_000,
            ),
        ]
    )

    assert report["scientific_output_parity"] is True
    assert report["runs"][1]["speedup_from_baseline"] == pytest.approx(1.25)
    assert report["runs"][1]["delta_from_baseline"]["psnr"] == 0.0
    assert report["runs"][1]["host_image_cache_limit_mib"] == 4_096
    assert report["runs"][1]["checkpoint_every"] == 4_000
    assert report["runs"][1]["topology_refinement_seconds"] == 1.5
    assert report["runs"][1]["image_cache_working_set_bytes"] == 8_000_000_000


def test_compare_performance_rejects_changed_ply(tmp_path):
    first = _performance_manifest(
        tmp_path / "four.json",
        prefetch_depth=4,
        decode_workers=4,
        wall_seconds=20.0,
    )
    second = _performance_manifest(
        tmp_path / "eight.json",
        prefetch_depth=8,
        decode_workers=8,
        wall_seconds=16.0,
        digest="c" * 64,
    )

    with pytest.raises(ValueError, match="changed the final point cloud"):
        compare_performance_manifests([first, second])


def test_compare_performance_rejects_scientific_drift(tmp_path):
    first = _performance_manifest(
        tmp_path / "four.json",
        prefetch_depth=4,
        decode_workers=4,
        wall_seconds=20.0,
    )
    second = _performance_manifest(
        tmp_path / "eight.json",
        prefetch_depth=8,
        decode_workers=8,
        wall_seconds=16.0,
        seed=43,
    )

    with pytest.raises(ValueError, match="outside the allowed"):
        compare_performance_manifests([first, second])
