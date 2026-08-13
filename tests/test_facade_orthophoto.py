from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from shared.facade_selection import exclude_basename_ranges, select_facade_images
from shared.quality_profiles import quality_profile
from shared.facade_process import (
    FACADE_PROCESS_OVERRIDES,
    FACADE_PROCESS_PROFILE_ID,
    apply_facade_process_profile,
    product_process_catalog,
)
from shared.validation import validate_pipeline_overrides


def test_facade_frame_recovers_plane_up_and_camera_side():
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "app1-colmap"))
    from gaussian_ortho.facade_frame import estimate_facade_frame

    rng = np.random.default_rng(7)
    x = rng.uniform(-12, 12, 2000)
    z = rng.uniform(-8, 8, 2000)
    y = rng.normal(0, 0.015, 2000)
    plane = np.column_stack((x, y, z))
    outliers = rng.uniform(-20, 20, (120, 3))
    points = np.vstack((plane, outliers))
    camera_rotation = np.column_stack(
        (np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, -1.0]), np.array([0.0, 1.0, 0.0]))
    )
    cameras = [
        SimpleNamespace(T=np.array([offset, 8.0, height]), R=camera_rotation)
        for offset, height in zip(np.linspace(-10, 10, 20), np.linspace(-6, 6, 20))
    ]

    frame = estimate_facade_frame(points, cameras)

    assert frame.inlier_ratio > 0.85
    assert frame.camera_side_ratio == 1.0
    assert abs(float(frame.world_to_facade[2] @ np.array([0.0, 1.0, 0.0]))) > 0.99
    assert float(frame.world_to_facade[1] @ np.array([0.0, 0.0, 1.0])) > 0.99
    np.testing.assert_allclose(
        frame.world_to_facade @ frame.world_to_facade.T,
        np.eye(3),
        atol=1e-6,
    )


def test_facade_selection_keeps_long_oblique_pass_and_deduplicates(tmp_path, monkeypatch):
    first = tmp_path / "pass_a"
    duplicate = tmp_path / "pass_b"
    detail = tmp_path / "details"
    first.mkdir()
    duplicate.mkdir()
    detail.mkdir()
    paths = []
    for sequence in range(1, 16):
        path = first / f"DJI_20250101120000_{sequence:04d}_D.JPG"
        path.write_bytes(f"image-{sequence}".encode())
        paths.append(path)
    duplicate_path = duplicate / paths[0].name
    duplicate_path.write_bytes(paths[0].read_bytes())
    paths.append(duplicate_path)
    for sequence in range(100, 104):
        path = detail / f"DJI_20250101130000_{sequence:04d}_D.JPG"
        path.write_bytes(f"detail-{sequence}".encode())
        paths.append(path)

    monkeypatch.setattr(
        "shared.facade_selection.parse_aerial_xmp",
        lambda path: {
            "gimbal_attitude_deg": {
                "pitch": 22.0 if "pass" in str(path) else 5.0,
                "yaw": 90.0,
            }
        },
    )
    selected, report = select_facade_images(paths, min_pass_images=10)

    assert len(selected) == 15
    assert report["selected_images"] == 15
    assert len(report["duplicate_basenames"]) == 1
    assert report["rejected_short_run_count"] == 4


def test_facade_selection_can_isolate_one_wall_by_circular_yaw(tmp_path, monkeypatch):
    paths = []
    yaw_by_name = {}
    for index, yaw in enumerate([350.0] * 12 + [90.0] * 12, start=1):
        path = tmp_path / f"DJI_20250101120000_{index:04d}_D.JPG"
        path.write_bytes(f"image-{index}".encode())
        paths.append(path)
        yaw_by_name[path.name] = yaw

    monkeypatch.setattr(
        "shared.facade_selection.parse_aerial_xmp",
        lambda path: {
            "gimbal_attitude_deg": {
                "pitch": 0.0,
                "yaw": yaw_by_name[path.name],
            }
        },
    )
    selected, report = select_facade_images(
        paths,
        min_pass_images=10,
        target_yaw_deg=-5.0,
        yaw_tolerance_deg=20.0,
    )

    assert len(selected) == 12
    assert all(yaw_by_name[path.name] == 350.0 for path in selected)
    assert report["rejected_yaw_count"] == 12


