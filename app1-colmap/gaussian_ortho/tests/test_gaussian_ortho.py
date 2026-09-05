"""
Tests for the gaussian_ortho package (CuPy backend).

Unit tests cover math utilities, model serialisation, partitioning,
and the ortho rendering pipeline.
"""
import os
import sys
import tempfile

import numpy as np
import pytest

try:
    import cupy as cp
except ImportError:
    cp = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gaussian_ortho.colmap_loader import (
    CameraInfo, PointCloud, apply_sim3_to_points, apply_sim3_to_camera,
)
from gaussian_ortho.scene_info import build_scene_info
from gaussian_ortho.partition import (
    CellBounds,
    compute_partition_grid,
    ground_projection_from_sim3,
    partition_scene,
)
from gaussian_ortho.geo_writer import _geotiff_creation_options, write_geotiff
from gaussian_ortho.camera_footprint import geographic_scene_frame
from gaussian_ortho.generate_gaussian_orthophoto import (
    DRONEGS_CHECKPOINT_MAGIC,
    _resume_checkpoint_path,
)

if cp is not None:
    from gaussian_ortho.gaussian_model import GaussianModel, num_sh_coefficients, SH_C0
    from gaussian_ortho.cuda_rasterizer import (
        eval_sh_basis,
        eval_sh,
        project_covariance_2d,
        quat_to_rotmat,
        rasterize_ortho,
    )
    from gaussian_ortho.rasterizer import make_view_matrix, make_ortho_proj
    from gaussian_ortho.ortho_renderer import compute_ortho_extent, render_orthophoto
    from gaussian_ortho.merge import merge_models


requires_gpu = pytest.mark.skipif(cp is None, reason="CuPy is not installed")


def test_resume_checkpoint_prefers_valid_primary(tmp_path):
    output = tmp_path / "cell_0"
    output.mkdir()
    primary = output / "training.ckpt"
    temporary = output / "training.ckpt.tmp"
    primary.write_bytes(DRONEGS_CHECKPOINT_MAGIC + b"primary")
    temporary.write_bytes(DRONEGS_CHECKPOINT_MAGIC + b"temporary")

    assert _resume_checkpoint_path(output) == str(primary)


def test_resume_checkpoint_falls_back_to_valid_temporary(tmp_path):
    output = tmp_path / "cell_3"
    output.mkdir()
    primary = output / "training.ckpt"
    temporary = output / "training.ckpt.tmp"
    primary.write_bytes(b"broken")
    temporary.write_bytes(DRONEGS_CHECKPOINT_MAGIC + b"complete")

    assert _resume_checkpoint_path(output) == str(temporary)


def test_resume_checkpoint_never_hides_invalid_recovery_state(tmp_path):
    output = tmp_path / "cell_3"
    output.mkdir()
    temporary = output / "training.ckpt.tmp"
    temporary.write_bytes(b"incomplete")

    assert _resume_checkpoint_path(output) == str(temporary)
    (output / "trainer_run.json").write_text("{}", encoding="utf-8")
    assert _resume_checkpoint_path(output) is None


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------

def _make_random_point_cloud(n=500, seed=42):
    rng = np.random.RandomState(seed)
    points = rng.randn(n, 3).astype(np.float32) * 10
    colors = rng.rand(n, 3).astype(np.float32)
    normals = np.zeros_like(points)
    return PointCloud(points=points, colors=colors, normals=normals)


def _make_cameras(n=10, seed=42):
    rng = np.random.RandomState(seed)
    cams = []
    for i in range(n):
        R = np.eye(3, dtype=np.float32)
        T = rng.randn(3).astype(np.float32) * 5
        cams.append(CameraInfo(
            uid=i, image_name=f"img_{i:04d}.jpg",
            width=640, height=480,
            fx=500.0, fy=500.0, cx=320.0, cy=240.0,
            R=R, T=T,
        ))
    return cams


def _make_scene(n_cams=10, n_points=500):
    cams = _make_cameras(n_cams)
    pc = _make_random_point_cloud(n_points)
    return build_scene_info(cams, [], pc)


