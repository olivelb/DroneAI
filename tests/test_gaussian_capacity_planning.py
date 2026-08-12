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


def test_hq_plan_separates_merged_target_from_resident_rtx3090_cap():
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
    assert plan.effective_scene_cap > 17_000_000
    assert plan.resident_cap == 12_000_000
    assert plan.required_cell_count == 3
    assert not plan.cells_sufficient
    assert plan.effective_cell_cap == 12_000_000


def test_adaptive_plan_adds_cells_for_a_smaller_gpu():
    preliminary = capacity.plan_gaussian_capacity(
        mode="adaptive",
        requested_cap=12_000_000,
        capacity_floor=5_000_000,
        target_spacing_pixels=8.0,
        points=_surface(500.0, 500.0),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.015,
        total_vram_bytes=12 * capacity.GIB,
    )

    assert preliminary.vram_cap is not None
    assert preliminary.resident_cap == preliminary.vram_cap
    assert preliminary.required_cell_count > 4
    assert preliminary.effective_scene_cap > preliminary.resident_cap

    plan = capacity.plan_gaussian_capacity(
        mode="adaptive",
        requested_cap=12_000_000,
        capacity_floor=5_000_000,
        target_spacing_pixels=8.0,
        points=_surface(500.0, 500.0),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.015,
        total_vram_bytes=12 * capacity.GIB,
        cell_count=preliminary.required_cell_count,
    )

    assert plan.cells_sufficient
    assert plan.effective_cell_cap <= plan.resident_cap


def test_two_centimeter_hq_candidate_targets_about_40m_merged_gaussians():
    plan = capacity.plan_gaussian_capacity(
        mode="adaptive",
        requested_cap=12_000_000,
        capacity_floor=5_000_000,
        target_spacing_pixels=3.6,
        points=_surface(500.0, 418.8),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.02,
        total_vram_bytes=24 * capacity.GIB,
    )

    assert plan.effective_scene_cap == pytest.approx(40_400_000, rel=0.03)
    assert plan.resident_cap == 12_000_000
    assert plan.required_cell_count == 7


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


def test_achieved_density_rejects_an_unsupported_requested_gsd():
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

    assessment = capacity.assess_gaussian_density(
        plan,
        actual_gaussian_count=10_000_000,
    )

    assert not assessment.accepted
    assert assessment.required_gaussian_count > 17_000_000
    assert assessment.achieved_spacing_pixels > 8.0
    assert assessment.minimum_compatible_gsd_m > 0.019


def test_achieved_density_accepts_a_supported_requested_gsd():
    plan = capacity.plan_gaussian_capacity(
        mode="adaptive",
        requested_cap=20_000_000,
        capacity_floor=5_000_000,
        target_spacing_pixels=8.0,
        points=_surface(500.0, 500.0),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.02,
        total_vram_bytes=48 * capacity.GIB,
    )

    assessment = capacity.assess_gaussian_density(
        plan,
        actual_gaussian_count=10_000_000,
    )

    assert assessment.accepted
    assert assessment.achieved_spacing_pixels < 8.0

    assert capacity.capacity_plan_from_dict(plan.as_dict()) == plan
    assert (
        capacity.density_assessment_from_dict(assessment.as_dict())
        == assessment
    )