def test_facade_detail_range_exclusion_is_inclusive_and_audited():
    paths = [
        Path(f"DJI_2025032416{sequence:04d}_{sequence:04d}_V.JPG")
        for sequence in range(300, 661)
    ]
    start = paths[7].name
    end = paths[358].name

    selected, report = exclude_basename_ranges(paths, [(start, end)])

    assert len(paths) == 361
    assert len(selected) == 9
    assert report["excluded_image_count"] == 352
    assert report["excluded_basenames"][0] == start
    assert report["excluded_basenames"][-1] == end
    assert report["excluded_image_ranges"] == [
        {
            "start": start,
            "end": end,
            "excluded_images": 352,
            "first_excluded": start,
            "last_excluded": end,
        }
    ]


def test_sparse_distribution_similarity_recovers_common_camera_frame():
    from tools.compare_facade_sparse_distribution import robust_similarity

    rng = np.random.default_rng(19)
    source = rng.normal(size=(80, 3))
    angle = np.deg2rad(27.0)
    rotation = np.array(
        [
            [np.cos(angle), -np.sin(angle), 0.0],
            [np.sin(angle), np.cos(angle), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    target = (2.4 * (rotation @ source.T)).T + np.array([3.0, -5.0, 1.5])
    target[-1] += 100.0

    scale, fitted_rotation, translation, inliers, residuals = robust_similarity(
        source,
        target,
    )

    assert inliers.sum() == 79
    assert scale == pytest.approx(2.4)
    assert fitted_rotation == pytest.approx(rotation)
    assert translation == pytest.approx([3.0, -5.0, 1.5])
    assert np.max(residuals[inliers]) < 1e-10


def test_facade_raster_comparison_recovers_identity(tmp_path):
    import cv2

    from tools.compare_facade_rasters import compare

    rng = np.random.default_rng(23)
    raster = rng.integers(0, 256, size=(256, 256, 3), dtype=np.uint8)
    source = tmp_path / "source.png"
    reference = tmp_path / "reference.png"
    assert cv2.imwrite(str(source), raster)
    assert cv2.imwrite(str(reference), raster)

    metrics, preview = compare(source, reference)

    assert metrics["homography_inliers"] >= 8
    assert metrics["homography_residual_px"]["rmse"] < 0.1
    assert metrics["coverage_of_reference_content"] > 0.99
    assert preview.shape == (256, 520, 3)


def test_local_raster_metadata_does_not_invent_a_crs(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    from shared.geospatial_assets import raster_metadata

    raster = tmp_path / "facade.tif"
    with rasterio.open(
        raster,
        "w",
        driver="GTiff",
        width=16,
        height=8,
        count=3,
        dtype="uint8",
        crs=None,
        transform=from_origin(0, 8, 0.01, 0.01),
    ):
        pass
    with rasterio.open(raster) as dataset:
        metadata = raster_metadata(dataset)

    assert metadata["crs"] is None
    assert metadata["coordinate_space"] == "local"
    assert metadata["bounds"]["wgs84"] is None


def test_facade_report_supports_resident_partition_geometry(tmp_path):
    import json
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "app1-colmap"))
    from gaussian_ortho.raster_product import (
        GaussianSceneSummary,
        _write_facade_report,
    )

    report_path = tmp_path / "facade_frame.json"
    config = SimpleNamespace(
        facade_frame_report=str(report_path),
        ortho_file=str(tmp_path / "facade.tif"),
        facade_texture_max_incidence_deg=45.0,
        facade_depth_iqr_multiplier=1.0,
        resolution=0.01,
    )
    summary = GaussianSceneSummary(
        sim3_aligned=False,
        exif_altitude_available=True,
        colmap_to_meters=2.0,
        scale_source="relative-gps-baselines",
        facade_frame={"origin": [0.0, 0.0, 0.0]},
        registered_camera_count=20,
        texture_camera_count=18,
        texture_filter_applied=True,
        minimum_sparse_observations=3,
        seed_max_error=2.0,
        seed_min_track=2,
        gaussian_seed_point_count=1_000,
        facade_subset_result={
            "partitioned": True,
            "cell_count": 2,
            "cells": [
                {"exported_points": 600, "coverage_balanced": True},
                {"exported_points": 700, "coverage_balanced": False},
            ],
        },
    )
    geometry = SimpleNamespace(
        facade_depth_bounds_model=(-0.5, 0.75),
        resolution_units="metres",
    )
    filtering = SimpleNamespace(
        render_state=None,
        partition_geometry=geometry,
        partition_models=(
            SimpleNamespace(
                bounds=SimpleNamespace(row=0, col=0),
                facade_depth_bounds_model=(-0.5, 0.75),
            ),
            SimpleNamespace(
                bounds=SimpleNamespace(row=0, col=1),
                facade_depth_bounds_model=(-0.25, 1.0),
            ),
        ),
    )

    _write_facade_report(
        config,
        summary,
        filtering,
        width=200,
        height=100,
        geo_x_min=0.0,
        geo_y_max=1.0,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["gaussian_seed"] == {
        "maximum_reprojection_error_px": 2.0,
        "minimum_track_length": 2,
        "points_after_loader_filter": 1_000,
        "training_workspace_points": 1_300,
        "coverage_balanced_cap_applied": True,
        "resident_partitioned": True,
        "resident_cell_count": 2,
    }
    assert report["depth_filter"]["scope"] == "resident-cells"
    assert report["depth_filter"]["bounds_metres"] == [-1.0, 2.0]
    assert len(report["depth_filter"]["resident_cells"]) == 2


def test_facade_override_does_not_require_hidden_custom_crs():
    validated = validate_pipeline_overrides(
        {"orthophoto_mode": "facade", "projected_crs_mode": "custom"}
    )

    assert validated["orthophoto_mode"] == "facade"


def test_facade_process_uses_the_qualified_coverage_profile():
    params = {"orthophoto_mode": "facade", "minimum_registration_ratio": "0.97"}

    facade = apply_facade_process_profile(
        params,
        {"orthophoto_mode": "facade"},
    )

    assert facade is True
    assert params["gps_pair_max_neighbors"] == "48"
    assert params["gps_pair_min_neighbors"] == "16"
    assert params["gps_pair_temporal_neighbors"] == "6"
    assert params["mapping_timeout_seconds"] == "14400"
    assert params["facade_texture_max_incidence_deg"] == "45"
    assert params["facade_canary_min_psnr"] == "18"
    assert params["facade_canary_min_ssim"] == "0.25"
    assert params["gs_iterations"] == "30000"
    assert params["gs_max_width"] == "4096"
    assert params["gs_cap_max"] == "12000000"
    assert params["gs_capacity_mode"] == "adaptive"
    assert params["gs_capacity_floor"] == "5000000"
    assert params["gs_target_gaussian_spacing_pixels"] == "3.6"
    assert params["gs_resident_partitioning"] is True
    assert params["minimum_registration_ratio"] == "0.9"


def test_facade_process_preserves_quality_overrides_but_enforces_local_frame():
    params = {
        "orthophoto_mode": "facade",
        "feature_max_image_size": "3000",
        "matching_strategy": "gps_pairs",
        "rtk_refinement_enabled": True,
        "minimum_registration_ratio": "0.80",
    }

    apply_facade_process_profile(params, {"colmap_params": dict(params)})

    assert params["feature_max_image_size"] == "3000"
    assert params["matching_strategy"] == "spatial"
    assert params["rtk_refinement_enabled"] is False
    assert params["minimum_registration_ratio"] == "0.8"


@pytest.mark.parametrize(
    (
        "profile_id",
        "iterations",
        "capacity_floor",
        "spacing",
        "initial_scale_policy",
        "capacity_targeted_growth",
    ),
    [
        ("normal-v3", "15000", "3000000", "8.0", "local-knn", False),
        ("normal-v4", "15000", "3000000", "8.0", "projected-knn", True),
        ("high-quality-v3", "30000", "5000000", "3.6", "local-knn", False),
        ("high-quality-v4", "30000", "5000000", "3.6", "projected-knn", True),
    ],
)
def test_facade_process_preserves_selected_resident_quality_profile(
    profile_id,
    iterations,
    capacity_floor,
    spacing,
    initial_scale_policy,
    capacity_targeted_growth,
):
    profile = quality_profile(profile_id)
    quality_parameters = dict(profile.parameters)
    params = {
        "orthophoto_mode": "facade",
        **quality_parameters,
    }

    apply_facade_process_profile(
        params,
        {
            "orthophoto_mode": "facade",
            "colmap_params": quality_parameters,
        },
    )

    assert params["gs_production_profile"] == profile_id
    assert params["gs_iterations"] == iterations
    assert params["gs_capacity_floor"] == capacity_floor
    assert params["gs_target_gaussian_spacing_pixels"] == spacing
    assert params["gs_resident_partitioning"] is True
    assert params["gs_initial_scale_policy"] == initial_scale_policy
    assert params["gs_capacity_targeted_growth"] is capacity_targeted_growth
    assert params["matching_strategy"] == "spatial"
    assert params["gcp_adjustment_enabled"] is False


def test_dashboard_facade_process_reuses_the_backend_profile():
    processes = {process["id"]: process for process in product_process_catalog()}
    validated = validate_pipeline_overrides(
        dict(processes["facade"]["parameters"])
    )

    assert processes["map"]["stages"] == ["COLMAP", "TILER", "IA"]
    assert processes["facade"]["stages"] == ["COLMAP"]
    assert processes["facade"]["label"] == "Façade"
    assert processes["facade"]["profile_id"] == FACADE_PROCESS_PROFILE_ID
    assert FACADE_PROCESS_PROFILE_ID == "FACADE_HD_V2"
    assert validated == dict(FACADE_PROCESS_OVERRIDES)


def test_facade_scale_prefers_original_workspace_images(tmp_path, monkeypatch):
    import importlib
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "app1-colmap"))
    module = importlib.import_module(
        "gaussian_ortho.generate_gaussian_orthophoto"
    )

    workspace = tmp_path / "workspace"
    source_images = workspace / "images"
    dense_images = workspace / "dense" / "images"
    source_images.mkdir(parents=True)
    dense_images.mkdir(parents=True)
    calls = []

    def fake_scale(_cameras, images_dir):
        calls.append(images_dir)
        if images_dir == str(source_images):
            return 2.5, "relative-gps-baselines"
        return 1.0, "model-units"

    monkeypatch.setattr(module, "compute_colmap_scale_geodesic", fake_scale)
    scale, source, images_dir = module._compute_facade_gps_scale(
        [], str(workspace / "dense")
    )

    assert scale == 2.5
    assert source == "relative-gps-baselines"
    assert images_dir == str(source_images)
    assert calls == [str(source_images)]


def test_colmap_subset_removes_tracks_to_omitted_images(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "app1-colmap"))
    from gaussian_ortho.colmap_subset import (
        _read_colmap_images_bin,
        _read_colmap_points3d_bin,
        _write_colmap_cameras_bin,
        _write_colmap_images_bin,
        _write_colmap_points3d_bin,
        export_colmap_subset,
    )

    source = tmp_path / "source"
    source.mkdir()
    camera = {"model_id": 0, "width": 10, "height": 10, "params": [5.0, 5.0, 5.0]}
    image_base = {
        "qw": 1.0,
        "qx": 0.0,
        "qy": 0.0,
        "qz": 0.0,
        "tx": 0.0,
        "ty": 0.0,
        "tz": 0.0,
        "camera_id": 1,
        "xys": [(5.0, 5.0)],
        "point3D_ids": [7],
    }
    _write_colmap_cameras_bin({1: camera}, source / "cameras.bin")
    _write_colmap_images_bin(
        {
            1: {**image_base, "name": "keep.jpg"},
            2: {**image_base, "name": "omit.jpg"},
        },
        source / "images.bin",
    )
    _write_colmap_points3d_bin(
        {
            7: {
                "xyz": (0.0, 0.0, 0.0),
                "rgb": (1, 2, 3),
                "error": 0.1,
                "track": [(1, 0), (2, 0)],
            }
        },
        source / "points3D.bin",
    )

    target = tmp_path / "target"
    export_colmap_subset(str(source), str(target), ["keep.jpg"])
    points = _read_colmap_points3d_bin(target / "sparse" / "0" / "points3D.bin")

    assert points[7]["track"] == [(1, 0)]

    empty_target = tmp_path / "empty-target"
    export_colmap_subset(
        str(source),
        str(empty_target),
        ["keep.jpg"],
        point_ids=set(),
    )
    images = _read_colmap_images_bin(
        empty_target / "sparse" / "0" / "images.bin"
    )
    assert images[1]["point3D_ids"] == [-1]


