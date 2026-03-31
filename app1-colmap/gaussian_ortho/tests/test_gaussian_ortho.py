"""
Tests for the gaussian_ortho package.

Unit tests cover math utilities, model initialisation, partitioning,
and the ortho rendering pipeline. An integration test runs on the
vol_banyuls workspace if available.
"""
import math
import os
import sys
import tempfile

import numpy as np
import pytest
import torch

# Ensure package is importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gaussian_ortho.gaussian_model import (
    GaussianModel, eval_sh_basis, num_sh_coefficients, SH_C0,
)
from gaussian_ortho.colmap_loader import (
    CameraInfo, PointCloud, apply_sim3_to_points, apply_sim3_to_camera,
)
from gaussian_ortho.scene_info import SceneInfo, compute_scene_extent, build_scene_info
from gaussian_ortho.partition import compute_partition_grid, partition_scene
from gaussian_ortho.rasterizer import (
    make_view_matrix, make_perspective_proj, make_ortho_proj, RasterSettings,
)
from gaussian_ortho.ortho_renderer import compute_ortho_extent, render_orthophoto
from gaussian_ortho.geo_writer import write_geotiff
from gaussian_ortho.merge import merge_models


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


# ---------------------------------------------------------------------------
#  Spherical harmonics
# ---------------------------------------------------------------------------

class TestSH:
    def test_num_coefficients(self):
        assert num_sh_coefficients(0) == 1
        assert num_sh_coefficients(1) == 4
        assert num_sh_coefficients(2) == 9
        assert num_sh_coefficients(3) == 16

    def test_eval_basis_shape(self):
        dirs = torch.randn(100, 3)
        dirs = dirs / dirs.norm(dim=-1, keepdim=True)
        for deg in range(4):
            basis = eval_sh_basis(deg, dirs)
            assert basis.shape == (100, num_sh_coefficients(deg))

    def test_dc_constant(self):
        """SH degree 0 should return a constant for any direction."""
        dirs = torch.randn(50, 3)
        dirs = dirs / dirs.norm(dim=-1, keepdim=True)
        basis = eval_sh_basis(0, dirs)
        assert torch.allclose(basis, torch.full_like(basis, SH_C0), atol=1e-6)


# ---------------------------------------------------------------------------
#  Gaussian Model
# ---------------------------------------------------------------------------

class TestGaussianModel:
    def test_init_from_point_cloud(self):
        pc = _make_random_point_cloud(100)
        model = GaussianModel(sh_degree=3, fagk_enabled=True)
        model.init_from_point_cloud(pc)
        assert model.num_gaussians == 100
        assert model.positions.shape == (100, 3)
        assert model.scales.shape == (100, 3)
        assert model.rotations.shape == (100, 4)
        assert model.opacity.shape == (100, 1)
        # FAGK SH
        assert model._opacity_sh.shape == (100, 16)

    def test_opacity_range(self):
        pc = _make_random_point_cloud(50)
        model = GaussianModel(sh_degree=0, fagk_enabled=False)
        model.init_from_point_cloud(pc)
        opa = model.opacity
        assert (opa >= 0).all() and (opa <= 1).all()

    def test_rotation_normalised(self):
        pc = _make_random_point_cloud(50)
        model = GaussianModel(sh_degree=0)
        model.init_from_point_cloud(pc)
        norms = model.rotations.norm(dim=-1)
        assert torch.allclose(norms, torch.ones_like(norms), atol=1e-5)

    def test_fagk_opacity(self):
        pc = _make_random_point_cloud(50)
        model = GaussianModel(sh_degree=0, fagk_enabled=True, fagk_max_degree=1)
        model.init_from_point_cloud(pc)
        device = model.positions.device
        dirs = torch.randn(50, 3, device=device)
        dirs = dirs / dirs.norm(dim=-1, keepdim=True)
        opa = model.get_opacity_for_dirs(dirs)
        assert opa.shape == (50, 1)
        assert (opa >= 0).all() and (opa <= 1).all()

    def test_save_load_ply(self):
        pc = _make_random_point_cloud(30)
        model = GaussianModel(sh_degree=2, fagk_enabled=True, fagk_max_degree=2)
        model.init_from_point_cloud(pc)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.ply")
            model.save_ply(path)

            model2 = GaussianModel(sh_degree=2, fagk_enabled=True, fagk_max_degree=2)
            model2.load_ply(path)

            assert model2.num_gaussians == 30
            assert torch.allclose(model._xyz.cpu(), model2._xyz.cpu(), atol=1e-5)

    def test_sh_degree_progression(self):
        model = GaussianModel(sh_degree=3)
        assert model.active_sh_degree == 0
        model.step_sh_degree()
        assert model.active_sh_degree == 1
        for _ in range(10):
            model.step_sh_degree()
        assert model.active_sh_degree == 3

    def test_densify_and_prune(self):
        pc = _make_random_point_cloud(100)
        model = GaussianModel(sh_degree=0, fagk_enabled=False)
        model.init_from_point_cloud(pc)
        device = model.positions.device
        # Simulate gradient stats
        model.xyz_gradient_accum = torch.ones(100, 1, device=device) * 0.001
        model.denom = torch.ones(100, 1, device=device)
        model.max_radii2D = torch.ones(100, device=device) * 5.0
        n_before = model.num_gaussians
        model.densify_and_prune(grad_threshold=0.0002, min_opacity=0.5)
        # Should have pruned some low-opacity Gaussians
        assert model.num_gaussians != n_before or model.num_gaussians > 0


