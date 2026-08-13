from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

from gaussian_training.backends import (  # noqa: E402
    DroneGSBackend,
    DroneGSTuning,
    TrainingRequest,
    resolve_training_backend,
)

from shared.dronegs_profile import (  # noqa: E402
    DRONEGS_PRODUCTION_PROFILE_V1,
)


def request(**overrides) -> TrainingRequest:
    values = {
        "data_path": "/data",
        "output_path": "/output",
        "iterations": 500,
        "strategy": "mrnf",
        "sh_degree": 1,
        "max_cap": 100_000,
        "resize_factor": 8,
        "max_width": 1024,
        "tile_mode": 4,
        "seed": 42,
        "dataset_fingerprint": "test-dataset:sha256:fixture",
        "dronegs": DroneGSTuning(
            profile_id="test-profile",
            topology_cooldown=100,
            photometric_finish=100,
            photometric_mse_percent=100,
            test_every=8,
            save_eval_images=True,
            canary_min_psnr=None,
            canary_min_ssim=None,
        ),
    }
    values.update(overrides)
    return TrainingRequest(**values)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("iterations", 0, "iterations"),
        ("iterations", True, "iterations"),
        ("strategy", "unknown", "strategy"),
        ("sh_degree", 4, "sh_degree"),
        ("max_cap", 0, "max_cap"),
        ("resize_factor", 3, "resize_factor"),
        ("max_width", 4097, "max_width"),
        ("tile_mode", 3, "tile_mode"),
        ("seed", -1, "seed"),
    ],
)
def test_training_request_validation(field, value, message):
    with pytest.raises(ValueError, match=message):
        request(**{field: value})


def test_dronegs_adapter_uses_canonical_contract():
    command = DroneGSBackend("/opt/dronegs").build_command(request())

    assert command[0] == "/opt/dronegs"
    assert command[command.index("--resize-factor") + 1] == "8"
    assert command[command.index("--seed") + 1] == "42"
    assert command[command.index("--raster-profile") + 1] == "fastgs"
    assert command[command.index("--topology-cooldown") + 1] == "100"
    assert command[command.index("--profile-id") + 1] == "test-profile"
    assert command[command.index("--dataset-fingerprint") + 1] == ("test-dataset:sha256:fixture")
    assert command[command.index("--run-manifest") + 1] == "/output/trainer_run.json"
    assert command[command.index("--checkpoint-every") + 1] == "2000"
    assert command[command.index("--checkpoint-path") + 1] == ("/output/training.ckpt")
    assert command[command.index("--test-split") + 1] == "modulo"
    assert command[command.index("--test-guard-percent") + 1] == "0"
    assert command[command.index("--adaptive-growth-target") + 1] == "0"
    assert command[command.index("--host-image-cache-mib") + 1] == "2048"
    assert "--resize_factor" not in command


def test_dronegs_adapter_passes_validated_production_tuning():
    training_request = request(
        dronegs=DroneGSTuning(
            profile_id="custom",
            optimizer_profile="dev38-staged-rotation008-absgrad050-fastgs",
            pruning_policy="spatial-bounds",
            raster_profile="fastgs",
            topology_cooldown=100,
            photometric_finish=100,
            photometric_mse_percent=100,
            adaptive_growth_target=True,
            test_every=8,
            canary_min_psnr=None,
            canary_min_ssim=None,
        )
    )

    command = DroneGSBackend("/opt/dronegs").build_command(training_request)

    assert command[command.index("--optimizer-profile") + 1] == ("dev38-staged-rotation008-absgrad050-fastgs")
    assert command[command.index("--pruning-policy") + 1] == "spatial-bounds"
    assert command[command.index("--raster-profile") + 1] == "fastgs"
    assert command[command.index("--topology-cooldown") + 1] == "100"
    assert command[command.index("--photometric-finish") + 1] == "100"
    assert command[command.index("--photometric-mse-percent") + 1] == "100"
    assert command[command.index("--adaptive-growth-target") + 1] == "1"


def test_dronegs_tuning_rejects_non_boolean_adaptive_growth():
    with pytest.raises(ValueError, match="adaptive_growth_target"):
        DroneGSTuning(adaptive_growth_target=1)


@pytest.mark.parametrize("value", [255, 65_537, True])
def test_dronegs_tuning_rejects_invalid_host_image_cache(value):
    with pytest.raises(ValueError, match="host_image_cache_mib"):
        DroneGSTuning(host_image_cache_mib=value)


