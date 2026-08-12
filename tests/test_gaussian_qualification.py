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
    compare_qualification_manifests,
)


def _manifest(path: Path, profile: str, weight: float, *, seed: int = 42) -> Path:
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
        },
        "timings": {"training_seconds": 12.0, "wall_seconds": 14.0},
        "metrics": {
            "final_gaussians": 5_700_000,
            "final_loss": 0.1 - weight / 100,
            "psnr": 20.0 + weight,
            "ssim": 0.5 + weight / 10,
            "lpips": None,
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
