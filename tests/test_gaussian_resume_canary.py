import json
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

from gaussian_training import DroneGSTuning, TrainingRequest
from shared.dronegs_profile import effective_raster_profile

generator = importlib.import_module("gaussian_ortho.generate_gaussian_orthophoto")


def _request(output, minimum_ssim):
    tuning = DroneGSTuning(
        profile_id="custom",
        topology_cooldown=100,
        photometric_finish=100,
        test_every=8,
        canary_min_psnr=15.0,
        canary_min_ssim=minimum_ssim,
    )
    return TrainingRequest(
        data_path="/data",
        output_path=str(output),
        iterations=500,
        sh_degree=1,
        max_cap=100_000,
        resize_factor=8,
        max_width=1024,
        tile_mode=1,
        seed=42,
        dataset_fingerprint="dataset-fingerprint",
        dronegs=tuning,
    )


def _manifest(request):
    tuning = request.dronegs
    return {
        "contract_version": 1,
        "status": "completed",
        "trainer_binary_sha256": "trainer-hash",
        "dataset": {
            "fingerprint": request.dataset_fingerprint,
            "training_image_count": 10,
            "held_out_image_count": 2,
            "ignored_image_count": 0,
        },
        "parameters": {
            "iterations": request.iterations,
            "strategy": request.strategy,
            "sh_degree": request.sh_degree,
            "max_cap": request.max_cap,
            "resize_factor": request.resize_factor,
            "max_width": request.max_width,
            "tile_mode": request.tile_mode,
            "seed": request.seed,
            "profile_id": tuning.profile_id,
            "optimizer_profile": tuning.optimizer_profile,
            "pruning_policy": tuning.pruning_policy,
            "raster_profile": tuning.raster_profile,
            "effective_raster_profile": effective_raster_profile(
                tuning.raster_profile,
                tuning.optimizer_profile,
            ),
            "sh_degree_interval": tuning.sh_degree_interval,
            "checkpoint_every": tuning.checkpoint_every,
            "test_every": tuning.test_every,
            "test_split": tuning.test_split,
            "test_guard_percent": tuning.test_guard_percent,
            "topology_cooldown_iterations": tuning.topology_cooldown,
            "photometric_finish_iterations": tuning.photometric_finish,
            "photometric_final_mse_percent": tuning.photometric_mse_percent,
            "adaptive_growth_target": tuning.adaptive_growth_target,
            "adaptive_native_crop_tiles": int(tuning.adaptive_native_crop_tiles),
            "initial_scale_policy": tuning.initial_scale_policy,
            "initial_max_projected_sigma_pixels": (tuning.initial_max_projected_sigma_pixels),
            "maximum_scale_growth_factor": (tuning.maximum_scale_growth_factor),
        },
        "metrics": {"psnr": 19.0, "ssim": 0.30},
    }


def test_completed_training_is_rechecked_without_retraining(monkeypatch, tmp_path):
    output = tmp_path / "full"
    output.mkdir()
    (output / "trainer_run.json").write_text("{}", encoding="utf-8")
    (output / "point_cloud.ply").write_text("ply", encoding="utf-8")
    (output / "training.ckpt").write_text("checkpoint", encoding="utf-8")
    relaxed = _request(output, minimum_ssim=0.25)
    manifest = _manifest(relaxed)
    manifest["parameters"]["maximum_scale_growth_factor"] = 54.59814835
    monkeypatch.setattr(generator, "load_run_manifest", lambda _path: manifest)
    monkeypatch.setattr(generator, "validate_run_manifest", lambda _manifest: None)
    monkeypatch.setattr(
        generator,
        "manifest_matches_ply",
        lambda _manifest, _path: True,
    )

    result = generator._reusable_dronegs_result(
        relaxed,
        trainer_binary_sha256="trainer-hash",
    )

    assert result is not None
    assert not (output / "training.ckpt").exists()
    canary_path = output / "canary_result.json"
    assert json.loads(canary_path.read_text(encoding="utf-8"))["status"] == "passed"

    strict = _request(output, minimum_ssim=0.35)
    with pytest.raises(RuntimeError, match="quality canary failed: ssim"):
        generator._reusable_dronegs_result(
            strict,
            trainer_binary_sha256="trainer-hash",
        )
    assert (output / "trainer_run.json").is_file()
    assert (output / "point_cloud.ply").is_file()
    assert json.loads(canary_path.read_text(encoding="utf-8"))["status"] == "failed"
