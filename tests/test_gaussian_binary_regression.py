from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

from gaussian_training.binary_regression import (  # noqa: E402
    compare_binary_regression_manifests,
)


def _manifest(
    path: Path,
    *,
    binary: str,
    digest: str = "c" * 64,
    seed: int = 42,
    psnr: float = 20.0,
) -> Path:
    payload = {
        "contract_version": 1,
        "backend": "dronegs-native-mrnf-fastgs",
        "trainer_version": "test",
        "trainer_binary_sha256": binary,
        "git_revision": binary[:8],
        "status": "completed",
        "dataset": {"fingerprint": "dataset"},
        "parameters": {
            "profile_id": "high-quality-v3",
            "optimizer_profile": "reference-absolute",
            "pruning_policy": "spatial-bounds",
            "raster_profile": "fastgs",
            "effective_raster_profile": "fastgs",
            "test_split": "modulo",
            "test_guard_percent": 0,
            "seed": seed,
            "iterations": 30_000,
            "checkpoint_path": f"/{binary[:4]}/training.ckpt",
        },
        "timings": {"training_seconds": 12.0, "wall_seconds": 14.0},
        "metrics": {
            "final_gaussians": 1_700_000,
            "final_loss": 0.1,
            "psnr": psnr,
            "ssim": 0.5,
        },
        "artifacts": {
            "point_cloud.ply": {
                "path": "point_cloud.ply",
                "sha256": digest,
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_binary_regression_requires_exact_scientific_parity(tmp_path):
    report = compare_binary_regression_manifests(
        [
            _manifest(tmp_path / "before.json", binary="a" * 64),
            _manifest(tmp_path / "after.json", binary="b" * 64),
        ]
    )

    assert report["scientific_output_parity"] is True
    assert report["point_cloud_sha256"] == "c" * 64
    assert len(report["runs"]) == 2


def test_binary_regression_rejects_parameter_drift(tmp_path):
    before = _manifest(tmp_path / "before.json", binary="a" * 64)
    after = _manifest(tmp_path / "after.json", binary="b" * 64, seed=43)

    with pytest.raises(ValueError, match="scientific parameters"):
        compare_binary_regression_manifests([before, after])


@pytest.mark.parametrize(
    ("digest", "psnr", "message"),
    [
        ("d" * 64, 20.0, "final point cloud"),
        ("c" * 64, 20.1, "scientific metrics"),
    ],
)
def test_binary_regression_rejects_output_drift(
    tmp_path,
    digest,
    psnr,
    message,
):
    before = _manifest(tmp_path / "before.json", binary="a" * 64)
    after = _manifest(
        tmp_path / "after.json",
        binary="b" * 64,
        digest=digest,
        psnr=psnr,
    )

    with pytest.raises(ValueError, match=message):
        compare_binary_regression_manifests([before, after])
