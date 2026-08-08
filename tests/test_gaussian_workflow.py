from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np


APP1_ROOT = Path(__file__).resolve().parents[1] / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

workflow = importlib.import_module("gaussian_ortho.generate_gaussian_orthophoto")


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
