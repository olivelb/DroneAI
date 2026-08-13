from argparse import ArgumentTypeError, Namespace

import pytest

from shared.facade_process import (
    FACADE_DRONEGS_PROFILE_ID,
    FACADE_QUALIFICATION_POLICY_ID,
)
from tools.run_local_gaussian import (
    PROFILES,
    output_paths,
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
    assert profile.cap_max == 12_000_000
    assert profile.data_factor == 1
    assert profile.max_width == 4096
    assert profile.profile_id == "high-quality-v3"
    assert profile.capacity_mode == "adaptive"
    assert profile.capacity_floor == 5_000_000
    assert profile.target_gaussian_spacing_pixels == 3.6
    assert profile.resident_partitioning is True
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
    assert profile.cap_max == 12_000_000
    assert profile.sh_degree == 3
    assert profile.iterations == 30_000
    assert profile.test_split == "modulo"
    assert profile.profile_id == FACADE_DRONEGS_PROFILE_ID
    assert profile.qualification_policy_id == FACADE_QUALIFICATION_POLICY_ID
    assert profile.capacity_mode == "adaptive"
    assert profile.capacity_floor == 5_000_000
    assert profile.target_gaussian_spacing_pixels == 3.6
    assert profile.resident_partitioning is True


def test_profile_overrides_are_explicit_and_validated():
    profile = resolve_profile(
        _arguments(iterations=750, max_width=1200, filter_enabled=False)
    )

    assert profile.iterations == 750
    assert profile.max_width == 1200
    assert profile.filter_enabled is False
    assert profile.cap_max == PROFILES["low-memory"].cap_max


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
    profile = resolve_profile(
        _arguments(profile="normal", checkpoint_every=4_000)
    )

    assert profile.checkpoint_every == 4_000
    assert profile.profile_id == "custom"

    with pytest.raises(ValueError, match="checkpoint-every must be positive"):
        resolve_profile(_arguments(checkpoint_every=0))


def test_facade_hd_overrides_preserve_separate_recipe_identities():
    training_override = resolve_profile(
        _arguments(profile="facade-hd", iterations=20_000)
    )
    qualification_override = resolve_profile(
        _arguments(profile="facade-hd", canary_min_psnr=20.0)
    )

    assert training_override.profile_id == "custom"
    assert (
        training_override.qualification_policy_id
        == FACADE_QUALIFICATION_POLICY_ID
    )
    assert qualification_override.profile_id == FACADE_DRONEGS_PROFILE_ID
    assert qualification_override.qualification_policy_id == "custom"


def test_balanced_raster_only_override_keeps_production_recipe():
    profile = resolve_profile(
        _arguments(profile="balanced", resolution=0.005)
    )

    assert profile.profile_id == "DRONEGS_PRODUCTION_PROFILE_V1"


def test_canary_overrides_are_validated():
    profile = resolve_profile(
        _arguments(canary_min_psnr=18.0, canary_min_ssim=0.25)
    )

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
