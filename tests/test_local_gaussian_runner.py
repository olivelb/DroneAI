import json
import os
import subprocess
from argparse import ArgumentTypeError, Namespace
from pathlib import Path

import pytest

from shared.facade_process import (
    FACADE_DRONEGS_PROFILE_ID,
    FACADE_QUALIFICATION_POLICY_ID,
)
from tools.run_local_gaussian import (
    PROFILES,
    clear_generated_outputs,
    output_paths,
    persist_runtime_plan,
    report_parameters,
    resolve_profile,
    validated_run_label,
)


def _arguments(**overrides):
    values = {
        "profile": "low-memory",
        "iterations": None,
        "cap_max": None,
        "sh_degree": None,
        "data_factor": None,
        "max_width": None,
        "tile_mode": None,
        "resolution": None,
        "canary_min_psnr": None,
        "canary_min_ssim": None,
        "checkpoint_every": None,
        "host_image_cache_mib": None,
        "filter_enabled": None,
    }
    values.update(overrides)
    return Namespace(**values)


def test_low_memory_profile_is_conservative_for_eight_gigabytes():
    profile = PROFILES["low-memory"]

    assert profile.backend == "dronegs"
    assert profile.cap_max <= 500_000
    assert profile.sh_degree <= 1
    assert profile.data_factor >= 4
    assert profile.tile_mode_auto is True
    assert profile.tile_mode == 4


def test_balanced_profile_matches_validated_dev45_recipe():
    profile = PROFILES["balanced"]

    assert profile.iterations == 15_000
    assert profile.cap_max == 1_500_000
    assert profile.sh_degree == 3
    assert profile.data_factor == 4
    assert profile.max_width == 1600
    assert profile.optimizer_profile == "reference-absolute"
    assert profile.raster_profile == "fastgs"
    assert profile.profile_id == "DRONEGS_PRODUCTION_PROFILE_V1"
    assert profile.pruning_policy == "spatial-bounds"
    assert profile.topology_cooldown == 1_000
    assert profile.photometric_finish == 1_000
    assert profile.photometric_mse_percent == 100


def test_fast_profile_matches_versioned_minimum_quality_envelope():
    profile = PROFILES["fast"]

    assert profile.iterations == 7_500
    assert profile.cap_max == 1_500_000
    assert profile.data_factor == 8
    assert profile.max_width == 1600
    assert profile.profile_id == "fast-v1"


def test_fast_resident_profile_exercises_preview_seams_without_changing_fast():
    profile = PROFILES["fast-resident"]

    assert profile.iterations == 7_500
    assert profile.cap_max == 1_500_000
    assert profile.data_factor == 8
    assert profile.max_width == 1600
    assert profile.profile_id == "fast-v2"
    assert profile.capacity_mode == "adaptive"
    assert profile.capacity_floor == 1_500_000
    assert profile.target_gaussian_spacing_pixels == 8.0
    assert profile.resident_partitioning is True
    assert profile.initial_scale_policy == "projected-knn"
    assert profile.capacity_targeted_growth is True
    assert profile.resolution == 0.05


def test_normal_profile_matches_versioned_quality_envelope():
    profile = PROFILES["normal"]

    assert profile.iterations == 15_000
    assert profile.cap_max == 8_000_000
    assert profile.data_factor == 4
    assert profile.max_width == 2400
    assert profile.profile_id == "normal-v3"
    assert profile.capacity_mode == "adaptive"
    assert profile.capacity_floor == 3_000_000
    assert profile.target_gaussian_spacing_pixels == 8.0
    assert profile.resident_partitioning is True
    assert profile.resolution == 0.02


def test_high_quality_profile_matches_versioned_quality_envelope():
    profile = PROFILES["high-quality"]

    assert profile.iterations == 30_000
    assert profile.cap_max == 6_000_000
    assert profile.data_factor == 1
    assert profile.max_width == 4096
    assert profile.profile_id == "high-quality-v4"
    assert profile.capacity_mode == "adaptive"
    assert profile.capacity_floor == 5_000_000
    assert profile.target_gaussian_spacing_pixels == 3.6
    assert profile.resident_partitioning is True
    assert profile.initial_scale_policy == "projected-knn"
    assert profile.initial_max_projected_sigma_pixels == 8.0
    assert profile.capacity_targeted_growth is True
    assert profile.resolution == 0.02


def test_ab_run_labels_are_bounded_and_cannot_escape_the_workspace():
    assert validated_run_label("hq-v3-absgrad025") == "hq-v3-absgrad025"
    for invalid in ("../escape", "/absolute", "Uppercase", "", "a" * 65):
        with pytest.raises(ArgumentTypeError, match="run-label must match"):
            validated_run_label(invalid)