def _make_gpu_model(n=200, sh_degree=0, seed=42):
    """Create a GaussianModel on GPU with random data (no training)."""
    rng = np.random.RandomState(seed)
    model = GaussianModel(sh_degree=sh_degree, opacity_sh_enabled=False)
    model._xyz = cp.array(rng.randn(n, 3).astype(np.float32) * 5)
    n_sh = num_sh_coefficients(sh_degree)
    model._features_dc = cp.array(rng.randn(n, 1, 3).astype(np.float32) * 0.1)
    model._features_rest = cp.zeros((n, max(n_sh - 1, 0), 3), dtype=cp.float32)
    model._scaling = cp.array(rng.randn(n, 3).astype(np.float32) * 0.5 - 3.0)
    rots = rng.randn(n, 4).astype(np.float32)
    rots /= np.linalg.norm(rots, axis=-1, keepdims=True)
    model._rotation = cp.array(rots)
    model._opacity = cp.array(rng.randn(n, 1).astype(np.float32))
    model._opacity_sh = cp.empty((n, 0), dtype=cp.float32)
    model.active_sh_degree = sh_degree
    return model


# ---------------------------------------------------------------------------
#  Spherical harmonics
# ---------------------------------------------------------------------------

@pytest.mark.gpu
@requires_gpu
class TestSH:
    def test_num_coefficients(self):
        assert num_sh_coefficients(0) == 1
        assert num_sh_coefficients(1) == 4
        assert num_sh_coefficients(2) == 9
        assert num_sh_coefficients(3) == 16

    def test_eval_basis_shape(self):
        dirs_np = np.random.randn(100, 3).astype(np.float32)
        dirs_np /= np.linalg.norm(dirs_np, axis=-1, keepdims=True)
        dirs = cp.array(dirs_np)
        for deg in range(4):
            basis = eval_sh_basis(deg, dirs)
            assert basis.shape == (100, num_sh_coefficients(deg))

    def test_dc_constant(self):
        dirs_np = np.random.randn(50, 3).astype(np.float32)
        dirs_np /= np.linalg.norm(dirs_np, axis=-1, keepdims=True)
        dirs = cp.array(dirs_np)
        basis = eval_sh_basis(0, dirs)
        assert cp.allclose(basis[:, 0], SH_C0)
        expected = cp.full_like(basis, SH_C0)
        assert cp.allclose(basis, expected, atol=1e-6)

    def test_basis_uses_native_dronegs_sign_convention(self):
        directions = cp.array(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=cp.float32
        )
        basis = eval_sh_basis(1, directions)
        assert float(basis[0, 3]) == pytest.approx(-0.4886025119)
        assert float(basis[1, 1]) == pytest.approx(-0.4886025119)

# ---------------------------------------------------------------------------
#  Gaussian Model
# ---------------------------------------------------------------------------

@pytest.mark.gpu
@requires_gpu
class TestGaussianModel:
    def test_properties(self):
        model = _make_gpu_model(50)
        assert model.num_gaussians == 50
        assert model.positions.shape == (50, 3)
        assert model.scales.shape == (50, 3)
        assert model.rotations.shape == (50, 4)
        assert model.opacity.shape == (50, 1)
        # Check ranges
        opa = model.opacity
        assert float(opa.min()) >= 0 and float(opa.max()) <= 1
        norms = cp.linalg.norm(model.rotations, axis=-1)
        assert cp.allclose(norms, cp.ones_like(norms), atol=1e-5)

    def test_filter_by_mask(self):
        model = _make_gpu_model(100)
        mask = model._xyz[:, 0] > 0
        n_keep = int(mask.sum())
        model.filter_by_mask(mask)
        assert model.num_gaussians == n_keep

    def test_save_load_ply(self):
        model = _make_gpu_model(30, sh_degree=2)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.ply")
            model.save_ply(path)
            model2 = GaussianModel(sh_degree=2, opacity_sh_enabled=False)
            model2.load_ply(path)
            assert model2.num_gaussians == 30
            np.testing.assert_allclose(
                cp.asnumpy(model._xyz), cp.asnumpy(model2._xyz), atol=1e-5)

    def test_quaternion_multiply(self):
        q1 = cp.array([[1, 0, 0, 0]], dtype=cp.float32)  # identity
        q2 = cp.array([[0, 1, 0, 0]], dtype=cp.float32)  # 180° around x
        result = GaussianModel._quaternion_multiply(q1, q2)
        assert cp.allclose(result, q2, atol=1e-6)