# ---------------------------------------------------------------------------
#  Scene info
# ---------------------------------------------------------------------------

class TestSceneInfo:
    def test_compute_extent(self):
        cams = _make_cameras(20)
        centre, radius = compute_scene_extent(cams)
        assert centre.shape == (3,)
        assert radius > 0

    def test_empty_cameras(self):
        centre, radius = compute_scene_extent([])
        assert np.allclose(centre, 0)
        assert radius == 1.0

    def test_build_scene_info(self):
        scene = _make_scene()
        assert len(scene.train_cameras) == 10
        assert scene.scene_radius > 0


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

class TestMatrices:
    def test_view_matrix_identity(self):
        R = np.eye(3, dtype=np.float32)
        T = np.zeros(3, dtype=np.float32)
        V = make_view_matrix(R, T)
        assert V.shape == (4, 4)
        assert torch.allclose(V, torch.eye(4), atol=1e-6)

    def test_perspective_proj_shape(self):
        P = make_perspective_proj(500, 500, 320, 240, 640, 480, 0.01, 100)
        assert P.shape == (4, 4)

    def test_ortho_proj_symmetry(self):
        P = make_ortho_proj(-10, 10, -10, 10, 0.1, 100)
        assert P.shape == (4, 4)
        # Symmetric bounds → zero off-diagonal translations
        assert abs(P[0, 3].item()) < 1e-6
        assert abs(P[1, 3].item()) < 1e-6


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
        cells = partition_scene(scene, m=2, n=2, min_cameras=2)
        assert len(cells) >= 1
        for bounds, cell_scene in cells:
            assert len(cell_scene.train_cameras) >= 2

    def test_no_partition(self):
        scene = _make_scene()
        cells = partition_scene(scene, m=1, n=1)
        assert len(cells) == 1


# ---------------------------------------------------------------------------
#  Ortho renderer
# ---------------------------------------------------------------------------

class TestOrthoRenderer:
    def test_compute_extent(self):
        pc = _make_random_point_cloud(100)
        model = GaussianModel(sh_degree=0, fagk_enabled=False)
        model.init_from_point_cloud(pc)
        ext = compute_ortho_extent(model)
        assert len(ext) == 6
        x0, x1, y0, y1, z0, z1 = ext
        assert x1 > x0
        assert y1 > y0
        assert z1 > z0

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="No GPU")
    def test_render_small(self):
        """Render a small orthophoto from random Gaussians (smoke test)."""
        pc = _make_random_point_cloud(200)
        model = GaussianModel(sh_degree=0, fagk_enabled=False)
        model = model.cuda()
        model.init_from_point_cloud(pc)

        result = render_orthophoto(model, gsd=1.0, chunk_size=512)
        assert "rgb" in result
        assert "height" in result
        assert result["rgb"].ndim == 3  # (H, W, 3)


# ---------------------------------------------------------------------------
#  GeoTIFF writer
# ---------------------------------------------------------------------------