def test_facade_hd_profile_keeps_4k_detail_with_bounded_capacity():
    profile = PROFILES["facade-hd"]

    assert profile.resolution == 0.01
    assert profile.data_factor == 1
    assert profile.max_width == 4096
    assert profile.cap_max == 6_000_000
    assert profile.sh_degree == 3
    assert profile.iterations == 30_000
    assert profile.test_split == "modulo"
    assert profile.profile_id == FACADE_DRONEGS_PROFILE_ID
    assert profile.qualification_policy_id == FACADE_QUALIFICATION_POLICY_ID
    assert profile.capacity_mode == "adaptive"
    assert profile.capacity_floor == 5_000_000
    assert profile.target_gaussian_spacing_pixels == 3.6
    assert profile.resident_partitioning is True
    assert profile.initial_scale_policy == "projected-knn"
    assert profile.initial_max_projected_sigma_pixels == 8.0
    assert profile.capacity_targeted_growth is True


def test_profile_overrides_are_explicit_and_validated():
    profile = resolve_profile(_arguments(iterations=750, max_width=1200, filter_enabled=False))

    assert profile.iterations == 750
    assert profile.max_width == 1200
    assert profile.filter_enabled is False
    assert profile.cap_max == PROFILES["low-memory"].cap_max


def test_numeric_tile_mode_is_an_expert_override():
    profile = resolve_profile(_arguments(profile="balanced", tile_mode=1))

    assert profile.tile_mode == 1
    assert profile.tile_mode_auto is False
    assert profile.profile_id == "custom"


def test_report_parameters_distinguish_automatic_policy_from_effective_mode():
    automatic = report_parameters(PROFILES["facade-hd"])
    expert = report_parameters(resolve_profile(_arguments(profile="balanced", tile_mode=1)))

    assert automatic["tile_mode"] == "auto"
    assert automatic["tile_mode_configured"] == 4
    assert automatic["tile_mode_auto"] is True
    assert expert["tile_mode"] == 1
    assert expert["tile_mode_configured"] == 1
    assert expert["tile_mode_auto"] is False


def test_effective_runtime_plan_is_persisted_before_training_completion(tmp_path):
    report_path = tmp_path / "gaussian_run.facade.json"
    report = {"schema_version": 2, "status": "running"}
    runtime_plan = {
        "tile_mode": 1,
        "tile_mode_plan": {"automatic": True, "effective_mode": 1},
    }

    persist_runtime_plan(report_path, report, runtime_plan)
    report.update(status="failed", error="later preparation failed")
    persist_runtime_plan(report_path, report, runtime_plan)

    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["effective_parameters"] == runtime_plan


def test_balanced_training_overrides_become_custom_recipe():
    profile = resolve_profile(
        _arguments(
            profile="balanced",
            iterations=30_000,
            cap_max=2_000_000,
            data_factor=1,
            max_width=4096,
        )
    )

    assert profile.profile_id == "custom"
    assert profile.optimizer_profile == "reference-absolute"
    assert profile.canary_min_psnr == PROFILES["balanced"].canary_min_psnr
    assert profile.canary_min_ssim == PROFILES["balanced"].canary_min_ssim


def test_checkpoint_cadence_override_is_an_explicit_training_recipe():
    profile = resolve_profile(_arguments(profile="normal", checkpoint_every=4_000))

    assert profile.checkpoint_every == 4_000
    assert profile.profile_id == "custom"

    with pytest.raises(ValueError, match="checkpoint-every must be positive"):
        resolve_profile(_arguments(checkpoint_every=0))


def test_host_image_cache_auto_is_operational_and_explicit_values_are_validated():
    automatic = resolve_profile(_arguments(profile="balanced"))
    explicit = resolve_profile(_arguments(profile="balanced", host_image_cache_mib=12_288))

    assert automatic.host_image_cache_mib == 0
    assert explicit.host_image_cache_mib == 12_288
    assert explicit.profile_id == automatic.profile_id

    with pytest.raises(ValueError, match="host-image-cache-mib"):
        resolve_profile(_arguments(host_image_cache_mib=255))


def test_projected_initialization_override_is_an_explicit_training_recipe():
    profile = resolve_profile(
        _arguments(
            profile="normal",
            initial_scale_policy="projected-knn",
            initial_max_projected_sigma_pixels=4.0,
            maximum_scale_growth_factor=8.0,
            capacity_targeted_growth=True,
        )
    )

    assert profile.profile_id == "custom"
    assert profile.initial_scale_policy == "projected-knn"
    assert profile.initial_max_projected_sigma_pixels == 4.0
    assert profile.maximum_scale_growth_factor == 8.0
    assert profile.capacity_targeted_growth is True