def test_dronegs_adapter_runs_contract_executable(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    executable = tmp_path / "fake_dronegs"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

arguments = dict(zip(sys.argv[1::2], sys.argv[2::2]))
output = Path(arguments["--output-path"])
output.mkdir(parents=True, exist_ok=True)
Path(arguments["--checkpoint-path"]).write_bytes(b"checkpoint")
(output / "point_cloud.ply").write_text(
    "ply\\nformat ascii 1.0\\nelement vertex 2\\nend_header\\n",
    encoding="utf-8",
)
manifest = Path(arguments["--run-manifest"])
manifest.write_text(
    json.dumps({
        "contract_version": 1,
        "backend": "dronegs-native-mrnf-fastgs",
        "trainer_version": "test",
        "git_revision": "test",
        "status": "completed",
        "dataset": {"fingerprint": arguments["--dataset-fingerprint"]},
        "parameters": {
            "profile_id": arguments["--profile-id"],
            "optimizer_profile": arguments["--optimizer-profile"],
            "pruning_policy": arguments["--pruning-policy"],
            "raster_profile": arguments["--raster-profile"],
            "effective_raster_profile": arguments["--raster-profile"],
            "test_split": arguments["--test-split"],
            "test_guard_percent": int(arguments["--test-guard-percent"]),
        },
        "timings": {},
        "metrics": {"psnr": 21.5, "ssim": 0.58},
        "artifacts": {
            "point_cloud.ply": {
                "path": str(output / "point_cloud.ply"),
                "sha256": None,
                "bytes": 0,
            },
        },
    }),
    encoding="utf-8",
)
print(json.dumps({
    "event": "progress", "iteration": 5, "loss": 0.25, "gaussians": 2,
}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    progress = []
    training_request = request(
        data_path=str(dataset),
        output_path=str(tmp_path / "output"),
    )

    result = DroneGSBackend(str(executable)).train(
        training_request,
        report_fn=lambda *event: progress.append(event),
    )

    assert result.ply_path.is_file()
    assert result.manifest_path.is_file()
    assert result.effective_seed == 42
    assert progress == [(5, 0.25, 2)]
    canary = json.loads(
        (tmp_path / "output" / "canary_result.json").read_text(encoding="utf-8")
    )
    assert canary["status"] == "passed"
    assert canary["qualification_policy_id"] == "DRONEGS_QUALIFICATION_POLICY_V1"
    assert not (tmp_path / "output" / "training.ckpt").exists()


def test_dronegs_adapter_allows_nonempty_output_only_for_resume(tmp_path):
    dataset = tmp_path / "dataset"
    output = tmp_path / "output"
    dataset.mkdir()
    output.mkdir()
    checkpoint = output / "training.ckpt"
    checkpoint.write_bytes(b"checkpoint")
    executable = tmp_path / "dronegs"
    executable.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    executable.chmod(0o755)
    training_request = request(
        data_path=str(dataset),
        output_path=str(output),
        dronegs=DroneGSTuning(
            profile_id="test-profile",
            topology_cooldown=100,
            photometric_finish=100,
            resume_from=str(checkpoint),
            canary_min_psnr=None,
            canary_min_ssim=None,
        ),
    )

    command = DroneGSBackend(str(executable)).build_command(training_request)

    assert command[command.index("--resume-from") + 1] == str(checkpoint)


def test_dronegs_quality_canary_rejects_below_threshold(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    executable = tmp_path / "fake_dronegs"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

arguments = dict(zip(sys.argv[1::2], sys.argv[2::2]))
output = Path(arguments["--output-path"])
output.mkdir(parents=True, exist_ok=True)
(output / "point_cloud.ply").write_text(
    "ply\\nformat ascii 1.0\\nelement vertex 1\\nend_header\\n",
    encoding="utf-8",
)
Path(arguments["--run-manifest"]).write_text(
    json.dumps({
        "contract_version": 1,
        "backend": "dronegs-native-mrnf-fastgs",
        "trainer_version": "test",
        "git_revision": "test",
        "status": "completed",
        "dataset": {"fingerprint": arguments["--dataset-fingerprint"]},
        "parameters": {
            "profile_id": arguments["--profile-id"],
            "optimizer_profile": arguments["--optimizer-profile"],
            "pruning_policy": arguments["--pruning-policy"],
            "raster_profile": arguments["--raster-profile"],
            "effective_raster_profile": arguments["--raster-profile"],
            "test_split": arguments["--test-split"],
            "test_guard_percent": int(arguments["--test-guard-percent"]),
        },
        "timings": {},
        "metrics": {"psnr": 17.0, "ssim": 0.30},
        "artifacts": {
            "point_cloud.ply": {
                "path": str(output / "point_cloud.ply"),
                "sha256": None,
                "bytes": 0,
            },
        },
    }),
    encoding="utf-8",
)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    training_request = request(
        data_path=str(dataset),
        output_path=str(tmp_path / "output"),
        dronegs=DroneGSTuning(
            profile_id="test-profile",
            topology_cooldown=100,
            photometric_finish=100,
            test_every=8,
            canary_min_psnr=18.0,
            canary_min_ssim=0.35,
        ),
    )

    with pytest.raises(RuntimeError, match="quality canary failed"):
        DroneGSBackend(str(executable)).train(training_request)

    canary = json.loads((tmp_path / "output" / "canary_result.json").read_text(encoding="utf-8"))
    assert canary["status"] == "failed"
    assert canary["failed_metrics"] == ["psnr", "ssim"]


def test_dronegs_adapter_rejects_nonempty_output(tmp_path):
    dataset = tmp_path / "dataset"
    output = tmp_path / "output"
    dataset.mkdir()
    output.mkdir()
    (output / "stale.ply").write_text("stale", encoding="utf-8")
    executable = tmp_path / "dronegs"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    with pytest.raises(FileExistsError, match="not empty"):
        DroneGSBackend(str(executable)).train(
            request(data_path=str(dataset), output_path=str(output)),
        )


def test_spatial_split_requires_valid_guard_configuration():
    tuning = {
        "profile_id": "custom",
        "topology_cooldown": 100,
        "photometric_finish": 100,
        "test_every": 8,
        "canary_min_psnr": None,
        "canary_min_ssim": None,
    }
    with pytest.raises(ValueError, match="test_guard_percent"):
        DroneGSTuning(**tuning, test_guard_percent=25)
    with pytest.raises(ValueError, match="unsupported DroneGS test_split"):
        DroneGSTuning(**tuning, test_split="random")

    spatial = DroneGSTuning(
        **tuning,
        test_split="spatial-block",
        test_guard_percent=25,
    )
    command = DroneGSBackend("/opt/dronegs").build_command(request(dronegs=spatial))
    assert command[command.index("--test-split") + 1] == "spatial-block"
    assert command[command.index("--test-guard-percent") + 1] == "25"


def test_resolver_defaults_to_dronegs():
    backend = resolve_training_backend(environment={})

    assert isinstance(backend, DroneGSBackend)


def test_resolver_supports_environment_selection():
    backend = resolve_training_backend(
        environment={"DRONEAI_GAUSSIAN_BACKEND": "dronegs", "DRONEGS_BIN": "/bin/dronegs"}
    )

    assert isinstance(backend, DroneGSBackend)
    assert backend.build_command(request())[0] == "/bin/dronegs"


def test_resolver_rejects_unknown_backend():
    with pytest.raises(ValueError, match="unknown Gaussian training backend"):
        resolve_training_backend("other", environment={})


def test_production_profile_command_matches_accepted_dev45_recipe():
    profile = DRONEGS_PRODUCTION_PROFILE_V1
    training_request = TrainingRequest(
        data_path="/data",
        output_path="/output",
        iterations=profile.iterations,
        strategy="mrnf",
        sh_degree=profile.sh_degree,
        max_cap=profile.cap_max,
        resize_factor=profile.data_factor,
        max_width=profile.max_width,
        tile_mode=profile.tile_mode,
        seed=profile.seed,
        dataset_fingerprint="benchmark-fixture",
        dronegs=DroneGSTuning(),
    )

    command = DroneGSBackend("/opt/dronegs").build_command(training_request)
    arguments = dict(zip(command[1::2], command[2::2], strict=True))

    assert arguments["--profile-id"] == "DRONEGS_PRODUCTION_PROFILE_V1"
    assert arguments["--optimizer-profile"] == "reference-absolute"
    assert arguments["--pruning-policy"] == "spatial-bounds"
    assert arguments["--raster-profile"] == "fastgs"
    assert arguments["--iter"] == "15000"
    assert arguments["--resize-factor"] == "4"
    assert arguments["--max-width"] == "1600"
    assert arguments["--max-cap"] == "1500000"
    assert arguments["--topology-cooldown"] == "1000"
    assert arguments["--photometric-finish"] == "1000"
    assert arguments["--photometric-mse-percent"] == "100"
    assert arguments["--test-split"] == "modulo"
    assert arguments["--test-guard-percent"] == "0"


def test_dronegs_cancellation_terminates_process_and_keeps_checkpoint(
    tmp_path,
):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    executable = tmp_path / "slow_dronegs"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys
import time
from pathlib import Path

arguments = dict(zip(sys.argv[1::2], sys.argv[2::2]))
checkpoint = Path(arguments["--checkpoint-path"])
checkpoint.parent.mkdir(parents=True, exist_ok=True)
checkpoint.write_bytes(b"recoverable")
print(json.dumps({
    "event": "checkpoint_saved",
    "iteration": 10,
    "path": str(checkpoint),
}), flush=True)
time.sleep(60)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    checks = 0
    checkpoints = []

    def cancel() -> None:
        nonlocal checks
        checks += 1
        if checks >= 3:
            raise RuntimeError("cancel requested")

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="cancel requested"):
        DroneGSBackend(str(executable)).train(
            request(
                data_path=str(dataset),
                output_path=str(tmp_path / "output"),
            ),
            cancellation_check=cancel,
            checkpoint_fn=lambda path, iteration: checkpoints.append((path, iteration)),
        )

    assert time.monotonic() - started < 5
    assert checkpoints[0][1] == 10
    assert checkpoints[0][0].read_bytes() == b"recoverable"