# ---------------------------------------------------------------------------
#  Sim3 transforms
# ---------------------------------------------------------------------------

class TestSim3:
    def test_identity_transform(self):
        transform = {"R": np.eye(3).tolist(), "scale": 1.0, "t": [0, 0, 0]}
        pts = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.float32)
        result = apply_sim3_to_points(pts, transform)
        np.testing.assert_allclose(result, pts, atol=1e-5)

    def test_scale_transform(self):
        transform = {"R": np.eye(3).tolist(), "scale": 2.0, "t": [0, 0, 0]}
        pts = np.array([[1, 0, 0]], dtype=np.float32)
        result = apply_sim3_to_points(pts, transform)
        np.testing.assert_allclose(result, [[2, 0, 0]], atol=1e-5)

    def test_camera_transform(self):
        cam = CameraInfo(uid=0, image_name="test.jpg", width=640, height=480,
                         fx=500, fy=500, cx=320, cy=240,
                         R=np.eye(3, dtype=np.float32),
                         T=np.array([1, 2, 3], dtype=np.float32))
        transform = {"R": np.eye(3).tolist(), "scale": 2.0, "t": [10, 0, 0]}
        result = apply_sim3_to_camera(cam, transform)
        np.testing.assert_allclose(result.T, [12, 4, 6], atol=1e-5)


# ---------------------------------------------------------------------------
#  Matrices
# ---------------------------------------------------------------------------

@pytest.mark.gpu
@requires_gpu
class TestMatrices:
    def test_direct_covariance_projection_matches_full_matrix_path(self):
        rng = np.random.default_rng(31)
        quaternions = rng.normal(size=(128, 4)).astype(np.float32)
        quaternions /= np.linalg.norm(quaternions, axis=1, keepdims=True)
        rotations = quat_to_rotmat(cp.asarray(quaternions))
        scales = cp.asarray(
            np.exp(rng.normal(-2.0, 0.4, size=(128, 3))).astype(np.float32)
        )
        projection = cp.asarray(
            [[120.0, 3.0, -1.0], [2.0, 95.0, 4.0]],
            dtype=cp.float32,
        )

        diagonal = cp.zeros((128, 3, 3), dtype=cp.float32)
        diagonal[:, 0, 0] = scales[:, 0]
        diagonal[:, 1, 1] = scales[:, 1]
        diagonal[:, 2, 2] = scales[:, 2]
        factors = cp.matmul(rotations, diagonal)
        covariance = cp.matmul(factors, factors.transpose(0, 2, 1))
        expected = cp.matmul(
            cp.matmul(projection[None, :, :], covariance),
            projection.T[None, :, :],
        )

        actual = project_covariance_2d(rotations, scales, projection)

        assert cp.allclose(actual, expected, rtol=2e-5, atol=2e-5)

    def test_view_matrix_identity(self):
        R = np.eye(3, dtype=np.float32)
        T = np.zeros(3, dtype=np.float32)
        V = make_view_matrix(R, T)
        assert V.shape == (4, 4)
        np.testing.assert_allclose(V, np.eye(4, dtype=np.float32), atol=1e-6)

    def test_ortho_proj_symmetry(self):
        P = make_ortho_proj(-10, 10, -10, 10, 0.1, 100)
        assert P.shape == (4, 4)
        assert abs(float(P[0, 3])) < 1e-6
        assert abs(float(P[1, 3])) < 1e-6


# ---------------------------------------------------------------------------
#  Partitioning
# ---------------------------------------------------------------------------