def test_facade_hd_overrides_preserve_separate_recipe_identities():
    training_override = resolve_profile(_arguments(profile="facade-hd", iterations=20_000))
    qualification_override = resolve_profile(_arguments(profile="facade-hd", canary_min_psnr=20.0))

    assert training_override.profile_id == "custom"
    assert training_override.qualification_policy_id == FACADE_QUALIFICATION_POLICY_ID
    assert qualification_override.profile_id == FACADE_DRONEGS_PROFILE_ID
    assert qualification_override.qualification_policy_id == "custom"


def test_balanced_raster_only_override_keeps_production_recipe():
    profile = resolve_profile(_arguments(profile="balanced", resolution=0.005))

    assert profile.profile_id == "DRONEGS_PRODUCTION_PROFILE_V1"


def test_canary_overrides_are_validated():
    profile = resolve_profile(_arguments(canary_min_psnr=18.0, canary_min_ssim=0.25))

    assert profile.canary_min_psnr == 18.0
    assert profile.canary_min_ssim == 0.25

    with pytest.raises(ValueError, match="canary-min-ssim"):
        resolve_profile(_arguments(canary_min_ssim=1.1))


def test_profile_rejects_invalid_resolution():
    with pytest.raises(ValueError, match="resolution"):
        resolve_profile(_arguments(resolution=0))


def test_custom_output_must_stay_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="inside the marked workspace"):
        output_paths(workspace, "smoke", tmp_path / "outside.tif")


def test_checkpoint_root_can_use_a_separate_fast_filesystem(tmp_path):
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()

    ortho, height, checkpoint = output_paths(
        workspace,
        "facade-metric",
        None,
        render_mode="facade",
        checkpoint_root=scratch,
    )

    assert ortho == workspace / "facade_orthophoto.facade-metric.tif"
    assert height == workspace / "facade_orthophoto.facade-metric.height.tif"
    assert checkpoint == scratch / "facade-metric"


def test_shell_wrapper_mounts_durable_and_training_roots_into_the_container(tmp_path):
    workspace = tmp_path / "workspace"
    checkpoint_root = tmp_path / "fast-checkpoints"
    training_workspace_root = tmp_path / "native-linux-training"
    fake_bin = tmp_path / "bin"
    docker_arguments = tmp_path / "docker-arguments.txt"
    workspace.mkdir()
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
if [[ "$1" == "image" && "$2" == "inspect" ]]; then
  exit 0
fi
printf '%s\\n' "$@" > "$DRONEAI_DOCKER_ARGS_LOG"
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DRONEAI_DOCKER_ARGS_LOG": str(docker_arguments),
    }

    subprocess.run(
        [
            "/usr/bin/bash",
            str(Path(__file__).parents[1] / "tools" / "run_local_gaussian.sh"),
            str(workspace),
            "--render-mode",
            "facade",
            "--checkpoint-root",
            str(checkpoint_root),
            "--training-workspace-root",
            str(training_workspace_root),
        ],
        check=True,
        env=environment,
    )

    arguments = docker_arguments.read_text(encoding="utf-8").splitlines()
    mount = f"{checkpoint_root.resolve()}:/checkpoints"
    assert arguments[arguments.index(mount) - 1] == "--volume"
    checkpoint_option = arguments.index("--checkpoint-root")
    assert arguments[checkpoint_option + 1] == "/checkpoints"
    training_mount = f"{training_workspace_root.resolve()}:/training-workspaces"
    assert arguments[arguments.index(training_mount) - 1] == "--volume"
    training_option = arguments.index("--training-workspace-root")
    assert arguments[training_option + 1] == "/training-workspaces"


def test_force_cleanup_removes_only_the_selected_training_workspace(tmp_path):
    ortho = tmp_path / "workspace" / "facade.tif"
    height = tmp_path / "workspace" / "facade.height.tif"
    checkpoint = tmp_path / "checkpoints" / "run-a"
    training = tmp_path / "training" / "run-a"
    sibling = tmp_path / "training" / "run-b"
    for directory in (ortho.parent, checkpoint, training, sibling):
        directory.mkdir(parents=True, exist_ok=True)
    ortho.write_bytes(b"ortho")
    height.write_bytes(b"height")
    (checkpoint / "state").write_bytes(b"checkpoint")
    (training / "subset").write_bytes(b"subset")
    (sibling / "keep").write_bytes(b"keep")

    clear_generated_outputs(ortho, height, checkpoint, training)

    assert not ortho.exists() and not height.exists()
    assert not checkpoint.exists() and not training.exists()
    assert (sibling / "keep").read_bytes() == b"keep"