def test_colmap_subset_can_apply_trainer_point_quality_gate(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "app1-colmap"))
    from gaussian_ortho.colmap_subset import (
        _read_colmap_images_bin,
        _read_colmap_points3d_bin,
        _write_colmap_cameras_bin,
        _write_colmap_images_bin,
        _write_colmap_points3d_bin,
        export_colmap_subset,
    )

    source = tmp_path / "source-quality"
    source.mkdir()
    _write_colmap_cameras_bin(
        {1: {"model_id": 0, "width": 10, "height": 10, "params": [5, 5, 5]}},
        source / "cameras.bin",
    )
    _write_colmap_images_bin(
        {
            1: {
                "qw": 1,
                "qx": 0,
                "qy": 0,
                "qz": 0,
                "tx": 0,
                "ty": 0,
                "tz": 0,
                "camera_id": 1,
                "name": "image-1.jpg",
                "xys": [(1, 1), (2, 2)],
                "point3D_ids": [7, 8],
            },
            2: {
                "qw": 1,
                "qx": 0,
                "qy": 0,
                "qz": 0,
                "tx": 1,
                "ty": 0,
                "tz": 0,
                "camera_id": 1,
                "name": "image-2.jpg",
                "xys": [(1, 1), (2, 2)],
                "point3D_ids": [7, 8],
            },
            3: {
                "qw": 1,
                "qx": 0,
                "qy": 0,
                "qz": 0,
                "tx": 2,
                "ty": 0,
                "tz": 0,
                "camera_id": 1,
                "name": "image-3.jpg",
                "xys": [(1, 1), (2, 2)],
                "point3D_ids": [7, 8],
            },
        },
        source / "images.bin",
    )
    common = {"xyz": (0, 0, 0), "rgb": (1, 2, 3)}
    _write_colmap_points3d_bin(
        {
            7: {**common, "error": 0.5, "track": [(1, 0), (2, 0), (3, 0)]},
            8: {**common, "error": 1.5, "track": [(1, 1), (2, 1), (3, 1)]},
        },
        source / "points3D.bin",
    )

    target = tmp_path / "quality-target"
    export_colmap_subset(
        str(source),
        str(target),
        ["image-1.jpg", "image-2.jpg", "image-3.jpg"],
        max_point_error=1.0,
        min_track_length=3,
    )
    points = _read_colmap_points3d_bin(target / "sparse" / "0" / "points3D.bin")
    images = _read_colmap_images_bin(target / "sparse" / "0" / "images.bin")

    assert set(points) == {7}
    assert images[1]["point3D_ids"] == [7, -1]


