from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest


APP1_ROOT = Path(__file__).resolve().parents[1] / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

workflow = importlib.import_module("gaussian_ortho.generate_gaussian_orthophoto")
raster_product = importlib.import_module("gaussian_ortho.raster_product")
seam_quality = importlib.import_module("gaussian_ortho.seam_quality")


def test_resident_training_parallelizes_only_image_cache_io():
    assert workflow.resident_image_cache_tuning(True) == (4, 4)
    assert workflow.resident_image_cache_tuning(False) == (1, 1)


def test_training_phase_exposes_backend_identity_and_explicit_state(monkeypatch):
    scene_state = SimpleNamespace(
        name="scene",
        point_cloud=SimpleNamespace(
            points=np.array(
                [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
                dtype=np.float32,
            )
        ),
        colmap_to_meters=1.0,
        cells=[(None, SimpleNamespace())],
    )
    training_state = SimpleNamespace(final_ply="final.ply")
    calls = {}
    backend = SimpleNamespace(
        name="dronegs",
        binary_sha256=lambda: "a" * 64,
    )
    monkeypatch.setattr(
        workflow,
        "prepare_gaussian_scene",
        lambda config: scene_state,
    )

    def train(config, scene, **kwargs):
        calls.update(kwargs)
        assert scene is scene_state
        return training_state

    monkeypatch.setattr(workflow, "train_and_merge_gaussian_models", train)
    monkeypatch.setattr(
        workflow,
        "replace",
        lambda config, **changes: SimpleNamespace(**(vars(config) | changes)),
    )

    result = workflow.execute_gaussian_training_phase(
        SimpleNamespace(
            capacity_mode="fixed",
            cap_max=1_500_000,
            capacity_floor=1_500_000,
            target_gaussian_spacing_pixels=0.0,
            resolution=0.02,
        ),
        backend=backend,
        model_class=lambda: None,
        merge_models_fn=lambda: None,
        cupy_module=SimpleNamespace(),
    )

    assert result.scene_state is scene_state
    assert result.training_state is training_state
    assert result.backend_name == "dronegs"
    assert result.trainer_binary_sha256 == "a" * 64
    assert calls["trainer_binary_sha256"] == "a" * 64


def test_training_phase_expands_adaptive_hq_to_resident_cells(monkeypatch):
    x, y = np.meshgrid(
        np.linspace(0.0, 500.0, 40),
        np.linspace(0.0, 418.8, 40),
    )
    points = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
    scene_state = SimpleNamespace(
        point_cloud=SimpleNamespace(points=points),
        colmap_to_meters=1.0,
        cells=[(None, SimpleNamespace())],
        use_partition=False,
    )
    backend = SimpleNamespace(
        name="dronegs",
        binary_sha256=lambda: "b" * 64,
    )
    trained_caps = []
    monkeypatch.setattr(
        workflow,
        "prepare_gaussian_scene",
        lambda _config: scene_state,
    )

    def apply_partition(scene, _config, *, required_cell_count):
        assert required_cell_count == 7
        scene.cells = [(SimpleNamespace(), SimpleNamespace())] * 9
        scene.use_partition = True

    monkeypatch.setattr(
        workflow,
        "_apply_required_resident_partition",
        apply_partition,
    )
    monkeypatch.setattr(
        workflow,
        "replace",
        lambda config, **changes: SimpleNamespace(**(vars(config) | changes)),
    )

    def train(config, _scene, **_kwargs):
        trained_caps.append(config.cap_max)
        return SimpleNamespace(final_ply="final.ply")

    monkeypatch.setattr(workflow, "train_and_merge_gaussian_models", train)
    config = SimpleNamespace(
        capacity_mode="adaptive",
        cap_max=12_000_000,
        capacity_floor=5_000_000,
        target_gaussian_spacing_pixels=3.6,
        resolution=0.02,
        partition_overlap=0.2,
        partition_m=1,
        partition_n=1,
        resident_partitioning=True,
        vol_id="mission",
        report_fn=None,
    )

    result = workflow.execute_gaussian_training_phase(
        config,
        backend=backend,
        model_class=lambda: None,
        merge_models_fn=lambda: None,
        cupy_module=SimpleNamespace(),
    )

    assert result.capacity_plan is not None
    assert result.capacity_plan.effective_scene_cap == pytest.approx(
        40_400_000,
        rel=0.03,
    )
    assert result.capacity_plan.cell_count == 9
    assert result.capacity_plan.resident_cap == 12_000_000
    assert result.capacity_plan.cells_sufficient
    assert trained_caps == [result.capacity_plan.effective_cell_cap]
    assert trained_caps[0] < result.capacity_plan.resident_cap


def test_filtering_phase_applies_render_preparation_once_and_records_counts(
    monkeypatch,
):
    training_model = SimpleNamespace(num_gaussians=100)
    filtered_model = SimpleNamespace(num_gaussians=72)
    training_phase = workflow.GaussianTrainingPhaseState(
        scene_state=SimpleNamespace(name="scene"),
        training_state=workflow.GaussianTrainingState(
            merged_model=training_model,
            final_ply="final.ply",
            facade_subset_result=None,
        ),
        backend_name="dronegs",
        trainer_binary_sha256="a" * 64,
    )
    calls = []

    def prepare(config, scene, training, *, cupy_module):
        calls.append((config, scene, training, cupy_module))
        return SimpleNamespace(merged_model=filtered_model)

    monkeypatch.setattr(workflow, "prepare_gaussian_render_state", prepare)
    config = SimpleNamespace(
        name="config",
        render_mode="map",
        capacity_mode="fixed",
    )
    cupy = SimpleNamespace(name="cupy")

    result = workflow.execute_gaussian_filtering_phase(
        config,
        training_phase,
        cupy_module=cupy,
    )

    assert len(calls) == 1
    assert calls[0] == (
        config,
        training_phase.scene_state,
        training_phase.training_state,
        cupy,
    )
    assert result.render_state.merged_model is filtered_model
    assert result.input_gaussians == 100
    assert result.output_gaussians == 72


def test_rasterization_phase_consumes_only_filtered_render_state():
    model = SimpleNamespace(name="filtered")
    render_state = SimpleNamespace(
        merged_model=model,
        local_gsd=0.025,
        render_extent=(-1.0, 1.0, -2.0, 2.0, 0.0, 3.0),
        rotation_geo=np.eye(3),
        frame_origin=None,
        sh_direction_rotation=np.eye(3),
        resolution_units="metres",
    )
    filtering_phase = workflow.GaussianFilteringPhaseState(
        render_state=render_state,
        input_gaussians=100,
        output_gaussians=72,
    )
    calls = []

    def render(filtered_model, **kwargs):
        calls.append((filtered_model, kwargs))
        return {
            "rgb": np.zeros((12, 20, 3), dtype=np.uint8),
            "height": np.zeros((12, 20), dtype=np.float32),
            "extent": (-1.0, 1.0, -2.0, 2.0),
        }

    result = workflow.execute_gaussian_rasterization_phase(
        SimpleNamespace(
            vol_id="mission",
            resolution=0.02,
            render_mode="map",
            capacity_mode="fixed",
            report_fn=None,
            ortho_mip_filter_variance=0.03,
            ortho_mip_filter_compensation=True,
        ),
        filtering_phase,
        render_fn=render,
    )

    assert len(calls) == 1
    assert calls[0][0] is model
    assert calls[0][1]["gsd"] == 0.025
    assert calls[0][1]["extent"] == render_state.render_extent
    assert result.width == 20
    assert result.height == 12


def test_partitioned_rasterization_feathers_overlapping_buffer_models(
    tmp_path,
):
    left_path = tmp_path / "left.ply"
    right_path = tmp_path / "right.ply"
    left_path.write_bytes(b"left")
    right_path.write_bytes(b"right")
    left_bounds = workflow.CellBounds(
        0.0, 10.0, 0.0, 10.0,
        -2.0, 12.0, -2.0, 12.0,
        0, 0,
    )
    right_bounds = workflow.CellBounds(
        10.0, 20.0, 0.0, 10.0,
        8.0, 22.0, -2.0, 12.0,
        0, 1,
        include_core_x_max=True,
        include_core_y_max=True,
    )
    geometry = workflow.GaussianRenderGeometry(
        geo_origin=np.zeros(3),
        frame_origin=None,
        rotation_geo=None,
        sh_direction_rotation=None,
        facade_depth_bounds_model=None,
        render_extent=(0.0, 20.0, 0.0, 10.0, -1.0, 2.0),
        local_gsd=1.0,
        resolution_units="metres",
        coverage_camera_positions=np.empty((0, 3)),
    )
    partitions = tuple(
        workflow.GaussianFilteredPartition(
            bounds=bounds,
            model_path=str(path),
            gaussian_count=8,
            core_gaussian_count=5,
            render_extent=geometry.render_extent,
        )
        for bounds, path in ((left_bounds, left_path), (right_bounds, right_path))
    )
    filtering = workflow.GaussianFilteringPhaseState(
        render_state=None,
        input_gaussians=10,
        output_gaussians=10,
        partition_geometry=geometry,
        partition_models=partitions,
    )

    class FakeModel:
        def __init__(self, **_kwargs):
            self.path = ""
            self.active_sh_degree = 0

        def load_ply(self, path):
            self.path = path

    def render(model, *, extent, gsd, pixel_dimensions, **_kwargs):
        assert model.active_sh_degree == 3
        width, height = pixel_dimensions
        value = 40 if model.path.endswith("left.ply") else 180
        return {
            "rgb": np.full((height, width, 3), value, dtype=np.uint8),
            "height": np.full((height, width), value, dtype=np.float32),
            "extent": extent[:4],
            "gsd": gsd,
        }

    pool = SimpleNamespace(free_all_blocks=lambda: None)
    config = SimpleNamespace(
        render_mode="map",
        resolution=1.0,
        capacity_mode="fixed",
        sh_degree=3,
        opacity_sh_enabled=True,
        ortho_mip_filter_variance=0.03,
        ortho_mip_filter_compensation=True,
        vol_id="mission",
        report_fn=None,
    )
    result = workflow.execute_partitioned_gaussian_rasterization_phase(
        config,
        filtering,
        model_class=FakeModel,
        render_fn=render,
        cupy_module=SimpleNamespace(
            get_default_memory_pool=lambda: pool,
        ),
    )

    assert result.result["rgb"].shape == (10, 20, 3)
    assert result.result["rgb"][0, :, 0].tolist() == (
        [40] * 8 + [68, 100, 120, 152] + [180] * 8
    )
    assert np.isfinite(result.result["height"]).all()

    seam_report = seam_quality.evaluate_core_seams(
        result.result["rgb"],
        result.result["height"],
        extent=result.result["extent"],
        gsd=1.0,
        geo_origin=np.zeros(3),
        partitions=partitions,
    )
    assert seam_report["seam_count"] == 1
    seam = seam_report["seams"][0]
    assert seam["orientation"] == "vertical"
    assert seam["rgb_absolute_8bit"]["mean"] == pytest.approx(20.0)
    assert seam["height_absolute"]["p95"] == pytest.approx(20.0)


def test_rasterization_rejects_gsd_unsupported_by_achieved_density():
    density = workflow.GaussianDensityAssessment(
        robust_ground_area_m2=250_000.0,
        requested_gsd_m=0.015,
        target_spacing_pixels=8.0,
        actual_gaussian_count=10_000_000,
        required_gaussian_count=17_361_112,
        achieved_spacing_m=0.1581,
        achieved_spacing_pixels=10.54,
        minimum_compatible_gsd_m=0.01976,
        accepted=False,
    )
    filtering_phase = workflow.GaussianFilteringPhaseState(
        render_state=SimpleNamespace(),
        input_gaussians=12_000_000,
        output_gaussians=10_000_000,
        density_assessment=density,
    )

    with pytest.raises(RuntimeError, match="Use at least 0.0198 m/px"):
        workflow.execute_gaussian_rasterization_phase(
            SimpleNamespace(
                render_mode="map",
                capacity_mode="adaptive",
            ),
            filtering_phase,
            render_fn=lambda *_args, **_kwargs: {},
        )


def test_raster_product_applies_shared_coverage_and_geotiff_contract(
    monkeypatch,
    tmp_path,
):
    coverage = {
        "accepted": True,
        "status": "accepted",
        "valid_pixel_ratio": 0.92,
        "covered_cells_ratio": 0.88,
        "worst_cell_ratio": 0.45,
        "checks": [{"name": "valid_ratio", "passed": True}],
    }
    monkeypatch.setattr(
        raster_product,
        "evaluate_spatial_coverage",
        lambda *_args, **_kwargs: coverage,
    )
    monkeypatch.setattr(
        raster_product,
        "georeference_raster_origin",
        lambda *_args, **_kwargs: (600_000.0, 4_900_000.0),
    )
    height_after_reference = np.full((2, 3), 125.0, dtype=np.float32)
    monkeypatch.setattr(
        raster_product,
        "georeference_height_map",
        lambda *_args, **_kwargs: (height_after_reference, 120.0, "sim3"),
    )
    writes = []

    def write_geotiff(**kwargs):
        writes.append(kwargs)

    monkeypatch.setattr(raster_product, "write_geotiff", write_geotiff)
    ortho_file = tmp_path / "orthomosaic.tif"
    config = SimpleNamespace(
        render_mode="map",
        coverage_grid_size=16,
        coverage_min_valid_ratio=0.5,
        coverage_cell_threshold=0.25,
        coverage_min_covered_cells_ratio=0.75,
        coverage_min_worst_cell_ratio=0.01,
        coverage_min_camera_cell_ratio=0.1,
        coverage_gate_enabled=True,
        ortho_file=str(ortho_file),
        vol_id="mission",
        report_fn=None,
        resolution=0.025,
        utm_crs="EPSG:32631",
        checkpoint_dir=str(tmp_path / "checkpoints"),
        facade_frame_report=None,
        facade_texture_max_incidence_deg=45.0,
        facade_depth_iqr_multiplier=1.0,
        ortho_mip_filter_variance=0.03,
        ortho_mip_filter_compensation=True,
    )
    filtering = SimpleNamespace(
        output_gaussians=1_200_000,
        density_assessment=None,
        partition_models=(),
        render_state=SimpleNamespace(
            coverage_camera_positions=np.array([[0.0, 0.0, 10.0]]),
            geo_origin=np.array([600_000.0, 4_900_000.0, 120.0]),
            resolution_units="metres",
            facade_depth_bounds_model=None,
        ),
    )
    rasterization = workflow.GaussianRasterizationPhaseState(
        result={
            "rgb": np.zeros((2, 3, 3), dtype=np.uint8),
            "height": np.zeros((2, 3), dtype=np.float32),
            "extent": (-1.0, 2.0, -2.0, 3.0),
        },
        width=3,
        height=2,
    )
    summary = raster_product.GaussianSceneSummary(
        sim3_aligned=True,
        exif_altitude_available=False,
        colmap_to_meters=1.0,
        scale_source="geographic-sim3",
        facade_frame=None,
        registered_camera_count=40,
        texture_camera_count=40,
        texture_filter_applied=False,
        minimum_sparse_observations=20,
        seed_max_error=1.0,
        seed_min_track=3,
        gaussian_seed_point_count=25_000,
        facade_subset_result=None,
    )

    result = raster_product.finalize_gaussian_raster_product(
        config,
        filtering,
        rasterization,
        summary,
        final_ply=str(tmp_path / "filtered.ply"),
        cupy_version="13.0-test",
    )

    assert len(writes) == 1
    assert writes[0]["x_min"] == 600_000.0
    assert writes[0]["y_max"] == 4_900_000.0
    assert writes[0]["crs"] == "EPSG:32631"
    np.testing.assert_array_equal(writes[0]["height_map"], height_after_reference)
    assert result["vertical_reference"] == "sim3"
    assert result["n_gaussians"] == 1_200_000
    assert result["gaussian_coverage"] is coverage
    assert result["renderer_contract"] == "cupy-ortho-v3-surface-color"
    assert result["resident_compositing"] is None
    assert Path(result["gaussian_coverage_report"]).is_file()


def test_prepare_gaussian_scene_keeps_loading_and_scale_contracts(
    monkeypatch,
    tmp_path,
) -> None:
    cameras = [
        SimpleNamespace(
            image_name=f"DJI_{index:04d}.JPG",
            T=np.array([float(index), 0.0, 10.0]),
            R=np.eye(3),
        )
        for index in range(3)
    ]
    point_cloud = SimpleNamespace(points=np.ones((12, 3), dtype=np.float32))
    scene = SimpleNamespace(point_cloud=point_cloud)
    monkeypatch.setattr(
        workflow,
        "load_colmap_reconstruction",
        lambda *_args, **_kwargs: (
            cameras,
            [],
            point_cloud,
            {"scale": 2.5},
        ),
    )
    monkeypatch.setattr(
        workflow,
        "extract_exif_altitudes",
        lambda _path: {camera.image_name: 100.0 for camera in cameras},
    )
    monkeypatch.setattr(
        workflow,
        "build_scene_info",
        lambda *_args, **_kwargs: scene,
    )
    reports = []
    config = SimpleNamespace(
        facade_seed_max_reprojection_error=2.0,
        facade_seed_min_track_length=2,
        render_mode="map",
        vol_id="mission",
        report_fn=lambda *_args, **kwargs: reports.append(kwargs["log"]),
        dense_path=str(tmp_path),
        transform_file=None,
        utm_crs="EPSG:32631",
        facade_scale_mode="gps-baseline",
        facade_meters_per_model_unit=1.0,
        facade_texture_max_incidence_deg=65.0,
        partition_m=1,
        partition_n=1,
        partition_overlap=0.2,
    )

    state = workflow.prepare_gaussian_scene(config)

    assert state.transform_data == {"scale": 2.5}
    assert state.colmap_to_meters == 2.5
    assert state.mean_exif_alt == 100.0
    assert state.cells == [(None, scene)]
    assert any("Loaded 3 cameras" in report for report in reports)


def test_prepare_facade_scene_rejects_implicit_model_unit_scale(
    monkeypatch,
    tmp_path,
) -> None:
    cameras = [
        SimpleNamespace(
            image_name=f"flight/DJI_{index:04d}.JPG",
            T=np.array([float(index), 0.0, 10.0]),
            R=np.eye(3),
        )
        for index in range(12)
    ]
    point_cloud = SimpleNamespace(points=np.ones((12, 3), dtype=np.float32))
    monkeypatch.setattr(
        workflow,
        "load_colmap_reconstruction",
        lambda *_args, **_kwargs: (cameras, [], point_cloud, None),
    )
    monkeypatch.setattr(workflow, "extract_exif_altitudes", lambda _path: {})
    monkeypatch.setattr(
        workflow,
        "_compute_facade_gps_scale",
        lambda *_args: (1.0, "model-units", str(tmp_path / "images")),
    )
    config = SimpleNamespace(
        facade_seed_max_reprojection_error=2.0,
        facade_seed_min_track_length=2,
        render_mode="facade",
        vol_id="mission",
        report_fn=None,
        dense_path=str(tmp_path / "dense"),
        transform_file=None,
        utm_crs=None,
        facade_scale_mode="gps-baseline",
        facade_meters_per_model_unit=1.0,
    )

    with pytest.raises(RuntimeError, match="requires metadata"):
        workflow.prepare_gaussian_scene(config)


def test_prepare_gaussian_render_state_preserves_facade_frame_and_extent(
    monkeypatch,
    tmp_path,
) -> None:
    extent = (-2.0, 2.0, -3.0, 3.0, -1.0, 1.0)
    ortho_renderer = ModuleType("gaussian_ortho.ortho_renderer")
    ortho_renderer.compute_ortho_extent = lambda *_args, **_kwargs: extent
    monkeypatch.setitem(
        sys.modules,
        "gaussian_ortho.ortho_renderer",
        ortho_renderer,
    )

    saved_paths = []
    model = SimpleNamespace(
        num_gaussians=42,
        save_ply=lambda path: saved_paths.append(path),
    )
    frame = SimpleNamespace(
        world_to_facade=np.eye(3),
        origin=np.array([1.0, 2.0, 3.0]),
        inlier_ratio=0.95,
        plane_rmse=0.01,
    )
    scene_state = workflow.GaussianSceneState(
        train_cameras=[],
        test_cameras=[],
        registered_cameras=[SimpleNamespace(T=np.array([0.0, 0.0, 5.0]))],
        point_cloud=SimpleNamespace(points=np.zeros((3, 3))),
        transform_data=None,
        mean_exif_alt=None,
        colmap_to_meters=0.5,
        scale_source="gps-baseline:images",
        facade_frame=frame,
        texture_camera_count=1,
        texture_filter_applied=False,
        minimum_sparse_observations=20,
        seed_max_error=2.0,
        seed_min_track=2,
        gaussian_seed_point_count=3,
        images_dir=str(tmp_path / "images"),
        scene=SimpleNamespace(),
        cells=[],
        use_partition=False,
    )
    config = SimpleNamespace(
        render_mode="facade",
        vol_id="mission",
        report_fn=lambda *_args, **_kwargs: None,
        filter_enabled=False,
        filter_max_scale=1.0,
        filter_min_retained_ratio=0.80,
        filter_dist_multiplier=1.0,
        filter_opacity_threshold=0.005,
        filter_needle_ratio=0.0,
        filter_sor_sigma=4.0,
        filter_sor=False,
        filter_cc=False,
        filter_z_floater=False,
        facade_depth_iqr_multiplier=0.0,
        resolution=0.02,
        utm_crs=None,
    )
    memory_pool = SimpleNamespace(free_all_blocks=lambda: None)
    fake_cupy = SimpleNamespace(
        get_default_memory_pool=lambda: memory_pool,
    )
    training_state = workflow.GaussianTrainingState(
        merged_model=model,
        final_ply=str(tmp_path / "final.ply"),
        facade_subset_result=None,
    )

    state = workflow.prepare_gaussian_render_state(
        config,
        scene_state,
        training_state,
        cupy_module=fake_cupy,
    )

    assert state.render_extent == extent
    assert state.local_gsd == 0.04
    assert state.resolution_units == "metres"
    np.testing.assert_allclose(state.frame_origin, [1.0, 2.0, 3.0])
    assert saved_paths == [str(tmp_path / "final.ply")]