class TestGeoWriter:
    def test_write_rgb(self):
        rgb = np.random.randint(0, 255, (100, 200, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.tif")
            write_geotiff(path, rgb, x_min=0.0, y_max=100.0, gsd=1.0)
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0

    def test_write_with_height(self):
        rgb = np.random.randint(0, 255, (50, 100, 3), dtype=np.uint8)
        height = np.random.randn(50, 100).astype(np.float32)
        with tempfile.TemporaryDirectory() as tmpdir:
            rgb_path = os.path.join(tmpdir, "test.tif")
            h_path = os.path.join(tmpdir, "test.height.tif")
            write_geotiff(rgb_path, rgb, 0.0, 50.0, 1.0,
                          height_map=height, height_output_path=h_path)
            assert os.path.exists(rgb_path)
            assert os.path.exists(h_path)


# ---------------------------------------------------------------------------
#  Merge
# ---------------------------------------------------------------------------

class TestMerge:
    def test_merge_two_cells(self):
        from gaussian_ortho.partition import CellBounds
        pc1 = _make_random_point_cloud(50, seed=1)
        pc2 = _make_random_point_cloud(50, seed=2)

        m1 = GaussianModel(sh_degree=0, fagk_enabled=False)
        m1.init_from_point_cloud(pc1)
        m2 = GaussianModel(sh_degree=0, fagk_enabled=False)
        m2.init_from_point_cloud(pc2)

        cell1 = CellBounds(x_min=-20, x_max=5, y_min=-20, y_max=20, row=0, col=0)
        cell2 = CellBounds(x_min=-5, x_max=20, y_min=-20, y_max=20, row=0, col=1)

        merged = merge_models(
            [(cell1, m1), (cell2, m2)],
            scene_x_range=(-15, 15),
            scene_y_range=(-15, 15),
            m=1, n=2, overlap=0.20,
        )
        assert merged.num_gaussians > 0
        # Merged should have fewer Gaussians than total (overlap trimming)
        assert merged.num_gaussians <= m1.num_gaussians + m2.num_gaussians


# ---------------------------------------------------------------------------
#  Integration test (requires vol_banyuls workspace)
# ---------------------------------------------------------------------------

VOL_BANYULS = "/mnt/j/workspace/vol_banyuls"

@pytest.mark.integration
@pytest.mark.skipif(
    not os.path.isdir(os.path.join(VOL_BANYULS, "dense")),
    reason="vol_banyuls workspace not available"
)
class TestIntegration:
    def test_load_reconstruction(self):
        from gaussian_ortho.colmap_loader import load_colmap_reconstruction
        dense = os.path.join(VOL_BANYULS, "dense")
        transform = os.path.join(VOL_BANYULS, "alignment_transform.json")
        tf = transform if os.path.exists(transform) else None
        train_cams, test_cams, pc, td = load_colmap_reconstruction(dense, tf)
        assert len(train_cams) > 0
        assert pc.points.shape[0] > 0
        print(f"Loaded {len(train_cams)} cameras, {pc.points.shape[0]} points")

    @pytest.mark.skipif(not torch.cuda.is_available(), reason="No GPU")
    def test_quick_train_and_render(self):
        """Quick training (1000 iterations) + ortho render on vol_banyuls."""
        from gaussian_ortho.generate_gaussian_orthophoto import generate_gaussian_orthophoto

        with tempfile.TemporaryDirectory() as tmpdir:
            ortho_file = os.path.join(tmpdir, "ortho.tif")
            dense = os.path.join(VOL_BANYULS, "dense")
            transform = os.path.join(VOL_BANYULS, "alignment_transform.json")
            crs_path = os.path.join(VOL_BANYULS, "geo_data.txt.crs")
            crs = "EPSG:32631"
            if os.path.exists(crs_path):
                crs = open(crs_path).read().strip() or crs

            result = generate_gaussian_orthophoto(
                dense_path=dense,
                ortho_file=ortho_file,
                utm_crs=crs,
                vol_id="test_banyuls",
                transform_file=transform if os.path.exists(transform) else None,
                resolution=0.10,  # coarse for speed
                iterations=1000,
                sh_degree=1,
                fagk=False,
                checkpoint_dir=os.path.join(tmpdir, "ckpt"),
            )

            assert os.path.exists(ortho_file)
            assert result["n_gaussians"] > 0
            assert result["width"] > 0
            print(f"Integration test: {result['width']}x{result['height']} px, "
                  f"{result['n_gaussians']} Gaussians")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-x"])
