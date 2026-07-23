from argparse import Namespace
from pathlib import Path

import pytest

from tools.run_local_detection import (
    PROFILES,
    ensure_inside_workspace,
    resolve_profile,
)


def _arguments(**overrides):
    values = {
        "profile": "full",
        "model_variant": None,
        "tile_size": None,
        "overlap": None,
        "confidence": None,
        "max_tiles": None,
    }
    values.update(overrides)
    return Namespace(**values)


def test_smoke_profile_uses_small_model_and_one_tile():
    profile = PROFILES["smoke"]

    assert profile.model_variant == "yolo26n"
    assert profile.max_tiles == 1


def test_profile_overrides_are_validated():
    profile = resolve_profile(
        _arguments(
            model_variant="11n",
            tile_size=768,
            overlap=128,
            confidence=0.35,
        )
    )

    assert profile.model_variant == "yolo11n"
    assert profile.tile_size == 768
    assert profile.overlap == 128
    assert profile.confidence == 0.35


def test_profile_rejects_overlap_equal_to_tile_size():
    with pytest.raises(ValueError, match="overlap"):
        resolve_profile(_arguments(tile_size=512, overlap=512))


def test_paths_must_stay_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert (
        ensure_inside_workspace(
            workspace / "output",
            workspace,
            "output-dir",
        )
        == workspace / "output"
    )
    with pytest.raises(ValueError, match="inside the marked workspace"):
        ensure_inside_workspace(
            tmp_path / "outside",
            workspace,
            "output-dir",
        )
