import os
import sys
import types
import unittest


APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

if "rasterio" not in sys.modules:
    rasterio_module = types.ModuleType("rasterio")
    rasterio_module.open = None
    sys.modules["rasterio"] = rasterio_module

if "rasterio.transform" not in sys.modules:
    rasterio_transform_module = types.ModuleType("rasterio.transform")
    rasterio_transform_module.from_origin = None
    sys.modules["rasterio.transform"] = rasterio_transform_module

if "pycolmap" not in sys.modules:
    sys.modules["pycolmap"] = types.ModuleType("pycolmap")

import numpy as np

import ortho_edge_support


class TestEdgeAssignmentPaintMask(unittest.TestCase):
    def test_rejects_gap_filled_pixels_in_wide_edge_band(self):
        valid_mask = np.array(
            [
                [True, True, True],
                [True, True, True],
            ],
            dtype=bool,
        )
        edge_assignment_mask = np.array(
            [
                [False, True, True],
                [False, True, False],
            ],
            dtype=bool,
        )
        support_distance_px = np.array(
            [
                [0.0, 0.0, 1.0],
                [2.0, 0.0, 0.0],
            ],
            dtype=np.float32,
        )

        paintable_mask, rejected_mask = ortho_edge_support.build_edge_assignment_paint_mask(
            valid_mask,
            edge_assignment_mask,
            support_distance_px,
            max_support_distance_px=0,
        )

        expected_rejected = np.array(
            [
                [False, False, True],
                [False, False, False],
            ],
            dtype=bool,
        )
        expected_paintable = valid_mask & (~expected_rejected)

        self.assertTrue(np.array_equal(rejected_mask, expected_rejected))
        self.assertTrue(np.array_equal(paintable_mask, expected_paintable))

    def test_negative_limit_keeps_existing_valid_mask(self):
        valid_mask = np.array([[True, False], [True, True]], dtype=bool)
        edge_assignment_mask = np.array([[True, True], [False, True]], dtype=bool)
        support_distance_px = np.array([[2.0, 3.0], [1.0, 4.0]], dtype=np.float32)

        paintable_mask, rejected_mask = ortho_edge_support.build_edge_assignment_paint_mask(
            valid_mask,
            edge_assignment_mask,
            support_distance_px,
            max_support_distance_px=-1,
        )

        self.assertTrue(np.array_equal(paintable_mask, valid_mask))
        self.assertFalse(rejected_mask.any())


class TestMixedDepthRejectionMask(unittest.TestCase):
    def test_rejects_edge_cells_with_conflicting_raw_depths(self):
        raw_valid_mask = np.array(
            [
                [True, True, True],
                [True, True, False],
            ],
            dtype=bool,
        )
        edge_assignment_mask = np.array(
            [
                [False, True, True],
                [False, True, False],
            ],
            dtype=bool,
        )
        sample_count = np.array(
            [
                [4.0, 3.0, 1.0],
                [2.0, 5.0, 0.0],
            ],
            dtype=np.float32,
        )
        raw_depth_min = np.array(
            [
                [10.0, 10.0, 8.0],
                [9.0, 10.0, 0.0],
            ],
            dtype=np.float32,
        )
        raw_depth_max = np.array(
            [
                [10.1, 10.5, 8.1],
                [9.1, 10.4, 0.0],
            ],
            dtype=np.float32,
        )

        rejected_mask = ortho_edge_support.build_mixed_depth_rejection_mask(
            raw_valid_mask,
            edge_assignment_mask,
            sample_count,
            raw_depth_min,
            raw_depth_max,
            max_depth_spread_m=0.3,
            min_samples=2,
        )

        expected_rejected = np.array(
            [
                [False, True, False],
                [False, True, False],
            ],
            dtype=bool,
        )
        self.assertTrue(np.array_equal(rejected_mask, expected_rejected))

    def test_depth_spread_rule_can_be_disabled(self):
        raw_valid_mask = np.array([[True, True]], dtype=bool)
        edge_assignment_mask = np.array([[True, True]], dtype=bool)
        sample_count = np.array([[2.0, 4.0]], dtype=np.float32)
        raw_depth_min = np.array([[1.0, 2.0]], dtype=np.float32)
        raw_depth_max = np.array([[3.0, 5.0]], dtype=np.float32)

        rejected_mask = ortho_edge_support.build_mixed_depth_rejection_mask(
            raw_valid_mask,
            edge_assignment_mask,
            sample_count,
            raw_depth_min,
            raw_depth_max,
            max_depth_spread_m=0,
            min_samples=2,
        )

        self.assertFalse(rejected_mask.any())


if __name__ == "__main__":
    unittest.main()