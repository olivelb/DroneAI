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
        resident_partitioning=True,
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
        resident_partitioning=True,
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
        resident_partitioning=True,
    )

    assert plan.cells_sufficient
    assert plan.effective_cell_cap <= plan.resident_cap


def test_normal_candidate_fits_each_resident_block_in_eight_gigabytes():
    preliminary = capacity.plan_gaussian_capacity(
        mode="adaptive",
        requested_cap=8_000_000,
        capacity_floor=3_000_000,
        target_spacing_pixels=8.0,
        points=_surface(500.0, 418.8),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.02,
        total_vram_bytes=8 * capacity.GIB,
        resident_partitioning=True,
    )

    assert preliminary.surface_target == pytest.approx(8_200_000, rel=0.03)
    assert preliminary.training_target is not None
    assert preliminary.resident_cap == 2_300_000
    assert preliminary.required_cell_count == 8
    assert preliminary.estimated_capacity_bytes < 3 * capacity.GIB

    plan = capacity.plan_gaussian_capacity(
        mode="adaptive",
        requested_cap=8_000_000,
        capacity_floor=3_000_000,
        target_spacing_pixels=8.0,
        points=_surface(500.0, 418.8),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.02,
        total_vram_bytes=8 * capacity.GIB,
        cell_count=preliminary.required_cell_count,
        resident_partitioning=True,
    )

    assert plan.cells_sufficient
    assert plan.effective_cell_cap == 2_100_000
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
        resident_partitioning=True,
    )

    assert plan.effective_scene_cap == pytest.approx(40_400_000, rel=0.03)
    assert plan.resident_cap == 12_000_000
    assert plan.required_cell_count == 7


def test_resident_scene_that_fits_does_not_pay_theoretical_buffer_overhead():
    plan = capacity.plan_gaussian_capacity(
        mode="adaptive",
        requested_cap=12_000_000,
        capacity_floor=5_000_000,
        target_spacing_pixels=3.6,
        points=_surface(200.0, 200.0),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.02,
        total_vram_bytes=24 * capacity.GIB,
        resident_partitioning=True,
    )

    assert 7_000_000 < plan.surface_target < 8_000_000
    assert plan.post_filter_retention_target == pytest.approx(0.98)
    assert plan.training_target is not None
    assert plan.training_target > plan.surface_target
    assert plan.effective_scene_cap == plan.training_target
    assert plan.required_cell_count == 1
    assert plan.effective_cell_cap == plan.effective_scene_cap


def test_resident_adaptive_plan_reserves_two_percent_for_post_filtering():
    plan = capacity.plan_gaussian_capacity(
        mode="adaptive",
        requested_cap=12_000_000,
        capacity_floor=5_000_000,
        target_spacing_pixels=3.6,
        points=_surface(200.0, 147.31),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.02,
        total_vram_bytes=24 * capacity.GIB,
        resident_partitioning=True,
    )

    assert plan.surface_target == 5_700_000
    assert plan.training_target == 5_800_000
    assert plan.effective_scene_cap == 5_800_000
    assert int(plan.training_target * plan.post_filter_retention_target) >= 5_683_256


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


def test_legacy_adaptive_profile_keeps_its_monolithic_operator_cap():
    plan = capacity.plan_gaussian_capacity(
        mode="adaptive",
        requested_cap=12_000_000,
        capacity_floor=5_000_000,
        target_spacing_pixels=3.6,
        points=_surface(500.0, 418.8),
        meters_per_model_unit=1.0,
        requested_gsd_m=0.02,
        total_vram_bytes=24 * capacity.GIB,
        resident_partitioning=False,
    )

    assert plan.effective_scene_cap == 12_000_000
    assert plan.required_cell_count == 1
    assert plan.post_filter_retention_target == 1.0
    assert plan.training_target == plan.surface_target


def test_detected_vram_is_optional_for_cpu_contract_tests():
    assert capacity.detected_vram_bytes(SimpleNamespace()) is None


def test_detected_vram_can_be_lowered_for_reproducible_qualification(
    monkeypatch,
):
    monkeypatch.setenv(capacity.VRAM_BUDGET_ENV, "8")
    cupy = SimpleNamespace(
        cuda=SimpleNamespace(Device=lambda _index: SimpleNamespace(mem_info=(20 * capacity.GIB, 24 * capacity.GIB)))
    )

    assert capacity.detected_vram_bytes(cupy) == (
        8 * capacity.GIB,
        8 * capacity.GIB,
    )


