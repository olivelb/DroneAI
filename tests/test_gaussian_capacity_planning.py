from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


APP1_ROOT = Path(__file__).resolve().parents[1] / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

capacity = importlib.import_module("gaussian_ortho.capacity_planning")


def _surface(width: float, height: float) -> np.ndarray:
    x, y = np.meshgrid(
        np.linspace(0.0, width, 40),
        np.linspace(0.0, height, 40),
    )
    z = 0.01 * x + 0.02 * y
    return np.column_stack((x.ravel(), y.ravel(), z.ravel()))


def test_robust_area_is_orientation_independent_and_rejects_outliers():
    points = _surface(500.0, 500.0)
    angle = np.deg2rad(37.0)
    rotation = np.array(
        [
            [np.cos(angle), 0.0, np.sin(angle)],
            [0.0, 1.0, 0.0],
            [-np.sin(angle), 0.0, np.cos(angle)],
        ]
    )
    rotated = points @ rotation.T
    rotated = np.vstack((rotated, [[100_000.0, -100_000.0, 50_000.0]]))

    area = capacity.robust_ground_area_m2(
        rotated,
        meters_per_model_unit=1.0,
    )

    assert area == pytest.approx(250_000.0, rel=0.08)


def test_villeseque_sized_hq_plan_uses_surface_then_rtx3090_ceiling():
    plan = capacity.plan_gaussian_capacity(
        mode="adaptive",
        requested_cap=12_000_000,
        capacity_floor=5_000_000,
        target_spacing_pixels=8.0,
        points=_surface(500.0, 500.0),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.015,
        total_vram_bytes=24 * capacity.GIB,
    )

    assert plan.surface_target is not None
    assert plan.surface_target > 12_000_000
    assert plan.vram_cap is not None
    assert plan.vram_cap > 12_000_000
    assert plan.effective_scene_cap == 12_000_000
    assert plan.effective_cell_cap == 12_000_000


def test_adaptive_plan_reduces_capacity_on_a_smaller_gpu_and_partitions_scene():
    plan = capacity.plan_gaussian_capacity(
        mode="adaptive",
        requested_cap=12_000_000,
        capacity_floor=5_000_000,
        target_spacing_pixels=8.0,
        points=_surface(500.0, 500.0),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.015,
        total_vram_bytes=12 * capacity.GIB,
        cell_count=4,
    )

    assert plan.vram_cap is not None
    assert plan.effective_scene_cap == plan.vram_cap
    assert plan.effective_cell_cap * 4 >= plan.effective_scene_cap
    assert plan.effective_cell_cap < plan.effective_scene_cap


def test_fixed_preview_keeps_its_reproducible_cap():
    plan = capacity.plan_gaussian_capacity(
        mode="fixed",
        requested_cap=1_500_000,
        capacity_floor=1_500_000,
        target_spacing_pixels=0.0,
        points=_surface(50.0, 50.0),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.05,
        total_vram_bytes=8 * capacity.GIB,
    )

    assert plan.robust_ground_area_m2 is None
    assert plan.effective_scene_cap == 1_500_000


def test_detected_vram_is_optional_for_cpu_contract_tests():
    assert capacity.detected_vram_bytes(SimpleNamespace()) is None
