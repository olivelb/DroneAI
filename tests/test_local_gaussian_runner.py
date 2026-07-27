from argparse import Namespace

import pytest

from tools.run_local_gaussian import PROFILES, output_paths, resolve_profile


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
    assert profile.optimizer_profile == (
        "dev38-staged-rotation008-absgrad050-fastgs"
    )
    assert profile.raster_profile == "fastgs"
    assert profile.pruning_policy == "lichtfeld-bounds"
    assert profile.topology_cooldown == 1_000
    assert profile.photometric_finish == 1_000
    assert profile.photometric_mse_percent == 100


def test_profile_overrides_are_explicit_and_validated():
    profile = resolve_profile(
        _arguments(iterations=750, max_width=1200, filter_enabled=False)
    )

    assert profile.iterations == 750
    assert profile.max_width == 1200
    assert profile.filter_enabled is False
    assert profile.cap_max == PROFILES["low-memory"].cap_max


def test_profile_rejects_invalid_resolution():
    with pytest.raises(ValueError, match="resolution"):
        resolve_profile(_arguments(resolution=0))


def test_custom_output_must_stay_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="inside the marked workspace"):
        output_paths(workspace, "smoke", tmp_path / "outside.tif")