def test_detected_vram_budget_never_increases_available_memory(monkeypatch):
    monkeypatch.setenv(capacity.VRAM_BUDGET_ENV, "32")
    cupy = SimpleNamespace(
        cuda=SimpleNamespace(Device=lambda _index: SimpleNamespace(mem_info=(6 * capacity.GIB, 24 * capacity.GIB)))
    )

    assert capacity.detected_vram_bytes(cupy) == (
        6 * capacity.GIB,
        24 * capacity.GIB,
    )


def test_detected_vram_rejects_an_invalid_operator_budget(monkeypatch):
    monkeypatch.setenv(capacity.VRAM_BUDGET_ENV, "not-a-number")
    cupy = SimpleNamespace(
        cuda=SimpleNamespace(Device=lambda _index: SimpleNamespace(mem_info=(8 * capacity.GIB, 8 * capacity.GIB)))
    )

    with pytest.raises(ValueError, match=capacity.VRAM_BUDGET_ENV):
        capacity.detected_vram_bytes(cupy)


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
    assert capacity.density_assessment_from_dict(assessment.as_dict()) == assessment


FULL_4K_REGION = [(4096, 4096, 4096, 4096, False)]


@pytest.mark.parametrize(
    ("gaussian_capacity_bytes", "expected_mode"),
    [
        (12_000_000 * capacity.GAUSSIAN_CAPACITY_BYTES, 1),
        (2_100_000 * capacity.GAUSSIAN_CAPACITY_BYTES, 2),
        (int(4.3 * capacity.GIB), 4),
    ],
)
def test_automatic_tile_mode_uses_the_largest_view_that_fits_vram(
    gaussian_capacity_bytes,
    expected_mode,
):
    total = 24 * capacity.GIB if expected_mode == 1 else 8 * capacity.GIB

    plan = capacity.plan_training_tile_mode(
        FULL_4K_REGION,
        configured_mode=4,
        automatic=True,
        resize_factor=1,
        max_width=4096,
        gaussian_capacity_bytes=gaussian_capacity_bytes,
        free_vram_bytes=total,
        total_vram_bytes=total,
    )

    assert plan.effective_mode == expected_mode
    assert plan.fits_estimate
    assert plan.estimated_pixel_bytes <= plan.available_pixel_bytes


def test_automatic_tile_mode_reports_when_even_four_tiles_do_not_fit():
    plan = capacity.plan_training_tile_mode(
        FULL_4K_REGION,
        configured_mode=4,
        automatic=True,
        resize_factor=1,
        max_width=4096,
        gaussian_capacity_bytes=5 * capacity.GIB,
        free_vram_bytes=8 * capacity.GIB,
        total_vram_bytes=8 * capacity.GIB,
    )

    assert plan.effective_mode == 4
    assert not plan.fits_estimate
    assert plan.estimated_pixel_bytes > plan.available_pixel_bytes


def test_automatic_tile_mode_falls_back_conservatively_without_vram_inventory():
    plan = capacity.plan_training_tile_mode(
        FULL_4K_REGION,
        configured_mode=4,
        automatic=True,
        resize_factor=1,
        max_width=4096,
        gaussian_capacity_bytes=0,
        free_vram_bytes=None,
        total_vram_bytes=None,
    )

    assert plan.effective_mode == 4
    assert plan.fits_estimate is None


def test_expert_tile_mode_is_never_silently_overridden():
    plan = capacity.plan_training_tile_mode(
        FULL_4K_REGION,
        configured_mode=1,
        automatic=False,
        resize_factor=1,
        max_width=4096,
        gaussian_capacity_bytes=5 * capacity.GIB,
        free_vram_bytes=8 * capacity.GIB,
        total_vram_bytes=8 * capacity.GIB,
    )

    assert plan.effective_mode == 1
    assert not plan.fits_estimate


def test_adaptive_native_crop_does_not_over_split_a_small_resident_view():
    regions = [(1024, 768, 4096, 3072, True)]

    pixels = capacity.maximum_training_view_pixels(
        regions,
        tile_mode=4,
        resize_factor=1,
        max_width=4096,
    )

    assert pixels == 1024 * 768


def test_insufficient_vram_does_not_invent_a_positive_capacity():
    from gaussian_ortho.capacity_planning import vram_gaussian_cap, GIB
    import pytest
    for total, free in ((4 * GIB, 4 * GIB), (8 * GIB, 4 * GIB), (8 * GIB, 0)):
        with pytest.raises(ValueError, match="Insufficient VRAM"):
            vram_gaussian_cap(total, free)