class TestPartition:
    def test_grid_count(self):
        scene = _make_scene()
        cells = compute_partition_grid(scene, m=2, n=3)
        assert len(cells) == 6

    def test_partition_scene(self):
        scene = _make_scene(n_cams=40, n_points=2000)
        for camera in scene.train_cameras:
            camera.R = np.diag([1.0, -1.0, -1.0]).astype(np.float32)
            camera.T[2] = 100.0
        transform = {"R": np.eye(3).tolist(), "scale": 1.0, "t": [0, 0, 0]}
        frame = geographic_scene_frame(
            scene.point_cloud.points,
            transform,
            terrain_margin_m=0.0,
        )
        cells = partition_scene(
            scene,
            m=2,
            n=2,
            min_cameras=2,
            geographic_frame=frame,
        )
        assert len(cells) >= 1
        for bounds, cell_scene in cells:
            assert len(cell_scene.train_cameras) >= 2

    def test_no_partition(self):
        scene = _make_scene()
        for camera in scene.train_cameras:
            camera.R = np.diag([1.0, -1.0, -1.0]).astype(np.float32)
            camera.T[2] = 100.0
        transform = {"R": np.eye(3).tolist(), "scale": 1.0, "t": [0, 0, 0]}
        frame = geographic_scene_frame(
            scene.point_cloud.points,
            transform,
            terrain_margin_m=0.0,
        )
        cells = partition_scene(
            scene,
            m=1,
            n=1,
            geographic_frame=frame,
        )
        assert len(cells) == 1

    def test_grid_extent_comes_from_ground_points_not_camera_centres(self):
        point_cloud = PointCloud(
            points=np.array(
                [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 20.0, 0.0]],
                dtype=np.float32,
            ),
            colors=np.ones((3, 3), dtype=np.float32),
            normals=np.zeros((3, 3), dtype=np.float32),
        )
        cameras = _make_cameras(5)
        for camera in cameras:
            camera.T[:] = 10_000.0
        scene = build_scene_info(cameras, [], point_cloud)

        cells = compute_partition_grid(
            scene,
            m=1,
            n=2,
            overlap=0.2,
        )

        assert cells[0].core_x_min == pytest.approx(0.0)
        assert cells[-1].core_x_max == pytest.approx(10.0)
        assert cells[-1].core_y_max == pytest.approx(20.0)
        assert cells[0].buffer_x_min == pytest.approx(-1.0)

    def test_sim3_projection_defines_projected_ground_grid(self):
        transform = {
            "R": [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
            "scale": 2.0,
            "t": [100.0, 200.0, 10.0],
        }
        linear, offset = ground_projection_from_sim3(transform)
        point_cloud = PointCloud(
            points=np.array(
                [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 20.0, 0.0]],
                dtype=np.float32,
            ),
            colors=np.ones((3, 3), dtype=np.float32),
            normals=np.zeros((3, 3), dtype=np.float32),
        )
        scene = build_scene_info(_make_cameras(5), [], point_cloud)

        cell = compute_partition_grid(
            scene,
            m=1,
            n=1,
            overlap=0.0,
            model_to_ground_linear=linear,
            model_to_ground_offset=offset,
        )[0]

        assert cell.core_x_min == pytest.approx(60.0)
        assert cell.core_x_max == pytest.approx(100.0)
        assert cell.core_y_min == pytest.approx(200.0)
        assert cell.core_y_max == pytest.approx(220.0)

    def test_adjacent_cores_have_single_boundary_owner(self):
        scene = _make_scene(n_points=500)
        left, right = compute_partition_grid(
            scene,
            m=1,
            n=2,
            overlap=0.2,
        )
        boundary = np.array(
            [[left.core_x_max, left.core_y_min, 0.0]],
            dtype=np.float32,
        )

        assert not bool(left.core_mask(boundary, array_module=np)[0])
        assert bool(right.core_mask(boundary, array_module=np)[0])

    @pytest.mark.gpu
    @requires_gpu
    def test_merge_uses_projected_ground_core(self):
        ground_linear = ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0))
        common = {
            "core_y_min": -1.0,
            "core_y_max": 1.0,
            "buffer_x_min": -1.0,
            "buffer_x_max": 11.0,
            "buffer_y_min": -2.0,
            "buffer_y_max": 2.0,
            "row": 0,
            "model_to_ground_linear": ground_linear,
        }
        left_cell = CellBounds(
            core_x_min=0.0,
            core_x_max=5.0,
            col=0,
            **common,
        )
        right_cell = CellBounds(
            core_x_min=5.0,
            core_x_max=10.0,
            col=1,
            include_core_x_max=True,
            **common,
        )
        left_model = _make_gpu_model(1)
        right_model = _make_gpu_model(1)
        left_model._xyz[:] = cp.asarray([[0.0, 8.0, 0.0]], dtype=cp.float32)
        right_model._xyz[:] = cp.asarray([[0.0, 8.0, 0.0]], dtype=cp.float32)
        left_model._features_dc[:] = 1.0
        right_model._features_dc[:] = 2.0

        merged = merge_models(
            [(left_cell, left_model), (right_cell, right_model)]
        )

        assert merged.num_gaussians == 1
        assert float(cp.asnumpy(merged._features_dc)[0, 0, 0]) == pytest.approx(2.0)