def test_coverage_balanced_seed_cap_preserves_facade_extent():
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "app1-colmap"))
    from gaussian_ortho.colmap_subset import _coverage_balanced_point_ids

    # Simulate a wall where the centre is much denser than the borders. A
    # quality-only cap would select the central points because they have the
    # longest tracks; the coverage pass must still retain both wall edges.
    points = {}
    point_id = 1
    for x in np.linspace(-10.0, 10.0, 81):
        for z in np.linspace(-5.0, 5.0, 41):
            central = abs(x) < 3.0 and abs(z) < 2.0
            points[point_id] = {
                "xyz": (x, 0.0, z),
                "rgb": (1, 2, 3),
                "error": 0.2 if central else 0.8,
                "track": [(index, 0) for index in range(8 if central else 2)],
            }
            point_id += 1

    retained = _coverage_balanced_point_ids(points, maximum=400)
    retained_xyz = np.asarray([points[point_id]["xyz"] for point_id in retained])

    assert len(retained) == 400
    assert retained_xyz[:, 0].min() <= -9.5
    assert retained_xyz[:, 0].max() >= 9.5
    assert retained_xyz[:, 2].min() <= -4.5
    assert retained_xyz[:, 2].max() >= 4.5
    assert np.count_nonzero(np.abs(retained_xyz[:, 0]) >= 8.0) >= 20