# ---------------------------------------------------------------------------
#  Ortho renderer
# ---------------------------------------------------------------------------

@pytest.mark.gpu
@requires_gpu
class TestOrthoRenderer:
    def test_compute_extent(self):
        model = _make_gpu_model(100)
        ext = compute_ortho_extent(model)
        assert len(ext) == 6
        x0, x1, y0, y1, z0, z1 = ext
        assert x1 > x0
        assert y1 > y0
        assert z1 > z0

    def test_render_small(self):
        """Render a small orthophoto from random Gaussians (smoke test)."""
        model = _make_gpu_model(200)
        result = render_orthophoto(model, gsd=1.0, chunk_size=512)
        assert "rgb" in result
        assert "height" in result
        assert result["rgb"].ndim == 3

    def test_exact_dimensions_avoid_floating_point_border_pixel(self):
        model = _make_gpu_model(1)
        result = render_orthophoto(
            model,
            gsd=0.02,
            extent=(1000.0, 1010.24, 1000.0, 1000.02, -1.0, 1.0),
            pixel_dimensions=(512, 1),
            chunk_size=512,
        )

        assert result["rgb"].shape == (1, 512, 3)
        assert result["height"].shape == (1, 512)

    def test_translucent_surface_color_is_not_composited_on_white(self):
        """Ortho RGB represents surface colour independently of opacity."""
        model = GaussianModel(sh_degree=0, opacity_sh_enabled=False)
        model._xyz = cp.array([[0.0, 0.0, 10.0]], dtype=cp.float32)
        model._features_dc = cp.zeros((1, 1, 3), dtype=cp.float32)
        model._features_rest = cp.empty((1, 0, 3), dtype=cp.float32)
        model._scaling = cp.zeros((1, 3), dtype=cp.float32)
        model._rotation = cp.array([[1.0, 0.0, 0.0, 0.0]], dtype=cp.float32)
        model._opacity = cp.zeros((1, 1), dtype=cp.float32)
        model._opacity_sh = cp.empty((1, 0), dtype=cp.float32)

        result = render_orthophoto(
            model,
            gsd=1.0,
            extent=(-0.5, 0.5, -0.5, 0.5, 9.0, 11.0),
            chunk_size=512,
        )

        assert result["rgb"][0, 0].tolist() == [127, 127, 127]

    def test_sim3_rotation_keeps_sh_in_training_frame(self):
        """Rotated geometry must evaluate directional SH in its learned frame."""
        sh = cp.zeros((1, 4, 3), dtype=cp.float32)
        sh[:, 3, 0] = 1.0
        rotation_geo_from_training = np.array(
            [[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]],
            dtype=np.float32,
        )
        geo_direction = cp.array([[0.0, 0.0, 1.0]], dtype=cp.float32)
        training_direction = (
            cp.asarray(rotation_geo_from_training.T) @ geo_direction[0]
        )

        expected = eval_sh(1, training_direction[None, :], sh)
        uncorrected = eval_sh(1, geo_direction, sh)

        assert not cp.allclose(expected, uncorrected)

    def test_depth_is_normalized_by_accumulated_opacity(self):
        """A translucent surface keeps its geometric depth, not alpha*depth."""
        _rgb, depth, alpha = rasterize_ortho(
            means_3d=cp.array([[0.0, 0.0, 10.0]], dtype=cp.float32),
            quats=cp.array([[1.0, 0.0, 0.0, 0.0]], dtype=cp.float32),
            scales=cp.ones((1, 3), dtype=cp.float32),
            opacities=cp.array([0.5], dtype=cp.float32),
            sh_coeffs=cp.zeros((1, 1, 3), dtype=cp.float32),
            sh_degree=0,
            viewmat=cp.eye(4, dtype=cp.float32),
            fx=1.0,
            fy=1.0,
            cx=0.5,
            cy=0.5,
            width=1,
            height=1,
        )

        assert float(depth[0, 0]) == pytest.approx(10.0, abs=1e-5)
        assert 0.0 < float(alpha[0, 0]) < 1.0

    def test_opacity_sh_changes_nadir_alpha(self):
        common = {
            "means_3d": cp.array([[0.0, 0.0, 10.0]], dtype=cp.float32),
            "quats": cp.array([[1.0, 0.0, 0.0, 0.0]], dtype=cp.float32),
            "scales": cp.ones((1, 3), dtype=cp.float32),
            "opacities": cp.array([0.5], dtype=cp.float32),
            "sh_coeffs": cp.zeros((1, 4, 3), dtype=cp.float32),
            "sh_degree": 1,
            "viewmat": cp.eye(4, dtype=cp.float32),
            "fx": 1.0,
            "fy": 1.0,
            "cx": 0.5,
            "cy": 0.5,
            "width": 1,
            "height": 1,
        }
        _rgb, _depth, scalar_alpha = rasterize_ortho(**common)
        opacity_sh = cp.zeros((1, 3), dtype=cp.float32)
        opacity_sh[0, 1] = 2.0
        _rgb, _depth, directional_alpha = rasterize_ortho(
            **common, opacity_sh=opacity_sh
        )

        assert float(directional_alpha[0, 0]) > float(scalar_alpha[0, 0])


# ---------------------------------------------------------------------------
#  GeoTIFF writer
# ---------------------------------------------------------------------------

class TestGeoWriter:
    def test_bigtiff_is_enabled_for_large_outputs(self):
        assert _geotiff_creation_options(photometric="rgb") == {
            "compress": "lzw",
            "BIGTIFF": "IF_SAFER",
            "photometric": "rgb",
        }

    def test_write_rgb(self):
        rgb = np.random.randint(0, 255, (100, 200, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.tif")
            write_geotiff(path, rgb, x_min=0.0, y_max=100.0, gsd=1.0, crs="EPSG:32631")
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0

    def test_write_with_height(self):
        import rasterio

        rgb = np.random.randint(0, 255, (50, 100, 3), dtype=np.uint8)
        height = np.random.randn(50, 100).astype(np.float32)
        height[0, 0] = np.nan
        with tempfile.TemporaryDirectory() as tmpdir:
            rgb_path = os.path.join(tmpdir, "test.tif")
            h_path = os.path.join(tmpdir, "test.height.tif")
            write_geotiff(rgb_path, rgb, 0.0, 50.0, 1.0, crs="EPSG:32631",
                          height_map=height, height_output_path=h_path)
            assert os.path.exists(rgb_path)
            assert os.path.exists(h_path)
            with rasterio.open(h_path) as dataset:
                assert np.isnan(dataset.nodata)
                assert np.isnan(dataset.read(1)[0, 0])
